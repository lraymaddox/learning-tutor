"""Built-in 'demo' provider: no keys, no network.

It exists so the whole pipeline (agents, redo loop, sync, player, eval) can be
exercised end to end. Its lesson is placeholder content cut from the paper's
own sentences; it is not a real lesson.
"""
import io
import json
import re
import wave

RATE = 8000
WPS = 2.5


def _sentences(text):
    flat = re.sub(r"\s+", " ", text)
    out = [s.strip() for s in re.split(r"(?<=[.!?])\s+(?=[A-Z])", flat)]
    return [s for s in out if 6 <= len(s.split()) <= 38 and not re.search(r"\d", s)]


def text(task, ctx):
    paper = ctx.get("paper", "")
    sents = _sentences(paper) or ["This demo lesson has no paper text to draw on."]
    if task == "digest":
        title = next((ln.strip() for ln in paper.splitlines() if len(ln.strip()) > 8), "Untitled paper")
        return json.dumps({"title": title[:120], "authors": "", "summary": " ".join(sents[:2]),
                           "key_points": [{"point": s, "quote": s} for s in sents[:5]]})
    if task == "plan":
        return json.dumps({"objectives": ["Recall how the paper opens", "Recall the paper's middle argument",
                                          "Recall how the paper closes"],
                           "sections": [{"title": f"Part {i + 1}", "goal": f"Walk through part {i + 1} of the paper",
                                         "key_points": []} for i in range(3)]})
    if task == "script":
        n, i, target = ctx["n_sections"], ctx["index"], ctx["target_words"]
        chunk = sents[len(sents) * i // n: len(sents) * (i + 1) // n] or sents
        out, total, since, k = [], 0, 99, 0
        while total < target * 0.95:
            s = chunk[k % len(chunk)]
            k += 1
            cue = None
            if since + len(s.split()) > 30:
                cue, since = "Write the key phrase: " + " ".join(s.split()[:3]), 0
            since += len(s.split())
            total += len(s.split())
            out.append({"text": s, "cue": cue})
        return json.dumps({"sentences": out})
    if task == "board":
        cues = []
        for i, desc in enumerate(ctx["cues"]):
            col, row = (i // 10) % 3, i % 10
            x, y = 30 + col * 255, 60 + row * 46
            label = desc.split(": ", 1)[-1][:18]
            cues.append([{"t": "text", "x": x, "y": y, "text": label, "size": 20, "color": "ink"},
                         {"t": "line", "x1": x, "y1": y + 8, "x2": x + 200, "y2": y + 8, "color": "hi", "w": 3}])
        return json.dumps({"cues": cues})
    if task == "quiz":
        qs = []
        for i, obj in enumerate(ctx["objectives"]):
            sec = min(i, ctx["n_sections"] - 1)
            right = ctx["first_sentences"][sec]
            wrong = [s for s in sents if s != right][:3] or ["Not stated in the paper"] * 3
            qs.append({"after_section": sec, "objective": i,
                       "question": f"Which sentence was read out in part {sec + 1}?",
                       "options": [right] + (wrong + ["Not stated in the paper"] * 3)[:3], "correct": 0,
                       "explain": "This sentence is quoted directly from the paper."})
        return json.dumps({"questions": qs})
    if task in ("judge_script", "judge_quiz"):
        return json.dumps({"issues": []})
    if task == "judge_teach":
        return json.dumps({"scores": {k: {"score": 5, "reason": "Demo judge: always passes."} for k in ctx["items"]}})
    if task == "tutor":
        return json.dumps({"answer": "Demo tutor: the closest passage in the paper is quoted below.",
                           "quote": sents[0], "elements": [{"t": "text", "x": 40, "y": 60, "text": "see quoted passage",
                                                           "size": 24, "color": "hi"}]})
    if task == "judge_tutor":
        return json.dumps({"verdict": "supported", "reason": "Demo judge."})
    raise ValueError(f"demo provider has no task {task!r}")


def speech(text_):
    """Silent audio with exact word timings at 2.5 words per second."""
    ws = text_.split()
    dur = len(ws) / WPS
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(1)
        w.setframerate(RATE)
        w.writeframes(b"\x80" * int(dur * RATE))
    words = [{"w": x, "s": i / WPS, "e": (i + 1) / WPS} for i, x in enumerate(ws)]
    return buf.getvalue(), "audio/wav", dur, words


def join_wav(parts):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as out:
        for i, p in enumerate(parts):
            with wave.open(io.BytesIO(p), "rb") as w:
                if i == 0:
                    out.setparams(w.getparams())
                out.writeframes(w.readframes(w.getnframes()))
    return buf.getvalue()
