# -*- coding: utf-8 -*-
"""The add-on's own screens. Layouts are in resources/skins/Default/1080i and use the Lucent skin's colours and fonts;
with another skin they are read from a converted copy (anyskin.py)."""
import os
import threading
import time

import xbmcgui
import xbmcvfs

import streams
import util

T = util.T
PATH = xbmcvfs.translatePath(util.ADDON.getAddonInfo("path"))


def path():
    import anyskin
    return anyskin.path()

BACK = (9, 10, 92)
LEFT_RIGHT = (1, 2)
MENU = (117, 101)          # context menu (menu button, long press on Android) and right mouse click
ORDER = ["4K", "1440p", "1080p", "720p", "SD"]


def _open(cls, xml, **kwargs):
    return cls(xml, path(), "Default", "1080i", **kwargs)


class Base(xbmcgui.WindowXMLDialog):
    props = {}

    def __init__(self, *args, **kwargs):
        xbmcgui.WindowXMLDialog.__init__(self, *args)
        self.result = None
        self.args = kwargs

    def fill(self, props):
        for key, value in props.items():
            self.setProperty(key, str(value if value is not None else ""))

    def onAction(self, action):
        if action.getId() in BACK:
            self.close()


def pills(meta):
    out = []
    if meta["kind"] == "episode":
        out.append("S%d · E%d" % (int(meta["season"]), int(meta["episode"])))
    if meta.get("year"):
        out.append(str(meta["year"]))
    if meta.get("rating"):
        out.append("★ %.1f" % float(meta["rating"]))
    if meta.get("runtime"):
        out.append("%d min" % int(meta["runtime"]))
    return out


def head_props(meta):
    show = meta.get("show") if meta["kind"] == "episode" else ""
    props = {"title": show or meta["title"], "logo": meta.get("logo"), "fanart": meta.get("fanart"), "plot": meta.get("plot"),
             "poster": meta.get("poster"),
             "subtitle": meta["title"] if show else (meta.get("tagline") or "")}
    for number, text in enumerate(pills(meta)[:4], 1):
        props["pill%d" % number] = text
    return props


class Preview(xbmcgui.WindowDialog):
    """A picture over everything (also over the source list); any key closes it."""

    def __init__(self, path, line):
        super().__init__()
        import os
        import xbmcvfs
        white = os.path.join(xbmcvfs.translatePath(util.ADDON.getAddonInfo("path")), "resources", "skins", "Default", "media", "lucent", "white.png")
        self.addControl(xbmcgui.ControlImage(0, 0, 1280, 720, white, colorDiffuse="E6000000"))
        self.addControl(xbmcgui.ControlImage(40, 30, 1200, 620, path, aspectRatio=2))
        self.addControl(xbmcgui.ControlLabel(40, 662, 1200, 40, line, alignment=0x00000002))

    def onAction(self, _action):
        self.close()


def show_preview(source):
    import usenet
    try:
        path = usenet.preview_file(source["preview"])
    except Exception as error:      # noqa: BLE001 - a picture is a bonus
        util.log("preview: %s" % type(error).__name__)
        return util.notify(T("Geen voorvertoning beschikbaar", "No preview available"))
    dialog = Preview(path, source.get("name", "")[:120])
    dialog.doModal()
    del dialog
    return None


