from __future__ import annotations

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import FastAPI, File, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .excel_writer import write_inventory_excel
from .images import load_pages_as_png_bytes, save_preview_png
from .jobs import (
    MAX_CONCURRENT_RECOGNIZE,
    OUT_DIR,
    UPLOAD_DIR,
    acquire_recognize_slot,
    load_job,
    new_job,
    release_recognize_slot,
    require_job,
    save_job,
)
from .models import BifGraph, InventoryRow
from .recognize import recognize_from_fixture, recognize_images

ROOT = Path(__file__).resolve().parents[2]
STATIC_DIR = ROOT / "static"
TEMPLATES_DIR = ROOT / "templates"

# Dedicated pool so long AI calls do not starve the event loop
_executor = ThreadPoolExecutor(
    max_workers=max(2, MAX_CONCURRENT_RECOGNIZE + 1),
    thread_name_prefix="flowpii-recognize",
)

app = FastAPI(title="FlowPII", version="0.2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


class RowEdit(BaseModel):
    process_id: str = Field(alias="A")
    process_name: str = Field(alias="B")
    file_name: str = Field(alias="G")
    file_type: str = Field(alias="H")
    source: str = Field(alias="AF")
    target: str = Field(alias="AG")
    transfer_method: str = Field(alias="AH")

    model_config = {"populate_by_name": True}


class ConfirmRequest(BaseModel):
    rows: list[RowEdit]
    graph: dict | None = None
    access_token: str


def _token_from(
    access_token: str | None = None,
    x_flowpii_token: str | None = None,
) -> str | None:
    return access_token or x_flowpii_token


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    index_path = TEMPLATES_DIR / "index.html"
    if not index_path.exists():
        return HTMLResponse("<h1>FlowPII</h1><p>templates/index.html missing</p>")
    return HTMLResponse(index_path.read_text(encoding="utf-8"))


@app.get("/api/health")
def health():
    return {
        "ok": True,
        "version": "0.2.0",
        "max_concurrent_recognize": MAX_CONCURRENT_RECOGNIZE,
        "multi_user": {
            "job_isolation": True,
            "disk_persisted": True,
            "access_token": True,
            "async_recognize": True,
            "auth_login": False,
        },
    }


def _run_recognize_sync(
    job_id: str,
    raw_path: Path,
    use_fixture: str | None,
) -> None:
    job = load_job(job_id)
    if not job:
        return
    job.status = "running"
    save_job(job)

    got_slot = acquire_recognize_slot(timeout=600)
    if not got_slot:
        job.status = "error"
        job.error = "系統忙碌中（已達同時辨識上限），請稍後再試"
        save_job(job)
        return

    try:
        job_dir = UPLOAD_DIR / job_id
        if use_fixture:
            fixture = ROOT / "tests" / "fixtures" / use_fixture
            if not fixture.exists():
                raise FileNotFoundError(f"fixture 不存在: {use_fixture}")
            result = recognize_from_fixture(fixture)
            preview = None
            try:
                pages = load_pages_as_png_bytes(raw_path, dpi=120)
                preview = save_preview_png(pages[0], job_dir / "preview.png")
            except Exception:  # noqa: BLE001
                preview = None
        else:
            pages = load_pages_as_png_bytes(raw_path, dpi=180)
            preview = save_preview_png(pages[0], job_dir / "preview.png")
            result = recognize_images(pages)

        job.status = "done"
        job.preview = str(preview) if preview else None
        job.warnings = list(result.warnings)
        job.metadata = result.graph.metadata.model_dump()
        job.graph = json.loads(result.graph.model_dump_json(by_alias=True))
        job.rows = [r.as_dict() for r in result.rows]
        job.error = None
        save_job(job)
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)
        if len(msg) > 500:
            msg = msg[:500] + "…"
        job.status = "error"
        job.error = f"辨識失敗: {msg}"
        save_job(job)
    finally:
        release_recognize_slot()


