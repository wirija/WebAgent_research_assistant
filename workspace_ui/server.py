"""Compose independent module HTTP adapters into one local workspace.

This layer only mounts apps and serves navigation. It never sequences module jobs.
"""
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
import sys
from dotenv import load_dotenv

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

# The standalone module is a separate local package, not a collector dependency.
# Resolve its location from this checkout so launching works from any directory.
PROJECT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT / '.env')
sys.path.insert(0, str(PROJECT / 'modules' / 'company_understanding'))

sys.path.insert(0, str(PROJECT / 'modules' / 'external_research'))

from external_research.server import app as external_app
from webagent.server import app as collection_app
from company_understanding.server import app as understanding_app


@asynccontextmanager
async def lifespan(app):
    # ASGI mounts do not automatically run their child apps' lifespans.
    async with AsyncExitStack() as stack:
        await stack.enter_async_context(collection_app.router.lifespan_context(collection_app))
        await stack.enter_async_context(understanding_app.router.lifespan_context(understanding_app))
        await stack.enter_async_context(external_app.router.lifespan_context(external_app))
        yield


app = FastAPI(title='Company Research Workspace', lifespan=lifespan, docs_url=None, redoc_url=None)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=['127.0.0.1', 'localhost', 'testserver'])
app.mount('/collection', collection_app)
app.mount('/understanding', understanding_app)
app.mount('/external', external_app)
app.mount('/', StaticFiles(directory=Path(__file__).parent / 'static', html=True), name='workspace')
