"""FastAPI application.

    python -m ghost.api.main          # uses GHOST_HOST / GHOST_PORT
"""
from __future__ import annotations

import json
import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .. import db
from ..config import settings
from ..engine import pipeline, virtual
from . import routes_data, routes_read, routes_write


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out = {"ts": round(record.created, 3), "level": record.levelname, "logger": record.name, "msg": record.getMessage()}
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)[-2000:]
        return json.dumps(out)


def setup_logging() -> None:
    h = logging.StreamHandler()
    h.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [h]
    root.setLevel(logging.INFO)
    logging.getLogger("uvicorn.access").disabled = True
    logging.getLogger("httpx").setLevel(logging.WARNING)


log = logging.getLogger("ghost.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    conn = db.connect()
    applied = db.migrate(conn)
    if applied:
        log.info("migrations applied: %s", applied)
    if not db.scalar("SELECT COUNT(*) FROM sensors", conn=conn):
        log.warning("database has no sensors yet - run: python -m ghost.etl.build_all")
    routes_data.mark_interrupted(conn)
    conn.close()
    virtual.load_bundle()
    if settings.background_engine:
        pipeline.engine.start()
    yield
    pipeline.engine.stop()


app = FastAPI(title="Ghost Sensor API", version="1.0.0", lifespan=lifespan,
              description="Digital twin, anomaly detection, virtual sensors and multi-agent incident management for the Campus Dijon building.")
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=False,
                   allow_methods=["GET", "POST", "DELETE"], allow_headers=["Authorization", "Content-Type", "X-Operator-Name", "X-Ghost-Token"])


@app.middleware("http")
async def guard_and_log(request: Request, call_next):
    started = time.time()
    length = request.headers.get("content-length")
    limit = settings.max_upload_mb * 1024 * 1024 + 65536 if request.url.path == "/api/uploads" else settings.max_body_bytes
    if length and length.isdigit() and int(length) > limit:
        return JSONResponse({"detail": "request body too large"}, status_code=413)
    try:
        response = await call_next(request)
    except Exception:
        log.exception("unhandled error on %s %s", request.method, request.url.path)
        response = JSONResponse({"detail": "internal error"}, status_code=500)
    if request.url.path != "/api/stream":
        log.info(json.dumps({"method": request.method, "path": request.url.path, "status": response.status_code,
                             "ms": int((time.time() - started) * 1000), "client": request.client.host if request.client else None}))
    return response


app.include_router(routes_read.router)
app.include_router(routes_write.ops)
app.include_router(routes_write.auto)
app.include_router(routes_data.router)

# Guide videos and their subtitles (large files kept in GHOST_DATA_DIR/media, outside the repo).
# StaticFiles answers HTTP range requests, so the browser can seek inside a video.
(settings.data_dir / "media").mkdir(parents=True, exist_ok=True)
app.mount("/api/media", StaticFiles(directory=settings.data_dir / "media"), name="media")


def run() -> None:
    import uvicorn

    setup_logging()
    uvicorn.run(app, host=settings.host, port=settings.port, log_config=None, timeout_keep_alive=30)


if __name__ == "__main__":
    run()
