"""Board geometry and timeline: pure functions, no I/O.

A board is 800x520. Each cue owns a list of elements; a section's board is the
cues whose start time has passed, so seeking and playing always agree.
"""
import re

W, H = 800, 520
COLORS = {"ink", "hi", "cool"}
TYPES = {"text", "line", "arrow", "rect", "circle", "path"}
SYNC_MS, SYNC_HARD_MS, MAX_GAP_S = 300, 1000, 20.0


def _num(el, *keys):
    return [float(el[k]) for k in keys]


def bbox(el):
    """(x0, y0, x1, y1) or raises ValueError/KeyError/TypeError on a malformed element."""
    t = el.get("t")
    if t not in TYPES:
        raise ValueError(f"unknown element type {t!r}")
    if el.get("color", "ink") not in COLORS:
        raise ValueError(f"unknown color {el.get('color')!r}")
    if t == "text":
        x, y = _num(el, "x", "y")
        size = float(el.get("size", 24))
        text = str(el["text"])
        if not text.strip():
            raise ValueError("empty text")
        return (x, y - size, x + 0.55 * size * len(text), y + 0.25 * size)
    if t in ("line", "arrow"):
        x1, y1, x2, y2 = _num(el, "x1", "y1", "x2", "y2")
        return (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))
    if t == "rect":
        x, y, w, h = _num(el, "x", "y", "w", "h")
        if w <= 0 or h <= 0:
            raise ValueError("rect needs positive w and h")
        return (x, y, x + w, y + h)
    if t == "circle":
        cx, cy, r = _num(el, "cx", "cy", "r")
        if r <= 0:
            raise ValueError("circle needs positive r")
        return (cx - r, cy - r, cx + r, cy + r)
    d = str(el["d"])
    if re.search(r"[^MLQCZ0-9eE.,\s-]", d) or not d.strip().startswith("M"):
        raise ValueError("path may only use absolute M, L, Q, C, Z commands")
    nums = [float(n) for n in re.findall(r"-?\d+\.?\d*(?:[eE]-?\d+)?", d)]
    if len(nums) < 4 or len(nums) % 2:
        raise ValueError("path needs an even number of coordinates")
    xs, ys = nums[0::2], nums[1::2]
    return (min(xs), min(ys), max(xs), max(ys))


def _overlap(a, b, pad=2):
    return a[0] < b[2] - pad and b[0] < a[2] - pad and a[1] < b[3] - pad and b[1] < a[3] - pad


def board_problems(cues):
    """cues: list of element lists, one per cue. Returns human-readable problems (empty = valid)."""
    problems, labels = [], []
    for ci, els in enumerate(cues):
        if not isinstance(els, list) or not els:
            problems.append(f"cue {ci + 1} has nothing to draw")
            continue
        for ei, el in enumerate(els):
            where = f"cue {ci + 1} element {ei + 1}"
            try:
                box = bbox(el)
            except (ValueError, KeyError, TypeError) as e:
                problems.append(f"{where}: {e}")
                continue
            if box[0] < 0 or box[1] < 0 or box[2] > W or box[3] > H:
                problems.append(f"{where} ({el.get('t')}) goes outside the {W}x{H} board: "
                                f"spans x {box[0]:.0f}-{box[2]:.0f}, y {box[1]:.0f}-{box[3]:.0f}")
            if el.get("t") == "text":
                for other_where, other_text, other in labels:
                    if _overlap(box, other):
                        problems.append(f"{where} label '{el['text']}' overlaps {other_where} label '{other_text}'")
                labels.append((where, el["text"], box))
    return problems


def build_timeline(sentences, words, duration, cue_elements):
    """Pin every cue to the first word of its sentence and give it a draw duration."""
    cues, wi = [], 0
    for si, s in enumerate(sentences):
        n = len(s["text"].split())
        if s.get("cue"):
            cues.append({"sentence": si, "word": wi, "t": words[wi]["s"] if wi < len(words) else duration})
        wi += n
    for i, c in enumerate(cues):
        nxt = cues[i + 1]["t"] if i + 1 < len(cues) else duration
        els = cue_elements[i] if i < len(cue_elements) else []
        c["elements"] = els
        c["dur"] = round(max(0.2, min(0.9 * (nxt - c["t"]), 0.7 * max(1, len(els)))), 3)
    return cues


def sync_checks(section):
    """Deterministic gate-2 checks for one finished section. Returns [(name, passed, evidence)]."""
    words, cues, dur = section["audio"]["words"], section["cues"], section["audio"]["duration"]
    marked = [i for i, s in enumerate(section["sentences"]) if s.get("cue")]
    out = []
    offs = [abs(c["t"] - words[c["word"]]["s"]) * 1000 for c in cues if c["word"] < len(words)]
    within = sum(o <= SYNC_MS for o in offs)
    ok = bool(offs) and within >= 0.95 * len(offs) and max(offs) <= SYNC_HARD_MS
    out.append(("Cues start within 300 ms of their trigger word", ok,
                f"{within}/{len(offs)} within {SYNC_MS} ms, worst {max(offs, default=0):.0f} ms"))
    empty = [i + 1 for i, c in enumerate(cues) if not c["elements"]]
    ok = len(cues) == len(marked) and not empty and [c["sentence"] for c in cues] == marked
    out.append(("Every cue mark has strokes and every stroke group has a cue", ok,
                f"{len(marked)} cue marks, {len(cues)} cues, empty cues: {empty or 'none'}"))
    times = [0.0] + [c["t"] for c in cues] + [dur]
    gap = max(b - a for a, b in zip(times, times[1:]))
    out.append(("No more than 20 s of narration with nothing new on the board", gap <= MAX_GAP_S,
                f"longest gap {gap:.1f} s"))
    late = [i + 1 for i, c in enumerate(cues)
            if c["t"] + c["dur"] > (cues[i + 1]["t"] if i + 1 < len(cues) else dur) + 1e-6]
    out.append(("Each cue finishes drawing before the next starts", not late, f"late cues: {late or 'none'}"))
    probs = board_problems([c["elements"] for c in cues])
    out.append(("Strokes stay inside the board and labels do not overlap", not probs,
                "; ".join(probs[:4]) or "no problems"))
    order = all(a["t"] <= b["t"] for a, b in zip(cues, cues[1:]))
    out.append(("Seeking shows the same board as playing through", order,
                "board is a pure function of time" if order else "cue times are not in order"))
    return out
