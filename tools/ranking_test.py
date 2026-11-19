"""Every rule of the order of sources, checked on the whole chain the source list uses:
ranking.apply -> learn.adjust -> ranking.heavy_last -> subcheck.first (and scrapers.rerank for the file check).

Run outside Kodi with the stubs in tools/kodistub:
    PYTHONPATH=tools/kodistub:plugin.video.lucent/resources/lib python3 tools/ranking_test.py
Each case states the rule in words; a failure shows the order that came out."""
import copy
import sys

import learn
import mylang
import ranking
import util

G = 1073741824
SUB = mylang.flags()[1]
FAILED = []
_flags = {"learn": True, "subs_first": True}
util.flag = lambda name: _flags.get(name, False)
_learned = {}
util.load_json = (lambda real: (lambda name, default=None: copy.deepcopy(_learned) if name == learn.FILE else real(name, default)))(util.load_json)


def src(name, gb=50, res="4K", flags=(), **extra):
    return dict(name=name, size=gb * G, resolution=res, cached=True, scraper=extra.pop("scraper", "Usenet"),
                flags=list(flags), **extra)


def profile(base, groups=None, orders=None, **filters):
    prof = copy.deepcopy(ranking.presets()[base])
    if groups:
        prof["groups"].sort(key=lambda g: groups.index(g["id"]) if g["id"] in groups else len(groups))
    for gid, order in (orders or {}).items():
        next(g for g in prof["groups"] if g["id"] == gid)["order"] = order
    prof["filters"].update(filters)
    return prof


def chain(sources, prof, kind="movie", original="en"):
    import subcheck
    ranked = ranking.heavy_last(learn.adjust(ranking.apply([dict(s) for s in sources], prof, kind, ["Usenet", "Easynews"], original)))
    return subcheck.first(ranked, {id(s) for s in ranked if s.get("subs_online")})


def expect(title, sources, prof, wanted, **kw):
    got = [s["name"].split("-")[-1] for s in chain(sources, prof, **kw)]
    ok = got[:len(wanted)] == wanted
    print("%s  %s" % ("ok  " if ok else "FOUT", title))
    if not ok:
        print("        verwacht: %s\n        kreeg:    %s" % (wanted, got))
        FAILED.append(title)


FEL = ["resolution", "visual", "source", "audio", "channels", "language", "size", "speed", "scraper", "codec"]
FEL_ORDERS = {"visual": ["fel", "p7", "dv2", "dv", "imax", "hdr10plus", "hdr", "hlg", "sdr", "10bit"],
              "audio": ["dtsx", "truehd", "atmos", "dtsma", "flac", "pcm", "dtshd", "dts", "ddplus", "dd", "aac", "opus"]}
BEST = ["resolution", "visual", "source", "audio", "channels", "language", "speed", "size", "codec", "scraper"]
TAAL_TOP = ["language"] + [g for g in FEL if g != "language"]

remux_nosub = src("Film.2160p.UHD.BluRay.REMUX.DV.HDR.HEVC.TrueHD.Atmos.7.1-RMX", 60, flags=["DV", "HDR", "REMUX", "TRUEHD", "ATMOS", "7.1"])
bluray_sub = src("Film.2160p.UHD.BluRay.DV.HDR.x265.TrueHD.Atmos.7.1-BRS", 30, flags=["DV", "HDR", "BLURAY", "TRUEHD", "ATMOS", "7.1", SUB])
bluray_10bit = src("Film.2160p.UHD.BluRay.DV.HDR.x265.10bit.TrueHD.Atmos.7.1-TEN", 30, flags=["DV", "HDR", "aio_10BIT", "BLURAY", "TRUEHD", "ATMOS", "7.1", SUB])
web_sub = src("Film.2160p.WEB-DL.DDP5.1.Atmos.DV.HDR.H.265-WEBS", 20, flags=["DV", "HDR", "WEB", "DD+", "ATMOS", "5.1", SUB])

