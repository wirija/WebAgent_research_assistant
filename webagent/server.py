from __future__ import annotations

import asyncio
import html
import json
import os
import secrets
import subprocess
import sys
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .agent import Agent
from .state import Run, RunRequest, elapsed_seconds

PROJECT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT / ".env")
OUTPUT = Path(os.getenv("WEBAGENT_OUTPUT_DIR", str(PROJECT / "output"))).expanduser().resolve()
TOKEN = secrets.token_urlsafe(32)
RUNS: dict[str, Run] = {}
TASKS: dict[str, asyncio.Task] = {}
AGENTS: dict[str, Agent] = {}


@asynccontextmanager
async def lifespan(app):
    OUTPUT.mkdir(parents=True, exist_ok=True)
    yield
    pending = [t for t in TASKS.values() if not t.done()]
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


app = FastAPI(title="Company Research", lifespan=lifespan, docs_url=None, redoc_url=None)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])


@app.middleware("http")
async def local_access(request: Request, call_next):
    if request.url.path.removeprefix(request.scope.get("root_path", "")).startswith("/api/") and request.method != "GET":
        if not secrets.compare_digest(request.headers.get("x-session-token", ""), TOKEN):
            return JSONResponse({"detail": "Invalid local session token. Refresh the page."}, status_code=403)
        origin = request.headers.get("origin")
        if origin and origin != f"{request.url.scheme}://{request.headers.get('host')}":
            return JSONResponse({"detail": "Cross-origin requests are disabled."}, status_code=403)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/api/config")
async def config():
    return {"model": os.getenv("OPENAI_MODEL", "gpt-5-mini"),
            "key_configured": bool(os.getenv("OPENAI_API_KEY")), "output_root": str(OUTPUT), "session_token": TOKEN}


@app.post("/api/runs")
async def start(request: RunRequest):
    if not request.company.strip():
        raise HTTPException(422, "Enter a company name.")
    if any(not task.done() for task in TASKS.values()):
        raise HTTPException(409, "A collection is already active. Stop it or wait for it to finish.")
    key = request.api_key.get_secret_value().strip() if request.api_key else os.getenv("OPENAI_API_KEY", "").strip()
    if not request.demo and not key:
        raise HTTPException(400, "Add an OpenAI API key in Settings, or set OPENAI_API_KEY in .env.")
    run = Run(request, OUTPUT, os.getenv("OPENAI_MODEL", "gpt-5-mini"))
    RUNS[run.id] = run
    if request.demo:
        TASKS[run.id] = asyncio.create_task(demo(run))
    else:
        agent = Agent(run, key)
        AGENTS[run.id] = agent
        async def work():
            try:
                await agent.execute()
            finally:
                AGENTS.pop(run.id, None)
        TASKS[run.id] = asyncio.create_task(work())
    # Secrets are never included in the Run state or written to disk.
    return run.snapshot()


def folder_for(run_id):
    if not run_id.isalnum() or len(run_id) != 32:
        raise HTTPException(404, "Collection not found")
    if run_id in RUNS:
        return RUNS[run_id].folder
    for folder in OUTPUT.glob(f"*-{run_id[:8]}"):
        try:
            if json.loads((folder / "catalogue.json").read_text())["id"] == run_id:
                return folder
        except (OSError, ValueError, KeyError):
            continue
    raise HTTPException(404, "Collection not found")


def read_run(run_id):
    if run_id in RUNS:
        return RUNS[run_id].snapshot()
    data = json.loads((folder_for(run_id) / "catalogue.json").read_text())
    if data["status"] in {"running", "needs_input"}:
        data["status"] = "interrupted"
        data["question"] = None
        data["summary"] = "The server stopped during this run. Saved files remain available. Start a new collection to retry."
        for item in data.get("queue", {}).values():
            if item["status"] in {"pending", "processing"}:
                item.update(status="interrupted", error="Server stopped before this item completed.")
        if data.get("queue_counts"):
            for status in ("pending", "processing", "interrupted"):
                data["queue_counts"][status] = sum(x["status"] == status for x in data["queue"].values())
    if not data.get("finished_at"):
        # Earlier versions did not record finish times. Their final activity is
        # the best available duration estimate; never keep an old timer ticking.
        events = data.get("events", [])
        data["finished_at"] = events[-1]["time"] if events else data["started_at"]
        data["elapsed_estimated"] = True
    data["elapsed_seconds"] = elapsed_seconds(data["started_at"], data["finished_at"])
    return data


