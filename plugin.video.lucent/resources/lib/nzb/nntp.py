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


class Connection:
    def __init__(self, host, port, use_ssl, user, password, timeout=TIMEOUT):
        self.buffer = b""
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
        self.buffer += chunk

    def _line(self):
        while b"\r\n" not in self.buffer:
            self._fill()
        line, self.buffer = self.buffer.split(b"\r\n", 1)
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
                body, self.buffer = self.buffer[:at + 2], self.buffer[at + 5:]
                break
            if self.buffer.startswith(b".\r\n"):         # an empty body
                body, self.buffer = b"", self.buffer[3:]
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

    def body(self, message_id):
        """The article body (still yEnc). Raises Missing when the server does not have it."""
        code, text = self._command(b"BODY " + self._id(message_id))
        self.used = time.time()
        if code in (420, 423, 430):
            raise Missing(str(code))
        if code != 222:
            raise NntpError("BODY %d" % code)
        return self._block()

    def stat_many(self, message_ids):
        """{message id: True when the server has it}. All questions go out first, then the answers are read."""
        self.sock.sendall(b"".join(b"STAT " + self._id(m) + b"\r\n" for m in message_ids))
        out = {}
        for message_id in message_ids:
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

    def _new(self):
        p = self.provider
        return Connection(p["host"], p["port"], p.get("ssl", True), p.get("user", ""), p.get("password", ""))

    def take(self, timeout=30):
        end = time.time() + timeout
        with self.lock:
            while True:
                if self.closed:
                    raise NntpError("closed")
                if self.idle:
                    return self.idle.pop()
                if self.count < self.limit:
                    self.count += 1
                    break
                left = end - time.time()
                if left <= 0:
                    raise NntpError("no free connection")
                self.lock.wait(left)
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
            self.lock.notify()

    def close(self):
        with self.lock:
            self.closed = True
            idle, self.idle = self.idle, []
            self.count -= len(idle)
            self.lock.notify_all()
        for connection in idle:
            connection.close()

    def body(self, message_id):
        """One article, with a fresh connection once when the first one broke."""
        for attempt in range(2):
            connection = self.take()
            try:
                data = connection.body(message_id)
            except Missing:
                self.give(connection)
                raise
            except (OSError, NntpError):
                self.give(connection, broken=True)
                if attempt:
                    raise
                continue
            self.give(connection)
            return data
        raise NntpError("unreachable")

    def stat_many(self, message_ids):
        connection = self.take()
        try:
            answer = connection.stat_many(message_ids)
        except (OSError, NntpError):
            self.give(connection, broken=True)
            raise
        self.give(connection)
        return answer