@app.post("/api/recognize")
async def api_recognize(
    file: UploadFile = File(...),
    use_fixture: str | None = None,
):
    """Accept upload, return job_id immediately, run recognition in background.

    同一瀏覽器連續上傳第二個檔案時：
    - 每次呼叫都會 ``new_job()``，產生全新 job_id / 目錄 / access_token
    - 不會覆寫或取消上一個 job（上一個仍可憑舊 token 查詢／下載）
    - 前端必須停止舊輪詢並綁定新 token，否則會顯示錯檔結果
    """
    suffix = Path(file.filename or "upload.bin").suffix.lower()
    if suffix not in {".pdf", ".png", ".jpg", ".jpeg"}:
        raise HTTPException(400, detail="僅支援 PDF / JPG / PNG")

    job = new_job(file.filename)
    raw_path = job.dir / f"input{suffix}"
    content = await file.read()
    raw_path.write_bytes(content)

    loop = asyncio.get_running_loop()
    loop.run_in_executor(
        _executor,
        _run_recognize_sync,
        job.job_id,
        raw_path,
        use_fixture,
    )

    return {
        "job_id": job.job_id,
        "access_token": job.access_token,
        "filename": file.filename,
        "status": job.status,
        "poll_url": f"/api/jobs/{job.job_id}",
        "message": "已受理上傳，辨識進行中（可多人同時使用，各工作彼此隔離）",
    }


@app.get("/api/jobs/{job_id}")
def job_status(
    job_id: str,
    access_token: str | None = None,
    x_flowpii_token: str | None = Header(default=None, alias="X-FlowPII-Token"),
):
    token = _token_from(access_token, x_flowpii_token)
    job = require_job(job_id, token)
    payload: dict = {
        "job_id": job.job_id,
        "filename": job.filename,
        "status": job.status,
        "warnings": job.warnings,
        "error": job.error,
        "confirmed": job.confirmed,
        "preview_url": (
            f"/api/jobs/{job.job_id}/preview?access_token={job.access_token}"
            if job.preview
            else None
        ),
    }
    if job.status == "done" or job.status == "confirmed":
        payload.update(
            {
                "metadata": job.metadata,
                "graph": job.graph,
                "rows": job.rows,
            }
        )
    if job.status == "confirmed" and job.excel:
        payload["download_url"] = (
            f"/api/jobs/{job.job_id}/download?access_token={job.access_token}"
        )
    return payload


@app.get("/api/jobs/{job_id}/preview")
def job_preview(
    job_id: str,
    access_token: str | None = None,
    x_flowpii_token: str | None = Header(default=None, alias="X-FlowPII-Token"),
):
    job = require_job(job_id, _token_from(access_token, x_flowpii_token))
    if not job.preview:
        raise HTTPException(404, detail="找不到預覽圖")
    return FileResponse(job.preview, media_type="image/png")


@app.post("/api/jobs/{job_id}/confirm")
def job_confirm(job_id: str, body: ConfirmRequest):
    job = require_job(job_id, body.access_token)
    if job.status not in {"done", "confirmed"}:
        raise HTTPException(400, detail=f"工作尚未完成辨識（status={job.status}）")

    rows = [
        InventoryRow(
            process_id=r.process_id,
            process_name=r.process_name,
            file_name=r.file_name,
            file_type=r.file_type,  # type: ignore[arg-type]
            source=r.source,
            target=r.target,
            transfer_method=r.transfer_method,
        )
        for r in body.rows
    ]
    graph = BifGraph.model_validate(body.graph) if body.graph else None
    out_path = OUT_DIR / f"{job_id}_inventory.xlsx"
    from .models import Metadata

    meta = graph.metadata if graph else Metadata.model_validate(job.metadata or {})
    write_inventory_excel(rows, out_path, graph=graph, metadata=meta)
    job.confirmed = True
    job.status = "confirmed"
    job.rows = [r.as_dict() for r in rows]
    if body.graph:
        job.graph = body.graph
    job.excel = str(out_path)
    save_job(job)
    return {
        "ok": True,
        "download_url": f"/api/jobs/{job_id}/download?access_token={job.access_token}",
        "row_count": len(rows),
    }


@app.get("/api/jobs/{job_id}/download")
def job_download(
    job_id: str,
    access_token: str | None = None,
    x_flowpii_token: str | None = Header(default=None, alias="X-FlowPII-Token"),
):
    job = require_job(job_id, _token_from(access_token, x_flowpii_token))
    if not job.confirmed or not job.excel:
        raise HTTPException(400, detail="請先確認覆核結果後再下載")
    return FileResponse(
        job.excel,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename="個人資料盤點清冊.xlsx",
    )


def create_app() -> FastAPI:
    return app
