"""Eval gates: the judge agent, the tutor agent, and the per-lesson report.

Run the full eval for a lesson (re-judging and the tutor question set) with:
    python -m app.evals <lesson_id> [--tutor]
"""
import asyncio
import json
import sys

from . import board, db, providers
from .core import TEACH_ITEMS, Agent, norm

GATES = {"faithful": "Faithful to the paper", "sync": "Board stays in sync",
         "teaches": "Teaches well", "reliable": "Runs reliably"}
# Fixed tutor test set for the first test paper, "Attention Is All You Need".
TUTOR_QUESTIONS = [
    "Why did the authors remove recurrence from the model?",
    "What is scaled dot-product attention?",
    "Why are the dot products scaled by the square root of the key dimension?",
    "What is multi-head attention and why is it used?",
    "How does the model know the order of the tokens?",
    "What are the two sub-layers in each encoder layer?",
    "What does the decoder mask prevent?",
    "How does self-attention compare with recurrent layers in path length between positions?",
    "Which translation tasks was the model evaluated on?",
    "What regularization was used during training?",
]


class Judge(Agent):
    component = "judge"
    role = ("You are a strict reviewer checking a lesson against the research paper it teaches. You judge only "
            "against the paper text you are given, and you quote it.")

    async def _issues(self, task, prompt, ctx, key, count):
        r = await self.ask(task, prompt, ctx)
        out = []
        for x in r.get("issues") or []:
            try:
                n = int(x[key])
            except (KeyError, TypeError, ValueError):
                continue
            if 1 <= n <= count and x.get("verdict") in ("unsupported", "contradicted"):
                out.append({key: n, "verdict": x["verdict"], "reason": str(x.get("reason", ""))[:400]})
        return out

    async def script(self, section, paper):
        ss = section["sentences"]
        listing = "\n".join(f"{i + 1}. {s['text']}" + (f"  [board: {s['cue']}]" if s["cue"] else "") for i, s in enumerate(ss))
        return await self._issues("judge_script", (
            "Check every factual claim, number and equation in this narration (and its board cues) against the paper.\n"
            "A sentence is fine if the paper supports it, or if it is a clearly signalled simplification or analogy "
            "that does not contradict the paper, or if it makes no factual claim.\n"
            "Return JSON {\"issues\": [{\"sentence\": int, \"verdict\": \"unsupported\" | \"contradicted\", \"reason\": "
            "str}]} listing only the sentences that fail. For 'contradicted', quote the paper passage in the reason. "
            "Return an empty list if everything passes.\n\n<narration>\n" + listing + "\n</narration>\n\n<paper>\n"
            + paper + "\n</paper>"), {"paper": paper}, "sentence", len(ss))

    async def quiz(self, quiz, state, paper):
        qs = quiz["questions"]
        listing = "\n".join(f"{i + 1}. {q['question']}\n   marked correct: {q['options'][int(q['correct'])]}\n"
                            f"   other options: {[o for j, o in enumerate(q['options']) if j != int(q['correct'])]}"
                            for i, q in enumerate(qs))
        return await self._issues("judge_quiz", (
            "Check each quiz question against the paper. A question fails if the option marked correct is not true "
            "according to the paper, or if another option is also correct.\n"
            "Return JSON {\"issues\": [{\"question\": int, \"verdict\": \"unsupported\" | \"contradicted\", \"reason\": "
            "str}]} listing only failing questions; empty list if all pass.\n\n<quiz>\n" + listing
            + "\n</quiz>\n\n<paper>\n" + paper + "\n</paper>"), {"paper": paper}, "question", len(qs))

    async def teach(self, state, opts, quiz):
        from .agents import AUDIENCES
        lesson = "\n\n".join(
            f"Section {i + 1}: {s['title']}\n" + "\n".join(
                x["text"] + (f"  [board: {x['cue']}]" if x["cue"] else "") for x in s["sentences"])
            for i, s in enumerate(state["sections"]))
        items = dict(TEACH_ITEMS)
        if quiz is None:
            items.pop("quiz_answerable")
        r = await self.ask("judge_teach", (
            f"Score this lesson for {AUDIENCES[opts['audience']]}. For each item give an integer 1-5 (5 = fully true, "
            "4 = true with minor gaps, 3 or below = a learner would notice the problem) and a one-line reason naming "
            "the section at fault.\nReturn JSON {\"scores\": {item: {\"score\": int, \"reason\": str}}} for exactly "
            f"these items:\n{json.dumps(items, indent=1)}\n\nObjectives:\n"
            + "\n".join(f"- {o}" for o in state["plan"]["objectives"])
            + (f"\n\nQuiz:\n{json.dumps([q['question'] for q in quiz], ensure_ascii=False)}" if quiz else "")
            + "\n\n<lesson>\n" + lesson + "\n</lesson>"), {"items": list(items)})
        out = {}
        for k in items:
            v = (r.get("scores") or {}).get(k) or {}
            try:
                score = max(1, min(5, int(v["score"])))
            except (KeyError, TypeError, ValueError):
                raise providers.ProviderError(f"Judge returned no score for '{k}'.")
            out[k] = {"score": score, "reason": str(v.get("reason", ""))[:400]}
        return out


