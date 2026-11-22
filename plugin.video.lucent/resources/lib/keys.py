# -*- coding: utf-8 -*-
# This file is public. Secret keys stay out of it: they come from the settings or keys.json (see apikeys.py).
# The Trakt Client ID is the exception: Lucent's Trakt app (production access, 13-11-2026) signs in without a secret
# (PKCE / device code), so its Client ID may be public, the way Trakt intends it.
# TMDB: Lucent's own free, non-commercial key (22-11-2026), so Lucent works right after installing. A key of your own in
# the settings (or keys.json) always goes first. A public TMDB v3 key only reads public film and series data.
TMDB_KEY = "81d47c322f4bbec199751d9f55e8dda1"
TRAKT_CLIENT_ID = "Ygmc_TAb8t2Y_c3VoSLYyG0i4jZ_cAVBqn18kiyvuvY"
SIMKL_CLIENT_ID = ""
# PunchPlay: Lucent Video's app is a public client (device code, no secret), so its Client ID may be public too.
PUNCHPLAY_CLIENT_ID = "ppc_6c991312667ab58dd351d99e"
