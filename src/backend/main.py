"""FastAPI application.

    uvicorn src.backend.main:app --port 8000

If the React build exists (src/frontend/dist) it is served at / so the whole prototype runs from one process.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from src.backend.api.routes import router
from src.backend.services import refresh
from src.config import ROOT

@asynccontextmanager
async def lifespan(_: FastAPI):
    refresh.start_scheduler()   # periodic re-check while the server runs
    refresh.start()   # background: fetch any new GDELT days, re-score, rebuild DB (never blocks startup)
    yield


app = FastAPI(lifespan=lifespan, title="Risk Intelligence Engine", version="1.0.0",
              description="Unstructured-text Risk Engine with Tactical Rebalancing and Strategic Stress Testing. LLM-optional.")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.include_router(router)

DIST = ROOT / "src" / "frontend" / "dist"
if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        f = DIST / path
        return FileResponse(f if path and f.is_file() else DIST / "index.html")