class Sources(Base):
    """Choose a quality on top, then the source. self.result is the chosen source."""

    def onInit(self):
        self.meta, self.sources = self.args["meta"], self.args["sources"]
        self.show_all = self.args.get("show_uncached", False)
        self.fill(head_props(self.meta))
        self.has_uncached = any(not s["cached"] for s in self.sources) and any(s["cached"] for s in self.sources)
        self.setProperty("has_uncached", "1" if self.has_uncached else "")
        self.tabs, self.rows = self.getControl(100), self.getControl(200)
        self.tab = None
        self.done = False
        self.build_tabs()
        self.setFocusId(200)
        if SEARCH_DONE.get("at"):
            util.log("sources: list shown %.1f s after the search" % (time.time() - SEARCH_DONE.pop("at")))
        self._log_order("list")
        self._start_checks()                    # after the list is up: looking up what is known costs time on slow boxes

    _rows_lock = threading.RLock()

    def _log_order(self, when):
        """For the report: your ranking profile and why the first releases of the tab stand where they stand."""
        try:
            import ranking
            profile = ranking.active()
            groups = " > ".join("%s(%s)" % (g["id"], ",".join(g.get("order", [])[:6])) for g in profile.get("groups", []))
            util.log("sources: order (%s, ranking rc1.8), profile '%s', tab %s: %s"
                     % (when, profile.get("name", "?"), self.tab, groups))
            import learn
            learned = util.load_json(learn.FILE, {})
            for number, source in enumerate(self.current[:40], 1):
                why = "; ".join("%s (%s)" % (what, rule) for what, rule in source.get("why") or [])
                after = []                      # what can move a source after the ranking itself
                if util.flag("learn") and learn.score(learn.key(source), learned):
                    after.append("learned %+d" % learn.score(learn.key(source), learned))
                if source.get("subs_online"):
                    after.append("online subtitle fits" + (" (subtitles first on)" if util.flag("subs_first") else ""))
                if source.get("heavy"):
                    after.append("too heavy")
                if source.get("locked"):
                    after.append("locked")
                util.log("sources: %d. %s | %s | %s | after: %s" % (number, source.get("name", "")[:90],
                                                                    ",".join(source.get("flags") or []), why or "-",
                                                                    ", ".join(after) or "-"))
        except Exception as error:              # noqa: BLE001 - only for the log
            util.log("sources: order not logged (%s)" % type(error).__name__)

    def _start_checks(self):
        """Usenet releases whose NZB Lucent already has: a full check (every article) in the background, shown as
        ' · ✓ compleet' or ' · 97% compleet' after the line under the name as soon as it is in."""
        threading.Thread(target=self._checks, daemon=True).start()

    def _easynews_dolby(self):
        """Easynews Dolby Vision files from a Blu-ray (where profile 7 lives): FEL or MEL read from their first
        megabytes, like the Usenet check, for the top few. The label is set and the order made again."""
        import usenet
        todo = [s for s in self.sources if s.get("service") == "EASYNEWS" and "DV" in (s.get("flags") or [])
                and {"REMUX", "BLURAY"} & set(s.get("flags") or []) and not {"aio_FEL", "aio_MEL"} & set(s.get("flags") or [])][:EASYNEWS_DOLBY]
        changed = False
        for source in todo:
            if self.done:
                return
            try:
                kind = usenet.dovi_kind(source)
            except Exception as error:      # noqa: BLE001 - only a label
                util.log("easynews: Dolby Vision not read (%s)" % type(error).__name__)
                continue
            label = {"fel": "aio_FEL", "mel": "aio_MEL", "p7": "aio_P7"}.get(kind)
            if label:
                source["flags"] = [f for f in source.get("flags") or [] if f not in ("aio_FEL", "aio_MEL", "aio_P7")] + [label]
                changed = True
                self._show_flags(source)        # the new label on its row, in place (the list is not built again)
        if changed and not self.done:
            util.log("easynews: Dolby Vision read for %d file(s)" % len(todo))
            self._rerank()                      # built again only when the order really changed, like the Usenet check

    def _show_flags(self, source):
        with self._rows_lock:
            try:
                position = next(n for n, s in enumerate(self.current) if s is source)
                item = self.rows.getListItem(position)
                shown = streams.tidy_flags(source["flags"])[:12]
                for number in range(1, 13):
                    item.setProperty("flag%d" % number, "lucent/flags/%s.png" % shown[number - 1] if number <= len(shown) else "")
            except (StopIteration, RuntimeError, AttributeError):
                pass                            # on another tab now: shown when that tab is opened

    def _checks(self):
        threading.Thread(target=self._easynews_dolby, daemon=True).start()
        from nzb import source as nzb_source
        todo = []
        for source in self.sources:             # what is already known (a file per release): shown as it is found
            if self.done:
                return
            job = source.get("resolve") or {}
            if job.get("scraper") != "Usenet" or not job.get("link"):
                continue
            known = nzb_source.health_known(job)
            heard = nzb_source.tracks_known(job)
            if known and heard:
                source["health"] = nzb_source.health_text(known) + nzb_source.tracks_text(heard, source["resolve"], source)
                self._subs_badge(source, heard)
            elif nzb_source.can_check(job):
                source["health"] = nzb_source.CHECKING % T("wordt gecontroleerd…", "being checked…")
                todo.append(source)
            else:
                continue
            self._show_health(source)
        early = []                              # the top Usenet releases: their NZB now, while you look at the list,
        for source in self.sources:             # so pressing play skips that wait (only with room at the indexer)
            job = source.get("resolve") or {}
            if job.get("scraper") == "Usenet" and job.get("link") and not source.get("url"):
                early.append(source)
            if len(early) >= EARLY_NZBS:
                break
        for source in early:
            if self.done:
                return
            if not any(s is source for s in todo) and not nzb_source.can_check(source["resolve"]):
                if nzb_source.ensure_nzb(source["resolve"], "list open"):
                    source["health"] = nzb_source.CHECKING % T("wordt gecontroleerd…", "being checked…")
                    self._show_health(source)
                    todo.insert(0, source)
        import queue
        ready = queue.Queue()                   # releases whose file was read: their full check starts at once

        checked = {id(s) for s in self.sources if s.get("health")} | {id(s) for s in todo}
        replaced = [0]

        def replacement():
            """A checked release fell away (taken down, locked, far from complete): the next Usenet release in the list
            is checked in its place, so as many releases as before are known to play. Its NZB only with room at the
            indexer; at most EXTRA_CHECKS per list."""
            if replaced[0] >= EXTRA_CHECKS or self.done:
                return None
            for candidate in list(self.sources):
                job = candidate.get("resolve") or {}
                if id(candidate) in checked or candidate.get("gone") or candidate.get("locked"):
                    continue
                if job.get("scraper") != "Usenet" or not job.get("link"):
                    continue
                checked.add(id(candidate))
                if not nzb_source.can_check(job) and not nzb_source.ensure_nzb(job, "check in place of a release that fell away"):
                    return None                 # no room at the indexer: no more replacements
                replaced[0] += 1
                candidate["health"] = nzb_source.CHECKING % T("wordt gecontroleerd…", "being checked…")
                self._show_health(candidate)
                util.log("sources: a checked release fell away: checking the next one (%s)" % candidate.get("name", "")[:70])
                found = nzb_source.tracks(job, stop=lambda: self.done)
                if found and not found.get("gone"):
                    candidate["health"] = (nzb_source.CHECKING % T("wordt gecontroleerd…", "being checked…")
                                           + nzb_source.tracks_text(found, job, candidate))
                    self._show_health(candidate)
                    self._subs_badge(candidate, found)
                return candidate, found
            return None

        def full_checks():                      # beside the reading of the next files, not after all of them
            extra = []                          # releases checked in place of ones that fell away
            while True:
                item = extra.pop(0) if extra else ready.get()
                if item is None or self.done:
                    return
                source, found = item
                if found and (found.get("locked") or found.get("gone")):
                    following = replacement()
                    if following:
                        extra.append(following)
                    source["health"] = nzb_source.tracks_text(found)
                    self._show_health(source)
                    if found.get("gone") and not source.get("gone"):
                        lost, asked = found["gone"]
                        source["gone"] = True   # taken down: to the end of the list, like a locked release
                        if lost >= asked:
                            nzb_source.mark_gone(source["resolve"])     # every tested piece gone: not shown again
                        self._rerank()
                    continue                    # never playable, or the sample says it is gone: no need to count
                result = nzb_source.health(source["resolve"], stop=lambda: self.done, beside=True)
                if self.done:
                    return
                source["health"] = nzb_source.health_text(result) + nzb_source.tracks_text(found, source["resolve"], source)
                self._show_health(source)
                if result and result[0] < result[1] * 0.95:
                    following = replacement()   # far from complete: it may stop halfway, so one more is checked
                    if following:
                        extra.append(following)

        checker = threading.Thread(target=full_checks, daemon=True)
        checker.start()
        try:
            for source in todo:                 # first what the file itself says (about a second each): labels at once
                if self.done:
                    return
                found = nzb_source.tracks(source["resolve"], stop=lambda: self.done)
                if self.done:
                    return
                if found and not found.get("gone"):
                    source["health"] = (nzb_source.CHECKING % T("wordt gecontroleerd…", "being checked…")
                                        + nzb_source.tracks_text(found, source["resolve"], source))
                    self._show_health(source)
                    self._subs_badge(source, found)
                ready.put((source, found))
        finally:
            ready.put(None)
        checker.join()

    def _subs_badge(self, source, found):
        """The labels set right by what the file itself says (sound, HDR, your language, FEL / P7): changed in place, the
        order of the list stays as it is while you look at it."""
        from nzb import source as nzb_source
        if not source.get("locked") and nzb_source.other_series(source, found):
            source["locked"] = True             # another series with the same name: to the very end of the list
            source["health"] = (source.get("health") or "") + "  ·  [COLOR FFE5534B]✗ %s[/COLOR]" % nzb_source.OTHER_SERIES
            self._show_health(source)
            self._rerank()
            return
        if found.get("locked") and not source.get("locked"):
            source["locked"] = True             # locked with a password or packed: to the very end of the list
            self._rerank()
            return
        flags = nzb_source.file_flags(source, found)
        if flags == list(source.get("flags") or []):
            return
        source["flags"] = flags
        if self._rerank():
            return                              # the tab was built again in the new order, with the new labels
        try:
            position = next(n for n, s in enumerate(self.current) if s is source)
            item = self.rows.getListItem(position)
            shown = streams.tidy_flags(flags)[:12]
            for number in range(1, 13):
                item.setProperty("flag%d" % number, "lucent/flags/%s.png" % shown[number - 1] if number <= len(shown) else "")
        except (StopIteration, RuntimeError, AttributeError):
            pass                                # on another tab now: shown when that tab is opened

    def _rerank(self):
        """The labels changed: the order of sources as your profile wants it with the new labels. The tab you look at is
        built again only when its order really changed, and the source you stand on stays selected."""
        with self._rows_lock:
            return self._rerank_locked()

    def _rerank_locked(self):
        import scrapers
        try:
            ranked = scrapers.rerank(self.sources, self.meta)
        except Exception as error:              # noqa: BLE001 - the order stays as it was
            util.log("sources: new order not made (%s)" % type(error).__name__)
            return False
        if [id(s) for s in ranked] == [id(s) for s in self.sources]:
            return False
        self.sources[:] = ranked
        shown = [s for s in self.visible() if s["resolution"] == self.tab]
        if [id(s) for s in shown] == [id(s) for s in self.current]:
            return False                        # another tab moved: shown in the new order when it is opened
        try:
            position = self.rows.getSelectedPosition()
            standing = self.current[position] if 0 <= position < len(self.current) else None
            name, self.tab = self.tab, None
            self.show_tab(name)
            if standing is not None and (standing.get("locked") or standing.get("gone")):
                self.rows.selectItem(min(position, len(self.current) - 1))     # stay on the same row, not with it down
            elif standing is not None:
                self.rows.selectItem(next(n for n, s in enumerate(self.current) if s is standing))
        except (RuntimeError, StopIteration, AttributeError):
            pass
        util.log("sources: order made again after the file check")
        self._log_order("after the file check")
        return True

    def _show_health(self, source):
        with self._rows_lock:                   # two checks run side by side: one of them may be building the tab again
            try:
                position = next(n for n, s in enumerate(self.current) if s is source)
                self.rows.getListItem(position).setProperty("health", source.get("health") or "")
            except (StopIteration, RuntimeError, AttributeError):
                pass                            # on another tab now: shown when that tab is opened

    def close(self):
        self.done = True
        Base.close(self)

    def visible(self):
        return [s for s in self.sources if self.show_all or s["cached"] or not self.has_uncached]

    def build_tabs(self):
        self.setProperty("uncached_label", T("Alleen gecachet", "Cached only") if self.show_all else T("Niet gecachet", "Uncached"))
        shown = self.visible()
        names = sorted({s["resolution"] for s in shown}, key=lambda r: ORDER.index(r) if r in ORDER else 9)
        self.names = names
        self.tabs.reset()
        for name in names:
            count = sum(1 for s in shown if s["resolution"] == name)
            self.tabs.addItem(xbmcgui.ListItem("%s  %d" % (name, count), offscreen=True))
        self.show_tab(names[0] if names else None)

    def show_tab(self, name):
        if name == self.tab and self.rows.size():
            return
        self.tab = name
        self.rows.reset()
        self.current = [s for s in self.visible() if s["resolution"] == name]
        for source in self.current:
            item = xbmcgui.ListItem(source["name"], offscreen=True)
            item.setProperty("res", source["resolution"])
            item.setProperty("meta", _with_scraper(source) + (T(" · Kan dit apparaat niet", " · Not for this device") if source.get("cannot") else ""))
            item.setProperty("scraper", SCRAPER_SHORT.get(source.get("scraper"), source.get("scraper") or ""))
            item.setProperty("health", source.get("health") or "")
            if source["cached"] and not source.get("always"):
                item.setProperty("cached", "1")   # Usenet and direct links are always there: no 'CACHED' for those
            for number, flag in enumerate(streams.tidy_flags(source["flags"])[:12], 1):
                item.setProperty("flag%d" % number, "lucent/flags/%s.png" % flag)
            self.rows.addItem(item)

    def onAction(self, action):
        if action.getId() in BACK:
            return self.close()
        if action.getId() in MENU and self.getFocusId() == 200:
            return self.menu()              # menu button / long press on a source: why it is here, or check it now
        if action.getId() in LEFT_RIGHT and self.getFocusId() == 100:
            position = self.tabs.getSelectedPosition()
            if 0 <= position < len(self.names):
                self.show_tab(self.names[position])
        return None

    def onClick(self, control):
        if control == 200:
            self.result = self.current[self.rows.getSelectedPosition()]
            self.close()
        elif control == 100:
            self.show_tab(self.names[self.tabs.getSelectedPosition()])
            self.setFocusId(200)
        elif control == 301:
            self.show_all = not self.show_all
            self.tab = None
            self.build_tabs()
            self.setFocusId(200)
        elif control == 302:
            self.close()
        elif control == 303:
            self.explain()

    def menu(self):
        """Usenet releases get a choice: why it stands here, or check it now (every article, also a release Lucent did not
        check by itself, for example because its NZB was not fetched yet). Other sources go straight to 'why'."""
        position = self.rows.getSelectedPosition()
        if not 0 <= position < len(self.current):
            return
        source = self.current[position]
        job = source.get("resolve") or {}
        if source.get("preview") or source.get("en_nzb"):  # Easynews: a picture with a few frames, or play it over Usenet
            import usenet
            options = [("why", T("Waarom staat deze hier", "Why is this one here"))]
            if source.get("preview"):
                options.append(("preview", T("Voorvertoning", "Preview")))
            if source.get("en_nzb") and usenet.can_nzb():
                options.append(("nzb", T("Afspelen via Usenet (NZB)", "Play over Usenet (NZB)")))
            choice = xbmcgui.Dialog().contextmenu([label for _key, label in options])
            picked = options[choice][0] if choice >= 0 else ""
            if picked == "why":
                self.explain()
            elif picked == "preview":
                show_preview(source)
            elif picked == "nzb":
                # the same source, played by vfs.lucent from an NZB Easynews makes (with your own Usenet provider)
                source["url"] = ""
                source["resolve"] = dict(source["en_nzb"], scraper="EasynewsNZB")
                self.result = source
                self.close()
            return None
        if job.get("scraper") != "Usenet" or not job.get("link"):
            return self.explain()
        choice = xbmcgui.Dialog().contextmenu([T("Waarom staat deze hier", "Why is this one here"),
                                               T("Deze release nu controleren", "Check this release now")])
        if choice == 0:
            self.explain()
        elif choice == 1:
            threading.Thread(target=self._check_now, args=(source,), daemon=True).start()

    def _check_now(self, source):
        """The check asked from the menu: the NZB (when there is room at the indexer), what the file says, then every
        article again, also when an earlier result is remembered."""
        from nzb import source as nzb_source
        job = source["resolve"]
        util.log("sources: check asked from the menu: %s" % source.get("name", "")[:80])
        source["health"] = nzb_source.CHECKING % T("wordt gecontroleerd…", "being checked…")
        self._show_health(source)
        if not nzb_source.ensure_nzb(job, "check from the menu"):
            source["health"] = nzb_source.CHECKING % T("NZB niet opgehaald (indexer: geen ruimte of fout)",
                                                       "NZB not fetched (indexer: no room or an error)")
            return self._show_health(source)
        found = nzb_source.tracks(job, stop=lambda: self.done)
        if self.done:
            return None
        if found and (found.get("locked") or found.get("gone")):
            source["health"] = nzb_source.tracks_text(found)
            return self._show_health(source)
        if found:
            self._subs_badge(source, found)
        result = nzb_source.health(job, stop=lambda: self.done, beside=True, again=True)
        if self.done:
            return None
        source["health"] = (nzb_source.health_text(result) or nzb_source.CHECKING % T("controle niet gelukt", "check did not work")) \
            + nzb_source.tracks_text(found, job, source)
        return self._show_health(source)

    def explain(self):
        position = self.rows.getSelectedPosition()
        if not 0 <= position < len(self.current):
            return
        source = self.current[position]
        dialog = _open(Why, "lucent_why.xml", source=source)
        dialog.doModal()
        del dialog


