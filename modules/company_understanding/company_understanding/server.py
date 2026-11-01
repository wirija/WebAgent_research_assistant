"""Optional standalone HTTP adapter. Core module also runs through CLI/Python."""
import asyncio
import json
import os
import secrets
import subprocess
import sys
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request as HTTPRequest
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .documents import source_path
from .engine import Job, Understanding
from .models import Request
from .sample import SampleClient, create_sample

PACKAGE = Path(__file__).resolve().parent
OUTPUT = Path(os.getenv('UNDERSTANDING_OUTPUT_DIR', str(PACKAGE.parent / 'data'))).expanduser().resolve()
TOKEN = secrets.token_urlsafe(32)
JOBS, TASKS = {}, {}


@asynccontextmanager
async def lifespan(app):
    OUTPUT.mkdir(parents=True, exist_ok=True)
    yield
    tasks = [t for t in TASKS.values() if not t.done()]
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


app = FastAPI(title='Module 2 · Company Understanding', lifespan=lifespan, docs_url=None, redoc_url=None)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=['127.0.0.1', 'localhost', 'testserver'])


@app.middleware('http')
async def local_access(request: HTTPRequest, call_next):
    if request.url.path.removeprefix(request.scope.get('root_path', '')).startswith('/api/') and request.method != 'GET':
        if not secrets.compare_digest(request.headers.get('x-session-token', ''), TOKEN):
            return JSONResponse({'detail': 'Refresh the page to renew the local session.'}, status_code=403)
        if request.headers.get('origin') and request.headers['origin'] != f'{request.url.scheme}://{request.headers.get("host")}':
            return JSONResponse({'detail': 'Cross-origin requests are disabled.'}, status_code=403)
    response = await call_next(request)
    response.headers.update({'X-Content-Type-Options': 'nosniff', 'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer'})
    return response


@app.get('/api/config')
async def config():
    from .engine import now
    return {'model': os.getenv('OPENAI_MODEL', 'gpt-5-mini'), 'key_configured': bool(os.getenv('OPENAI_API_KEY')),
            'output_root': str(OUTPUT), 'session_token': TOKEN, 'today': now()[:10]}


def locate(job_id):
    if len(job_id) != 32 or not all(c in '0123456789abcdef' for c in job_id):
        raise HTTPException(404, 'Job not found')
    folder = (OUTPUT / job_id).resolve()
    if not folder.is_relative_to(OUTPUT) or not (folder / 'job.json').is_file():
        raise HTTPException(404, 'Job not found')
    return folder


def read_job(job_id):
    if job_id in JOBS:
        return JOBS[job_id].snapshot()
    data = json.loads((locate(job_id) / 'job.json').read_text())
    if data['status'] == 'running':
        data.update(status='interrupted', stage='Interrupted')
        data['issues'] = [*data['issues'], 'The server stopped. Start a new job; saved evidence is retained.']
    return data


@app.get('/api/jobs')
async def history():
    results = []
    for file in OUTPUT.glob('*/job.json'):
        try:
            data = read_job(file.parent.name)
            results.append({k: data[k] for k in ('id', 'company', 'started_at', 'status', 'demo', 'financial_year_end')})
        except (OSError, ValueError, KeyError, HTTPException):
            pass
    return sorted(results, key=lambda d: d['started_at'], reverse=True)


def launch(request, demo=False):
    if any(not t.done() for t in TASKS.values()):
        raise HTTPException(409, 'A Module 2 job is active. Stop it or wait for it to finish.')
    key = request.api_key.get_secret_value().strip() if request.api_key else os.getenv('OPENAI_API_KEY', '').strip()
    if not demo and not key:
        raise HTTPException(400, 'Enter an API key or configure this module’s .env file.')
    try:
        job = Job(request, OUTPUT, os.getenv('OPENAI_MODEL', 'gpt-5-mini'), demo=demo)
    except (ValueError, OSError, KeyError) as exc:
        from .engine import safe_error
        raise HTTPException(400, safe_error(exc))
    JOBS[job.id] = job
    TASKS[job.id] = asyncio.create_task(Understanding(job, key, SampleClient() if demo else None).execute())
    return job.snapshot()


@app.post('/api/jobs')
async def start(request: Request):
    return launch(request)


@app.post('/api/demo')
async def sample():
    source = create_sample(OUTPUT / '_sample_input')
    return launch(Request(source_folder=str(source), financial_year_end='2026-09-30', information_cutoff='2026-11-01'), demo=True)


@app.get('/api/jobs/{job_id}')
async def status(job_id: str):
    return read_job(job_id)


@app.post('/api/jobs/{job_id}/cancel')
async def cancel(job_id: str):
    if job_id in TASKS and not TASKS[job_id].done():
        TASKS[job_id].cancel()
    return {'ok': True}


@app.get('/api/jobs/{job_id}/files/{name}')
async def artifact(job_id: str, name: str):
    if name not in {'job.json', 'report.html', 'company_profile.json', 'significant_changes.json', 'exposure_register.json', 'open_questions.json', 'research_questions.json', 'question_coverage.json', 'evidence_comparisons.json', 'claim_checks.json'}:
        raise HTTPException(404, 'Artifact not found')
    path = locate(job_id) / name
    if not path.is_file():
        raise HTTPException(404, 'Artifact is not ready')
    return FileResponse(path, filename=name, media_type='application/octet-stream')


@app.get('/api/jobs/{job_id}/sources/{document_id}')
async def source(job_id: str, document_id: str):
    data = read_job(job_id)
    row = next((x for x in data['documents'] if x['id'] == document_id), None)
    if not row or not row.get('file'):
        raise HTTPException(404, 'Source not found')
    try:
        path = source_path(Path(data['source_folder']), row['file'])
        if row.get('sha256'):
            import hashlib
            digest = await asyncio.to_thread(lambda: hashlib.sha256(path.read_bytes()).hexdigest())
            if digest != row['sha256']:
                raise ValueError('Source changed since this job ran. Check the saved text evidence.')
    except (OSError, ValueError) as exc:
        raise HTTPException(409, str(exc))
    return FileResponse(path, filename=path.name, media_type='application/octet-stream')


@app.get('/api/jobs/{job_id}/zip')
async def download_zip(job_id: str):
    folder = locate(job_id)
    if read_job(job_id)['status'] == 'running':
        raise HTTPException(409, 'Stop or finish the job before exporting.')
    target = OUTPUT / f'{job_id}.zip'
    def pack():
        with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as archive:
            for child in sorted(folder.rglob('*')):
                if child.is_file() and not child.is_symlink():
                    archive.write(child, arcname=str(child.relative_to(folder)))
    await asyncio.to_thread(pack)
    return FileResponse(target, filename=f'company-understanding-{job_id[:8]}.zip')


@app.post('/api/jobs/{job_id}/open-folder')
async def open_folder(job_id: str):
    folder = locate(job_id)
    command = ['open', str(folder)] if sys.platform == 'darwin' else ['explorer', str(folder)] if sys.platform == 'win32' else ['xdg-open', str(folder)]
    try:
        await asyncio.to_thread(subprocess.run, command, timeout=10, check=True, capture_output=True)
    except (OSError, subprocess.SubprocessError):
        raise HTTPException(500, f'Open this folder manually: {folder}')
    return {'ok': True}


app.mount('/', StaticFiles(directory=PACKAGE / 'static', html=True), name='ui')
