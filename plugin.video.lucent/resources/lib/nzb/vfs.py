# -*- coding: utf-8 -*-
"""Hand an opened release to the add-on vfs.lucent (C++), when it is installed and the setting is on.

Lucent still finds and opens the release (NZB, first article, RAR or 7z layout). Then it writes a small plan beside
its settings, usenet-vfs/<token>.plan, and Kodi plays lucentv://<token>/<name>: vfs.lucent reads the articles itself,
so the film never passes through Python or the local web server. Spoelen in big films goes much faster that way.

The plan holds the provider's sign-in (as vfs.tmdbm does: base64, not secret). So it lives only in Lucent's own
profile folder, is never logged, and is removed as soon as the film has stopped for a minute, when the next film is
opened, and when the service stops.
"""
import base64
import os
import re
import secrets
import ssl
import time
import urllib.parse
import zlib

import util

ADDON_ID = "vfs.lucent"
SCHEME = "lucentv"
FOLDER = "usenet-vfs"
HARD_PREFETCH = 96 * 1024 * 1024        # vfs.lucent reads at most this far ahead
PER_SLOT = 1536 * 1024                  # read-ahead per connection (two articles in flight each)
CACHE_MARGIN = 32 * 1024 * 1024
MAX_WORKERS = 48
WARM = 4                                # first articles that Lucent already has, handed over beside the plan
GONE_AFTER = 60                         # seconds without playback before the plan (with the sign-in) is removed

_state = {"token": "", "stopped": 0.0, "hosts": {}}


def available():
    try:
        import xbmc
        return bool(xbmc.getCondVisibility("System.AddonIsEnabled(%s)" % ADDON_ID))
    except Exception:                   # noqa: BLE001 - outside Kodi: never
        return False


def wanted():
    return util.flag("nzb_native") and available()


def folder():
    path = os.path.join(util.PROFILE, FOLDER)
    os.makedirs(path, exist_ok=True)
    return path


def collect():
    """The counts vfs.lucent left (one .stats file each time the player stopped) go to the statistics."""
    from . import stats
    base = os.path.join(util.PROFILE, FOLDER)
    try:
        names = [n for n in os.listdir(base) if n.endswith(".stats")]
    except OSError:
        return
    for name in names:
        path = os.path.join(base, name)
        try:
            with open(path, encoding="utf-8") as handle:
                lines = handle.read().splitlines()
            os.remove(path)
        except (OSError, ValueError):
            continue
        if not lines or not lines[0].startswith("lucent-stats 1"):
            continue
        for line in lines[1:]:
            parts = line.split()
            if len(parts) != 9 or parts[0] != "provider":
                continue
            host = _state["hosts"].get(parts[1])
            try:
                b, a, m, e, ln, ls_ms, busy_ms = (int(p) for p in parts[2:])
            except ValueError:
                continue
            stats.provider(host, b=b, a=a, m=m, e=e, ln=ln, ls=ls_ms / 1000.0, busy=busy_ms / 1000.0)


def clear():
    """Remove every plan and article file (and with that the sign-in)."""
    collect()
    _state["token"], _state["stopped"] = "", 0.0
    try:
        base = os.path.join(util.PROFILE, FOLDER)
        for name in os.listdir(base):
            try:
                os.remove(os.path.join(base, name))
            except OSError:
                pass
    except OSError:
        pass


def exists(url):
    """Is the plan for this lucentv:// address still there (so vfs.lucent can still read it)?"""
    token = urllib.parse.urlsplit(url).hostname or ""
    return bool(re.fullmatch(r"[0-9a-f]{8,64}", token)) and os.path.exists(os.path.join(util.PROFILE, FOLDER, token + ".plan"))


def tick(playing):
    """From the service: a minute after the film stopped, the plan goes."""
    if not _state["token"]:
        return
    collect()
    if playing:
        _state["stopped"] = 0.0
    elif not _state["stopped"]:
        _state["stopped"] = time.time()
    elif time.time() - _state["stopped"] > GONE_AFTER:
        clear()
        util.log("usenet: fast reader plan removed (film stopped)")


def _b64(value):
    return base64.b64encode(str(value or "").encode("utf-8")).decode("ascii") or "===="


def _safe_name(name):
    """The name in the address: letters, digits, dot, dash and underscore only (vfs.lucent compares it)."""
    name = os.path.basename(name or "") or "film.mkv"
    name = re.sub(r"[^A-Za-z0-9._-]+", ".", name).strip(".") or "film.mkv"
    if "." not in name:
        name += ".mkv"
    return name[-180:]


def _cacert():
    """The certificate file Python itself trusts (vfs.lucent also looks in the usual places of the system)."""
    try:
        found = ssl.get_default_verify_paths().cafile
    except Exception:                   # noqa: BLE001
        found = None
    return found if found and os.path.isfile(found) and " " not in found else ""


def _container(name):
    ext = name.rsplit(".", 1)[-1].lower()
    return ext if ext in ("mkv", "mp4", "m4v", "avi", "ts", "m2ts", "mov", "wmv") else "mkv"


def _servers(provider, backup):
    out = []
    for item, spare in ((provider, False), (backup, True)):
        if not item or not item.get("host"):
            continue
        out.append("server %s %d %d %d %d %s %s %s" % (
            item["host"], int(item.get("port") or 563), 1 if item.get("ssl", True) else 0,
            max(1, int(item.get("connections") or 1)), 1 if spare else 0,
            _b64(item.get("user")), _b64(item.get("password")), _b64("backup" if spare else "provider")))
    return out