class Why(Base):
    def onInit(self):
        source = self.args["source"]
        self.setProperty("name", source["name"])
        import ranking
        self.setProperty("profile", T("Profiel: %s", "Profile: %s") % ranking.active()["name"])
        rows = self.getControl(100)
        lines = source.get("why") or []
        self.setProperty("empty", "" if lines else T("Je profiel kijkt niet naar kenmerken van deze bron.", "Your profile does not look at properties of this source."))
        for what, rule in lines:
            item = xbmcgui.ListItem(what, rule, offscreen=True)
            rows.addItem(item)
        self.setFocusId(100)

    def onClick(self, _control):
        self.close()


SCRAPER_SHORT = {"AIOStreams": "AIO", "CocoScrapers": "Coco", "FenomScrapers": "Fenom", "OpenScrapers": "Open", "GearsScrapers": "Gears",
                 "Easynews": "Easynews", "TorBox": "TorBox"}


def _with_scraper(source):
    """'AIO · TorBox · 78.1 GB': which scraper found it first (not twice when the scraper is also the service)."""
    short = SCRAPER_SHORT.get(source.get("scraper"), source.get("scraper") or "")
    line = source.get("meta") or ""
    if not short or line.upper().startswith(short.upper()):
        return line
    return "%s · %s" % (short, line) if line else short


