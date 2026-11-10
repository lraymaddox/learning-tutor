"""Orchestrator behavior with a scripted model: redo on a failed gate, flag what
still fails, publish anyway, and never repeat finished steps on resume."""
import asyncio
import json

import pytest

from app import agents, core, db, evals, mock, providers

PAPER = "A Paper Title Here\n\n" + " ".join(
    f"The encoder number {w} maps the input sequence to a useful representation without recurrence." for w in
    "one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen".split())


class FakeDB:
    def __init__(self, options):
        self.row = {"id": "L", "state": {}, "options": options, "paper_text": PAPER, "usage": [], "status": "queued"}
        self.report = None

    async def one(self, sql, *a):
        return json.loads(json.dumps(self.row))

    async def q(self, sql, *a):
        if "usage ||" in sql:
            self.row["usage"] += json.loads(a[0])
        return []

    async def save_lesson(self, _id, **f):
        self.row.update(json.loads(json.dumps(f)))


@pytest.fixture
def world(monkeypatch, tmp_path):
    fake = FakeDB({"audience": "undergrad", "length": "short", "board": "chalk", "review": False})
    for name in ("one", "q", "save_lesson"):
        monkeypatch.setattr(db, name, getattr(fake, name))
    monkeypatch.setattr(agents, "DATA", tmp_path)
    calls, behavior = [], {}

    async def complete(comp, task, system, prompt, ctx=None):
        calls.append(task)
        n = calls.count(task)
        text = behavior[task](n, ctx, prompt) if task in behavior else mock.text(task, ctx or {})
        return text, {"component": comp, "task": task, "provider": "fake", "model": "fake"}

    async def speak(sentences):
        data, mime, dur, words = mock.speech(" ".join(sentences))
        return data, mime, dur, words, False, {"component": "voice", "task": "speak", "provider": "fake", "model": "fake"}

    async def write_report(*a, **k):
        fake.report = True

    monkeypatch.setattr(providers, "complete", complete)
    monkeypatch.setattr(providers, "speak", speak)
    monkeypatch.setattr(evals, "write_report", write_report)
    return fake, calls, behavior


def run(fake):
    asyncio.run(agents.Orchestrator("L").run())
    assert fake.row.get("error") is None, fake.row.get("error")
    return fake.row["state"]


def test_clean_run_is_ready_with_no_flags(world):
    fake, calls, _ = world
    state = run(fake)
    assert fake.row["status"] == "ready" and fake.report and state["flags"] == []
    assert state["redos"] == {"planner": 0, "narrator": 0, "quiz": 0, "board": 0}
    assert all(s["cues"] and s["audio"]["words"] for s in state["sections"])


def test_bad_board_is_sent_back_with_the_problem_and_then_passes(world):
    fake, calls, behavior = world
    seen = []

    def board_task(n, ctx, prompt):
        seen.append(prompt)
        good = json.loads(mock.text("board", ctx))
        if "previous attempt was rejected" not in prompt:  # every first attempt runs off the board
            good["cues"][0][0]["x"] = 790
        return json.dumps(good)

    behavior["board"] = board_task
    state = run(fake)
    assert calls.count("board") == 6 and state["redos"]["board"] == 1
    assert any("goes outside" in p and "previous attempt was rejected" in p for p in seen)
    assert not [f for f in state["flags"] if f["gate"] == "sync"]


def test_unfixable_claim_uses_two_redos_then_publishes_with_a_flag(world):
    fake, calls, behavior = world
    behavior["judge_script"] = lambda n, ctx, prompt: json.dumps(
        {"issues": [{"sentence": 1, "verdict": "unsupported", "reason": "not in the paper"}]})
    state = run(fake)
    assert fake.row["status"] == "ready"
    assert calls.count("script") == 3 * (agents.REDO_ROUNDS + 1) and state["redos"]["narrator"] == agents.REDO_ROUNDS
    flags = [f for f in state["flags"] if f["gate"] == "faithful"]
    assert len(flags) == 3 and all("Unverified claim" in f["text"] for f in flags)


def test_low_teaching_score_triggers_a_narrator_redo(world):
    fake, calls, behavior = world
    behavior["judge_teach"] = lambda n, ctx, prompt: json.dumps({"scores": {
        k: {"score": 2 if (k == "ordering" and n == 1) else 5, "reason": "section 2 uses a term before defining it"}
        for k in ctx["items"]}})
    state = run(fake)
    assert calls.count("script") == 6 and calls.count("judge_teach") == 2
    assert state["teach"]["ordering"]["score"] == 5 and state["flags"] == []


def test_resume_does_not_repeat_finished_steps(world):
    fake, calls, behavior = world

    def boom(n, ctx, prompt):
        raise providers.ProviderError("voice provider is down")

    behavior["quiz"] = boom
    asyncio.run(agents.Orchestrator("L").run())
    assert fake.row["status"] == "failed" and "voice provider is down" in fake.row["error"]
    first = list(calls)
    del behavior["quiz"]
    state = run(fake)
    again = calls[len(first):]
    assert fake.row["status"] == "ready"
    assert not {"digest", "plan", "script", "judge_script", "judge_teach"} & set(again)
    assert "quiz" in again and "board" in again and state["quiz"]


def test_review_option_pauses_before_any_script_is_written(world):
    fake, calls, _ = world
    fake.row["options"]["review"] = True
    asyncio.run(agents.Orchestrator("L").run())
    assert fake.row["status"] == "awaiting_review" and "script" not in calls
    fake.row["state"]["plan_approved"] = True
    run(fake)
    assert fake.row["status"] == "ready"
