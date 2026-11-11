# -*- coding: utf-8 -*-
"""Homey cinema mode: a short signal to your Homey when a film or episode starts, pauses, goes on and ends. What
happens (lights down, up a little, on again) you decide yourself in a Homey Flow with the trigger 'Webhook' and the
event 'lucent'; the tag tells which moment it is: start, pauze, verder or einde.

Setting 'homey_address' holds either your Homey ID (via Homey's cloud: https://webhook.homey.app/<id>/lucent) or
the address of a Homey Pro in your own network (an IP address: http://<ip>/webhook?event=lucent). Nothing is sent
when it is empty. Only at these four moments, in a thread of its own, once: nothing runs while you watch."""
import re
import threading
import urllib.parse

import util

EVENT = "lucent"
TAGS = ("start", "pauze", "verder", "einde", "test")


def address():
    return util.setting("homey_address").strip()


def url_for(tag):
    value = address()
    if not value:
        return ""
    if re.match(r"^(https?://)?[\d.]+(:\d+)?/?$", value) or value.startswith("http://"):
        host = re.sub(r"^https?://", "", value).rstrip("/")
        return "http://%s/webhook?%s" % (host, urllib.parse.urlencode({"event": EVENT, "tag": tag}))
    return "https://webhook.homey.app/%s/%s?%s" % (urllib.parse.quote(value, safe=""), EVENT, urllib.parse.urlencode({"tag": tag}))


def _send(tag):
    url = url_for(tag)
    if not url:
        return False
    try:
        util.http(url, timeout=5)
        return True
    except util.HttpError as error:
        util.log("Homey %s: %s" % (tag, error))       # never the address: it holds your Homey ID
        return False


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
    if _send("test"):
        return xbmcgui.Dialog().ok("Homey", T("Homey heeft het seintje ontvangen. Maak in Homey een Flow met de kaart "
                                              "'Webhook', event 'lucent'. De tag is start, pauze, verder of einde.",
                                              "Homey received the signal. Make a Flow in Homey with the card 'Webhook', "
                                              "event 'lucent'. The tag is start, pauze, verder or einde."))
    return xbmcgui.Dialog().ok(T("Niet gelukt", "Failed"), T("Homey gaf geen antwoord. Controleer het Homey-ID (of het IP-adres) "
                                                              "en of Homey aan staat.",
                                                              "Homey did not answer. Check the Homey ID (or the IP address) "
                                                              "and that Homey is on."))
