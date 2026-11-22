# -*- coding: utf-8 -*-
"""'Account information' for every service: who you are linked as, your subscription and until when.
Also checks the connection. E-mail addresses, keys and tokens are never shown or logged."""
import datetime
import re

import xbmcgui

import util

T = util.T


def _date(value):
    """An ISO date, a unix time or '' -> '12 maart 2027' style, or ''."""
    if not value:
        return ""
    try:
        if isinstance(value, (int, float)) or str(value).isdigit():
            when = datetime.datetime.utcfromtimestamp(int(value))
        else:
            when = datetime.datetime.strptime(str(value)[:10], "%Y-%m-%d")
    except (ValueError, OverflowError, OSError):
        return ""
    months = T("januari februari maart april mei juni juli augustus september oktober november december",
               "January February March April May June July August September October November December").split()
    return "%d %s %d" % (when.day, months[when.month - 1], when.year)


def _days_left(value):
    try:
        if isinstance(value, (int, float)) or str(value).isdigit():
            when = datetime.datetime.utcfromtimestamp(int(value))
        else:
            when = datetime.datetime.strptime(str(value)[:10], "%Y-%m-%d")
    except (ValueError, OverflowError, OSError):
        return None
    return (when - datetime.datetime.utcnow()).days


def _until(value):
    if not value:
        return ""
    left = _days_left(value)
    text = T("Geldig tot %s", "Valid until %s") % _date(value)
    if left is not None:
        text += T(" (nog %d dagen)", " (%d days left)") % max(0, left)
    return text


def trakt():
    import trakt as service
    if not service.logged_in():
        return [T("Niet gekoppeld.", "Not linked.")]
    data = service.call("users/settings")[0] or {}
    user = data.get("user") or {}
    lines = [T("Gekoppeld als %s", "Linked as %s") % (user.get("username") or "?")]
    lines.append("VIP" if user.get("vip") else T("Gratis account", "Free account"))
    if user.get("joined_at"):
        lines.append(T("Lid sinds %s", "Member since %s") % _date(user["joined_at"]))
    try:
        stats = service.call("users/me/stats")[0] or {}
        movies, episodes = stats.get("movies") or {}, stats.get("episodes") or {}
        hours = int(((movies.get("minutes") or 0) + (episodes.get("minutes") or 0)) / 60)
        lines.append(T("Bekeken: %d films, %d afleveringen (%d uur)", "Watched: %d films, %d episodes (%d hours)")
                     % (movies.get("watched") or 0, episodes.get("watched") or 0, hours))
    except util.HttpError:
        pass
    return lines


def simkl():
    import simkl as service
    if not service.logged_in():
        return [T("Niet gekoppeld.", "Not linked.")]
    data = service.call("users/settings", data={}) or {}
    user = data.get("user") or {}
    lines = [T("Gekoppeld als %s", "Linked as %s") % (user.get("name") or "?")]
    if user.get("joined_at"):
        lines.append(T("Lid sinds %s", "Member since %s") % _date(user["joined_at"]))
    return lines


def punchplay():
    import punchplay as service
    if not service.logged_in():
        return [T("Niet gekoppeld.", "Not linked.")]
    me = service.call("me") or {}
    lines = [T("Gekoppeld als %s", "Linked as %s") % (me.get("username") or (me.get("profile") or {}).get("displayName") or "?"),
             T("Gratis account", "Free account")]
    try:
        movies = len(service.watched_movies())
        episodes = sum(len(v) for v in service.watched_episodes().values())
        lines.append(T("Bekeken: %d %s, %d %s", "Watched: %d %s, %d %s") % (movies, T("film" if movies == 1 else "films", "film" if movies == 1 else "films"),
                     episodes, T("aflevering" if episodes == 1 else "afleveringen", "episode" if episodes == 1 else "episodes")))
    except util.HttpError:
        pass
    return lines