@app.get("/api/runs")
async def history():
    items = []
    for file in sorted(OUTPUT.glob("*/catalogue.json"), reverse=True):
        try:
            data = json.loads(file.read_text())
            items.append({k: data[k] for k in ["id", "company", "started_at", "status", "demo"]})
        except (OSError, ValueError, KeyError):
            pass
    return sorted(items, key=lambda x: x["started_at"], reverse=True)[:30]


@app.get("/api/runs/{run_id}")
async def status(run_id: str):
    return read_run(run_id)


@app.post("/api/runs/{run_id}/cancel")
async def cancel(run_id: str):
    task = TASKS.get(run_id)
    if task and not task.done():
        task.cancel()
    return {"ok": True}


class Answer(BaseModel):
    answer: str = Field(min_length=1, max_length=2000)


@app.post("/api/runs/{run_id}/answer")
async def answer(run_id: str, body: Answer):
    agent = AGENTS.get(run_id)
    if not agent or agent.run.status != "needs_input" or not agent.answer.empty():
        raise HTTPException(409, "This run is not awaiting an answer.")
    await agent.answer.put(body.answer)
    return {"ok": True}


@app.get("/api/runs/{run_id}/files/{path:path}")
async def file(run_id: str, path: str):
    root = folder_for(run_id).resolve()
    target = (root / path).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        raise HTTPException(404, "File not found")
    # Force attachment: collected HTML must never execute on the application's origin.
    return FileResponse(target, filename=target.name, media_type="application/octet-stream")


@app.get("/api/runs/{run_id}/zip")
async def download_zip(run_id: str):
    folder = folder_for(run_id)
    if read_run(run_id)["status"] in {"running", "needs_input"}:
        raise HTTPException(409, "Stop the collection before downloading its ZIP.")
    destination = folder.parent / f"{folder.name}.zip"
    def pack():
        with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
            for child in sorted(folder.rglob("*")):
                if child.is_file() and not child.is_symlink():
                    archive.write(child, arcname=f"{folder.name}/{child.relative_to(folder)}")
    await asyncio.to_thread(pack)
    return FileResponse(destination, filename=destination.name, media_type="application/zip")


@app.post("/api/runs/{run_id}/open-folder")
async def open_folder(run_id: str):
    folder = folder_for(run_id)
    command = ["open", str(folder)] if sys.platform == "darwin" else ["explorer", str(folder)] if sys.platform == "win32" else ["xdg-open", str(folder)]
    try:
        await asyncio.to_thread(subprocess.run, command, check=True, timeout=10, capture_output=True)
    except (OSError, subprocess.SubprocessError):
        raise HTTPException(500, f"Could not open the file manager. Folder: {folder}")
    return {"ok": True, "folder": str(folder)}


async def demo(run: Run):
    """Explicitly synthetic UI preview. Never calls an API or impersonates a real collection."""
    try:
        run.event("info", "DEMO — synthetic sample documents. No web search or model calls are made.")
        for stage, message in [("Searching the web", "Example: search for the company’s official website"),
                               ("Exploring sources", "Example: inspect investor relations and newsroom archives"),
                               ("Collecting documents", "Example: save documents and preserve source metadata")]:
            run.stage = stage
            run.step += 1
            run.event("demo", message)
            await asyncio.sleep(0.8)
        samples = [("Annual report — sample", "annual_report"), ("Results announcement — sample", "results"), ("Company update — sample", "press_release")]
        for title, category in samples:
            run.queue['demo:' + category] = {'id': 'demo:' + category, 'kind': 'document', 'url': None,
                'title': title, 'category': category, 'status': 'pending', 'parents': [], 'archives': [], 'error': None}
        run.save()
        for title, category in samples:
            path = f"documents/sample-{category}.html"
            content = f"<!doctype html><html><head><title>Demo document</title></head><body><h1>{html.escape(title)}</h1><p>DEMO ONLY. Synthetic content for {html.escape(run.company)}. This is not an actual company disclosure.</p></body></html>"
            (run.folder / path).write_text(content, encoding="utf-8")
            run.documents.append({"id": f"doc-{len(run.documents)+1}", "title": title, "category": category,
                "published_date": None, "date_status": "demo", "status": "saved", "url": None, "source_url": None,
                "file": path, "duplicate_of": None, "error": None})
            run.queue['demo:' + category].update(status='completed', document_id=run.documents[-1]['id'])
            run.event("saved", f"Demo sample saved: {title}")
            await asyncio.sleep(0.4)
        run.status = "demo"
        run.summary = "Demo complete. Three synthetic HTML files illustrate the output folder; no real research was performed."
    except asyncio.CancelledError:
        run.status = "cancelled"
        run.summary = "Demo stopped."
    finally:
        run.stage = "Finished"
        run.finalize()


app.mount("/", StaticFiles(directory=PROJECT / "static", html=True), name="ui")