def pipeline():
    """Requests at once per connection (the setting, 1 to 8; 2 is the normal value)."""
    return max(1, min(8, util.number("nzb_pipeline", 2)))


def _sizes(release, provider, backup):
    """(workers, prefetch, cache): as many workers as connections, read-ahead from Lucent's own settings."""
    connections = sum(max(1, int(p.get("connections") or 1)) for p in (provider, backup) if p and p.get("host"))
    workers = max(1, min(MAX_WORKERS, connections))
    prefetch = min(HARD_PREFETCH, max(int(release.ahead), min(HARD_PREFETCH, workers * pipeline() * PER_SLOT // 2)))
    cache = max(int(release.cache_limit), prefetch + CACHE_MARGIN)
    return workers, prefetch, cache


def plan_text(release, provider, backup, token, name, fill=True):
    """The plan for vfs.lucent, or None when this release cannot be described (then the web server plays it)."""
    if not release.pieces:
        return None
    workers, prefetch, cache = _sizes(release, provider, backup)
    lines = ["lucent-plan 1",
             "token " + token,
             "name " + name,
             "container " + _container(name),
             "length %d" % int(release.size),
             "timeout 20",
             "prefetch %d" % prefetch,
             "cache %d" % cache,
             "pipeline %d" % pipeline(),
             "workers %d" % workers,
             "holefilling %d" % (1 if fill else 0),
             "holepercent 2",
             "holeconsecutive 4"]
    if _cacert():
        lines.append("cacert " + _cacert())
    lines += _servers(provider, backup)
    if not any(line.startswith("server ") for line in lines):
        return None
    for number, piece in enumerate(release.pieces):
        posted = piece.posted
        if posted.index in release.uneven:
            return None                 # articles of uneven size: vfs.lucent works with one size per file
        part = int(release.part.get(posted.index) or 0)
        lines.append("part %d %d %d %d" % (number, int(piece.data_offset), int(piece.data_length), part))
        prefix = "a %d " % number
        ids = [message_id for _n, _size, message_id in posted.segments]
        if not ids:
            continue
        block = prefix + ("\n" + prefix).join(ids)          # one join for 100,000+ articles (slow boxes)
        if "<" in block or ">" in block or " " in block[len(prefix):].replace("\n" + prefix, "\n"):
            block = "\n".join(prefix + str(message_id).strip().strip("<>") for message_id in ids)
        lines.append(block)
    return "\n".join(lines) + "\n"


def _write(path, data):
    temp = path + ".tmp"
    with open(temp, "wb") as handle:
        handle.write(data)
    os.replace(temp, path)


def _write_first(base, token, release):
    """The first articles Lucent already read, so vfs.lucent can start without waiting for them."""
    posted = release.pieces[0].posted
    first = release.cache.get((posted.index, 0))
    segments = posted.segments
    if not first or not segments:
        return
    _write(os.path.join(base, token + ".first"),
           ("lucent-first 1 %s %d %08x\n" % (str(segments[0][2]).strip("<>"), len(first), zlib.crc32(first) & 0xffffffff)).encode("ascii")
           + first)
    warm = [(n, release.cache.get((posted.index, n))) for n in range(1, min(len(segments), WARM + 1))]
    warm = [(n, data) for n, data in warm if data]
    if not warm:
        return
    head = ["lucent-warm 1 %d %d" % (len(warm), sum(len(d) for _n, d in warm))]
    head += ["%d %s %d %08x" % (n, str(segments[n][2]).strip("<>"), len(d), zlib.crc32(d) & 0xffffffff) for n, d in warm]
    _write(os.path.join(base, token + ".first2"), ("\n".join(head) + "\n").encode("ascii") + b"".join(d for _n, d in warm))


def hand_over(release, provider, backup, fill=True):
    """The lucentv:// address for this release, or None (then the local web server plays it as before)."""
    started = time.time()
    clear()
    token = secrets.token_hex(16)
    name = _safe_name(release.name)
    try:
        text = plan_text(release, provider, backup, token, name, fill)
        made = time.time()
        if not text:
            util.log("usenet: fast reader cannot describe this release, the normal way plays it")
            return None
        base = folder()
        try:
            _write_first(base, token, release)
        except Exception as error:      # noqa: BLE001 - only a quicker start
            util.log("usenet: fast reader start articles not saved (%s)" % type(error).__name__)
        _write(os.path.join(base, token + ".plan"), text.encode("utf-8"))
        _write(os.path.join(base, "active"), ("lucent-active 1\n%s\n" % token).encode("ascii"))
        util.log("usenet: plan for the fast reader: made in %.2f s, written in %.2f s (%.1f MB)"
                 % (made - started, time.time() - made, len(text) / 1048576.0))
    except OSError as error:
        util.log("usenet: fast reader plan not written (%s), the normal way plays it" % type(error).__name__)
        clear()
        return None
    _state["token"], _state["stopped"] = token, 0.0
    _state["hosts"] = {"provider": (provider or {}).get("host"), "backup": (backup or {}).get("host")}
    util.log("usenet: handed to the fast reader (vfs.lucent): %d piece(s), %.2f GB" % (len(release.pieces), release.size / 1e9))
    return "%s://%s/%s" % (SCHEME, token, urllib.parse.quote(name))
