# -*- coding: utf-8 -*-
"""Homey cinema mode: a short signal to your Homey when a film or episode starts, pauses, goes on and ends. What
happens (lights down, up a little, on again) you decide yourself in a Homey Flow with the trigger 'Webhook' and the
event 'lucent'; the tag tells which moment it is: start-film, start-serie, pauze, verder, aftiteling or einde.

Setting 'homey_address' holds either your Homey ID (via Homey's cloud: https://webhook.homey.app/<id>/lucent) or
the address of a Homey Pro in your own network (an IP address: http://<ip>/webhook?event=lucent). Nothing is sent
when it is empty. Only at these four moments, in a thread of its own, once: nothing runs while you watch."""
import re
import threading
import urllib.parse

import util

EVENT = "lucent"


def address():
    """The Homey ID or IP address; a pasted webhook link gives its ID."""
    value = util.setting("homey_address").strip().strip("/")
    found = re.search(r"webhook\.homey\.app/([^/?#\s]+)", value)
    return found.group(1) if found else value.replace(" ", "")


def url_for(tag):
    value = address()
    if not value:
        return ""
    if re.match(r"^(https?://)?[\d.]+(:\d+)?/?$", value) or value.startswith("http://"):
        host = re.sub(r"^https?://", "", value).rstrip("/")
        return "http://%s/webhook?%s" % (host, urllib.parse.urlencode({"event": EVENT, "tag": tag}))
    return "https://webhook.homey.app/%s/%s?%s" % (urllib.parse.quote(value, safe=""), EVENT, urllib.parse.urlencode({"tag": tag}))


def _send(tag):
    """True when Homey took the signal (any 2xx). Homey answers with plain text, not JSON, so only the status counts.
    Returns (ok, reason) - the reason never holds the address."""
    import urllib.error
    import urllib.request
    url = url_for(tag)
    if not url:
        return False, ""
    request = urllib.request.Request(url, headers={"User-Agent": "Lucent-Video"})
    try:
        with urllib.request.urlopen(request, timeout=6) as response:
            response.read(512)
            return 200 <= response.status < 300, str(response.status)
    except urllib.error.HTTPError as error:
        util.log("Homey %s: HTTP %s" % (tag, error.code))
        return False, "HTTP %s" % error.code
    except (urllib.error.URLError, OSError, ValueError) as error:
        util.log("Homey %s: %s" % (tag, type(error).__name__))
        return False, type(error).__name__


def signal(tag):
    """Sends one signal in the background. Does nothing without an address."""
    if address():
        threading.Thread(target=_send, args=(tag,), daemon=True).start()


def check():
    """The test button: sends 'test' and says whether Homey answered."""
    import xbmcgui
    T = util.T
    if not address():
        return xbmcgui.Dialog().ok("Homey", T("Vul eerst je Homey-ID of het IP-adres van je Homey Pro in.",
                                              "Enter your Homey ID or the IP address of your Homey Pro first."))
    ok, reason = _send("test")
    if ok:
        return xbmcgui.Dialog().ok("Homey", T("Homey heeft het seintje ontvangen. Maak in Homey een Flow met de kaart "
                                              "'Webhook', event 'lucent'. De tag is start-film, start-serie, pauze, verder, aftiteling of einde.",
                                              "Homey received the signal. Make a Flow in Homey with the card 'Webhook', "
                                              "event 'lucent'. The tag is start-film, start-serie, pauze, verder, aftiteling or einde."))
    if reason.startswith("HTTP 404"):
        text = T("Homey kent dit adres niet (code 404). Controleer het Homey-ID. Bij een IP-adres: alleen een Homey Pro "
                 "(2023 of nieuwer) of Homey Pro mini heeft lokale webhooks.",
                 "Homey does not know this address (code 404). Check the Homey ID. With an IP address: only a Homey Pro "
                 "(2023 or newer) or Homey Pro mini has local webhooks.")
    elif reason.startswith("HTTP"):
        text = T("Homey weigerde het seintje (%s). Controleer het Homey-ID of het IP-adres.", "Homey refused the signal (%s). "
                 "Check the Homey ID or the IP address.") % reason
    else:
        text = T("Geen verbinding met Homey (%s). Controleer het Homey-ID of het IP-adres, en of Homey aan staat en in "
                 "hetzelfde netwerk zit.", "No connection with Homey (%s). Check the Homey ID or the IP address, and that "
                 "Homey is on and in the same network.") % reason
    return xbmcgui.Dialog().ok(T("Niet gelukt", "Failed"), text)