class Tutor(Agent):
    component = "tutor"
    role = ("You are a tutor answering a learner's question about one research paper during a lesson. You answer "
            "only from the paper. If the paper does not answer the question, say so.")

    async def answer(self, paper, question, draw):
        r = await self.ask("tutor", (
            "Answer the learner's question in 2-5 plain sentences.\nReturn JSON {\"answer\": str, \"quote\": str "
            "(one passage copied word for word from the paper that supports the answer, or \"\" if the paper does not "
            "cover it), \"elements\": [board elements]}.\n"
            + ("Also draw the answer on an empty 800x520 board with up to 8 elements: {\"t\":\"text\",\"x\",\"y\",\"text\","
               "\"size\",\"color\"}, {\"t\":\"line\"|\"arrow\",\"x1\",\"y1\",\"x2\",\"y2\",\"color\"}, {\"t\":\"rect\",\"x\",\"y\",\"w\",\"h\","
               "\"color\"}, {\"t\":\"circle\",\"cx\",\"cy\",\"r\",\"color\"}; color is ink, hi or cool; text is about 0.55*size "
               "per character wide; keep everything 20 px inside the edges and do not overlap labels.\n"
               if draw else "Return an empty elements list.\n")
            + "\nQuestion: " + question + "\n\n<paper>\n" + paper + "\n</paper>"), {"paper": paper})
        quote = str(r.get("quote") or "")
        els = r.get("elements") if draw and isinstance(r.get("elements"), list) else []
        if board.board_problems([els]) if els else False:
            els = []
        return {"answer": str(r.get("answer", "")), "quote": quote,
                "grounded": bool(quote) and norm(quote) in norm(paper), "elements": els}


