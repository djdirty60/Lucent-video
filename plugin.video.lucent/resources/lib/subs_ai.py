# -*- coding: utf-8 -*-
"""Lucent: AI subtitles - an AI service translates an English subtitle into your language.

Started from the live subtitle list (subs_live.py). It works from where you are watching: the next few minutes are
translated first and shown at once, the rest follows in the background and the subtitle is reloaded when it is done.
The finished translation is kept (addon_data/plugin.video.lucent/subs_ai/), so the next time it opens at once.

Needs your own key for one of the services (Lucent Video settings > Subtitles and sound > AI translation):
Google Gemini (free key), OpenRouter (one key for many models) or Anthropic Claude. The subtitle text is sent to that
service for the translation.
"""
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request

import subconf
import xbmc
import xbmcgui
import xbmcvfs

NL = xbmc.getLanguage(xbmc.ISO_639_1) == "nl"
KEY_SETTING = "lucent.gemini.key"
PROVIDER_SETTING = "lucent.ai.provider"
# service -> (name shown, key setting, model setting, default model)
PROVIDERS = {
    "gemini": ("Google Gemini", "lucent.gemini.key", "", ""),
    "openrouter": ("OpenRouter", "lucent.openrouter.key", "lucent.openrouter.model", "openrouter/auto"),
    "claude": ("Claude", "lucent.claude.key", "lucent.claude.model", "claude-haiku-5-5"),
}
MODELS = ("gemini-flash-latest", "gemini-3.8-flash", "gemini-3.6-flash", "gemini-2.5-flash")
CHUNK = 60                   # subtitle lines per request
WORKERS = 2                  # parts asked at the same time after the first (more runs into the free Gemini limit)
REFRESH = 4                  # parts done before the partly translated subtitle is loaded again
DIR = subconf.data_dir("subs_ai")
LANG_NAMES = {"nl": "Dutch", "de": "German", "fr": "French", "es": "Spanish", "it": "Italian", "pt": "Portuguese",
              "da": "Danish", "sv": "Swedish", "no": "Norwegian", "fi": "Finnish", "pl": "Polish", "tr": "Turkish"}


def t(en, nl):
    return nl if NL else en


def log(text):
    xbmc.log(f"[lucent.video] subs_ai: {text}", xbmc.LOGINFO)


def notify(text, ms=4000, icon=xbmcgui.NOTIFICATION_INFO):
    xbmcgui.Dialog().notification(t("AI subtitles", "AI-ondertitels"), text, icon, ms)


# ----------------------------------------------------------------------------- SRT
TIME = re.compile(r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)")


def read_text(path):
    with xbmcvfs.File(path) as fh:
        raw = fh.readBytes()
    raw = bytes(raw)
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def to_seconds(h, m, s, ms):
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms[:3].ljust(3, "0")) / 1000


def parse_srt(text):
    cues = []
    for block in re.split(r"\r?\n\s*\r?\n", text.strip()):
        lines = [x for x in block.splitlines() if x.strip() != ""]
        idx = next((i for i, x in enumerate(lines) if TIME.search(x)), None)
        if idx is None:
            continue
        m = TIME.search(lines[idx])
        start, end = to_seconds(*m.groups()[:4]), to_seconds(*m.groups()[4:])
        body = "\n".join(lines[idx + 1:]).strip()
        if body:
            cues.append({"start": start, "end": end, "time": lines[idx].strip(), "text": body})
    return cues


# hearing-impaired notes: (he sighs), [wind howling], ♪ music ♪, and speaker names like "JOHN:" or "- MAN:"
SDH_BRACKETS = re.compile(r"\([^)]*\)|\[[^\]]*\]|\{\*?[^}]*\}|<i>\s*\([^)]*\)\s*</i>")
SDH_SPEAKER = re.compile(r"^(\s*-?\s*)(?:<i>)?[A-Z][A-Z0-9 .'#-]{1,30}:\s*(?:</i>)?")
SDH_MUSIC = re.compile(r"^[\s\-]*[♪♫#*]+.*$|^.*[♪♫]+[\s\-]*$")


def clean_sdh(text):
    """Remove what is meant for the hard of hearing; keep the spoken words. '' when nothing spoken is left."""
    out = []
    for line in text.split("\n"):
        line = SDH_BRACKETS.sub("", line)
        line = SDH_SPEAKER.sub(r"\1", line)
        if SDH_MUSIC.match(line):
            continue
        line = re.sub(r"<(i|b|u)>\s*</\1>", "", line)
        line = re.sub(r"\s{2,}", " ", line).strip()
        if line in ("", "-", "...", "- ...") or not re.search(r"\w", line):
            continue
        out.append(line)
    if len(out) == 1 and out[0].startswith("- "):
        out[0] = out[0][2:]                               # a dialogue dash with only one speaker left
    return "\n".join(out)


