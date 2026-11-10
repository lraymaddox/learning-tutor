"""The lesson agents.

Orchestrator  owns one lesson; runs the specialists, checks their work against
              the eval gates, sends failing work back (two redo rounds each),
              and flags whatever still fails. Every finished step is saved, so
              a restart resumes instead of starting over.
LessonPlanner reads the paper, plans the lesson, writes the quiz.
Narrator      writes the spoken script with board cues, then records it.
BoardArtist   turns every cue into strokes.
"""
import asyncio
import json
import os
import re
from pathlib import Path

from . import board, db, evals, providers
from .core import TEACH_ITEMS, Agent, feedback_block, norm, parse_json  # noqa: F401

REDO_ROUNDS = 2
WORDS_PER_MIN = 150
MAX_WORDS_BETWEEN_CUES = 42  # ~17 s at narration pace, inside the 20 s gate
LENGTHS = {"short": (5, 3), "standard": (15, 5), "deep": (30, 8)}  # minutes, sections
AUDIENCES = {
    "beginner": "a curious beginner with no background in the field",
    "undergrad": "an undergraduate studying the field",
    "researcher": "a researcher from a different field",
    "expert": "an expert who only wants the paper's contributions",
}
PAPER_LIMIT = 400_000
DATA = Path(os.environ.get("DATA_DIR", "./data"))
class LessonPlanner(Agent):
    component = "planner"
    role = "You design lessons that teach one research paper to one learner. You only use what the paper says."

    async def digest(self, paper):
        d = await self.ask("digest", (
            "Read this paper and return JSON {\"title\": str, \"authors\": str, \"summary\": str (3-5 sentences), "
            "\"key_points\": [{\"point\": str, \"quote\": str}]} with the 8-15 ideas a learner must understand. "
            "Each quote is copied word for word from the paper.\n\n<paper>\n" + paper + "\n</paper>"),
            {"paper": paper}, component="reader")
        if not isinstance(d.get("title"), str) or not isinstance(d.get("key_points"), list):
            raise providers.ProviderError("Paper reader returned a digest without title or key_points.")
        return d

    async def plan(self, digest, opts, problems=()):
        minutes, n = LENGTHS[opts["length"]]
        p = await self.ask("plan", (
            f"Plan a {minutes}-minute narrated chalkboard lesson on this paper for {AUDIENCES[opts['audience']]}.\n"
            f"Return JSON {{\"objectives\": [3-5 strings, each starting with a verb], \"sections\": [exactly {n} of "
            "{\"title\": str, \"goal\": str, \"key_points\": [str]}]}. Order the sections so every concept is "
            "introduced before it is used. Every objective must be taught by at least one section.\n\n"
            "Paper digest:\n" + json.dumps(digest, ensure_ascii=False) + feedback_block(problems)), {"paper": ""})
        return p

    @staticmethod
    def plan_problems(plan, opts):
        n = LENGTHS[opts["length"]][1]
        out = []
        objs, secs = plan.get("objectives"), plan.get("sections")
        if not isinstance(objs, list) or not 3 <= len(objs) <= 5 or not all(isinstance(o, str) and o.strip() for o in objs):
            out.append("objectives must be a list of 3 to 5 non-empty strings")
        if not isinstance(secs, list) or len(secs) != n:
            out.append(f"sections must be a list of exactly {n} items")
        elif not all(isinstance(s, dict) and str(s.get("title", "")).strip() and str(s.get("goal", "")).strip() for s in secs):
            out.append("every section needs a title and a goal")
        return out

    async def quiz(self, state, paper, problems=()):
        plan = state["plan"]
        lesson = "\n\n".join(f"Section {i + 1}: {s['title']}\n" + " ".join(x["text"] for x in s["sentences"])
                             for i, s in enumerate(state["sections"]))
        return await self.ask("quiz", (
            "Write check-in quiz questions for this lesson. Return JSON {\"questions\": [{\"after_section\": int "
            "(0-based section the question follows), \"objective\": int (0-based objective it tests), \"question\": "
            "str, \"options\": [4 strings], \"correct\": int (0-3), \"explain\": str}]}. Write at least one question "
            "per objective. Every question must be answerable from the lesson narration alone, and the correct "
            "answer must be true according to the paper.\n\nObjectives:\n"
            + "\n".join(f"{i}. {o}" for i, o in enumerate(plan["objectives"]))
            + "\n\n<lesson>\n" + lesson + "\n</lesson>\n\n<paper>\n" + paper + "\n</paper>" + feedback_block(problems)),
            {"paper": paper, "objectives": plan["objectives"], "n_sections": len(state["sections"]),
             "first_sentences": [s["sentences"][0]["text"] for s in state["sections"]]}, component="quiz")

    @staticmethod
    def quiz_problems(quiz, plan, n_sections):
        qs = quiz.get("questions")
        if not isinstance(qs, list) or not qs:
            return ["questions must be a non-empty list"]
        out = []
        for i, q in enumerate(qs):
            try:
                ok = (isinstance(q["options"], list) and len(q["options"]) == 4 and 0 <= int(q["correct"]) <= 3
                      and 0 <= int(q["after_section"]) < n_sections and 0 <= int(q["objective"]) < len(plan["objectives"])
                      and str(q["question"]).strip() and str(q.get("explain", "")).strip())
            except (KeyError, TypeError, ValueError):
                ok = False
            if not ok:
                out.append(f"question {i + 1} is malformed (needs 4 options, correct 0-3, valid after_section and objective, explain)")
        if not out:
            missing = sorted(set(range(len(plan["objectives"]))) - {int(q["objective"]) for q in qs})
            out += [f"objective {m} ('{plan['objectives'][m]}') has no quiz question" for m in missing]
        return out


