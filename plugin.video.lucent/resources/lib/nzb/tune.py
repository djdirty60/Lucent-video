# -*- coding: utf-8 -*-
"""'Tune connections': measures a Usenet provider and suggests a port and a number of connections.

In a few minutes it:
1. finds how many connections the account really accepts (connections are opened until the provider refuses one;
   other devices on the same account lower this number), up to the setting's maximum of 60;
2. with an encrypted connection: compares the secure ports 563 and 443;
3. measures the download speed with more and more connections.

The test data are recent articles of one big binary group (GROUP + BODY by number), so no NZB is needed. At most
MAX_BYTES are fetched; with a block account that counts. Then it suggests:
- connections: brought down when the setting is above the real limit (more than the limit gives refused connections
  and stalls); otherwise never lowered, and only raised when more connections were clearly faster;
- port: the other secure port only when it was clearly faster.
Nothing changes until you press Apply. The username and password are never logged or shown.
"""
import random
import threading
import time

import xbmc
import xbmcaddon
import xbmcgui

import util

from . import nntp, server

T = util.T
GROUPS = ("alt.binaries.boneless", "alt.binaries.multimedia", "alt.binaries.misc")
LIMIT_MAX = 60                  # the setting's maximum
MAX_BYTES = 800 * 1048576       # test data for the whole run
STEP_SECONDS = 6.0
PORT_CONNECTIONS = 8
CLEARLY = 1.10                  # 'clearly faster': at least 10 % more
SETTLE = 10                     # seconds: Lucent's own connections were just closed, the provider may still count them


class _Probe(nntp.Connection):
    chunk = None

    def __init__(self, *args, **kwargs):
        nntp.Connection.__init__(self, *args, **kwargs)
        self.chunk = bytearray(262144)

    def group(self, name):
        code, text = self._command(b"GROUP " + name.encode("ascii"))
        if code != 211:
            raise nntp.NntpError("GROUP %d" % code)
        parts = text.split()
        return int(parts[1]), int(parts[2])            # first, last article number

    def body_size(self, number):
        """One article by number; returns its size (0 when it is gone). Only counts the bytes and looks for the end
        mark at the edge of each piece: the test must measure the provider, not how fast Python is on this box."""
        code, _text = self._command(b"BODY %d" % number)
        if code in (420, 423, 430):
            return 0
        if code != 222:
            raise nntp.NntpError("BODY %d" % code)
        size, tail = len(self.buffer), bytes(self.buffer[-4:])
        if b"\r\n.\r\n" in bytes(self.buffer) or bytes(self.buffer[:3]) == b".\r\n":
            self.buffer = bytearray()                   # small: the whole body was already read with the status line
            return size
        self.buffer = bytearray()
        view = memoryview(self.chunk)
        while True:
            got = self.sock.recv_into(view)
            if not got:
                raise nntp.NntpError("connection closed")
            size += got
            edge = tail + bytes(view[max(0, got - 5):got])
            if edge.endswith(b"\r\n.\r\n") or edge == b".\r\n":
                return size
            tail = edge[-4:]