EARLY_NZBS = 2                    # NZBs fetched beforehand for the top Usenet releases while the list is open
EXTRA_CHECKS = 4                  # at most this many releases checked in place of checked ones that fell away
EASYNEWS_DOLBY = 4                # Easynews Blu-ray Dolby Vision files read for FEL / MEL while the list is open
SEARCH_DONE = {}                  # when the last search finished (player.py), for the log line when the list is up


def choose_source(meta, sources, show_uncached):
    dialog = _open(Sources, "lucent_sources.xml", meta=meta, sources=sources, show_uncached=show_uncached)
    dialog.doModal()
    result = dialog.result
    del dialog
    return result


class Busy(Base):
    """'Finding sources'. Back cancels."""
    cancelled = False

    def onInit(self):
        self.fill(self.args["props"])
        self.bar = self.getControl(50)
        self.ready = True

    def percent(self, value):
        try:
            self.bar.setPercent(value)
        except (RuntimeError, AttributeError):
            pass

    def onAction(self, action):
        if action.getId() in BACK:
            self.cancelled = True
            self.close()

    def live(self, live, final=False):
        """While searching: a pill per scraper (busy, done with its count, nothing, error) and the resolutions so far."""
        props = {}
        for number in range(1, 7):
            props["src%d" % number], props["src%dstate" % number] = "", ""
        for number, name in enumerate(list(live["order"])[:6], 1):
            row = live["rows"].get(name) or {}
            short = SCRAPER_SHORT.get(name, name)
            state = row.get("state", "busy")
            if final and state == "busy":
                state = "error"                     # it took too long: the search went on without it
            props["src%d" % number] = {"done": "✓ %s  %d" % (short, row.get("count", 0)), "busy": "… %s" % short,
                                       "none": "%s  0" % short, "error": "%s  ✗" % short}.get(state, short)
            if state == "error" and final and row.get("state") == "busy":
                props["src%d" % number] = "%s  %s" % (short, T("te traag", "too slow"))
            if row.get("rest"):                 # skipped for a while after failing twice: stays in view with its cross
                props["src%d" % number] = "%s  ✗ %s" % (short, T("pauze", "paused"))
            props["src%dstate" % number] = state
        counts = dict(live["res"])
        names = sorted([r for r in counts if r], key=lambda r: ORDER.index(r) if r in ORDER else 9)
        for number in range(1, 5):
            props["res%d" % number] = "%s  %d" % (names[number - 1], counts[names[number - 1]]) if number <= len(names) else ""
        total = sum(counts.values())
        props["count"] = total if total else ""
        self.fill(props)

    def done(self, sources):
        counts = {}
        for source in sources:
            counts[source["resolution"]] = counts.get(source["resolution"], 0) + 1
        names = sorted(counts, key=lambda r: ORDER.index(r) if r in ORDER else 9)
        props = {"count": len(sources), "best": names[0] if names else ""}
        for number, name in enumerate(names[:4], 1):
            props["res%d" % number] = "%s  %d" % (name, counts[name])
        props["ready"] = T("%d direct speelbaar", "%d ready to play") % sum(1 for s in sources if s["cached"])
        self.fill(props)
        self.percent(100)