class Narrator(Agent):
    component = "narrator"
    role = ("You write the spoken script for a narrated chalkboard lesson on a research paper. You speak plainly, "
            "one idea per sentence, and you never state anything the paper does not support.")

    async def script(self, state, index, paper, opts, target, problems=()):
        plan = state["plan"]
        sec = plan["sections"][index]
        prev = state["sections"][index].get("sentences") if problems else None
        return await self.ask("script", (
            f"Write the narration for section {index + 1} of {len(plan['sections'])}: \"{sec['title']}\".\n"
            f"Goal: {sec['goal']}\nKey points: {json.dumps(sec.get('key_points', []), ensure_ascii=False)}\n"
            f"Audience: {AUDIENCES[opts['audience']]}.\n"
            f"Lesson outline, so you do not repeat other sections: {json.dumps([s['title'] for s in plan['sections']], ensure_ascii=False)}\n\n"
            "Return JSON {\"sentences\": [{\"text\": str, \"cue\": str or null}]}.\n"
            f"- Total length {int(target * 0.85)}-{int(target * 1.15)} words. Sentences are spoken aloud: no markdown, "
            "no symbols a voice cannot read, spell out equations in words.\n"
            "- A cue tells the board artist what to draw at the moment that sentence starts: a diagram, a label, an "
            "equation, an arrow. Describe the drawing concretely, including the exact text or equation to write. "
            "Prefer diagrams and equations over copying the sentence.\n"
            f"- The first sentence has a cue. Never go more than {MAX_WORDS_BETWEEN_CUES - 7} spoken words without a new cue. "
            "The board is cleared at the start of each section and fills up, so plan 6-14 cues that fit one board.\n"
            "- Every fact, number and equation must come from the paper. If you simplify or use an analogy, say so "
            "out loud (for example \"roughly speaking\" or \"as an analogy\").\n\n"
            + (f"Your previous script:\n{json.dumps(prev, ensure_ascii=False)}\n\n" if prev else "")
            + "<paper>\n" + paper + "\n</paper>" + feedback_block(problems)),
            {"paper": paper, "index": index, "n_sections": len(plan["sections"]), "target_words": target})

    @staticmethod
    def script_problems(script, target, paper):
        ss = script.get("sentences")
        if not isinstance(ss, list) or not ss or not all(
                isinstance(s, dict) and isinstance(s.get("text"), str) and s["text"].strip() for s in ss):
            return ["sentences must be a non-empty list of {text, cue}"]
        out = []
        for s in ss:
            s["text"] = " ".join(s["text"].split())
            s["cue"] = s["cue"].strip() if isinstance(s.get("cue"), str) and s["cue"].strip() else None
        if not ss[0]["cue"]:
            out.append("the first sentence must have a cue")
        run = 0
        for i, s in enumerate(ss):
            run = len(s["text"].split()) if s["cue"] else run + len(s["text"].split())
            if run > MAX_WORDS_BETWEEN_CUES and (i + 1 == len(ss) or ss[i + 1]["cue"]):
                out.append(f"{run} spoken words pass with nothing new on the board, ending at sentence {i + 1}; "
                           f"add a cue so no stretch exceeds {MAX_WORDS_BETWEEN_CUES} words")
        total = sum(len(s["text"].split()) for s in ss)
        if not 0.8 * target <= total <= 1.2 * target:
            out.append(f"section is {total} words; it must be between {int(target * 0.8)} and {int(target * 1.2)}")
        flat = norm(paper).replace(",", "")
        for i, s in enumerate(ss):
            for n in re.findall(r"\d[\d,]*\.?\d*", s["text"] + " " + (s["cue"] or "")):
                n = n.rstrip(".,").replace(",", "")
                if (len(n) > 1 or "." in n) and n not in flat:
                    out.append(f"sentence {i + 1} uses the number {n}, which does not appear in the paper")
        return out

    async def record(self, lesson_id, index, sentences):
        data, mime, duration, words, estimated, usage = await providers.speak([s["text"] for s in sentences])
        expected = sum(len(s["text"].split()) for s in sentences)
        if len(words) != expected:
            words, estimated = providers.estimate_words(" ".join(s["text"] for s in sentences), duration), True
        folder = DATA / "audio" / str(lesson_id)
        folder.mkdir(parents=True, exist_ok=True)
        name = f"{index}.{'wav' if mime == 'audio/wav' else 'mp3'}"
        (folder / name).write_bytes(data)
        await db.q("UPDATE lessons SET usage = usage || %s::jsonb WHERE id=%s", json.dumps([usage]), lesson_id)
        return {"file": name, "mime": mime, "duration": duration, "words": words, "estimated": estimated}