class _Run:
    def __init__(self, provider, progress):
        self.p = provider
        self.progress = progress
        self.bytes = 0
        self.range = None
        self.lock = threading.Lock()

    def cancelled(self):
        return self.progress.iscanceled() or xbmc.Monitor().abortRequested() or self.bytes >= MAX_BYTES

    def connect(self, port=None):
        return _Probe(self.p["host"], port or self.p["port"], self.p["ssl"], self.p["user"], self.p["password"], timeout=15)

    # ------------------------------------------------------------------ 1. the real limit
    def limit(self):
        """Opens connections ten at a time and keeps them open, until one is refused or LIMIT_MAX is reached."""
        held, refused, reason = [], False, ""
        try:
            while len(held) < LIMIT_MAX and not refused and not self.cancelled():
                want = min(10, LIMIT_MAX - len(held))
                results = [None] * want

                def open_one(slot):
                    try:
                        results[slot] = self.connect()
                    except Exception as error:      # noqa: BLE001 - every kind of refusal ends the count
                        results[slot] = error
                threads = [threading.Thread(target=open_one, args=(n,), daemon=True) for n in range(want)]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join(30)
                for item in results:
                    if isinstance(item, nntp.Connection):
                        held.append(item)
                    else:
                        refused = True
                        reason = type(item).__name__ if item is not None else "timeout"
                self.progress.update(5 + int(20 * len(held) / LIMIT_MAX),
                                     T("Verbindingen openen: %d" % len(held), "Opening connections: %d" % len(held)))
        finally:
            for connection in held:
                connection.close()
        return len(held), refused, reason

    # ------------------------------------------------------------------ test data
    def articles(self):
        if self.range:
            return self.range
        connection = self.connect()
        try:
            for name in GROUPS:
                try:
                    first, last = connection.group(name)
                except nntp.NntpError:
                    continue
                if last - first > 100000:
                    self.range = (name, max(first, last - 3000000), last - 5000)
                    return self.range
        finally:
            connection.close()
        raise nntp.NntpError("no test group")

    # ------------------------------------------------------------------ 2 and 3. speed
    def speed(self, connections, port=None, seconds=STEP_SECONDS):
        """MB/s with this many connections, each fetching random articles for a few seconds."""
        name, low, high = self.articles()
        got = [0] * connections
        stop = time.time() + seconds
        errors = []

        def work(slot):
            try:
                connection = self.connect(port)
            except Exception as error:      # noqa: BLE001
                errors.append(type(error).__name__)
                return
            try:
                connection.group(name)
                while time.time() < stop and self.bytes < MAX_BYTES:
                    size = connection.body_size(random.randint(low, high))
                    got[slot] += size
                    with self.lock:
                        self.bytes += size
            except Exception as error:      # noqa: BLE001
                errors.append(type(error).__name__)
            finally:
                connection.close()
        started = time.time()
        threads = [threading.Thread(target=work, args=(n,), daemon=True) for n in range(connections)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(seconds + 40)
        elapsed = max(0.1, time.time() - started)
        return sum(got) / 1048576.0 / elapsed, len(errors)


def _steps(limit):
    steps = [n for n in (4, 8, 12, 16, 20, 30, 40, 50, 60) if n <= limit]
    if limit not in steps:
        steps.append(limit)
    return steps


def _even(number):
    return max(2, min(LIMIT_MAX, int(number) // 2 * 2))


def measure(provider, progress):
    """Returns the results as a dict (or raises)."""
    run = _Run(provider, progress)
    out = {"current_conns": provider["connections"], "current_port": provider["port"], "ssl": provider["ssl"]}
    progress.update(1, T("Wachten tot oude verbindingen dicht zijn…", "Waiting for old connections to close…"))
    for _ in range(SETTLE * 10):                                # a provider may still count connections just closed
        if run.cancelled() or xbmc.Monitor().waitForAbort(0.1):
            return out
    progress.update(2, T("Inloggen…", "Logging in…"))
    run.connect().close()                                       # a wrong password stops here, before anything else
    limit, refused, reason = run.limit()
    out.update(limit=limit, limit_reached=refused, limit_reason=reason)
    if run.cancelled() or limit < 1:
        return out
    if provider["ssl"]:
        ports = {}
        for number, port in enumerate(sorted({563, 443, provider["port"]})):
            progress.update(30 + 8 * number, T("Poort %d vergelijken…" % port, "Comparing port %d…" % port))
            try:
                ports[port] = run.speed(min(PORT_CONNECTIONS, limit), port)[0]
            except Exception:                   # noqa: BLE001 - a port that does not answer
                ports[port] = 0.0
            if run.cancelled():
                break
        out["ports"] = ports
        best = max(ports, key=ports.get) if ports else provider["port"]
        current = ports.get(provider["port"], 0.0)
        out["port"] = best if ports.get(best, 0) >= current * CLEARLY and best != provider["port"] else provider["port"]
    else:
        out["port"] = provider["port"]
    speeds, slower = {}, 0
    steps = _steps(limit)
    for number, connections in enumerate(steps):
        if run.cancelled():
            break
        progress.update(55 + int(40 * number / len(steps)),
                        T("Snelheid met %d verbindingen…" % connections, "Speed with %d connections…" % connections))
        before = max(speeds.values()) if speeds else 0.0
        speeds[connections] = run.speed(connections, out["port"])[0]
        slower = slower + 1 if speeds[connections] < before * 1.03 else 0
        if slower >= 2:
            break                                               # two steps in a row no faster: stop early
    out["speeds"] = speeds
    out["bytes"] = run.bytes
    out["conns"] = _suggest(provider["connections"], limit, refused, speeds)
    return out


def _suggest(current, limit, refused, speeds):
    if refused and current > limit:
        return _even(limit)                     # over the real limit: always down to it
    if not speeds:
        return current
    top = max(speeds.values())
    fastest = min(n for n, s in speeds.items() if s >= top * 0.95)      # the fewest connections for (almost) the top
    at_current = max((s for n, s in speeds.items() if n <= current), default=0.0)
    if fastest > current and top >= at_current * CLEARLY:
        return _even(fastest)                   # clearly faster with more
    return current


def _report(result, backup):
    lines = []
    limit = result.get("limit", 0)
    if result.get("limit_reached"):
        lines.append(T("Je account accepteert [B]%d[/B] verbindingen (nu ingesteld: %d)." % (limit, result["current_conns"]),
                       "Your account accepts [B]%d[/B] connections (set now: %d)." % (limit, result["current_conns"])))
    else:
        lines.append(T("Je account accepteert er minstens [B]%d[/B] (meer test Lucent niet; nu ingesteld: %d)." % (limit, result["current_conns"]),
                       "Your account accepts at least [B]%d[/B] (Lucent tests no more; set now: %d)." % (limit, result["current_conns"])))
    if result.get("ports"):
        lines.append(T("Poorten: ", "Ports: ") + ", ".join("%d: %.1f MB/s" % (p, s) for p, s in sorted(result["ports"].items())))
    if result.get("speeds"):
        lines.append(T("Snelheid: ", "Speed: ") + ", ".join("%d: %.1f MB/s" % (n, s) for n, s in sorted(result["speeds"].items())))
    if result.get("bytes"):
        lines.append(T("Testdata: %d MB." % (result["bytes"] >> 20), "Test data: %d MB." % (result["bytes"] >> 20)))
    change = []
    if result.get("conns", result["current_conns"]) != result["current_conns"]:
        change.append(T("verbindingen %d → [B]%d[/B]" % (result["current_conns"], result["conns"]),
                        "connections %d → [B]%d[/B]" % (result["current_conns"], result["conns"])))
    if result.get("port", result["current_port"]) != result["current_port"]:
        change.append(T("poort %d → [B]%d[/B]" % (result["current_port"], result["port"]),
                        "port %d → [B]%d[/B]" % (result["current_port"], result["port"])))
    lines.append("")
    if change:
        lines.append(T("Voorstel: ", "Suggestion: ") + ", ".join(change) + ".")
    else:
        lines.append(T("Voorstel: alles laten zoals het is.", "Suggestion: keep everything as it is."))
    return "[CR]".join(lines), bool(change)


def run(backup=None):
    """The button in the settings (Usenet)."""
    dialog = xbmcgui.Dialog()
    if xbmc.Player().isPlaying():
        return dialog.ok(T("Verbindingen afstemmen", "Tune connections"),
                         T("Stop eerst wat er speelt: de test meet je volle snelheid.", "Stop what is playing first: the test measures your full speed."))
    if backup is None:
        if server.provider(True):
            picked = dialog.select(T("Welke provider?", "Which provider?"), [T("Provider", "Provider"), T("Reserve-provider", "Backup provider")])
            if picked < 0:
                return None
            backup = picked == 1
        else:
            backup = False
    provider = server.provider(backup)
    if not provider or not provider.get("host"):
        return dialog.ok(T("Verbindingen afstemmen", "Tune connections"), T("Vul eerst je provider in.", "Fill in your provider first."))
    if not dialog.yesno(T("Verbindingen afstemmen", "Tune connections"),
                        T("Lucent meet in een paar minuten hoeveel verbindingen je account echt toestaat, vergelijkt de veilige poorten en "
                          "meet je snelheid. Daarvoor haalt het tot ongeveer %d MB testdata op; bij een blokaccount telt dat mee. "
                          "Speel intussen niets af. Er verandert niets tot je op Toepassen drukt.[CR][CR]Starten?" % (MAX_BYTES >> 20),
                          "In a few minutes Lucent measures how many connections your account really allows, compares the secure ports "
                          "and measures your speed. It fetches up to about %d MB of test data; with a block account that counts. "
                          "Don't play anything meanwhile. Nothing changes until you press Apply.[CR][CR]Start?" % (MAX_BYTES >> 20))):
        return None
    server.stop()                                  # Lucent's own Usenet connections out of the way
    progress = xbmcgui.DialogProgress()
    progress.create(T("Verbindingen afstemmen", "Tune connections"), T("Bezig…", "Working…"))
    try:
        result = measure(provider, progress)
    except nntp.AuthError:
        progress.close()
        return dialog.ok(T("Verbindingen afstemmen", "Tune connections"), T("Inloggen lukt niet: kijk je gebruikersnaam en wachtwoord na.",
                                                                            "Login failed: check your username and password."))
    except Exception as error:      # noqa: BLE001
        progress.close()
        util.log("usenet tune: %s" % type(error).__name__)
        return dialog.ok(T("Verbindingen afstemmen", "Tune connections"), T("De test lukte niet (%s)." % type(error).__name__,
                                                                            "The test did not work (%s)." % type(error).__name__))
    progress.close()
    util.log("usenet tune (%s): limit %d%s, ports %s, speeds %s, %d MB" % (
        "backup" if backup else "provider", result.get("limit", 0), "" if result.get("limit_reached") else "+",
        ", ".join("%d %.1f" % kv for kv in sorted((result.get("ports") or {}).items())),
        ", ".join("%d %.1f" % kv for kv in sorted((result.get("speeds") or {}).items())), result.get("bytes", 0) >> 20))
    text, change = _report(result, backup)
    if not change:
        return dialog.ok(T("Verbindingen afstemmen", "Tune connections"), text)
    if dialog.yesno(T("Verbindingen afstemmen", "Tune connections"), text, nolabel=T("Niet nu", "Not now"), yeslabel=T("Toepassen", "Apply")):
        pre = "nzb2_" if backup else "nzb_"
        addon = xbmcaddon.Addon(util.ADDON_ID)
        addon.setSetting(pre + "conns", str(result["conns"]))
        addon.setSetting(pre + "port", str(result["port"]))
        util.notify(T("Toegepast", "Applied"))
    return None
