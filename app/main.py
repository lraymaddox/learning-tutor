"""HTTP API and static player."""
import io
import re
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import agents, db, evals, providers

STATIC = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app):
    await db.init()
    # Resume anything that was mid-generation when the app last stopped.
    for row in await db.q("SELECT id FROM lessons WHERE status IN ('queued','running')"):
        agents.start(row["id"])
    yield
    await db.close()


app = FastAPI(title="Paper to Lesson", lifespan=lifespan)


def extract_pdf(data):
    from pypdf import PdfReader
    try:
        reader = PdfReader(io.BytesIO(data))
        text = "\n\n".join((p.extract_text() or "") for p in reader.pages)
    except Exception as e:
        raise HTTPException(400, f"Could not read that PDF: {e}")
    text = text.replace("\x00", "")
    if len(text.split()) < 200:
        raise HTTPException(400, "That PDF has almost no extractable text (it may be scanned images).")
    return text


async def fetch_pdf(url):
    url = url.strip()
    m = re.match(r"https?://arxiv\.org/abs/([^?#]+)", url)
    if m:
        url = f"https://arxiv.org/pdf/{m.group(1)}"
    if not re.match(r"https?://", url):
        raise HTTPException(400, "Enter a full http(s) link.")
    try:
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as c:
            r = await c.get(url, headers={"User-Agent": "paper-to-lesson"})
    except httpx.HTTPError as e:
        raise HTTPException(400, f"Could not download that link: {e!r}")
    if r.status_code != 200 or not r.content.startswith(b"%PDF"):
        raise HTTPException(400, f"That link did not return a PDF (status {r.status_code}). Download the PDF and upload it.")
    return r.content


@app.post("/api/lessons")
async def create_lesson(file: UploadFile | None = File(None), url: str = Form(""), audience: str = Form("undergrad"),
                        length: str = Form("standard"), board_style: str = Form("chalk"), review: bool = Form(True)):
    if audience not in agents.AUDIENCES or length not in agents.LENGTHS:
        raise HTTPException(400, "Unknown audience or length.")
    if file is not None and file.filename:
        data = await file.read()
    elif url.strip():
        data = await fetch_pdf(url)
    else:
        raise HTTPException(400, "Upload a PDF or paste a link.")
    try:
        for comp in providers.COMPONENTS:
            await providers.key_for((await providers.component(comp))["provider"])
    except providers.ProviderError as e:
        raise HTTPException(400, str(e))
    text = extract_pdf(data)
    lesson_id = uuid.uuid4()
    opts = {"audience": audience, "length": length, "board": board_style, "review": review}
    await db.q("INSERT INTO lessons(id,title,options,paper_text) VALUES(%s,%s,%s,%s)", lesson_id,
               (file.filename if file is not None and file.filename else url)[:200], db.Jsonb(opts), text)
    agents.start(lesson_id)
    return {"id": str(lesson_id)}


@app.get("/api/lessons")
async def list_lessons():
    return await db.q("SELECT id,title,status,step,error,created_at FROM lessons ORDER BY created_at DESC")


async def _lesson(lesson_id):
    try:
        uuid.UUID(lesson_id)
    except ValueError:
        raise HTTPException(404, "No such lesson.")
    row = await db.one("SELECT * FROM lessons WHERE id=%s", lesson_id)
    if not row:
        raise HTTPException(404, "No such lesson.")
    return row


@app.get("/api/lessons/{lesson_id}")
async def get_lesson(lesson_id: str):
    row = await _lesson(lesson_id)
    st = row["state"]
    out = {k: row[k] for k in ("id", "title", "status", "step", "error", "options", "created_at")}
    out.update(plan=st.get("plan"), digest=st.get("digest"), flags=st.get("flags", []), quiz=st.get("quiz", []))
    if row["status"] == "ready":
        out["sections"] = [{"title": s["title"], "sentences": s["sentences"], "cues": s["cues"],
                            "audio": {k: s["audio"][k] for k in ("file", "duration", "words", "estimated")}}
                           for s in st["sections"]]
    return out


class PlanIn(BaseModel):
    objectives: list[str]
    sections: list[dict]


@app.post("/api/lessons/{lesson_id}/approve")
async def approve_plan(lesson_id: str, plan: PlanIn):
    row = await _lesson(lesson_id)
    if row["status"] != "awaiting_review":
        raise HTTPException(409, "This lesson is not waiting for a plan review.")
    new = plan.model_dump()
    problems = agents.LessonPlanner.plan_problems(new, row["options"])
    if problems:
        raise HTTPException(400, "; ".join(problems))
    state = row["state"]
    state["plan"], state["plan_approved"] = new, True
    await db.save_lesson(lesson_id, state=state, status="queued")
    agents.start(row["id"])
    return {"ok": True}


