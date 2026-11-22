# -*- coding: utf-8 -*-
"""The Usenet statistics screen (Settings > Usenet > Statistieken): data per day, providers and indexers."""
import xbmcgui

import dialogs
import util
from nzb import newznab, server, stats

T = util.T
COLORS = ["FF8C7BFF", "FF5ED38A", "FFFFB84D", "FF4FC3F7", "FFFF7A6B", "FFC792EA", "FF7FD8C4", "FFE6A1C0"]
PERIODS = {101: 30, 102: 7, 103: 0}


def _dec(value, digits=1):
    text = "%.*f" % (digits, value)
    return text.replace(".", ",") if util.NL else text


def _bytes(value):
    if value >= 1e12:
        return _dec(value / 1e12, 2) + " TB"
    if value >= 1e11:
        return "%d GB" % round(value / 1e9)
    if value >= 1e9:
        return _dec(value / 1e9) + " GB"
    if value >= 1e6:
        return "%d MB" % round(value / 1e6)
    return "%d KB" % max(1, round(value / 1e3)) if value else "0"


def _count(value):
    value = int(value)
    if value >= 1e6:
        return _dec(value / 1e6) + "M"
    if value >= 1e4:
        return "%dK" % round(value / 1e3)
    return str(value)


def _ms(seconds):
    if seconds >= 1:
        return _dec(seconds, 2) + " s"
    return "%d ms" % round(seconds * 1000)


def _pct(part, whole, digits=None):
    if not whole:
        return "–"
    if not part:
        return "0%"
    value = 100.0 * part / whole
    if digits is None:
        digits = 2 if value < 0.1 else (1 if value < 100 else 0)
    return _dec(value, digits) + "%"


def _bar(fraction):
    return "lucent/stat/h_%03d.png" % (int(round(max(0.0, min(1.0, fraction)) * 20)) * 5)


def _vbar(fraction):
    return "lucent/stat/v_%03d.png" % (int(round(max(0.0, min(1.0, fraction)) * 50)) * 2)