def busy_dialog(meta):
    props = head_props(meta) if meta else {}
    if meta and meta["kind"] == "episode":
        props["subtitle"] = "S%d · E%d   %s" % (int(meta["season"]), int(meta["episode"]), meta["title"])
    dialog = _open(Busy, "lucent_busy.xml", props=props)
    dialog.show()
    return dialog


def recap(meta):
    """'Previous time': the story of the episode before this one (also across seasons); for a film or a first
    episode, what this one is about."""
    import tmdb
    if meta.get("kind") == "episode":
        season, episode = int(meta["season"]), int(meta["episode"])
        try:
            if episode > 1:
                rows = tmdb.season(meta["tmdb"], season).get("episodes") or []
                before = next((r for r in rows if r.get("episode_number") == episode - 1), None)
            elif season > 1:
                rows = tmdb.season(meta["tmdb"], season - 1).get("episodes") or []
                before = rows[-1] if rows else None
            else:
                before = None
        except Exception:          # no recap is never a reason not to resume
            before = None
        if before and before.get("overview"):
            return (T("Vorige keer: S%d · E%d  %s", "Previous time: S%d · E%d  %s")
                    % (before["season_number"], before["episode_number"], before.get("name") or ""), before["overview"])
    return T("Waar het over gaat", "What it is about"), meta.get("plot") or ""