def mdbtrack():
    import mdbtrack as service
    if not service.logged_in():
        return [T("Niet gekoppeld.", "Not linked.")]
    lines = [T("Gekoppeld als %s", "Linked as %s") % (service.account().get("user") or "?")]
    try:
        movies = len(service.watched_movies())
        episodes = sum(len(v) for v in service.watched_episodes().values())
        lines.append(T("Bekeken: %d %s, %d %s", "Watched: %d %s, %d %s") % (movies, T("film" if movies == 1 else "films", "film" if movies == 1 else "films"),
                     episodes, T("aflevering" if episodes == 1 else "afleveringen", "episode" if episodes == 1 else "episodes")))
    except util.HttpError:
        pass
    return lines


def easynews():
    import usenet
    ok, text = usenet.check_easynews()
    lines = [text]
    if ok:
        lines.insert(0, T("Gebruikersnaam: %s", "Username: %s") % util.setting("easynews_user"))
        try:
            _user, plan, expires, status, used, left, loyalty = usenet.easynews_account()
        except Exception as error:      # noqa: BLE001 - the account pages are a bonus; the check above already said 'connected'
            util.log("Easynews account pages: %s" % type(error).__name__)
            return lines
        if plan:
            lines.append(T("Abonnement: %s", "Plan: %s") % plan + (" (%s)" % status if status else ""))
        if expires:
            lines.append(_until(expires) if re.match(r"\d{4}-\d{2}-\d{2}", expires) else T("Geldig tot %s", "Valid until %s") % expires)
        if used:
            lines.append(T("Verbruikt: %s", "Used: %s") % used)
        if left:
            lines.append(T("Over: %s", "Left: %s") % left)
        if loyalty:
            lines.append(T("Loyaliteit: %s", "Loyalty: %s") % loyalty)
        if not plan and not used:
            util.log("Easynews account pages: not read (their layout may have changed)")
        lines += usenet.speed_lines()
    return lines


def torbox():
    if not util.setting("torbox_key"):
        return [T("Vul eerst je API-sleutel in.", "Enter your API key first.")]
    import time
    import usenet
    for attempt in (1, 2):
        try:
            answer, _ = util.http(usenet.TB_API + "/user/me", headers=usenet._tb_head())
            break
        except util.HttpError as error:
            util.log("TorBox account: %s; key: %s" % (usenet.tb_reason(error), usenet.tb_key_shape()))
            if attempt == 1 and "AUTH_ERROR" in usenet.tb_reason(error):
                time.sleep(2)                   # TorBox: 'an error occurred while verifying your token, please try again'
                continue
            return [T("Geen verbinding: %s", "No connection: %s") % usenet.tb_reason(error)]
    data = answer.get("data") if isinstance(answer, dict) else None
    if not isinstance(data, dict):
        util.log("TorBox account: unexpected answer (%s)" % usenet._shape(answer))
        raise ValueError("TorBox answer")
    plans = {0: T("Gratis", "Free"), 1: "Essential", 2: "Pro", 3: "Standard"}
    try:
        plan = plans.get(int(data.get("plan")), data.get("plan"))
    except (TypeError, ValueError):
        plan = data.get("plan") or "?"
    lines = [T("Verbonden met TorBox.", "Connected to TorBox."), T("Abonnement: %s", "Plan: %s") % plan]
    if data.get("premium_expires_at"):
        lines.append(_until(data["premium_expires_at"]))
    try:
        if data.get("total_downloaded"):
            lines.append(T("Gedownload in totaal: %d", "Downloaded in total: %d") % int(float(data["total_downloaded"])))
    except (TypeError, ValueError):
        pass                                    # a counter in another form: not worth an error
    return lines


