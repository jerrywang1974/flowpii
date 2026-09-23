"""Per-job storage for multi-user concurrent uploads.

Each recognition gets an isolated directory under uploads/{job_id}/.
Metadata is persisted to disk so jobs survive process restarts and
do not rely solely on in-memory dict (safe for single-process uvicorn).
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
UPLOAD_DIR = ROOT / "uploads"
OUT_DIR = ROOT / "out"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
OUT_DIR.mkdir(parents=True, exist_ok=True)

# Limit simultaneous SpaceXAI recognitions (expensive / long-running)
MAX_CONCURRENT_RECOGNIZE = int(__import__("os").getenv("FLOWPII_MAX_CONCURRENT", "2"))
_recognize_sema = threading.Semaphore(MAX_CONCURRENT_RECOGNIZE)
_lock = threading.Lock()


@dataclass
class JobRecord:
    job_id: str
    filename: str | None = None
    status: str = "queued"  # queued | running | done | error | confirmed
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    preview: str | None = None  # first-page preview path (compat)
    excel: str | None = None
    error: str | None = None
    warnings: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    graph: dict[str, Any] | None = None
    rows: list[dict[str, Any]] = field(default_factory=list)
    confirmed: bool = False
    # opaque token required for confirm/download (returned only to uploader)
    access_token: str = field(default_factory=lambda: uuid.uuid4().hex)
    page_total: int = 0
    pages_done: int = 0
    page_index: int = 0  # 1-based current page being processed
    # map "1" -> absolute path of preview_p1.png
    page_previews: dict[str, str] = field(default_factory=dict)

    @property
    def dir(self) -> Path:
        return UPLOAD_DIR / self.job_id

    @property
    def meta_path(self) -> Path:
        return self.dir / "job.json"


def new_job(filename: str | None) -> JobRecord:
    """Create an isolated job directory + access token.

    Sequential uploads (same user, file A then file B) each call this once;
    jobs never share directories or tokens, so B cannot overwrite A's artifacts.
    """
    job_id = uuid.uuid4().hex[:12]
    job = JobRecord(job_id=job_id, filename=filename, status="queued")
    job.dir.mkdir(parents=True, exist_ok=True)
    save_job(job)
    return job


def save_job(job: JobRecord) -> None:
    job.updated_at = time.time()
    job.dir.mkdir(parents=True, exist_ok=True)
    tmp = job.meta_path.with_suffix(".tmp")
    with _lock:
        tmp.write_text(
            json.dumps(asdict(job), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(job.meta_path)


def load_job(job_id: str) -> JobRecord | None:
    from dataclasses import fields

    path = UPLOAD_DIR / job_id / "job.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        allowed = {f.name for f in fields(JobRecord)}
        return JobRecord(**{k: v for k, v in data.items() if k in allowed})
    except Exception:  # noqa: BLE001
        return None


def require_job(job_id: str, access_token: str | None = None) -> JobRecord:
    """Load job and require a non-empty matching access_token (constant-time compare)."""
    import hmac

    from fastapi import HTTPException

    job = load_job(job_id)
    if not job:
        raise HTTPException(404, detail="找不到工作")
    if not access_token:
        raise HTTPException(403, detail="缺少 access_token")
    if not hmac.compare_digest(access_token, job.access_token):
        raise HTTPException(403, detail="無權存取此工作（access_token 不符）")
    return job


def acquire_recognize_slot(timeout: float = 1.0) -> bool:
    return _recognize_sema.acquire(timeout=timeout)


def release_recognize_slot() -> None:
    _recognize_sema.release()