def _buckets(days, most=30):
    """At most `most` bars: long periods are added up per few days."""
    if len(days) <= most:
        return days
    size = -(-len(days) // most)
    out = []
    for start in range(0, len(days), size):
        chunk = days[start:start + size]
        out.append((chunk[0][0], sum(v for _d, v in chunk)))
    return out


# the usual spelling of well-known Usenet providers, by a word in their server address
PROVIDERS = {"easynews": "Easynews", "newsdemon": "NewsDemon", "thundernews": "ThunderNews", "eweka": "Eweka",
             "newshosting": "Newshosting", "usenetserver": "UsenetServer", "frugalusenet": "Frugal Usenet",
             "giganews": "Giganews", "tweaknews": "Tweaknews", "pureusenet": "Pure Usenet", "farm": "Usenet.Farm",
             "newsgroupninja": "Newsgroup Ninja", "xsnews": "XS News", "blocknews": "Blocknews",
             "usenetexpress": "UsenetExpress", "vipernews": "ViperNews", "bulknews": "BulkNews",
             "newsgroupdirect": "Newsgroup Direct", "supernews": "Supernews", "astraweb": "Astraweb",
             "hitnews": "Hitnews", "cheapnews": "Cheapnews", "torbox": "TorBox", "elbracht": "Elbracht",
             "abavia": "Abavia", "newsoo": "Newsoo", "sunnyusenet": "Sunny Usenet", "usenetfire": "UsenetFire",
             "newsfire": "UsenetFire", "ngd": "Newsgroup Direct", "fastusenet": "FastUsenet", "usenet-farm": "Usenet.Farm"}


def provider_name(host):
    """'Easynews' for secure-eu.news.easynews.com: the provider's name, not its server address. An unknown provider:
    the main part of the address with a capital (news.example.net -> Example)."""
    host = (host or "").lower().strip()
    if not host or host.replace(".", "").replace(":", "").isdigit():
        return host                               # an address of your own
    parts = [p for p in host.split(".") if p]
    for part in parts:
        if part in PROVIDERS:
            return PROVIDERS[part]
    main = parts[-2] if len(parts) >= 2 else host
    if len(parts) >= 3 and main in ("co", "com", "net", "org"):
        main = parts[-3]
    return main[:1].upper() + main[1:]


class Screen(dialogs.Base):

    def onInit(self):
        self.period = 30
        self.setFocusId(101)
        self.fill({"title": T("Statistieken", "Statistics"), "p30": T("30 dagen", "30 days"), "p7": T("7 dagen", "7 days"),
                   "pall": T("Alles", "All"), "wipe": T("Wissen", "Clear"), "close": T("Sluiten", "Close"),
                   "h_day": T("DATA PER DAG", "DATA PER DAY"), "h_prov": "PROVIDERS", "h_ix": "INDEXERS",
                   "h_name": "PROVIDER", "h_iname": "INDEXER", "h_share": T("AANDEEL", "SHARE"), "h_data": "DATA",
                   "h_speed": T("SNELHEID", "SPEED"), "h_articles": T("STUKJES", "ARTICLES"),
                   "h_wait": T("WACHTTIJD", "LATENCY"), "h_errors": T("FOUTEN", "ERRORS"), "h_missing": T("ONTBREEKT", "MISSING"),
                   "h_searches": T("ZOEKOPDRACHTEN", "SEARCHES"), "h_nzbs": "NZB'S", "h_ok": T("GELUKT", "SUCCESS"),
                   "h_bad": T("MISLUKT", "FAILED"), "h_resp": T("REACTIETIJD", "RESPONSE")})
        self.show()

    def show(self):
        self.setProperty("period", str(self.period))
        data = stats.summary(self.period)
        self._days(data["days"])
        self._providers(data["providers"])
        self._indexers(data["indexers"])
        self.setProperty("foot", T("Gelukt: de NZB kon echt afgespeeld worden. Wachttijd: hoe snel de provider begint te "
                                   "antwoorden. Opslag: %s; alles ouder dan een jaar gaat vanzelf weg.",
                                   "Success: the NZB could really be played. Latency: how fast the provider starts to "
                                   "answer. Storage: %s; anything older than a year is removed by itself.") % _bytes(stats.size()))

    def _days(self, days):
        total = sum(v for _d, v in days)
        shown = len(days)
        if self.period == 7:
            control, bars = self.getControl(401), days
            self.setProperty("bars", "7")
        else:
            control, bars = self.getControl(400), _buckets(days)
            self.setProperty("bars", "30")
            bars = ([("", 0)] * (30 - len(bars))) + bars          # newest on the right, as with 30 days
        top = max([v for _d, v in bars] + [1])
        items = []
        for _day, value in bars:
            item = xbmcgui.ListItem("")
            item.setProperty("bar", _vbar(value / top) if value else "")
            items.append(item)
        for cid in (400, 401):
            self.getControl(cid).reset()
        control.addItems(items)
        if total and shown:
            self.setProperty("nodays", "")
            self.setProperty("daysum", T("%s in %d dagen · %s per dag", "%s in %d days · %s per day")
                             % (_bytes(total), shown, _bytes(total / shown)))
        else:
            self.setProperty("daysum", "")
            self.setProperty("nodays", T("Nog niets gekeken via Usenet in deze periode", "Nothing watched over Usenet in this period"))

    def _providers(self, providers):
        control = self.getControl(200)
        control.reset()
        backup = (server.provider(backup=True) or {}).get("host")
        total = sum(v.get("b", 0) for v in providers.values())
        items = []
        for number, (host, v) in enumerate(sorted(providers.items(), key=lambda kv: -kv[1].get("b", 0))):
            item = xbmcgui.ListItem(provider_name(host) + (T(" (reserve)", " (backup)") if host == backup else ""))
            asked = v.get("a", 0) + v.get("m", 0)
            item.setProperty("color", COLORS[number % len(COLORS)])
            item.setProperty("sbar", _bar(v.get("b", 0) / total if total else 0))
            item.setProperty("share", _pct(v.get("b", 0), total))
            item.setProperty("data", _bytes(v.get("b", 0)))
            busy = v.get("busy", 0)
            item.setProperty("speed", (_dec(v.get("b", 0) / busy / 1e6) + " MB/s") if busy > 1 else "–")
            item.setProperty("articles", _count(v.get("a", 0)))
            item.setProperty("wait", _ms(v.get("ls", 0) / v["ln"]) if v.get("ln") else "–")
            item.setProperty("errors", _pct(v.get("e", 0), asked + v.get("e", 0)))
            item.setProperty("missing", _pct(v.get("m", 0), asked))
            items.append(item)
        control.addItems(items)
        self.setProperty("noprov", "" if items else T("Nog geen gegevens van je provider", "No data from your provider yet"))

    def _indexers(self, indexers):
        control = self.getControl(300)
        control.reset()
        names = {newznab.name_of(base) for _slot, base, _key in newznab.indexers()}
        total = sum(v.get("g", 0) for v in indexers.values())
        items = []
        order = sorted(indexers.items(), key=lambda kv: (-kv[1].get("g", 0), -kv[1].get("s", 0), kv[0]))
        for number, (name, v) in enumerate(order):
            item = xbmcgui.ListItem(name)
            if name not in names:
                item.setProperty("gone", "1")                   # no longer set up: shown dimmed
            item.setProperty("color", COLORS[number % len(COLORS)])
            item.setProperty("sbar", _bar(v.get("g", 0) / total if total else 0))
            item.setProperty("share", _pct(v.get("g", 0), total))
            item.setProperty("searches", _count(v.get("s", 0)))
            item.setProperty("nzbs", _count(v.get("g", 0)))
            done = v.get("ok", 0) + v.get("bad", 0)
            item.setProperty("ok", _pct(v.get("ok", 0), done, 1))
            if done:
                rate = 100.0 * v.get("ok", 0) / done
                item.setProperty("okc", "g" if rate >= 92 else ("a" if rate >= 85 else "r"))
            item.setProperty("bad", _count(v.get("bad", 0)))
            answered = v.get("s", 0) - v.get("se", 0)
            item.setProperty("resp", _ms(v.get("ss", 0) / answered) if answered > 0 else "–")
            items.append(item)
        control.addItems(items)
        self.setProperty("ixmore", T("%d indexers · scroll omlaag voor de rest", "%d indexers · scroll down for the rest") % len(items)
                         if len(items) > 6 else "")         # room for 6 rows
        self.setProperty("noix", "" if items else T("Nog geen zoekopdrachten bij je indexers", "No searches at your indexers yet"))

    def onClick(self, control):
        if control in PERIODS:
            self.period = PERIODS[control]
            self.show()
        elif control == 104:
            if xbmcgui.Dialog().yesno(T("Statistieken wissen", "Clear statistics"),
                                      T("Alle Usenet-statistieken wissen? Dit kan niet ongedaan worden.",
                                        "Clear all Usenet statistics? This cannot be undone.")):
                stats.clear()
                self.show()
        elif control == 105:
            self.close()


def run():
    dialogs._open(Screen, "lucent_stats.xml").doModal()       # noqa: SLF001 - the add-on's own helper