def keep_sdh():
    return subconf.on("lucent.subs.ai.keephi")


def write_srt(path, cues):
    """A cue whose translation is empty (only a sound, such as (sighs)) is left out; one not translated yet stays English."""
    out = []
    for c in cues:
        text = c["text"] if c.get("tr") is None else c["tr"]
        if text.strip():
            out.append(f"{len(out) + 1}\n{c['time']}\n{text}\n")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out))


# ----------------------------------------------------------------------------- services
def skin_string(name):
    return subconf.text(name) if name else ""


def provider():
    """The chosen AI service; when it has no key but another one has, that one is used."""
    chosen = skin_string(PROVIDER_SETTING) or "gemini"
    if chosen not in PROVIDERS:
        chosen = "gemini"
    if skin_string(PROVIDERS[chosen][1]):
        return chosen
    return next((p for p, v in PROVIDERS.items() if skin_string(v[1])), chosen)


def provider_name(p=None):
    return PROVIDERS[p or provider()][0]


def has_key():
    return bool(skin_string(PROVIDERS[provider()][1]))


def make_prompt(lines, target, before=()):
    """before: the English lines just before this part, only so the model knows who speaks and how (formal or informal
    address, names): they are not translated."""
    context = ""
    if before:
        context = ("For context only, the lines just before (do NOT translate or return these):\n"
                   + json.dumps(list(before), ensure_ascii=False) + "\n\n")
    return (f"Translate these film/TV subtitle lines from English into natural, spoken {target}. "
            "Keep it short like real subtitles, keep line breaks (\\n) inside a line, keep names, do not add notes. "
            "Keep the way people address each other (formal or informal) the same as in the lines before. "
            "Do not translate or add descriptions of sounds or actions such as (sighs) or [music]; give an empty string for a line "
            "that is only such a description. "
            f"Return only a JSON array with exactly {len(lines)} strings, in the same order.\n\n"
            + context + "Lines to translate:\n" + json.dumps(lines, ensure_ascii=False))


def json_list(text, count):
    """The JSON array in an answer (models sometimes wrap it in ``` or add a sentence)."""
    text = (text or "").strip()
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end < start:
        raise ValueError("no JSON array in the answer")
    out = json.loads(text[start:end + 1])
    if not isinstance(out, list) or len(out) != count:
        raise ValueError(f"got {len(out) if isinstance(out, list) else '?'} lines for {count}")
    return [str(x) for x in out]


def post(url, body, headers):
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=90) as resp:
        return json.loads(resp.read().decode("utf-8"))


def openrouter(key, lines, target, before=()):
    """Thinking (reasoning) off: a subtitle needs no reasoning, and those tokens cost time and money. A model that must
    always think refuses that request (400): then the same request goes again without it."""
    model = skin_string(PROVIDERS["openrouter"][2]) or PROVIDERS["openrouter"][3]
    body = {"model": model, "temperature": 0.2, "messages": [{"role": "user", "content": make_prompt(lines, target, before)}],
            "reasoning": {"effort": "none"}}
    head = {"Authorization": f"Bearer {key}", "HTTP-Referer": "https://github.com/djdirty60/Lucent", "X-Title": "Lucent"}
    try:
        data = post("https://openrouter.ai/api/v1/chat/completions", body, head)
    except urllib.error.HTTPError as error:
        if error.code != 400:
            raise
        log("OpenRouter: this model does not take 'no reasoning', asking again with its own setting")
        body.pop("reasoning")
        data = post("https://openrouter.ai/api/v1/chat/completions", body, head)
    text = (((data.get("choices") or [{}])[0].get("message") or {}).get("content")) or ""
    return json_list(text, len(lines))


def claude(key, lines, target, before=()):
    model = skin_string(PROVIDERS["claude"][2]) or PROVIDERS["claude"][3]
    data = post("https://api.anthropic.com/v1/messages",
                {"model": model, "max_tokens": 8000, "messages": [{"role": "user", "content": make_prompt(lines, target, before)}]},
                {"x-api-key": key, "anthropic-version": "2023-06-01"})
    text = "".join(b.get("text", "") for b in data.get("content") or [] if b.get("type") == "text")
    return json_list(text, len(lines))


def translate(lines, target, before=()):
    p = provider()
    key = skin_string(PROVIDERS[p][1])
    if p == "openrouter":
        return openrouter(key, lines, target, before)
    if p == "claude":
        return claude(key, lines, target, before)
    return gemini(key, lines, target, before)


