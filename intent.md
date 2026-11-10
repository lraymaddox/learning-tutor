# Paper to Lesson — Intent

## What it is

A standalone web app that turns a research paper into a narrated lesson with a
chalkboard or whiteboard that draws in step with the audio. It lives in
`paper-to-lesson/` and does not depend on Hermes internals.

## Who it is for

One person studying a paper they did not write. No accounts, no sharing, no
lesson library beyond "my past lessons" in the first version.

## What the learner gets

1. Upload a PDF or paste an arXiv / DOI / PDF link, pick audience, length,
   board style and voice.
2. Review and edit the lesson plan before any audio is made.
3. A player where narration, board and transcript share one timeline:
   - board drawn stroke by stroke, or shown as a full board;
   - chalkboard / whiteboard toggle;
   - click a plan section or transcript sentence to seek;
   - check-in quizzes between sections;
   - ask-the-paper tutor that pauses the lesson, answers from the paper, and
     can draw the answer on the board.

## How lessons are made

An **orchestrator agent** owns each lesson and delegates to three specialists:

| Agent | Produces | Components it runs |
|---|---|---|
| Lesson planner | Objectives, sections, quiz points | paper reader, lesson planner, quiz writer |
| Narrator | Spoken script with board cue marks, audio, word timestamps | narration writer, narrator voice |
| Board artist | Vector strokes, labels and equations for every cue | board artist |

The orchestrator checks each specialist's output against the gates in
`eval.md` and sends failing work back for a redo, at most two rounds per
specialist. If a gate still fails, the lesson is published with the failing
sections visibly flagged in the player. It is never silently passed and never
blocked.

The tutor is a separate agent that runs at play time, not during generation.

## Decisions already made

- **Providers are chosen per component** (seven components: paper reader,
  lesson planner, narration writer, board artist, narrator voice, quiz writer,
  tutor). First version: Anthropic, OpenAI, Google, OpenRouter for text;
  ElevenLabs, OpenAI, Google for voice.
- **The board is vector strokes produced by a text model**, not generated
  images. This is what makes stroke-level sync possible.
- **Sync is anchored to word timestamps.** Every board cue is pinned to a word
  in the script. If a voice provider returns no timestamps, they are estimated
  from the audio and the lesson is flagged "timing estimated".
- **Agent runtime is a small loop inside this app** with a thin provider layer.
- **Runs in Docker**: one app container plus the official Postgres image, via
  the app's own `docker-compose.yml`. Postgres holds lessons, plans, scripts,
  board cues, audio metadata, job state and eval results. Audio files live on
  a mounted volume.
- **API keys stay on the server** and are never sent to the browser.
- **Generation resumes** from the last finished step after a failure or
  restart; it does not start over.
- **Time and cost are measured and reported, not capped.**

## Not in the first version

Accounts and multi-user, sharing or export to video, local/offline models,
image-model boards, editing strokes by hand, languages other than English,
a job queue beyond what Postgres provides.

## Done means

The app starts with `docker compose up`, generates a lesson from
"Attention Is All You Need" end to end without manual help, and that lesson
passes every gate in `eval.md`.
