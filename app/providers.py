"""Thin provider layer: text models and voices, one function each way.

Every component (reader, planner, narrator, board, quiz, tutor, judge, voice)
is configured independently on the Models page.
"""
import base64
import io
import os
import time

import anthropic
import httpx

from . import db, mock

TEXT_PROVIDERS = ["anthropic", "openai", "google", "openrouter", "demo"]
VOICE_PROVIDERS = ["elevenlabs", "openai", "google", "demo"]
COMPONENTS = {
    "reader": "Paper reader",
    "planner": "Lesson planner",
    "narrator": "Narration writer",
    "board": "Board artist",
    "voice": "Narrator voice",
    "quiz": "Quiz writer",
    "tutor": "Tutor",
    "judge": "Eval judge",
}
ENV = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "google": "GOOGLE_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "elevenlabs": "ELEVENLABS_API_KEY",
}
OPENAI_VOICES = ["alloy", "echo", "fable", "onyx", "nova", "shimmer"]
TIMEOUT = httpx.Timeout(600, connect=20)


class ProviderError(Exception):
    pass


async def key_for(provider):
    if provider == "demo":
        return "demo"
    k = os.environ.get(ENV[provider]) or (await db.get_setting("keys", {})).get(provider)
    if not k:
        raise ProviderError(f"No API key saved for {provider}. Add one on the Models page.")
    return k


async def component(name):
    cfg = (await db.get_setting("components", {})).get(name) or {}
    if not cfg.get("provider"):
        raise ProviderError(f"No provider chosen for '{COMPONENTS[name]}'. Set it on the Models page.")
    if cfg["provider"] != "demo" and not cfg.get("model"):
        raise ProviderError(f"No model chosen for '{COMPONENTS[name]}'. Set it on the Models page.")
    return cfg


def _check(r, provider):
    if r.status_code >= 400:
        raise ProviderError(f"{provider} returned {r.status_code}: {r.text[:400]}")
    return r


async def _post(provider, url, headers, body, tries=3):
    last = None
    async with httpx.AsyncClient(timeout=TIMEOUT) as c:
        for attempt in range(tries):
            try:
                r = await c.post(url, headers=headers, json=body)
            except httpx.HTTPError as e:
                last = ProviderError(f"{provider} network error: {e!r}")
            else:
                if r.status_code not in (408, 409, 429) and r.status_code < 500:
                    return _check(r, provider)
                last = ProviderError(f"{provider} returned {r.status_code}: {r.text[:400]}")
            import asyncio
            await asyncio.sleep(2 ** attempt * 2)
    raise last


async def complete(comp, task, system, prompt, ctx=None):
    """Run one text-model call for a component. Returns (text, usage)."""
    cfg = await component(comp)
    provider, model = cfg["provider"], cfg.get("model", "")
    key = await key_for(provider)
    t0 = time.monotonic()
    usage = {"component": comp, "task": task, "provider": provider, "model": model}
    if provider == "demo":
        text, tin, tout = mock.text(task, ctx or {}), 0, 0
    elif provider == "anthropic":
        client = anthropic.AsyncAnthropic(api_key=key, max_retries=3)
        try:
            async with client.messages.stream(
                model=model, max_tokens=32000, system=system,
                messages=[{"role": "user", "content": prompt}],
            ) as stream:
                msg = await stream.get_final_message()
        except anthropic.APIError as e:
            raise ProviderError(f"anthropic: {e}") from e
        if msg.stop_reason == "refusal":
            raise ProviderError("anthropic declined this request (stop_reason: refusal).")
        if msg.stop_reason == "max_tokens":
            raise ProviderError("anthropic stopped at the output limit before finishing.")
        text = "".join(b.text for b in msg.content if b.type == "text")
        tin, tout = msg.usage.input_tokens, msg.usage.output_tokens
    elif provider in ("openai", "openrouter"):
        url = ("https://api.openai.com/v1" if provider == "openai" else "https://openrouter.ai/api/v1") + "/chat/completions"
        body = {"model": model, "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]}
        if provider == "openrouter":
            body["usage"] = {"include": True}
        j = (await _post(provider, url, {"Authorization": f"Bearer {key}"}, body)).json()
        if not j.get("choices"):
            raise ProviderError(f"{provider} returned no choices: {str(j)[:300]}")
        text = j["choices"][0]["message"].get("content") or ""
        u = j.get("usage") or {}
        tin, tout = u.get("prompt_tokens", 0), u.get("completion_tokens", 0)
        if u.get("cost") is not None:
            usage["cost_usd"] = u["cost"]
    elif provider == "google":
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model.removeprefix('models/')}:generateContent"
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json"},
        }
        j = (await _post(provider, url, {"x-goog-api-key": key}, body)).json()
        cands = j.get("candidates") or []
        if not cands or "content" not in cands[0]:
            raise ProviderError(f"google returned no content: {str(j)[:300]}")
        text = "".join(p.get("text", "") for p in cands[0]["content"].get("parts", []))
        u = j.get("usageMetadata") or {}
        tin, tout = u.get("promptTokenCount", 0), u.get("candidatesTokenCount", 0)
    else:
        raise ProviderError(f"Unknown text provider '{provider}'")
    usage.update(in_tokens=tin, out_tokens=tout, seconds=round(time.monotonic() - t0, 2))
    return text, usage