BUSY = (429, 500, 502, 503, 504, 529)      # a busy service or a short limit: waiting helps
FATAL = (400, 401, 402, 403, 404)          # wrong key, no credit, unknown model: waiting does not help


def translate_part(lines, target, before=(), tries=3, wait=None):
    """One part, made sturdy: a busy service (429 / 5xx) is asked again after a pause; an answer with the wrong number
    of lines is split in two halves (a long part is where models lose count). Fatal errors are passed on."""
    wait = wait or (lambda seconds: xbmc.Monitor().waitForAbort(seconds))
    for attempt in range(tries):
        try:
            return translate(lines, target, before)
        except urllib.error.HTTPError as error:
            if error.code not in BUSY or attempt == tries - 1:
                raise
            pause = _retry_after(error) or 10 * (attempt + 1)
            log(f"service busy (HTTP {error.code}), again in {pause} s")
            if wait(pause):
                raise
        except ValueError as error:                       # wrong number of lines, or no JSON
            if len(lines) <= 8 or attempt == tries - 1:
                raise
            log(f"{error}: asking again in two halves")
            half = len(lines) // 2
            return (translate_part(lines[:half], target, before, tries, wait)
                    + translate_part(lines[half:], target, list(before[-3:]) + lines[max(0, half - 3):half], tries, wait))
    raise RuntimeError("no answer")


def _retry_after(error):
    try:
        return max(1, min(60, int(float(error.headers.get("Retry-After") or 0))))
    except (TypeError, ValueError, AttributeError):
        return 0


# ----------------------------------------------------------------------------- Gemini
def gemini(key, lines, target, before=()):
    """Translate a list of subtitle lines; returns a list of the same length."""
    prompt = make_prompt(lines, target, before)
    body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json",
                                 "responseSchema": {"type": "ARRAY", "items": {"type": "STRING"}}}}
    last = None
    for model in MODELS:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST",
                                     headers={"Content-Type": "application/json", "x-goog-api-key": key})
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            last = error
            if error.code in (400, 401, 403, 429):
                raise                              # wrong key / no quota: another model will not help
            continue                               # 404 (model gone) / 5xx: try the next model
        except Exception as error:  # noqa: BLE001
            last = error
            continue
        text = "".join(p.get("text", "") for p in ((data.get("candidates") or [{}])[0].get("content") or {}).get("parts", []))
        out = json.loads(text)
        if isinstance(out, list) and len(out) == len(lines):
            return [str(x) for x in out]
        last = ValueError(f"got {len(out) if isinstance(out, list) else '?'} lines for {len(lines)}")
    raise last or RuntimeError("no model answered")


# ----------------------------------------------------------------------------- cache
def media_key():
    show = xbmc.getInfoLabel("VideoPlayer.TVShowTitle")
    if show:
        name = f"{show} S{xbmc.getInfoLabel('VideoPlayer.Season')}E{xbmc.getInfoLabel('VideoPlayer.Episode')}"
    else:
        name = f"{xbmc.getInfoLabel('VideoPlayer.Title')} {xbmc.getInfoLabel('VideoPlayer.Year')}"
    imdb = xbmc.getInfoLabel("VideoPlayer.IMDBNumber")
    return hashlib.md5(f"{imdb}|{name}".lower().encode("utf-8")).hexdigest()[:16]


def cached_for_current(code):
    path = os.path.join(DIR, f"{media_key()}.{code}.v2.srt")
    return path if os.path.exists(path) else ""


# ----------------------------------------------------------------------------- work
def show(path, code):
    """Switch the translation on, also when the film's own subtitle (for example a 'forced' one) is selected."""
    try:
        from subs_live import activate
        activate(path, code, 8)
    except Exception as error:  # noqa: BLE001
        log(f"show: {error}")
        xbmc.Player().setSubtitles(path)
        xbmc.Player().showSubtitles(True)


