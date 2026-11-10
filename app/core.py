"""Shared agent base: one model-backed role that returns parsed JSON."""
import json
import re

from . import db, providers

TEACH_ITEMS = {
    "objectives_covered": "Every objective is taught in the narration.",
    "ordering": "Every concept is introduced before it is used.",
    "level": "The level matches the stated audience.",
    "board_adds": "The board adds structure, diagrams or equations instead of repeating the script as text.",
    "quiz_answerable": "Every quiz question can be answered from the lesson alone.",
}
JSON_RULE = "Reply with one JSON object and nothing else: no prose, no code fences."


def parse_json(text):
    text = text.strip()
    a, b = text.find("{"), text.rfind("}")
    if a < 0 or b <= a:
        raise ValueError("no JSON object in reply")
    return json.loads(text[a:b + 1])


def norm(s):
    return re.sub(r"[\s\-­]+", " ", s).strip().lower()


class Agent:
    """One model-backed role. `ask` returns parsed JSON, retrying once on unparseable output."""
    component = ""
    role = ""

    def __init__(self, lesson_id):
        self.lesson_id = lesson_id

    async def ask(self, task, prompt, ctx=None, component=None):
        system = f"{self.role}\n\n{JSON_RULE}"
        err = None
        for _ in range(2):
            p = prompt if err is None else f"{prompt}\n\nYour previous reply was not valid JSON ({err}). {JSON_RULE}"
            text, usage = await providers.complete(component or self.component, task, system, p, ctx)
            await db.q("UPDATE lessons SET usage = usage || %s::jsonb WHERE id=%s", json.dumps([usage]), self.lesson_id)
            try:
                return parse_json(text)
            except ValueError as e:
                err = str(e)
        raise providers.ProviderError(f"{self.component} did not return valid JSON for '{task}': {err}")


def feedback_block(problems):
    if not problems:
        return ""
    return ("\n\nYour previous attempt was rejected. Fix every one of these problems, keep what was fine:\n- "
            + "\n- ".join(problems[:25]))
