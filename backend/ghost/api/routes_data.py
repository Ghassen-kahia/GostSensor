"""CSV upload + data-quality check, and the assistant (chat) endpoint."""
from __future__ import annotations

import logging
import re
import threading
import uuid
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from pydantic import BaseModel, ConfigDict, Field

from .. import datacheck, db
from ..agents import chat
from ..config import settings
from .security import limiter, require_operator, require_read
from .views import iso

log = logging.getLogger("ghost.uploads")
router = APIRouter(prefix="/api")
_analysis_slots = threading.Semaphore(2)
ALLOWED_EXT = {".csv", ".txt", ".tsv"}


def upload_dir() -> Path:
    d = settings.data_dir / "uploads"
    d.mkdir(parents=True, exist_ok=True)
    return d


def mark_interrupted(conn) -> None:
    """Analyses cannot survive a restart: flag them instead of leaving them PROCESSING forever."""
    db.execute("UPDATE uploads SET status='FAILED', error='interrupted by a server restart - upload again' WHERE status='PROCESSING'", conn=conn)


def _run_analysis(uid: str, path: Path, filename: str) -> None:
    conn = db.connect()
    try:
        with _analysis_slots:
            summary, series, long = datacheck.analyse(path, filename, conn)
            long[["ts", "series", "value"]].to_parquet(upload_dir() / f"{uid}.parquet", index=False)
        db.execute("UPDATE uploads SET status='DONE', summary=?, result=?, finished_real=? WHERE id=?",
                   (db.dumps(summary), db.dumps(series), db.now_real(), uid), conn)
    except Exception as exc:  # malformed files are expected input, report them
        log.warning("analysis of %s failed: %s", uid, exc)
        db.execute("UPDATE uploads SET status='FAILED', error=?, finished_real=? WHERE id=?", (str(exc)[:500], db.now_real(), uid), conn)
    finally:
        conn.close()


@router.post("/uploads")
async def upload(file: UploadFile, request: Request, actor: str = Depends(require_operator)):
    limiter.check(request)
    name = Path(file.filename or "upload.csv").name
    if Path(name).suffix.lower() not in ALLOWED_EXT:
        raise HTTPException(400, "only .csv, .tsv or .txt files are accepted")
    uid = uuid.uuid4().hex[:12]
    dest = upload_dir() / f"{uid}.csv"
    limit = settings.max_upload_mb * 1024 * 1024
    size = 0
    with open(dest, "wb") as out:
        while chunk := await file.read(1 << 20):
            size += len(chunk)
            if size > limit:
                out.close()
                dest.unlink(missing_ok=True)
                raise HTTPException(413, f"file larger than {settings.max_upload_mb} MB")
            if size == len(chunk) and b"\x00" in chunk[:4096]:
                out.close()
                dest.unlink(missing_ok=True)
                raise HTTPException(400, "this does not look like a text CSV file")
            out.write(chunk)
    if size == 0:
        dest.unlink(missing_ok=True)
        raise HTTPException(400, "empty file")
    safe = re.sub(r"[^\w.\- ()]", "_", name)[:200]
    db.execute("INSERT INTO uploads(id, filename, size_bytes, status, uploaded_by, created_real) VALUES (?,?,?,?,?,?)",
               (uid, safe, size, "PROCESSING", actor, db.now_real()))
    threading.Thread(target=_run_analysis, args=(uid, dest, safe), daemon=True, name=f"datacheck-{uid}").start()
    return {"id": uid, "filename": safe, "sizeBytes": size, "status": "PROCESSING"}


def _row(u: dict, full: bool = False) -> dict:
    out = {"id": u["id"], "filename": u["filename"], "sizeBytes": u["size_bytes"], "status": u["status"], "error": u["error"],
           "uploadedBy": u["uploaded_by"], "createdAt": iso(u["created_real"]), "finishedAt": iso(u["finished_real"]),
           "summary": db.loads(u["summary"])}
    if full and u["result"]:
        series = db.loads(u["result"], {})
        out["series"] = [{
            "series": m["series"], "displayName": m["displayName"], "type": m["type"], "unit": m["unit"], "room": m["room"],
            "platformSensor": m["platformSensor"], "rows": m["rows"], "validValues": m["validValues"], "first": m["first"], "last": m["last"],
            "completenessPct": m["completenessPct"], "gaps": m["gaps"]["count"], "gapHours": m["gaps"]["totalHours"],
            "outOfRange": m["outOfRange"]["count"], "spikes": m["spikes"], "longestFlatHours": m.get("longestFlatHours"),
            "stats": m.get("stats"), "issues": m["issues"],
            "errors": sum(1 for i in m["issues"] if i["level"] == "error"), "warnings": sum(1 for i in m["issues"] if i["level"] == "warn"),
        } for m in series.values()]
    return out


@router.get("/uploads", dependencies=[Depends(require_read)])
def list_uploads():
    return {"items": [_row(u) for u in db.rows("SELECT * FROM uploads ORDER BY created_real DESC LIMIT 100")],
            "maxUploadMb": settings.max_upload_mb}


def _get(uid: str) -> dict:
    u = db.row("SELECT * FROM uploads WHERE id=?", (uid,))
    if not u:
        raise HTTPException(404, "upload not found")
    return u


@router.get("/uploads/{uid}", dependencies=[Depends(require_read)])
def get_upload(uid: str):
    return _row(_get(uid), full=True)


@router.get("/uploads/{uid}/series", dependencies=[Depends(require_read)])
def get_series(uid: str, name: str, points: int = 1500):
    u = _get(uid)
    if u["status"] != "DONE":
        raise HTTPException(409, "analysis not finished")
    series = db.loads(u["result"], {})
    if name not in series:
        raise HTTPException(404, "series not found in this upload")
    pq = upload_dir() / f"{uid}.parquet"
    pts = datacheck.series_points(pq, name, max(100, min(points, 5000))) if pq.exists() else {"points": [], "resolutionS": None}
    return {"metrics": series[name], **pts}


@router.delete("/uploads/{uid}")
def delete_upload(uid: str, request: Request, actor: str = Depends(require_operator)):
    limiter.check(request)
    _get(uid)
    for ext in (".csv", ".parquet"):
        (upload_dir() / f"{uid}{ext}").unlink(missing_ok=True)
    db.execute("DELETE FROM uploads WHERE id=?", (uid,))
    return {"deleted": uid}


# ------------------------------------------------------------------ assistant
class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(max_length=8000)


class ChatBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    messages: list[ChatMessage] = Field(min_length=1, max_length=40)
    page: str | None = Field(default=None, max_length=200)
    lang: Literal["en", "fr"] = "en"


@router.post("/chat")
def chat_endpoint(body: ChatBody, request: Request, actor: str = Depends(require_operator)):
    """Answers questions with read-only tools over the platform data (LLM if configured, built-in otherwise)."""
    limiter.check(request)
    if body.messages[-1].role != "user":
        raise HTTPException(400, "the last message must come from the user")
    return chat.answer([m.model_dump() for m in body.messages], page=body.page, lang=body.lang)


@router.get("/chat/status", dependencies=[Depends(require_read)])
def chat_status():
    return chat.status()