def translate_and_load(source_path, code):
    if not has_key():
        notify(t("No AI key - Lucent Video settings > Subtitles and sound", "Geen AI-sleutel - instellingen van Lucent Video > Ondertitels en geluid"))
        return
    name = provider_name()
    target = LANG_NAMES.get(code) or xbmc.convertLanguage(code, xbmc.ENGLISH_NAME) or xbmc.getLanguage(xbmc.ENGLISH_NAME)
    try:
        cues = parse_srt(read_text(source_path))
    except Exception as error:  # noqa: BLE001
        log(f"read {source_path}: {error}")
        cues = []
    if cues and not keep_sdh():
        before = len(cues)
        for c in cues:
            c["text"] = clean_sdh(c["text"])
        cues = [c for c in cues if c["text"]]
        log(f"hearing-impaired notes removed: {before - len(cues)} lines were only sounds")
    if not cues:
        notify(t("This subtitle cannot be read (not SRT)", "Deze ondertitel is niet te lezen (geen SRT)"), icon=xbmcgui.NOTIFICATION_WARNING)
        return
    os.makedirs(DIR, exist_ok=True)
    player = xbmc.Player()
    now = player.getTime() if player.isPlayingVideo() else 0
    first = next((i for i, c in enumerate(cues) if c["end"] >= now - 5), 0)
    order = list(range(first, len(cues))) + list(range(0, first))      # from where you are, then the beginning
    chunks = [order[i:i + CHUNK] for i in range(0, len(order), CHUNK)]
    final = os.path.join(DIR, f"{media_key()}.{code}.v2.srt")
    notify(t("Translating… the subtitle appears in a moment", "Vertalen… de ondertitel verschijnt zo"), 5000)
    log(f"{len(cues)} lines, starting at line {first} ({int(now)} s), {len(chunks)} parts")
    monitor = xbmc.Monitor()
    state = {"shown": False, "since": 0, "fatal": None, "limit": False}

    def context(chunk):
        first_index = chunk[0]
        return [cues[i]["text"] for i in range(max(0, first_index - 3), first_index)]

    def run(chunk):
        if state["fatal"] or monitor.abortRequested() or not player.isPlayingVideo():
            return False
        try:
            out = translate_part([cues[i]["text"] for i in chunk], target, context(chunk))
        except urllib.error.HTTPError as error:
            log(f"{name} HTTP {error.code}: {error.read()[:200]!r}")
            if error.code in FATAL:
                state["fatal"] = error.code
            elif error.code == 429:
                state["limit"] = True
            return False
        except Exception as error:  # noqa: BLE001
            log(f"part at line {chunk[0]}: {error}")
            return False
        for i, text in zip(chunk, out):
            cues[i]["tr"] = text
        return True

    def refresh(force=False):
        """The partly translated subtitle on screen: at the start, and again every few parts, so you do not run into
        English lines while the rest is still on its way."""
        if not player.isPlayingVideo() or not (force or state["since"] >= REFRESH):
            return
        part = os.path.join(DIR, f"{media_key()}.{code}.part{int(time.time() * 1000)}.srt")
        write_srt(part, cues)
        show(part, code)
        state["since"] = 0
        if not state["shown"]:
            state["shown"] = True
            notify(t("AI subtitle on - the rest follows in the background", "AI-ondertitel staat aan - de rest volgt op de achtergrond"))

    # the part where you are first, alone, so the subtitle comes as quickly as possible; then the rest two at a time
    if chunks and run(chunks[0]):
        refresh(force=True)
    rest = chunks[1:]
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for ok in pool.map(run, rest):
            if ok:
                state["since"] += 1
                refresh()
    missing = [c for c in chunks if not all(cues[i].get("tr") is not None for i in c)]
    if missing and not state["fatal"]:
        log(f"{len(missing)} part(s) failed, one more try")
        for chunk in missing:
            run(chunk)
    if state["fatal"]:
        reason = {400: t("the request or model is not accepted", "het verzoek of model wordt niet geaccepteerd"),
                  401: t("the key is not valid", "de sleutel klopt niet"), 402: t("no credit left", "geen tegoed meer"),
                  403: t("the key is not allowed", "de sleutel mag dit niet"),
                  404: t("model not found", "model niet gevonden")}.get(state["fatal"], f"HTTP {state['fatal']}")
        notify(f"{name}: {reason}", 6000, xbmcgui.NOTIFICATION_WARNING)
    if state["limit"] and not state["fatal"] and any(cues[i].get("tr") is None for c in chunks for i in c):
        notify(f"{name}: " + t("limit reached, the rest stays English - try again later",
                               "limiet bereikt, de rest blijft Engels - probeer het later"), 6000, xbmcgui.NOTIFICATION_WARNING)
    shown = state["shown"]
    done = sum(1 for c in cues if c.get("tr") is not None)
    if done == len(cues):
        write_srt(final, cues)
        for name in os.listdir(DIR):
            if ".part" in name:
                try:
                    os.remove(os.path.join(DIR, name))
                except OSError:
                    pass
        if player.isPlayingVideo():
            show(final, code)
        log(f"finished, kept as {final}")
    elif done and player.isPlayingVideo() and not shown:
        part = os.path.join(DIR, f"{media_key()}.{code}.part{int(time.time())}.srt")
        write_srt(part, cues)
        show(part, code)
