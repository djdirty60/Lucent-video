# -*- coding: utf-8 -*-
"""Usenet as a scraper: your indexers give the sources, your provider plays them (via the service's local server).

fetch(meta)  -> source dicts for the list (None when Usenet is not set up), like the other scrapers
link(job)    -> when you press play: fetch the NZB, let the service open the release, give Kodi the local address
"""
import hashlib
import json
import os
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import usenet
import util

from . import newznab, server

SERVICE = "USENET"


def configured():
    return util.lab() and util.flag("nzb_on") and bool(util.setting("nzb_host")) and newznab.configured()


def fetch(meta):
    if not configured():
        return None
    ixs = newznab.indexers()
    titles = usenet.titles_for(meta)

    def ask(entry):
        slot, base, key = entry
        try:
            return newznab.search(slot, base, key, meta, titles)
        except newznab.IndexerError as error:
            util.log("usenet: indexer %d: %s" % (slot, error))
            return None

    with ThreadPoolExecutor(max_workers=len(ixs)) as pool:
        answers = list(pool.map(ask, ixs))
    if all(a is None for a in answers):
        raise util.HttpError("indexers")
    out, seen = [], set()
    for rows in answers:
        for row in rows or []:
            name = row["title"]
            key = name.lower()
            if key in seen or not usenet.title_matches(name, titles, meta):
                continue
            low = name.lower()
            if any(word in low for word in (".iso", " iso ", "sample")):
                continue
            seen.add(key)
            job = {"scraper": "Usenet", "slot": row["slot"], "link": row["link"], "param": row["param"], "name": name,
                   "season": meta.get("season"), "episode": meta.get("episode")}
            out.append(usenet._source(name, row["size"], SERVICE, resolve=job))       # noqa: SLF001 - same add-on
    return out


def _port():
    return util.HOME.getProperty(server.PORT_PROPERTY)


def link(job):
    """The local address Kodi plays, or None (the player then tries the next source)."""
    port = _port()
    if not port:
        util.log("usenet: the service's player server is not running")
        return None
    try:
        data = newznab.nzb(job)
    except newznab.IndexerError as error:
        util.log("usenet: %s" % error)
        return None
    name = hashlib.sha1(job["link"].encode("utf-8")).hexdigest()[:16] + ".nzb"
    path = os.path.join(server.folder(), name)
    with open(path + ".tmp", "wb") as handle:
        handle.write(data)
    os.replace(path + ".tmp", path)
    query = {"nzb": name}
    if job.get("season") and job.get("episode"):
        query.update(season=job["season"], episode=job["episode"])
    try:
        with urllib.request.urlopen("http://127.0.0.1:%s/prepare?%s" % (port, urllib.parse.urlencode(query)), timeout=90) as response:
            answer = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError) as error:
        util.log("usenet: service did not answer (%s)" % type(error).__name__)
        return None
    if not answer.get("ok"):
        util.log("usenet: release cannot be played (%s)" % answer.get("error"))
        if answer.get("error") == "login":
            util.notify(util.T("Usenet: inloggen bij je provider lukt niet", "Usenet: signing in to your provider fails"))
        return None
    return "http://127.0.0.1:%s/stream/%s/%s" % (port, answer["key"], urllib.parse.quote(answer.get("name") or "film.mkv"))


def test():
    """(ok, lines) for the settings' test button: the provider login, then each indexer."""
    from . import nntp
    lines, ok = [], True
    p = server.provider()
    if not p["host"]:
        return False, [util.T("Vul eerst de server van je provider in.", "Enter your provider's server first.")]
    try:
        connection = nntp.Connection(p["host"], p["port"], p["ssl"], p["user"], p["password"])
        connection.close()
        lines.append(util.T("Provider: ingelogd.", "Provider: signed in."))
    except nntp.AuthError:
        ok = False
        lines.append(util.T("Provider: gebruikersnaam of wachtwoord klopt niet.", "Provider: username or password is wrong."))
    except nntp.TooMany:
        lines.append(util.T("Provider: bereikbaar, maar al je verbindingen zijn in gebruik.", "Provider: reachable, but all your connections are in use."))
    except (nntp.NntpError, OSError) as error:
        ok = False
        lines.append(util.T("Provider: geen verbinding (%s).", "Provider: no connection (%s).") % type(error).__name__)
    ixs = newznab.indexers()
    if not ixs:
        ok = False
        lines.append(util.T("Geen indexer ingevuld.", "No indexer filled in."))
    for slot, base, key in ixs:
        good, text = newznab.test(slot, base, key)
        ok = ok and good
        host = urllib.parse.urlsplit(base).hostname or "?"
        lines.append(util.T("Indexer %d (%s): %s", "Indexer %d (%s): %s") % (slot, host, util.T("werkt", "works") if good else text))
    return ok, lines
