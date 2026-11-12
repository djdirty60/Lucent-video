# -*- coding: utf-8 -*-
"""TheIntroDB (theintrodb.org), built into Lucent Video: where the intro, the recap ('previously on'), the credits and
the preview of a film or episode begin and end. No key is needed; with your own key (setting tidb_key) you get more
requests a day.

    GET https://api.theintrodb.org/v4/media?tmdb_id=<id>[&season=<s>&episode=<e>][&duration_ms=<ms>]
    -> {"intro": [{"start_ms", "end_ms"}], "recap": [...], "credits": [...], "preview": [...]}

end_ms can be missing: then the part runs to the end of the video."""
import json
import urllib.error
import urllib.parse
import urllib.request

import util

API = "https://api.theintrodb.org/v4/media"
KINDS = ("intro", "recap", "credits", "preview")
KEEP = 7 * 86400
KEEP_NONE = 86400          # not in TheIntroDB: ask again tomorrow


def _url(meta, total=0):
    query = {"tmdb_id": meta["tmdb"]}
    if meta.get("kind") == "episode":
        query.update(season=int(meta["season"]), episode=int(meta["episode"]))
    if total:
        query["duration_ms"] = int(total * 1000)
    return API + "?" + urllib.parse.urlencode(query)


def _fetch(url):
    head = {"User-Agent": "Lucent-Video", "Accept": "application/json"}
    key = util.setting("tidb_key")
    if key:
        head["Authorization"] = "Bearer %s" % key
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=head), timeout=8) as response:
            return json.loads(response.read().decode("utf-8")), True
    except urllib.error.HTTPError as error:
        util.log("TheIntroDB: HTTP %s" % error.code)
        return {}, error.code == 404
    except Exception as error:      # noqa: BLE001 - no answer: nothing to skip, never a problem for playback
        util.log("TheIntroDB: %s" % type(error).__name__)
        return {}, False


def segments(meta, total=0):
    """{kind: [(start seconds, end seconds or None)]} for a film or episode; {} when TheIntroDB does not know it."""
    if not meta or meta.get("kind") not in ("movie", "episode") or not meta.get("tmdb"):
        return {}
    key = "tidb4%s:%s:%s" % (meta["tmdb"], meta.get("season") or "", meta.get("episode") or "")
    data = util.cache_get(key, KEEP)
    if data and data.get("none") and util.cache_get(key, KEEP_NONE) is None:
        data = None                                     # was not in TheIntroDB: look again after a day
    if data is None:
        data, sure = _fetch(_url(meta, total))
        if data or sure:
            util.cache_set(key, data or {"none": True})
    out = {}
    for kind in KINDS:
        rows = []
        for row in (data or {}).get(kind) or []:
            start, end = row.get("start_ms"), row.get("end_ms")
            if start is None:
                continue
            start = start / 1000.0
            end = end / 1000.0 if end is not None else None
            if end is not None and end - start < 2:
                continue
            rows.append((start, end))
        if rows:
            out[kind] = sorted(rows)
    return out
