# -*- coding: utf-8 -*-
"""Talking to a Usenet provider (NNTP, RFC 3977 and RFC 4643 for the login).

Only what playing an NZB needs: the greeting, AUTHINFO USER/PASS, STAT (is this article still there?) and BODY (give
me the article). STAT can be sent many at once before the answers are read (pipelining): checking 40 articles then
costs one round trip. One Connection is one socket and is used by one thread at a time; a Pool hands them out.
The username and password are never written to a log line or an error text.
"""
import socket
import ssl
import threading
import time

READ = 262144
TIMEOUT = 20
STAT_BATCH = 200          # STAT questions sent before reading their answers


class NntpError(Exception):
    """The server answered something we cannot use."""


class AuthError(NntpError):
    """Wrong username or password (or the account is not active)."""


class Missing(NntpError):
    """The article is not on this server (any more)."""


class TooMany(NntpError):
    """The provider refuses another connection: the connection limit of the account is reached."""


def _too_many(code, text):
    low = text.lower()
    return code in (400, 481, 502) and any(word in low for word in ("connection", "too many", "limit", "max"))


ANSWERS = {"count": 0, "seconds": 0.0}     # how long the provider takes to start answering a BODY (for the speed line)


def _answered(seconds):
    ANSWERS["count"] += 1                       # a rough figure for the log: no lock needed
    ANSWERS["seconds"] += seconds


class Connection:
    def __init__(self, host, port, use_ssl, user, password, timeout=TIMEOUT):
        self.buffer = bytearray()            # grows in place: an SSL socket hands over 16 KB at a time, and copying
        raw = socket.create_connection((host, int(port)), timeout=timeout)
        try:
            if use_ssl:
                context = ssl.create_default_context()
                raw = context.wrap_socket(raw, server_hostname=host)
            self.sock = raw
            code, text = self._line()
            if code not in (200, 201):
                raise (TooMany if _too_many(code, text) else NntpError)("greeting %d" % code)
            if user:
                code, text = self._command(b"AUTHINFO USER " + user.encode("utf-8"))
                if code == 381:
                    code, text = self._command(b"AUTHINFO PASS " + password.encode("utf-8"))
                if code != 281:
                    if _too_many(code, text):
                        raise TooMany("login %d" % code)
                    raise AuthError("login %d" % code)
        except BaseException:
            try:
                raw.close()
            except OSError:
                pass
            raise
        self.used = time.time()

    # ------------------------------------------------------------------ low level
    def _fill(self):
        chunk = self.sock.recv(READ)
        if not chunk:
            raise NntpError("connection closed")
        self.buffer += chunk                # bytearray: appended in place, not copied again for every piece

    def _line(self):
        at = self.buffer.find(b"\r\n")
        while at < 0:
            start = max(0, len(self.buffer) - 1)
            self._fill()
            at = self.buffer.find(b"\r\n", start)
        line = bytes(self.buffer[:at])
        del self.buffer[:at + 2]
        text = line.decode("utf-8", "replace")
        try:
            return int(text[:3]), text[4:]
        except ValueError:
            raise NntpError("bad answer") from None

    def _command(self, data):
        self.sock.sendall(data + b"\r\n")
        return self._line()

    def _block(self):
        """A multi-line answer up to the line with only a dot, with the leading dots of other lines undone."""
        start = 0
        while True:
            at = self.buffer.find(b"\r\n.\r\n", max(0, start - 4))
            if at >= 0:
                body = bytes(self.buffer[:at + 2])
                del self.buffer[:at + 5]
                break
            if self.buffer.startswith(b".\r\n"):         # an empty body
                body = b""
                del self.buffer[:3]
                break
            start = len(self.buffer)
            self._fill()
        if body.startswith(b".."):
            body = body[1:]
        return body.replace(b"\r\n..", b"\r\n.")

    # ------------------------------------------------------------------ commands
    @staticmethod
    def _id(message_id):
        message_id = message_id.strip()
        if not message_id.startswith("<"):
            message_id = "<%s>" % message_id
        return message_id.encode("utf-8")

    def body_many(self, message_ids, each=None):
        """Several bodies, all asked at once and read in order: the answer time is paid once. A Missing instance stands
        in for an article the server does not have. each(number, body) is called as soon as one is in."""
        asked = time.time()
        self.sock.sendall(b"".join(b"BODY " + self._id(m) + b"\r\n" for m in message_ids))
        out = []
        for number, _m in enumerate(message_ids):
            code, _text = self._line()
            if not number:
                self.answer = time.time() - asked
                _answered(self.answer)
            if code in (420, 423, 430):
                out.append(Missing(str(code)))
            elif code == 222:
                out.append(self._block())
            else:
                raise NntpError("BODY %d" % code)
            if each:
                each(number, out[-1])
        self.used = time.time()
        return out

    def body(self, message_id):
        """The article body (still yEnc). Raises Missing when the server does not have it."""
        asked = time.time()
        code, text = self._command(b"BODY " + self._id(message_id))
        self.used = time.time()
        self.answer = self.used - asked
        _answered(self.answer)
        if code in (420, 423, 430):
            raise Missing(str(code))
        if code != 222:
            raise NntpError("BODY %d" % code)
        return self._block()

    def stat_many(self, message_ids):
        """{message id: True when the server has it}. All questions go out first, then the answers are read."""
        out = {}
        ids = list(message_ids)
        for at in range(0, len(ids), STAT_BATCH):       # small batches: a long burst could fill both buffers and stall
            part = ids[at:at + STAT_BATCH]
            self.sock.sendall(b"".join(b"STAT " + self._id(m) + b"\r\n" for m in part))
            for message_id in part:
                code, _text = self._line()
                out[message_id] = code == 223
        self.used = time.time()
        return out

    def close(self):
        try:
            self.sock.sendall(b"QUIT\r\n")
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