print("1. Volgorde van de groepen beslist")
expect("Bron boven Taal: Remux zonder NL-subs boven Blu-ray met NL-subs",
       [bluray_sub, web_sub, remux_nosub], profile("fel", FEL, FEL_ORDERS), ["RMX", "BRS", "WEBS"])
expect("Taal bovenaan: NL-subs gaan voor, ook op een Remux",
       [remux_nosub, bluray_sub, web_sub], profile("fel", TAAL_TOP, FEL_ORDERS), ["BRS", "WEBS", "RMX"])
expect("Extra's (10-bit) beslissen niet vóór Bron",
       [bluray_10bit, remux_nosub], profile("fel", FEL, FEL_ORDERS), ["RMX", "TEN"])

print("2. Volgorde binnen een groep beslist")
dtsx = src("Film.2160p.UHD.BluRay.REMUX.DV.HDR.HEVC.DTS-X.7.1-DTSX", 60, flags=["DV", "HDR", "REMUX", "DTS-X", "7.1"])
thd_atmos = src("Film.2160p.UHD.BluRay.REMUX.DV.HDR.HEVC.TrueHD.Atmos.7.1-THDA", 60, flags=["DV", "HDR", "REMUX", "TRUEHD", "ATMOS", "7.1"])
thd = src("Film.2160p.UHD.BluRay.REMUX.DV.HDR.HEVC.TrueHD.7.1-THD", 60, flags=["DV", "HDR", "REMUX", "TRUEHD", "7.1"])
expect("DTS:X bovenaan bij Geluid: DTS:X boven TrueHD Atmos",
       [thd_atmos, thd, dtsx], profile("fel", FEL, FEL_ORDERS), ["DTSX", "THDA", "THD"])
expect("TrueHD bovenaan bij Geluid: TrueHD Atmos boven TrueHD zonder Atmos (zelfde plek, punten)",
       [thd, thd_atmos, dtsx], profile("fel", FEL, {"audio": ["truehd", "atmos", "dtsx"] + FEL_ORDERS["audio"][3:]}),
       ["THDA", "THD", "DTSX"])
fel = src("Film.2160p.UHD.BluRay.REMUX.DV.HDR.HEVC.TrueHD.Atmos.7.1-FELR", 60, flags=["DV", "aio_FEL", "HDR", "REMUX", "TRUEHD", "ATMOS", "7.1"])
expect("FEL bovenaan bij Beeld: FEL-Remux boven gewone DV-Remux",
       [thd_atmos, fel], profile("fel", FEL, FEL_ORDERS), ["FELR", "THDA"])
hdr_only = src("Film.2160p.UHD.BluRay.REMUX.HDR.HEVC.TrueHD.Atmos.7.1-HDRR", 60, flags=["HDR", "REMUX", "TRUEHD", "ATMOS", "7.1"])
expect("Beeld boven Bron: DV-WEB-DL boven HDR10-Remux",
       [hdr_only, web_sub], profile("best", BEST), ["WEBS", "HDRR"])

print("3. Zelflerend en ondertitels eerst gaan nooit boven je keuzes")
_learned.clear()
_learned["rmx|4K|usenet"] = {"good": 5, "bad": 0, "at": 1}
remux_sub = src("Film.2160p.UHD.BluRay.REMUX.DV.HDR.HEVC.TrueHD.Atmos.7.1-RSUB", 55, flags=["DV", "HDR", "REMUX", "TRUEHD", "ATMOS", "7.1", SUB])
expect("Geleerd +5 zonder NL-subs blijft onder gelijke Remux met NL-subs (Taal op 6)",
       [remux_nosub, remux_sub], profile("best", BEST), ["RSUB", "RMX"])
twin = src("Film.2160p.UHD.BluRay.REMUX.DV.HDR.HEVC.TrueHD.Atmos.7.1-OTHER", 70, flags=["DV", "HDR", "REMUX", "TRUEHD", "ATMOS", "7.1"])
expect("Geleerd +5 gaat wel voor op een release die je profiel gelijk vindt (ook al is die groter)",
       [twin, remux_nosub], profile("best", BEST), ["RMX", "OTHER"])
