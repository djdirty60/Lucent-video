# -*- coding: utf-8 -*-
"""Skip intros by themselves, per series: a series switched on here is skipped by itself (intro, recap and preview),
whatever the general settings say. Used by skipper.py, which does the skipping with TheIntroDB's data."""
import util

T = util.T
FILE = "intro_skip.json"


def available():
    return True


def series_auto(show):
    return bool(util.load_json(FILE, {}).get("series", {}).get(str(show)))


def prepare(meta):
    """Nothing to prepare any more: Lucent skips by itself (kept so older calls do no harm)."""
    return


def toggle(show):
    data = util.load_json(FILE, {})
    series = data.setdefault("series", {})
    now = not series.get(str(show), False)
    series[str(show)] = now
    util.save_json(FILE, data)
    util.notify(T("Intro's van deze serie worden vanzelf overgeslagen", "Intros of this series are skipped by themselves") if now
                else T("Intro's van deze serie niet meer vanzelf overslaan", "Intros of this series are no longer skipped by themselves"))
