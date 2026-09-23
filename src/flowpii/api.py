from __future__ import annotations

import asyncio
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError

from .excel_writer import write_inventory_excel
from .images import load_pages, save_preview_png
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
from .models import BifGraph, InventoryRow, Metadata
from .recognize import recognize_from_fixture, recognize_images, recognize_multipage

ROOT = Path(__file__).resolve().parents[2]
STATIC_DIR = ROOT / "static"
TEMPLATES_DIR = ROOT / "templates"

# Dedicated pool so long AI calls do not starve the event loop
_executor = ThreadPoolExecutor(
    max_workers=max(2, MAX_CONCURRENT_RECOGNIZE + 1),
    thread_name_prefix="flowpii-recognize",
)

app = FastAPI(title="FlowPII", version="0.3.0")
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
    file_type: Literal["1. 紙本", "2. 電子檔"] = Field(alias="H")
    source: str = Field(alias="AF")
    target: str = Field(alias="AG")
    transfer_method: str = Field(alias="AH")
    source_page: int | None = Field(default=None, alias="page")

    model_config = {"populate_by_name": True}


class ConfirmRequest(BaseModel):
    rows: list[RowEdit]
    graph: dict | None = None
    access_token: str


def _token_from(
    access_token: str | None = None,
    x_flowpii_token: str | None = None,
) -> str | None:
    token = access_token or x_flowpii_token
    if token is not None:
        token = token.strip() or None
    return token


def _resolve_fixture(use_fixture: str) -> Path:
    """Allow only a basename under tests/fixtures; block path traversal."""
    allow = os.getenv("FLOWPII_ALLOW_FIXTURES", "1").strip().lower()
    if allow not in {"1", "true", "yes", "on"}:
        raise HTTPException(403, detail="fixture 模式未啟用（FLOWPII_ALLOW_FIXTURES）")

    fixtures_dir = (ROOT / "tests" / "fixtures").resolve()
    raw = (use_fixture or "").replace("\\", "/").strip()
    if not raw or "/" in raw or raw in {".", ".."} or Path(raw).is_absolute():
        raise HTTPException(400, detail="無效的 fixture 名稱")
    name = Path(raw).name
    if name != raw:
        raise HTTPException(400, detail="無效的 fixture 名稱")
    path = (fixtures_dir / name).resolve()
    if path.parent != fixtures_dir or not path.is_file():
        raise HTTPException(400, detail="fixture 不存在")
    return path


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
        "version": "0.3.0",
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
    fixture_path: Path | None,
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
        pdf_for_units = raw_path if raw_path.suffix.lower() == ".pdf" else None
        loaded = load_pages(raw_path, dpi=160 if fixture_path else 180)
        page_previews: dict[str, str] = {}
        for i, png in enumerate(loaded.pages):
            p = save_preview_png(png, job_dir / f"preview_p{i + 1}.png")
            page_previews[str(i + 1)] = str(p)
        first_preview = page_previews.get("1")
        job.page_total = len(loaded.pages)
        job.pages_done = 0
        job.page_index = 0
        job.page_previews = page_previews
        job.preview = first_preview
        job.warnings = list(loaded.warnings)
        save_job(job)

        if fixture_path is not None:
            result = recognize_from_fixture(fixture_path, pdf_path=pdf_for_units)
            # stamp all rows as page 1 for fixture mode unless multipage fixtures later
            result.rows = [
                r.model_copy(update={"source_page": r.source_page or 1})
                for r in result.rows
            ]
            result.page_total = len(loaded.pages)
            result.pages_done = len(loaded.pages)
        elif len(loaded.pages) > 1:

            def _progress(page_no, total, _page_result, _err):
                j = load_job(job_id)
                if not j:
                    return
                j.page_index = page_no
                j.page_total = total
                j.pages_done = page_no
                save_job(j)

            result = recognize_multipage(
                loaded.pages,
                pdf_path=pdf_for_units,
                work_dir=job_dir,
                on_page_done=_progress,
            )
        else:
            result = recognize_images(
                loaded.pages, pdf_path=pdf_for_units
            )
            result.rows = [
                r.model_copy(update={"source_page": 1}) for r in result.rows
            ]
            result.page_total = 1
            result.pages_done = 1

        job = load_job(job_id) or job
        job.status = "done"
        job.preview = first_preview
        job.page_previews = page_previews
        job.page_total = result.page_total or len(loaded.pages)
        job.pages_done = result.pages_done or len(loaded.pages)
        job.page_index = job.pages_done
        job.warnings = list(loaded.warnings) + list(result.warnings)
        job.metadata = result.graph.metadata.model_dump()
        job.graph = json.loads(result.graph.model_dump_json(by_alias=True))
        job.rows = [r.as_dict() for r in result.rows]
        job.error = None
        save_job(job)
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)
        if len(msg) > 500:
            msg = msg[:500] + "…"
        job = load_job(job_id) or job
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

    fixture_path = _resolve_fixture(use_fixture) if use_fixture else None

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
        fixture_path,
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
    # Do not embed access_token in URLs; client already holds it from upload response.
    payload: dict = {
        "job_id": job.job_id,
        "filename": job.filename,
        "status": job.status,
        "warnings": job.warnings,
        "error": job.error,
        "confirmed": job.confirmed,
        "page_total": job.page_total,
        "pages_done": job.pages_done,
        "page_index": job.page_index,
        "preview_url": (f"/api/jobs/{job.job_id}/preview?page=1" if job.preview or job.page_previews else None),
    }
    if job.status in {"running", "done", "confirmed"} and job.page_total:
        payload["progress"] = f"{job.pages_done}/{job.page_total}"
    if job.status == "done" or job.status == "confirmed":
        payload.update(
            {
                "metadata": job.metadata,
                "graph": job.graph,
                "rows": job.rows,
            }
        )
    if job.status == "confirmed" and job.excel:
        payload["download_url"] = f"/api/jobs/{job.job_id}/download"
    return payload


@app.get("/api/jobs/{job_id}/preview")
def job_preview(
    job_id: str,
    page: int = 1,
    access_token: str | None = None,
    x_flowpii_token: str | None = Header(default=None, alias="X-FlowPII-Token"),
):
    job = require_job(job_id, _token_from(access_token, x_flowpii_token))
    path = None
    if job.page_previews:
        path = job.page_previews.get(str(page))
    if not path and page == 1:
        path = job.preview
    if not path or not Path(path).is_file():
        raise HTTPException(404, detail=f"找不到第 {page} 頁預覽圖")
    return FileResponse(path, media_type="image/png")


@app.post("/api/jobs/{job_id}/confirm")
def job_confirm(job_id: str, body: ConfirmRequest):
    job = require_job(job_id, body.access_token)
    if job.status not in {"done", "confirmed"}:
        raise HTTPException(400, detail=f"工作尚未完成辨識（status={job.status}）")

    try:
        rows = [
            InventoryRow(
                process_id=r.process_id,
                process_name=r.process_name,
                file_name=r.file_name,
                file_type=r.file_type,
                source=r.source,
                target=r.target,
                transfer_method=r.transfer_method,
                source_page=r.source_page,
            )
            for r in body.rows
        ]
        graph = BifGraph.model_validate(body.graph) if body.graph else None
        meta = graph.metadata if graph else Metadata.model_validate(job.metadata or {})
    except ValidationError as exc:
        raise HTTPException(422, detail=exc.errors()) from exc

    out_path = OUT_DIR / f"{job_id}_inventory.xlsx"
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
        "download_url": f"/api/jobs/{job_id}/download",
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