class Pool:
    """At most `limit` connections to one provider. A connection that failed is closed, never handed out again."""

    def __init__(self, provider, limit=None):
        self.provider = provider
        self.limit = max(1, int(limit or provider.get("connections") or 8))
        self.idle = []
        self.count = 0
        self.lock = threading.Condition()
        self.closed = False
        self.urgent = 0                     # takers for an article Kodi is waiting for right now: they go first
        self.record = False                 # True for the pool that plays: its counts go to the statistics
        self.busy = 0                       # fetches running now, and since when (for the speed in the statistics)
        self.busy_since = 0.0

    def _count(self, started, **values):
        """Statistics of a fetch that ended (only when this pool plays)."""
        if not self.record:
            return
        from . import stats
        with self.lock:
            self.busy -= 1
            if not self.busy:
                values["busy"] = time.time() - self.busy_since
        stats.provider(self.provider.get("host"), **values)

    def _begin(self):
        if self.record:
            with self.lock:
                if not self.busy:
                    self.busy_since = time.time()
                self.busy += 1
        return time.time()

    def _new(self):
        p = self.provider
        return Connection(p["host"], p["port"], p.get("ssl", True), p.get("user", ""), p.get("password", ""))

    def take(self, timeout=30, urgent=False):
        end = time.time() + timeout
        with self.lock:
            if urgent:
                self.urgent += 1
            try:
                while True:
                    if self.closed:
                        raise NntpError("closed")
                    if urgent or not self.urgent:
                        if self.idle:
                            return self.idle.pop()
                        if self.count < self.limit:
                            self.count += 1
                            break
                    left = end - time.time()
                    if left <= 0:
                        raise NntpError("no free connection")
                    self.lock.wait(left)
            finally:
                if urgent:
                    self.urgent -= 1
                    self.lock.notify_all()
        try:
            return self._new()
        except TooMany:
            with self.lock:
                self.count -= 1
                self.limit = max(1, self.count)      # the account allows fewer than set: stay below it
                self.lock.notify()
            raise
        except BaseException:
            with self.lock:
                self.count -= 1
                self.lock.notify()
            raise

    def give(self, connection, broken=False):
        with self.lock:
            if broken or self.closed:
                self.count -= 1
                connection.close()
            else:
                self.idle.append(connection)
            self.lock.notify_all()

    def drop_idle(self):
        """Close the connections nobody uses now (the pool itself stays usable and makes new ones when needed)."""
        with self.lock:
            idle, self.idle = self.idle, []
            self.count -= len(idle)
            self.lock.notify_all()
        for connection in idle:
            connection.close()

    def close(self):
        with self.lock:
            self.closed = True
            idle, self.idle = self.idle, []
            self.count -= len(idle)
            self.lock.notify_all()
        for connection in idle:
            connection.close()

    def body(self, message_id, urgent=False):
        """One article, with a fresh connection once when the first one broke. urgent: Kodi waits for it, it goes first."""
        for attempt in range(2):
            connection = self.take(urgent=urgent)
            started = self._begin()
            try:
                data = connection.body(message_id)
            except Missing:
                self._count(started, m=1, ln=1, ls=getattr(connection, "answer", 0))
                self.give(connection)
                raise
            except (OSError, NntpError):
                self._count(started, e=1)
                self.give(connection, broken=True)
                if attempt:
                    raise
                continue
            except BaseException:
                self._count(started, e=1)
                self.give(connection, broken=True)      # never lose a connection, whatever went wrong
                raise
            self._count(started, b=len(data), a=1, ln=1, ls=getattr(connection, "answer", 0))
            self.give(connection)
            return data
        raise NntpError("unreachable")

    def body_many(self, message_ids, each=None):
        """Neighbouring articles over one connection, asked at once (see Connection.body_many)."""
        connection = self.take()
        started = self._begin()
        try:
            out = connection.body_many(message_ids, each)
        except BaseException:                       # broken, or anything unexpected: never lose the connection
            self._count(started, e=1)
            self.give(connection, broken=True)
            raise
        missing = sum(1 for body in out if isinstance(body, Missing))
        self._count(started, b=sum(len(body) for body in out if not isinstance(body, Missing)), a=len(out) - missing,
                    m=missing, ln=1, ls=getattr(connection, "answer", 0))
        self.give(connection)
        return out

    def stat_many(self, message_ids):
        connection = self.take()
        try:
            answer = connection.stat_many(message_ids)
        except BaseException:                       # broken, or anything unexpected: never lose the connection
            self.give(connection, broken=True)
            raise
        self.give(connection)
        return answer