class BoardArtist(Agent):
    component = "board"
    role = ("You draw a teacher's board as simple vector elements. You place things carefully so nothing overlaps "
            "and everything stays readable.")

    async def draw(self, section, problems=(), previous=None):
        cues = [s["cue"] for s in section["sentences"] if s["cue"]]
        listing = "\n".join(f"{i + 1}. {c}" for i, c in enumerate(cues))
        return await self.ask("board", (
            f"Draw the board for the lesson section \"{section['title']}\". The board is {board.W} wide and {board.H} "
            "tall, origin top-left. It starts empty and every cue ADDS to it, so lay out the whole board first and "
            "leave room for later cues.\n\n"
            f"Return JSON {{\"cues\": [ [elements for cue 1], [elements for cue 2], ... ]}} with exactly {len(cues)} lists.\n"
            "Element types (all coordinates are numbers):\n"
            "- {\"t\":\"text\",\"x\",\"y\",\"text\",\"size\",\"color\"}: x is the left edge, y the baseline. A label is about "
            "0.55 * size * (number of characters) wide and size tall. Use size 18-34. Write equations with plain "
            "Unicode (√, ², ₖ, ·, →, Σ).\n"
            "- {\"t\":\"line\"|\"arrow\",\"x1\",\"y1\",\"x2\",\"y2\",\"color\",\"w\"}\n"
            "- {\"t\":\"rect\",\"x\",\"y\",\"w\",\"h\",\"color\"} and {\"t\":\"circle\",\"cx\",\"cy\",\"r\",\"color\"} (outlines)\n"
            "- {\"t\":\"path\",\"d\",\"color\",\"w\"}: d uses only absolute M, L, Q, C, Z commands.\n"
            "color is \"ink\" (main), \"hi\" (emphasis) or \"cool\" (secondary).\n"
            "Rules: keep every element at least 20 px inside the board edges; no two text labels may overlap; "
            "1-8 elements per cue; draw what the cue asks for and include the exact text it names.\n\n"
            "Cues in the order they are spoken:\n" + listing
            + (f"\n\nYour previous board:\n{json.dumps(previous)}" if previous else "") + feedback_block(problems)),
            {"cues": cues})

    @staticmethod
    def problems(result, n_cues):
        cues = result.get("cues")
        if not isinstance(cues, list) or len(cues) != n_cues:
            return [f"cues must be a list of exactly {n_cues} element lists"]
        return board.board_problems(cues)