# ---------------------------------------------------------------- voices

def _words_from_chars(chars, starts, ends):
    words, cur, s, e = [], "", None, None
    for ch, a, b in zip(chars, starts, ends):
        if ch.isspace():
            if cur:
                words.append({"w": cur, "s": s, "e": e})
            cur, s = "", None
        else:
            if s is None:
                s = a
            cur, e = cur + ch, b
    if cur:
        words.append({"w": cur, "s": s, "e": e})
    return words


def estimate_words(text, duration):
    """Spread a known duration over words by length. Used when a voice returns no timings."""
    ws = text.split()
    total = sum(len(w) + 1 for w in ws) or 1
    out, t = [], 0.0
    for w in ws:
        d = duration * (len(w) + 1) / total
        out.append({"w": w, "s": round(t, 3), "e": round(t + d, 3)})
        t += d
    return out


def _mp3_seconds(data):
    from mutagen.mp3 import MP3
    return MP3(io.BytesIO(data)).info.length


def _chunks(sentences, limit):
    out, cur = [], ""
    for s in sentences:
        if cur and len(cur) + len(s) + 1 > limit:
            out.append(cur)
            cur = ""
        cur = (cur + " " + s).strip()
    if cur:
        out.append(cur)
    return out


async def _speak_chunk(provider, voice, key, text):
    """Returns (audio bytes, mime, duration, words or None)."""
    if provider == "demo":
        return mock.speech(text)
    if provider == "elevenlabs":
        j = (await _post(provider, f"https://api.elevenlabs.io/v1/text-to-speech/{voice}/with-timestamps",
                         {"xi-api-key": key}, {"text": text, "model_id": "eleven_multilingual_v2"})).json()
        audio = base64.b64decode(j["audio_base64"])
        al = j.get("alignment")
        words = _words_from_chars(al["characters"], al["character_start_times_seconds"],
                                  al["character_end_times_seconds"]) if al else None
        if words is not None and len(words) != len(text.split()):
            words = None
        return audio, "audio/mpeg", _mp3_seconds(audio), words
    if provider == "openai":
        r = await _post(provider, "https://api.openai.com/v1/audio/speech", {"Authorization": f"Bearer {key}"},
                        {"model": "tts-1", "voice": voice, "input": text, "response_format": "mp3"})
        return r.content, "audio/mpeg", _mp3_seconds(r.content), None
    if provider == "google":
        from html import escape
        ws = text.split()
        ssml = "<speak>" + " ".join(f'<mark name="{i}"/>{escape(w)}' for i, w in enumerate(ws)) + "</speak>"
        j = (await _post(provider, "https://texttospeech.googleapis.com/v1beta1/text:synthesize", {"x-goog-api-key": key}, {
            "input": {"ssml": ssml}, "voice": {"languageCode": "-".join(voice.split("-")[:2]), "name": voice},
            "audioConfig": {"audioEncoding": "MP3"}, "enableTimePointing": ["SSML_MARK"],
        })).json()
        audio = base64.b64decode(j["audioContent"])
        dur = _mp3_seconds(audio)
        tp = {int(p["markName"]): p["timeSeconds"] for p in j.get("timepoints", [])}
        words = None
        if len(tp) == len(ws):
            words = [{"w": w, "s": tp[i], "e": tp.get(i + 1, dur)} for i, w in enumerate(ws)]
        return audio, "audio/mpeg", dur, words
    raise ProviderError(f"Unknown voice provider '{provider}'")