class Resume(Base):
    def onInit(self):
        meta = self.args["meta"]
        head, text = recap(meta)
        self.fill({"title": meta.get("show") or meta["title"], "fanart": meta.get("thumb") or meta.get("fanart"),
                   "stopped": T("Gestopt bij %s", "Stopped at %s") % self.args["clock"], "recap_head": head, "recap": text})
        self.getControl(50).setPercent(self.args["percent"])

    def onClick(self, control):
        self.result = control
        self.close()


def ask_resume(meta, clock, percent):
    """True = resume, False = from the start, None = cancelled."""
    dialog = _open(Resume, "lucent_resume.xml", meta=meta, clock=clock, percent=percent)
    dialog.doModal()
    result = {10: True, 11: False}.get(dialog.result)
    del dialog
    return result


class Timed(Base):
    """Runs next to the video: the service keeps it up to date and closes it."""

    def start(self):
        self.closed = False
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        self.doModal()
        self.closed = True

    def finish(self):
        try:
            self.close()
        except RuntimeError:
            pass


class NextUp(Timed):
    def onInit(self):
        self.fill(self.args["props"])

    def countdown(self, seconds):
        self.setProperty("kicker", T("VOLGENDE AFLEVERING · %d s", "NEXT EPISODE · %d s") % max(0, int(seconds)))

    def onClick(self, control):
        self.result = {10: "now", 11: "stop", 12: "close"}.get(control)
        self.close()

    def onAction(self, action):
        if action.getId() in BACK:
            self.result = "close"
            self.close()


