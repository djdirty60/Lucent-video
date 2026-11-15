# -*- coding: utf-8 -*-
"""Usenet as a scraper: your indexers give the sources, your provider plays them (via the service's local server).

fetch(meta)  -> source dicts for the list (None when Usenet is not set up), like the other scrapers
link(job)    -> when you press play: fetch the NZB, let the service open the release, give Kodi the local address
"""
import hashlib
import json
import os
import re
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import streams
import usenet
import util

from . import newznab, server

SERVICE = "USENET"


def configured():
    return util.lab() and util.flag("nzb_on") and bool(util.setting("nzb_host")) and newznab.configured()


def is_pack(name, titles, meta):
    """'Show.S02.1080p...' or 'Show Season 2': the whole season (not an episode, not several seasons)."""
    words = usenet._words(name)                      # noqa: SLF001 - same add-on
    season = int(meta["season"])
    for title in titles:
        wanted = usenet._words(title)                # noqa: SLF001
        if not wanted or words[:len(wanted)] != wanted:
            continue
        rest = " ".join(words[len(wanted):])
        if re.search(r"\bs\d{1,2} ?e\d{1,3}\b|\b\d{1,2}x\d{2,3}\b|\bs\d{1,2} ?s\d{1,2}\b|\bcomplete series\b", rest):
            return False
        if re.search(r"\bs0*%d\b|\bseason 0*%d\b" % (season, season), rest):
            return True
    return False


def _indexer_name(base):
    """'NZBGEEK' for https://api.nzbgeek.info/api: the site's own name, for the source list."""
    host = (urllib.parse.urlsplit(base).hostname or "").lower()
    if re.fullmatch(r"[\d.:]+", host):
        return host                        # an address of your own (a local indexer)
    parts = [p for p in host.split(".") if p not in ("www", "api", "feed", "feeds")]
    if len(parts) >= 2:
        parts = parts[:-1]                 # without .info / .com / .net
    return (parts[-1] if parts else host).upper()[:20]


def fetch(meta):
    if not configured():
        return None
    ixs = newznab.indexers()
    titles = usenet.titles_for(meta)

    def ask(entry):
        slot, base, key = entry
        try:
            return newznab.search(slot, base, key, meta, titles, background=bool(meta.get("background")))
        except newznab.IndexerError as error:
            util.log("usenet: indexer %d: %s%s" % (slot, error, " (the API key is not accepted)" if str(error) in ("HTTP 401", "HTTP 403") else
                                                    " (%s today; asked again tomorrow)" % newznab.usage(base) if str(error) == "daily limit" else ""))
            return None

    with ThreadPoolExecutor(max_workers=len(ixs)) as pool:
        answers = list(pool.map(ask, ixs))
    if all(a is None for a in answers):
        raise util.HttpError("indexers")
    names = {slot: _indexer_name(base) for slot, base, _key in ixs}
    most = server.most_per_second()
    seconds = int(meta.get("runtime") or 0) * 60
    out, seen = [], set()
    for rows in answers:
        for row in rows or []:
            name = row["title"]
            key = name.lower()
            pack = meta["kind"] != "movie" and is_pack(name, titles, meta)
            if key in seen or not (pack or usenet.title_matches(name, titles, meta)):
                continue
            low = name.lower()
            if any(word in low for word in (".iso", " iso ", "sample")):
                continue
            seen.add(key)
            job = {"scraper": "Usenet", "slot": row["slot"], "link": row["link"], "param": row["param"], "name": name,
                   "season": meta.get("season"), "episode": meta.get("episode")}
            where = names.get(row["slot"]) or ""
            # needs more per second than this box ever got from Usenet: say so (it may stop to buffer)
            heavy = bool(most and seconds and row["size"] / seconds > most * 0.95)
            warn = util.T("mogelijk te zwaar", "may be too heavy") if heavy else ""
            if not pack:
                source = usenet._source(name, row["size"], SERVICE, resolve=job)          # noqa: SLF001 - same add-on
                source["meta"] = " · ".join(p for p in (SERVICE, where, streams.size_text(row["size"]), warn) if p)
                source["always"] = True
                out.append(source)
                continue
            # a pack: rank it by the size of one episode, show the size of the whole season
            job["pack"] = True
            episodes = int(meta.get("season_count") or 0) or 10
            source = usenet._source(name, row["size"] // episodes, SERVICE, resolve=job)    # noqa: SLF001
            if most and seconds and row["size"] / episodes / seconds > most * 0.95:
                warn = util.T("mogelijk te zwaar", "may be too heavy")
            else:
                warn = ""
            source["meta"] = " · ".join(p for p in (SERVICE, where, util.T("Seizoenspakket", "Season pack"), streams.size_text(row["size"]), warn) if p)
            source["pack"] = True
            source["always"] = True
            out.append(source)
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
    if job.get("pack"):
        query["pack"] = "1"
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
        if not good and text.startswith("API key not accepted"):
            reason = text.split(": ", 1)[1] if ": " in text else ""
            text = util.T("de API-sleutel wordt niet geaccepteerd", "the API key is not accepted") + ((" – %s" % reason) if reason else "")
        elif not good and text == "answer is a web page":
            text = util.T("de indexer geeft een webpagina terug in plaats van een antwoord (adres fout, of geblokkeerd)",
                          "the indexer answers with a web page instead (wrong address, or blocked)")
        elif not good and text in ("answer is not XML", "answer is not readable", "answer is not a Newznab answer"):
            text = util.T("het antwoord van de indexer is onleesbaar (zie het foutrapport)", "the indexer's answer cannot be read (see the error report)")
        elif not good and text == "limit reached":
            text = util.T("het daglimiet is bereikt", "the daily limit is reached")
        used = newznab.usage(base).replace("searches", util.T("zoekopdrachten", "searches")).replace("NZBs", util.T("NZB's", "NZBs"))
        lines.append(util.T("Indexer %d (%s): %s", "Indexer %d (%s): %s") % (slot, host, (util.T("werkt", "works") if good else text)
                                                                             + " · " + util.T("vandaag %s", "today %s") % used))
    return ok, lines