class Orchestrator:
    """Owns one lesson from paper to playable result."""

    def __init__(self, lesson_id):
        self.id = lesson_id
        self.planner, self.narrator, self.artist = LessonPlanner(lesson_id), Narrator(lesson_id), BoardArtist(lesson_id)
        self.judge = evals.Judge(lesson_id)

    async def save(self, state, step=None, **extra):
        if step:
            extra["step"] = step
        await db.save_lesson(self.id, state=state, **extra)

    def flag(self, state, gate, text, section=None):
        f = {"gate": gate, "text": text, "section": section}
        if f not in state["flags"]:
            state["flags"].append(f)

    async def run(self):
        try:
            await self._run()
        except Exception as e:  # reported to the user, never swallowed
            await db.save_lesson(self.id, status="failed", error=f"{type(e).__name__}: {e}")

    async def _run(self):
        row = await db.one("SELECT * FROM lessons WHERE id=%s", self.id)
        state, opts, paper = row["state"], row["options"], row["paper_text"]
        state.setdefault("flags", [])
        state.setdefault("redos", {"planner": 0, "narrator": 0, "quiz": 0, "board": 0})
        await db.save_lesson(self.id, status="running", error=None)
        if len(paper) > PAPER_LIMIT:
            raise providers.ProviderError(
                f"The paper is {len(paper):,} characters, over the {PAPER_LIMIT:,} limit this version reads in one pass.")

        if "digest" not in state:
            await self.save(state, "Reading the paper")
            state["digest"] = await self.planner.digest(paper)
            await self.save(state, title=state["digest"]["title"][:200])

        if "plan" not in state:
            await self.save(state, "Building the lesson plan")
            problems = []
            for attempt in range(REDO_ROUNDS + 1):
                plan = await self.planner.plan(state["digest"], opts, problems)
                problems = self.planner.plan_problems(plan, opts)
                if not problems:
                    break
                state["redos"]["planner"] = attempt + 1 if attempt < REDO_ROUNDS else REDO_ROUNDS
            if problems:
                raise providers.ProviderError("Lesson planner could not produce a valid plan: " + "; ".join(problems))
            state["plan"] = plan
            await self.save(state)

        if opts.get("review") and not state.get("plan_approved"):
            await self.save(state, "Waiting for you to review the plan", status="awaiting_review")
            return
        plan = state["plan"]
        minutes = LENGTHS[opts["length"]][0]
        target = minutes * WORDS_PER_MIN // len(plan["sections"])
        if "sections" not in state:
            state["sections"] = [{"title": s["title"]} for s in plan["sections"]]

        if not state.get("script_done"):
            await self._write_scripts(state, opts, paper, target)
        if "quiz" not in state:
            await self._write_quiz(state, paper)

        await self.save(state, "Recording the narration")
        async def rec(i, sec):
            if "audio" not in sec:
                sec["audio"] = await self.narrator.record(self.id, i, sec["sentences"])
                await self.save(state)
        await asyncio.gather(*(rec(i, s) for i, s in enumerate(state["sections"])))

        await self.save(state, "Drawing the board")
        await asyncio.gather(*(self._draw(state, i) for i in range(len(state["sections"]))))

        await self.save(state, "Checking the lesson")
        for i, sec in enumerate(state["sections"]):
            a = sec["audio"]
            sec["cues"] = board.build_timeline(sec["sentences"], a["words"], a["duration"], sec["board"])
            if a["estimated"]:
                self.flag(state, "sync", "Timing estimated: the voice returned no word timestamps", i)
            for name, ok, evidence in board.sync_checks(sec):
                if not ok:
                    self.flag(state, "sync", f"{name}: {evidence}", i)
        await self.save(state)
        await evals.write_report(self.id, state, opts, paper)
        await self.save(state, "Ready", status="ready")

    async def _write_scripts(self, state, opts, paper, target):
        """Narrator redo loop: structural checks and the faithfulness judge per section, then the teaching judge."""
        secs = state["sections"]
        todo = {i: [] for i, s in enumerate(secs) if "sentences" not in s}
        for rnd in range(REDO_ROUNDS + 1):
            if todo:
                await self.save(state, "Writing the narration" if rnd == 0 else f"Revising the narration (redo {rnd})")

                async def write(i, problems):
                    script = await self.narrator.script(state, i, paper, opts, target, problems)
                    found = self.narrator.script_problems(script, target, paper)
                    if found == ["sentences must be a non-empty list of {text, cue}"]:
                        if "sentences" not in secs[i]:
                            raise providers.ProviderError(f"Narrator returned no usable script for section {i + 1}.")
                        return i, found, []
                    secs[i]["sentences"] = script["sentences"]
                    issues = await self.judge.script(secs[i], paper)
                    return i, found, issues

                results = await asyncio.gather(*(write(i, p) for i, p in todo.items()))
                todo = {}
                for i, found, issues in results:
                    secs[i]["script_problems"], secs[i]["faith_issues"] = found, issues
                    msgs = found + [f"sentence {x['sentence']} is {x['verdict']} by the paper: {x['reason']}" for x in issues]
                    if msgs:
                        todo[i] = msgs
                await self.save(state)
            if not todo:
                state["teach"] = await self.judge.teach(state, opts, quiz=None)
                low = [f"teaching quality, {TEACH_ITEMS[k]} scored {v['score']}/5: {v['reason']}"
                       for k, v in state["teach"].items() if v["score"] < 4]
                if low and rnd < REDO_ROUNDS:
                    todo = {i: list(low) for i in range(len(secs))}
                await self.save(state)
            if not todo:
                break
            if rnd < REDO_ROUNDS:
                state["redos"]["narrator"] = rnd + 1
        if "teach" not in state:
            state["teach"] = await self.judge.teach(state, opts, quiz=None)
        for i, s in enumerate(secs):
            for p in s.get("script_problems", []):
                self.flag(state, "sync" if "board" in p or "cue" in p else "faithful" if "number" in p else "teaches", p, i)
            for x in s.get("faith_issues", []):
                self.flag(state, "faithful", f"Unverified claim: \"{s['sentences'][x['sentence'] - 1]['text']}\" ({x['reason']})", i)
        for k, v in state["teach"].items():
            if v["score"] < 4:
                self.flag(state, "teaches", f"{TEACH_ITEMS[k]} Scored {v['score']}/5: {v['reason']}")
        state["script_done"] = True
        await self.save(state)

    async def _write_quiz(self, state, paper):
        await self.save(state, "Writing the quiz")
        problems, quiz, issues = [], None, []
        for rnd in range(REDO_ROUNDS + 1):
            cand = await self.planner.quiz(state, paper, problems)
            problems = self.planner.quiz_problems(cand, state["plan"], len(state["sections"]))
            if any("malformed" in p or "non-empty" in p for p in problems):
                state["redos"]["quiz"] = min(rnd + 1, REDO_ROUNDS)
                continue
            quiz = cand
            issues = await self.judge.quiz(quiz, state, paper)
            problems += [f"question {x['question']} is {x['verdict']}: {x['reason']}" for x in issues]
            if not problems:
                break
            state["redos"]["quiz"] = min(rnd + 1, REDO_ROUNDS)
        if quiz is None:
            quiz = {"questions": []}
            self.flag(state, "teaches", "The quiz writer did not produce a usable quiz")
        bad = {x["question"] - 1 for x in issues}
        for x in issues:
            self.flag(state, "faithful", f"Quiz question {x['question']} is {x['verdict']}: {x['reason']}")
        for i, q in enumerate(quiz["questions"]):
            q["flagged"] = i in bad
        for p in problems:
            if "has no quiz question" in p:
                self.flag(state, "teaches", p)
        state["quiz"], state["quiz_issues"] = quiz["questions"], issues
        await self.save(state)

    async def _draw(self, state, i):
        sec = state["sections"][i]
        if "board" in sec:
            return
        n = sum(1 for s in sec["sentences"] if s["cue"])
        problems, previous, best = [], None, None
        for rnd in range(REDO_ROUNDS + 1):
            result = await self.artist.draw(sec, problems, previous)
            problems = self.artist.problems(result, n)
            if isinstance(result.get("cues"), list) and len(result["cues"]) == n:
                previous = result["cues"]
                if best is None or len(problems) < best[0]:
                    best = (len(problems), result["cues"], problems)
            if not problems:
                break
            state["redos"]["board"] = max(state["redos"]["board"], min(rnd + 1, REDO_ROUNDS))
        if best is None:
            best = (n, [[] for _ in range(n)], ["The board artist returned no usable board"])
        cues = []
        for els in best[1]:
            keep = []
            for el in els if isinstance(els, list) else []:
                try:
                    board.bbox(el)
                    keep.append(el)
                except (ValueError, KeyError, TypeError):
                    pass
            cues.append(keep)
        sec["board"] = cues
        await self.save(state)


RUNNING = {}


def start(lesson_id):
    """Run (or resume) a lesson in the background, once."""
    key = str(lesson_id)
    if key in RUNNING and not RUNNING[key].done():
        return
    RUNNING[key] = asyncio.create_task(Orchestrator(lesson_id).run())
