# Paper to Lesson

Turns a research paper (PDF) into a narrated lesson with a chalkboard or
whiteboard that draws in step with the audio. See `intent.md` for what it is
and `eval.md` for what "good" means.

## Run

```bash
docker compose up --build
```

Open http://localhost:8080.

1. **Models**: paste API keys, press Save, then choose a provider and model for
   each component (model lists load from the provider once its key is saved).
   Or press **Use the demo provider for everything** to try the app with no
   keys: it makes placeholder lessons from the paper's own sentences, with
   silent audio.
2. **Lessons**: upload a PDF or paste an arXiv / PDF link, pick audience,
   length and board, and generate. If "review the plan" is ticked you edit and
   approve the plan before narration is written.
3. Open the lesson to play it. **Eval report** (left panel) shows the four
   gates and per-component usage.

Keys can also be set in a `.env` file next to `docker-compose.yml` (see
`.env.example`). Data lives in two Docker volumes: `lesson-db` (Postgres) and
`lesson-data` (audio).

## How it is built

| Path | What |
|---|---|
| `app/agents.py` | Orchestrator and the three specialists (lesson planner, narrator, board artist) |
| `app/evals.py` | Judge and tutor agents, the eval report, offline eval CLI |
| `app/board.py` | Board geometry, cue timeline and the deterministic sync checks |
| `app/providers.py` | Text and voice providers: Anthropic, OpenAI, Google, OpenRouter, ElevenLabs |
| `app/mock.py` | The no-key demo provider |
| `app/main.py` | HTTP API |
| `app/static/` | Browser player, plan review, report, model settings |

The orchestrator is code, not a model: it runs each specialist, checks the
result against the gates, sends failures back with the exact problems (two
redo rounds each), and flags whatever still fails. Each finished step is saved
to Postgres, so a restart resumes where it stopped.

## Eval

Every lesson gets a report automatically. To re-judge a finished lesson from
scratch and run the 10-question tutor set (written for "Attention Is All You
Need"):

```bash
docker compose exec app python -m app.evals <lesson-id> --tutor
```

Gate 4's multi-run checks are manual: generate the test paper three times,
restart the app container mid-run (`docker compose restart app`), and repeat
with a second provider on each component.

## Tests

```bash
pip install -r requirements.txt && python -m pytest tests -q
```