class Still(Timed):
    def onInit(self):
        self.fill(self.args["props"])

    def countdown(self, seconds):
        pass

    def onClick(self, control):
        self.result = {10: "continue", 11: "stop"}.get(control)
        self.close()


def next_dialog(following):
    info = "S%d · E%d" % (int(following["season"]), int(following["episode"]))
    if following.get("rating"):
        info += "   ★ %.1f" % float(following["rating"])
    if following.get("runtime"):
        info += " · %d min" % int(following["runtime"])
    dialog = _open(NextUp, "lucent_next.xml", props={"thumb": following.get("thumb"), "info": info, "title": following["title"],
                                                     "plot": following.get("plot")})
    dialog.start()
    return dialog


def still_dialog(show, count):
    dialog = _open(Still, "lucent_still.xml", props={"line": T("Je hebt %d afleveringen van %s achter elkaar gekeken.",
                                                                 "You watched %d episodes of %s in a row.") % (count, show)})
    dialog.start()
    return dialog


class TraktLink(Base):
    def onInit(self):
        self.fill(self.args["props"])

    def onClick(self, _control):
        self.close()


def link_dialog(service, url, code, qr_url=None):
    """The screen with the code and QR for linking an account. Returns (dialog, path of the QR picture)."""
    import segno
    os.makedirs(util.CACHE, exist_ok=True)
    picture = os.path.join(util.CACHE, "link_qr_%s_%s.png" % (service, code.replace(" ", "")))
    segno.make(qr_url or url, error="m").save(picture, kind="png", scale=12, border=2, dark="#0B1119", light="#FFFFFF")
    host = url.split("//")[-1]
    props = {"qr": picture, "code": code, "heading": T("%s koppelen", "Link %s") % service,
             "line": T("Scan de code of ga naar %s", "Scan the code or go to %s") % host, "button": T("Annuleren", "Cancel")}
    return _open(TraktLink, "lucent_trakt.xml", props=props), picture


