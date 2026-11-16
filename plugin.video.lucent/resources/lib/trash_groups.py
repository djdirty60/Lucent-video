# -*- coding: utf-8 -*-
"""Release group tiers from the TRaSH Guides (https://trash-guides.info, github.com/TRaSH-Guides/Guides,
docs/json/radarr/cf), taken on 2026-11-16. MIT License, Copyright (c) 2021 TRaSH:

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated
documentation files (the "Software"), to deal in the Software without restriction, including without limitation the
rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit
persons to whom the Software is furnished to do so, subject to the following conditions: The above copyright notice and
this permission notice shall be included in all copies or substantial portions of the Software.
THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND.

TIERS: (kind, tier, TRaSH score, group patterns). LQ: groups and title patterns TRaSH scores -10000."""

TIERS = [
    ('web', 1, 1700, ['^(ABBIE)$', '^(AJP69)$', '^(APEX|PAXA|PEXA|XEPA)$', '^(BLUTONiUM)$', '^(BYNDR)$', '^(CMRG)$', '^(CRFW)$', '^(CRUD)$', '^(FLUX)$', '^(GNOME)$', '^(HONE)$', '^(KiNGS)$', '^(Kitsune)$', '^(MADSKY)$', '^(NOSiViD)$', '^(NTb)$', '^(NTG)$', '^(RAWR)$', '^(SiC)$', '^(TEPES)$', '^(TheFarm)$', '^(ZoroSenpai)$']),
    ('web', 2, 1650, ['^(dB)$', '^(Flights)$', '^(MiU)$', '^(monkee)$', '^(MZABI)$', '^(PHOENiX)$', '^(playWEB)$', '^(SbR)$', '^(SMURF)$', '^(TOMMY)$', '^(XEBEC|4KBEC|CEBEX)$']),
    ('web', 3, 1600, ['^(BLOOM)$', '^(Dooky)$', '^(GNOMiSSiON)$', '^(NINJACENTRAL)$', '^(NPMS)$', '^(ROCCaT)$', '^(SiGMA)$', '^(SLiGNOME)$', '^(SwAgLaNdEr)$']),
    ('remux', 1, 1950, ['^(3L)$', '^(ATELiER)$', '^(BiZKiT)$', '^(BLURANiUM)$', '^(BMF)$', '^(CiNEPHiLES)$', '^(FraMeSToR)$', '^(PiRAMiDHEAD)$', '^(PmP)$', '^(WiLDCAT)$', '^(ZQ)$']),
    ('remux', 2, 1900, ['^(NCmt)$', '^(playBD)$', '^(SiCFoI)$', '^(SURFINBIRD)$', '^(TEPES)$']),
    ('remux', 3, 1850, ['^(12GaugeShotgun)$', '^(decibeL)$', '^(EPSiLON)$', '^(HiFi)$', '^(iFT)$', '^(KRaLiMaRKo)$', '^(NTb)$', '^(PTP)$', '^(SumVision)$', '^(TOA)$', '^(TRiToN)$']),
    ('hdbluray', 1, 1800, ['^(ATELiER)$', '^(BBQ)$', '^(BMF)$', '^(c0kE)$', '^(Chotab)$', '^(CRiSC)$', '^(CtrlHD)$', '^(D-Z0N3)$', '^(Dariush)$', '^(decibeL)$', '^(DON)$', '^(EbP)$', '^(EDPH)$', '^(Geek)$', '^(LolHD)$', '^(NCmt)$', '^(PTer)$', '^(TayTO)$', '^(TDD)$', '^(TnP)$', '^(VietHD)$', '^(ZQ)$', '^(ZoroSenpai)$']),
    ('hdbluray', 2, 1750, ['^(EA)$', '^(HiDt)$', '^(HiSD)$', '^(iFT)$', '^(NTb)$', '^(QOQ)$', '^(SA89)$', '^(sbR)$']),
    ('hdbluray', 3, 1700, ['^(BHDStudio)$', '^(hallowed)$', '^(HiFi)$', '^(HONE)$', '^(LoRD)$', '^(playHD)$', '^(SPHD)$', '^(W4NK3R)$']),
    ('uhdbluray', 1, 1800, ['^(CtrlHD)$', '^(MainFrame)$', '^(DON)$', '^(W4NK3R)$']),
    ('uhdbluray', 2, 1750, ['^(HiDt)$', '^(HQMUX)$', '^(RandomBytes)$']),
    ('uhdbluray', 3, 1700, ['^(BHDStudio)$', '^(hallowed)$', '^(HONE)$', '^(PTer)$', '^(SPHD)$', '^(WEBDV)$']),
]
LQ_GROUPS = ['^(126811)$', '\\b(24xHD)\\b', '^(41RGB)$', '^(4K4U)$', '^(AOC)$', '^(AROMA)$', '^(aXXo)$', '^(AZAZE)$', '^(BARC0DE)$', '^(BAUCKLEY)$', '^(BdC)$', '^(beAst)$', '^(BTM)$', '^(C1NEM4)$', '^(C4K)$', '^(CDDHD)$', '^(CHAOS)$', '^(CHD)$', '^(CiNE)$', '^(CLEANUP)$', '^(COLLECTiVE)$', '^(CREATiVE24)$', '^(CrEwSaDe)$', '^(CTFOH)$', '^(d3g)$', '^(DDR)$', '^(DNL)$', '^(DRX)$', '^(E)$', '^(EPiC)$', '^(EuReKA)$', '^(FaNGDiNG0)$', '^(Feranki1980)$', '^(FGT)$', '^(FMD)$', '^(FRDS)$', '^(FS)$', '^(FZHD)$', '^(GalaxyRG)$', '^(GHD)$', '^(GPTHD)$', '^(HDHUB4U)$', '^(10bit-)?HDS$', '^(HDT)$', '^(HDTime)$', '^(HDWinG)$', '^(iNTENSO)$', '^(iPlanet)$', '^(iVy)$', '^(jennaortega(UHD)?)$', '^(JFF)$', '^(KC)$', '^(KiNGDOM)$', '^(KIRA)$', '^(L0SERNIGHT)$', '^(LAMA)$', '^(Leffe)$', '^(Liber8)$', '^(LiGaS)$', '^(LUCY)$', '^(MarkII)$', '^(MeGusta)$', '^(Mesc)$', '^(mHD)$', '^(mSD)$', '^(MTeam|MT)$', '^(MySiLU)$', '^(NhaNc3)$', '^(nHD)$', '^(nikt0)$', 'NoGr(ou)?p', '^(nSD)$', '^(OFT)$', 'Pahe(\\.(ph|in))?\\b', '^(PATOMiEL)$', '^(PRODJi)$', '^(PSA)$', '^(PTNK)$', '^(RARBG)$', '^(RBB)$', '^(RDN)$', '^(Rifftrax)$', '^(RU4HD)$', '^(SANTi)$', '^(Scene)$', '^(SHD)$', '^(ShieldBearer)$', '^(STUTTERSHIT)$', '^(SUNSCREEN)$', '^(SyncUP)$', '^(TBS)$', '^(TEKNO3D)$', '^(Tigole)$', '^(TIKO)$', '^(VISIONPLUSHDR(-X|1000)?)$', '^(WAF)$', '^(WiKi)$', '^(x0r)$', '^(YIFY)$', '^(YTS(.(MX|LT|AG))?)$', '^(Zeus)$']
LQ_TITLES = ['\\b(1XBET)\\b', '\\b(BEN[ ._-]THE[ ._-]MEN)\\b', '(-D3US|D3US-)', '(?<!\\bweb[ ._-]?(dl|rip)?\\b.*)-(EVO)\\b', '\\b(Feranki1980)\\b', '\\b(GalaxyRG)\\b', '^(?!.*\\bMA\\b.*\\bWEB-?DL\\b).*\\b(HHWEB)\\b', '(?<!-)\\b(jennaortega(UHD)?)\\b', '(?<!\\bweb[ ._-]?(dl|rip)?\\b.*)-(PiRaTeS)\\b', '\\b(R&H)\\b', '\\b(READ(\\s|\\.)+NOTE)\\b', '^(?=.*\\b(CMv4)\\b).*\\b(SiCFoI)\\b', '\\b(SWTYBLZ)\\b', '\\b(TeeWee)\\b', '\\b(TEKNO3D)\\b', '(?<!\\b(remux).*?)\\b(unkn0wn)\\b', '\\b(Will1869)\\b']
