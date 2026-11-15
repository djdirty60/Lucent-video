# -*- coding: utf-8 -*-
"""Usenet with your own provider and indexers, without AIOStreams (Lucent Video's own code, MIT).

    yenc.py      decoding the articles (yEnc)
    nntp.py      talking to the Usenet provider (NNTP, RFC 3977): TLS, login, STAT, BODY
    nzbfile.py   reading an NZB: which posted file is the film, and the parts of a RAR set
    archive.py   where the film sits inside a stored (uncompressed) RAR4 or RAR5 volume
    stream.py    a release as one long file: byte offset -> articles, read ahead, a small memory cache
    server.py    the local web server in the service that Kodi plays (with seeking)
    newznab.py   searching your indexers and fetching the NZB
    source.py    the scraper: sources for the list, and the link when you press play

Usernames, passwords and API keys never reach a log line, the error report or the cache.
"""
