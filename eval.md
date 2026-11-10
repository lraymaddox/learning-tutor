# Paper to Lesson — Eval

What "good" means for a generated lesson, and how it is checked. The same
checks run in two places: inside the orchestrator during generation (to decide
on redos and warnings) and in the offline eval run before a release.

## Test set

One paper for the first version: **"Attention Is All You Need"** (Vaswani et
al., 2017), standard length, undergraduate audience, chalkboard. Widen the set
before trusting the scores on papers heavy in data figures or proofs.

## Gates (pass / fail)

A lesson is **good** only if all four pass. A failed gate after two redo
rounds does not block the lesson; the failing sections are flagged in the
player and listed in the report.

### 1. Faithful to the paper

| Check | How | Pass |
|---|---|---|
| Every factual claim in the narration is supported by the paper | LLM judge, claim by claim, must quote the supporting passage | 100% supported; 0 contradicted |
| Every number and equation on the board or in the script matches the paper | Deterministic match against extracted text, judge for notation variants | 100% |
| Quiz answers marked correct are correct per the paper | LLM judge with quoted passage | 100% |
| Tutor answers cite a passage that exists and supports the answer | Fixed set of 10 questions, LLM judge | 9 of 10; 0 contradicted |

Simplifications and analogies are allowed if the narration signals them as
such and they do not contradict the paper.

### 2. Board stays in sync

All deterministic, computed from the stored timeline.

| Check | Pass |
|---|---|
| Each cue starts within 300 ms of its trigger word | 95% of cues; none over 1 s |
| Every cue mark in the script has board strokes, and every stroke group has a cue | 100% |
| Longest stretch of narration with nothing new on the board | 20 s or less |
| A cue's strokes finish drawing before the next cue starts | 100% |
| Strokes stay inside the board and labels do not overlap | 100% |
| Seeking to any section shows the same board as playing through to it | 100% |

Lessons using estimated timestamps are held to the same numbers and carry the
"timing estimated" flag.

### 3. Teaches well

LLM judge scores each item 1 to 5 against a written rubric, with a one-line
reason. Pass is **4 or higher on every item**.

- Objectives are all covered, and each has at least one quiz question.
- Every concept is introduced before it is used.
- Level matches the chosen audience.
- The board adds something the narration alone does not (structure, a diagram,
  the equation) rather than repeating the script as text.
- Quiz questions are answerable from the lesson alone.
- Spoken length is within 20% of the chosen lesson length.

**Human spot check before a release:** watch the test lesson once and confirm
(a) you could explain the paper's main idea afterwards, (b) nothing on the
board was confusing or wrong, (c) the judge's scores match your impression. A
disagreement means the rubric is fixed, not overridden.

### 4. Runs reliably

| Check | Pass |
|---|---|
| Generation finishes with no manual help | 3 of 3 runs on the test paper |
| Killing the app container mid-generation, then restarting, resumes from the last finished step | Yes, with no repeated provider calls for finished steps |
| A provider error on one component is retried and reported, not swallowed | Yes |
| Swapping the provider for any single component still yields a lesson that passes gates 1 to 3 | Each of the seven components tried with a second provider |
| `docker compose up` on a clean machine reaches a working app | Yes |

## Reported, not gated

Per lesson: wall-clock generation time, cost per component, tokens per
component, number of redo rounds per specialist, and which gates needed a redo
to pass.

## Report

Each eval run writes one row per check (pass/fail or score, evidence, lesson
section) to Postgres and renders a single page: gates at the top, flagged
sections with their evidence, then the reported numbers.