@app.post("/api/lessons/{lesson_id}/retry")
async def retry(lesson_id: str):
    row = await _lesson(lesson_id)
    if row["status"] != "failed":
        raise HTTPException(409, "Only a failed lesson can be retried.")
    await db.save_lesson(lesson_id, status="queued", error=None)
    agents.start(row["id"])
    return {"ok": True}


@app.delete("/api/lessons/{lesson_id}")
async def delete_lesson(lesson_id: str):
    row = await _lesson(lesson_id)
    await db.q("DELETE FROM lessons WHERE id=%s", row["id"])
    folder = agents.DATA / "audio" / str(row["id"])
    if folder.is_dir():
        for f in folder.iterdir():
            f.unlink()
        folder.rmdir()
    return {"ok": True}


@app.get("/api/lessons/{lesson_id}/audio/{name}")
async def audio(lesson_id: str, name: str):
    row = await _lesson(lesson_id)
    if not re.fullmatch(r"\d+\.(mp3|wav)", name):
        raise HTTPException(404)
    path = agents.DATA / "audio" / str(row["id"]) / name
    if not path.is_file():
        raise HTTPException(404, "Audio file is missing.")
    return FileResponse(path, media_type="audio/wav" if name.endswith("wav") else "audio/mpeg")


@app.get("/api/lessons/{lesson_id}/report")
async def report(lesson_id: str):
    row = await _lesson(lesson_id)
    checks = await db.q("SELECT gate,name,passed,score,section,evidence FROM eval_checks WHERE lesson_id=%s ORDER BY id", row["id"])
    gates = [{"id": g, "title": t, "passed": all(c["passed"] for c in checks if c["gate"] == g),
              "checks": [c for c in checks if c["gate"] == g]} for g, t in evals.GATES.items()]
    per = {}
    for u in row["usage"]:
        p = per.setdefault(u["component"], {"component": providers.COMPONENTS[u["component"]], "provider": u["provider"],
                                            "model": u["model"], "calls": 0, "in_tokens": 0, "out_tokens": 0,
                                            "chars": 0, "seconds": 0.0, "cost_usd": None})
        p["calls"] += 1
        for k in ("in_tokens", "out_tokens", "chars", "seconds"):
            p[k] += u.get(k, 0)
        if u.get("cost_usd") is not None:
            p["cost_usd"] = (p["cost_usd"] or 0) + u["cost_usd"]
    return {"gates": gates, "usage": list(per.values()), "redos": row["state"].get("redos", {})}


class AskIn(BaseModel):
    question: str
    draw: bool = True


@app.post("/api/lessons/{lesson_id}/ask")
async def ask(lesson_id: str, body: AskIn):
    row = await _lesson(lesson_id)
    if not body.question.strip():
        raise HTTPException(400, "Type a question first.")
    try:
        return await evals.Tutor(row["id"]).answer(row["paper_text"], body.question.strip(), body.draw)
    except providers.ProviderError as e:
        raise HTTPException(502, str(e))


@app.get("/api/settings")
async def get_settings():
    import os
    saved = await db.get_setting("keys", {})
    return {
        "components": await db.get_setting("components", {}),
        "labels": providers.COMPONENTS,
        "text_providers": providers.TEXT_PROVIDERS, "voice_providers": providers.VOICE_PROVIDERS,
        "keys": {p: ("env" if os.environ.get(e) else "saved" if saved.get(p) else "") for p, e in providers.ENV.items()},
    }


class SettingsIn(BaseModel):
    components: dict[str, dict[str, str]]
    keys: dict[str, str] = {}


@app.put("/api/settings")
async def put_settings(body: SettingsIn):
    for name, cfg in body.components.items():
        allowed = providers.VOICE_PROVIDERS if name == "voice" else providers.TEXT_PROVIDERS
        if name not in providers.COMPONENTS or cfg.get("provider") not in allowed:
            raise HTTPException(400, f"Unknown component or provider: {name}")
    await db.set_setting("components", {n: {"provider": c["provider"], "model": c.get("model", "").strip()}
                                        for n, c in body.components.items()})
    keys = await db.get_setting("keys", {})
    for p, k in body.keys.items():
        if p in providers.ENV and k.strip():
            keys[p] = k.strip()
    await db.set_setting("keys", keys)
    return {"ok": True}


@app.delete("/api/settings/keys/{provider}")
async def delete_key(provider: str):
    keys = await db.get_setting("keys", {})
    keys.pop(provider, None)
    await db.set_setting("keys", keys)
    return {"ok": True}


@app.get("/api/models/{provider}")
async def models(provider: str, kind: str = "text"):
    if provider not in providers.TEXT_PROVIDERS + providers.VOICE_PROVIDERS:
        raise HTTPException(404)
    try:
        return await providers.list_models(provider, kind)
    except providers.ProviderError as e:
        raise HTTPException(400, str(e))
    except (httpx.HTTPError, Exception) as e:
        raise HTTPException(502, f"Could not list models for {provider}: {e}")


@app.get("/")
async def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