def realdebrid():
    import debrid
    if not debrid.key("realdebrid"):
        return [T("Vul eerst je API-sleutel in.", "Enter your API key first.")]
    data, _ = util.http(debrid.RD + "user", headers=debrid._head("realdebrid"))
    data = data or {}
    lines = [T("Gekoppeld als %s", "Linked as %s") % (data.get("username") or "?"),
             "Premium" if data.get("type") == "premium" else T("Gratis account", "Free account")]
    if data.get("expiration"):
        lines.append(_until(data["expiration"]))
    if data.get("points") is not None:
        lines.append(T("Fidelity-punten: %s", "Fidelity points: %s") % data["points"])
    return lines


def alldebrid():
    import debrid
    if not debrid.key("alldebrid"):
        return [T("Vul eerst je API-sleutel in.", "Enter your API key first.")]
    user = (debrid._ad_call("v4/user").get("user") or {})
    lines = [T("Gekoppeld als %s", "Linked as %s") % (user.get("username") or "?"),
             "Premium" if user.get("isPremium") else T("Gratis account", "Free account")]
    if user.get("premiumUntil"):
        lines.append(_until(user["premiumUntil"]))
    return lines


def premiumize():
    import debrid
    if not debrid.key("premiumize"):
        return [T("Vul eerst je API-sleutel in.", "Enter your API key first.")]
    data, _ = util.http(debrid.PM + "account/info", headers=debrid._head("premiumize"))
    data = data or {}
    if data.get("status") != "success":
        return [T("Premiumize accepteert deze sleutel niet.", "Premiumize does not accept this key.")]
    lines = [T("Verbonden met Premiumize.", "Connected to Premiumize.")]
    if data.get("premium_until"):
        lines.append(_until(data["premium_until"]))
    if data.get("limit_used") is not None:
        lines.append(T("Fair use gebruikt: %d%%", "Fair use used: %d%%") % round(float(data["limit_used"]) * 100))
    return lines


def mdblist():
    import mdblist as service
    ok, text = service.check()
    return [text]


SERVICES = {"trakt": ("Trakt", trakt), "simkl": ("Simkl", simkl), "punchplay": ("PunchPlay", punchplay), "mdbtrack": ("MDBList", mdbtrack), "easynews": ("Easynews", easynews), "torbox": ("TorBox", torbox),
            "realdebrid": ("Real-Debrid", realdebrid), "alldebrid": ("AllDebrid", alldebrid), "premiumize": ("Premiumize", premiumize),
            "mdblist": ("MDBList", mdblist)}


def show(service):
    name, read = SERVICES[service]
    try:
        lines = read()
    except util.HttpError as error:
        lines = [T("Geen verbinding: %s", "No connection: %s") % error]
    except (KeyError, ValueError, TypeError):
        lines = [T("Het antwoord van %s kon niet worden gelezen.", "The answer from %s could not be read.") % name]
    xbmcgui.Dialog().ok(T("Accountinformatie · %s", "Account information · %s") % name, "\n".join(l for l in lines if l))


def publish_names():
    """Puts the linked account names in the settings, so the settings can show 'Linked as ...' (first profile only)."""
    import users
    if users.current_id() != users.MAIN:
        return
    for setting, module in (("trakt_user", "trakt"), ("simkl_user", "simkl"), ("punchplay_user", "punchplay"), ("mdbtrack_user", "mdbtrack")):
        account = __import__(module)
        name = account.account().get("user", "") if account.logged_in() else ""
        if not name and account.logged_in():
            name = T("ja", "yes")
        if util.setting(setting) != name:
            util.ADDON.setSetting(setting, name)
    import wetrakr
    name = (wetrakr.account().get("user") or T("ja", "yes")) if wetrakr.linked() else ""
    if util.setting("wetrakr_user") != name:
        util.ADDON.setSetting("wetrakr_user", name)
    import tmdbacct
    name = (tmdbacct.account().get("user") or T("ja", "yes")) if tmdbacct.linked() else ""
    if util.setting("tmdb_user") != name:
        util.ADDON.setSetting("tmdb_user", name)