async def speak(sentences):
    """Narrate a list of sentences. Returns (bytes, mime, duration, words, estimated, usage)."""
    cfg = await component("voice")
    provider, voice = cfg["provider"], cfg.get("model", "")
    key = await key_for(provider)
    limit = {"elevenlabs": 2500, "openai": 3500, "google": 600}.get(provider, 100000)
    t0 = time.monotonic()
    parts, words, offset, estimated, mime = [], [], 0.0, False, "audio/mpeg"
    for chunk in _chunks(sentences, limit):
        audio, mime, dur, ws = await _speak_chunk(provider, voice, key, chunk)
        if ws is None:
            ws, estimated = estimate_words(chunk, dur), True
        words += [{"w": w["w"], "s": round(w["s"] + offset, 3), "e": round(w["e"] + offset, 3)} for w in ws]
        parts.append(audio)
        offset += dur
    data = mock.join_wav(parts) if mime == "audio/wav" else b"".join(parts)
    usage = {"component": "voice", "task": "speak", "provider": provider, "model": voice,
             "chars": sum(len(s) for s in sentences), "seconds": round(time.monotonic() - t0, 2)}
    return data, mime, round(offset, 3), words, estimated, usage


# ---------------------------------------------------------------- model lists

async def list_models(provider, kind):
    """Model ids (text) or voice ids (voice) the saved key can use."""
    if provider == "demo":
        return [{"id": "demo", "name": "Built-in demo (no key, placeholder content)"}]
    key = await key_for(provider)
    async with httpx.AsyncClient(timeout=30) as c:
        if kind == "voice":
            if provider == "openai":
                return [{"id": v, "name": v} for v in OPENAI_VOICES]
            if provider == "elevenlabs":
                j = _check(await c.get("https://api.elevenlabs.io/v1/voices", headers={"xi-api-key": key}), provider).json()
                return [{"id": v["voice_id"], "name": v.get("name", v["voice_id"])} for v in j.get("voices", [])]
            j = _check(await c.get("https://texttospeech.googleapis.com/v1/voices?languageCode=en-US",
                                   headers={"x-goog-api-key": key}), provider).json()
            return [{"id": v["name"], "name": v["name"]} for v in j.get("voices", [])]
        if provider == "anthropic":
            client = anthropic.AsyncAnthropic(api_key=key)
            return [{"id": m.id, "name": m.display_name} async for m in client.models.list()]
        if provider == "google":
            j = _check(await c.get("https://generativelanguage.googleapis.com/v1beta/models?pageSize=200",
                                   headers={"x-goog-api-key": key}), provider).json()
            return [{"id": m["name"].removeprefix("models/"), "name": m.get("displayName", m["name"])}
                    for m in j.get("models", []) if "generateContent" in m.get("supportedGenerationMethods", [])]
        url = "https://api.openai.com/v1/models" if provider == "openai" else "https://openrouter.ai/api/v1/models"
        j = _check(await c.get(url, headers={"Authorization": f"Bearer {key}"}), provider).json()
        return sorted(({"id": m["id"], "name": m.get("name", m["id"])} for m in j.get("data", [])), key=lambda m: m["id"])