_learned.clear()
_learned["rmx|4K|usenet"] = {"good": 0, "bad": 3, "at": 1}
expect("Een release die steeds mislukte zakt naar het eind van zijn resolutie",
       [remux_nosub, bluray_sub, web_sub], profile("fel", FEL, FEL_ORDERS), ["BRS", "WEBS", "RMX"])
_learned.clear()
online = src("Film.2160p.UHD.BluRay.DV.HDR.x265.TrueHD.Atmos.7.1-ONL", 30, flags=["DV", "HDR", "BLURAY", "TRUEHD", "ATMOS", "7.1", SUB], subs_online=True)
expect("Passende online ondertitel gaat niet boven een Remux (Bron boven Taal)",
       [online, remux_nosub], profile("fel", FEL, FEL_ORDERS), ["RMX", "ONL"])

print("4. Rode vlaggen, slechte groepen, nasynchronisatie")
cam = src("Film.2026.2160p.HDCAM.x264-CAMR", 3, flags=["CAM"])
tiny = src("Film.2160p.UHD.BluRay.REMUX.DV.HDR.HEVC.TrueHD.Atmos-TINY", 2, flags=["DV", "HDR", "REMUX", "TRUEHD", "ATMOS"])
expect("CAM en een veel te kleine 4K-'Remux' onderaan",
       [cam, tiny, web_sub], profile("fel", FEL, FEL_ORDERS, hide=[]), ["WEBS"])
dub = src("Film.2160p.GERMAN.UHD.BluRay.REMUX.DV.HDR.HEVC.TrueHD.Atmos.7.1-DUB", 60, flags=["DV", "HDR", "REMUX", "TRUEHD", "ATMOS", "7.1"])
expect("Duitse nasynchronisatie van een Engelse film onder de Engelse",
       [dub, web_sub], profile("fel", FEL, FEL_ORDERS), ["WEBS", "DUB"])
multi = src("Film.2160p.MULTi.UHD.BluRay.REMUX.DV.HDR.HEVC.TrueHD.Atmos.7.1.GERMAN-MUL", 60, flags=["DV", "HDR", "REMUX", "TRUEHD", "ATMOS", "7.1", "aio_MULTI"])
expect("MULTi houdt het origineel: niet als nasynchronisatie behandeld",
       [web_sub, multi], profile("fel", FEL, FEL_ORDERS), ["MUL", "WEBS"])

print("5. Gelijke stand")
big = src("Film.2160p.UHD.BluRay.REMUX.DV.HDR.HEVC.TrueHD.Atmos.7.1-BIG", 80, flags=["DV", "HDR", "REMUX", "TRUEHD", "ATMOS", "7.1"])
small = src("Film.2160p.UHD.BluRay.REMUX.DV.HDR.HEVC.TrueHD.Atmos.7.1-SMALL", 50, flags=["DV", "HDR", "REMUX", "TRUEHD", "ATMOS", "7.1"])
expect("Alles gelijk, Grootte 'grootste': grootste eerst",
       [small, big], profile("best", BEST), ["BIG", "SMALL"])
tier1 = src("Film.2160p.UHD.BluRay.REMUX.DV.HDR.HEVC.TrueHD.Atmos.7.1-FraMeSToR", 50, flags=["DV", "HDR", "REMUX", "TRUEHD", "ATMOS", "7.1"])
expect("TRaSH-groep (tier 1) gaat niet boven NL-subs (Taal op 6)",
       [tier1, remux_sub], profile("best", BEST), ["RSUB", "FraMeSToR"])

print("6. Te zwaar voor je lijn")
heavy = dict(remux_nosub, heavy=True)
expect("Te zwaar zakt naar het eind van zijn resolutie (bewuste uitzondering)",
       [heavy, web_sub], profile("fel", FEL, FEL_ORDERS), ["WEBS", "RMX"])

print()
print("ALLES GOED" if not FAILED else "%d FOUT: %s" % (len(FAILED), "; ".join(FAILED)))
sys.exit(1 if FAILED else 0)