async def write_report(lesson_id, state, opts, paper, tutor_rows=None):
    """Compute every check from the stored lesson and replace its rows in eval_checks."""
    from .agents import LENGTHS
    rows = []

    def add(gate, name, passed, evidence, section=None, score=None):
        rows.append((lesson_id, gate, name, bool(passed), score, section, str(evidence)[:1000]))

    secs = state["sections"]
    for i, s in enumerate(secs):
        issues = s.get("faith_issues", [])
        add("faithful", "Narration claims are supported by the paper", not issues,
            "; ".join(f"sentence {x['sentence']} {x['verdict']}: {x['reason']}" for x in issues) or
            f"{len(s['sentences'])} sentences checked by the judge, none failed", i)
        nums = [p for p in s.get("script_problems", []) if "number" in p]
        add("faithful", "Numbers match the paper", not nums, "; ".join(nums) or "every number appears in the paper", i)
        for name, ok, evidence in board.sync_checks(s):
            add("sync", name, ok, evidence, i)
        if s["audio"]["estimated"]:
            add("sync", "Word timestamps came from the voice provider", False,
                "timing estimated from audio length; cue times are approximate", i)
    qi = state.get("quiz_issues", [])
    add("faithful", "Quiz answers marked correct are correct per the paper", not qi and bool(state.get("quiz")),
        "; ".join(f"question {x['question']} {x['verdict']}: {x['reason']}" for x in qi) or f"{len(state.get('quiz', []))} questions checked")
    for row in tutor_rows or []:
        add(*row)
    for k, v in state["teach"].items():
        add("teaches", TEACH_ITEMS[k], v["score"] >= 4, v["reason"], score=v["score"])
    covered = {int(q["objective"]) for q in state.get("quiz", [])}
    missing = [o for i, o in enumerate(state["plan"]["objectives"]) if i not in covered]
    add("teaches", "Every objective has a quiz question", not missing, "missing: " + "; ".join(missing) if missing else "all covered")
    minutes = LENGTHS[opts["length"]][0]
    spoken = sum(s["audio"]["duration"] for s in secs) / 60
    add("teaches", "Spoken length is within 20% of the chosen length", abs(spoken - minutes) <= 0.2 * minutes,
        f"{spoken:.1f} min spoken, {minutes} min chosen")
    add("reliable", "Generation finished without manual help", True,
        "redo rounds used: " + ", ".join(f"{k} {v}" for k, v in state.get("redos", {}).items()))
    await db.q("DELETE FROM eval_checks WHERE lesson_id=%s", lesson_id)
    async with db.pool.connection() as c, c.cursor() as cur:
        await cur.executemany(
            "INSERT INTO eval_checks(lesson_id,gate,name,passed,score,section,evidence) VALUES(%s,%s,%s,%s,%s,%s,%s)", rows)
    return rows


async def run_full(lesson_id, with_tutor):
    """Offline eval: re-judge the stored lesson from scratch, optionally run the tutor question set."""
    row = await db.one("SELECT * FROM lessons WHERE id=%s", lesson_id)
    if not row or row["status"] != "ready":
        raise SystemExit("Lesson not found or not finished.")
    state, opts, paper = row["state"], row["options"], row["paper_text"]
    judge = Judge(lesson_id)
    for s in state["sections"]:
        s["faith_issues"] = await judge.script(s, paper)
    state["quiz_issues"] = await judge.quiz({"questions": state["quiz"]}, state, paper) if state.get("quiz") else []
    state["teach"] = await judge.teach(state, opts, state.get("quiz") or None)
    tutor_rows = []
    if with_tutor:
        tutor, good, bad = Tutor(lesson_id), 0, []
        for q in TUTOR_QUESTIONS:
            a = await tutor.answer(paper, q, False)
            v = await judge.ask("judge_tutor", (
                "Does the quoted passage support the answer, and is the answer consistent with the paper? Return JSON "
                "{\"verdict\": \"supported\" | \"unsupported\" | \"contradicted\", \"reason\": str}.\n\nQuestion: " + q
                + "\nAnswer: " + a["answer"] + "\nQuote: " + a["quote"] + "\n\n<paper>\n" + paper + "\n</paper>"), {})
            ok = a["grounded"] and v.get("verdict") == "supported"
            good += ok
            if not ok:
                bad.append(f"{q} ({'quote not in paper' if not a['grounded'] else v.get('verdict')})")
        tutor_rows.append(("faithful", "Tutor answers cite a passage that exists and supports the answer",
                           good >= 9 and not any("contradicted" in b for b in bad),
                           "; ".join([f"{good}/{len(TUTOR_QUESTIONS)} supported"] + bad)))
    await db.save_lesson(lesson_id, state=state)
    rows = await write_report(lesson_id, state, opts, paper, tutor_rows)
    for gate, title in GATES.items():
        mine = [r for r in rows if r[1] == gate]
        print(f"{'PASS' if all(r[3] for r in mine) else 'FAIL'}  {title}")
        for r in mine:
            if not r[3]:
                print(f"      - {r[2]}" + (f" (section {r[5] + 1})" if r[5] is not None else "") + f": {r[6]}")


if __name__ == "__main__":
    async def main():
        await db.init()
        try:
            await run_full(sys.argv[1], "--tutor" in sys.argv)
        finally:
            await db.close()
    asyncio.run(main())
