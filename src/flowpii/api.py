from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .excel_writer import write_inventory_excel
from .images import load_pages_as_png_bytes, save_preview_png
from .models import BifGraph, InventoryRow
from .recognize import recognize_from_fixture, recognize_images

ROOT = Path(__file__).resolve().parents[2]
UPLOAD_DIR = ROOT / "uploads"
OUT_DIR = ROOT / "out"
STATIC_DIR = ROOT / "static"
TEMPLATES_DIR = ROOT / "templates"

UPLOAD_DIR.mkdir(exist_ok=True)
OUT_DIR.mkdir(exist_ok=True)

# In-memory job store (single-process MVP)
JOBS: dict[str, dict] = {}

app = FastAPI(title="FlowPII", version="0.1.0")
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


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    index_path = TEMPLATES_DIR / "index.html"
    if not index_path.exists():
        return HTMLResponse("<h1>FlowPII</h1><p>templates/index.html missing</p>")
    return HTMLResponse(index_path.read_text(encoding="utf-8"))


@app.get("/api/health")
def health():
    return {"ok": True, "version": "0.1.0"}


@app.post("/api/recognize")
async def api_recognize(
    file: UploadFile = File(...),
    use_fixture: str | None = None,
):
    suffix = Path(file.filename or "upload.bin").suffix.lower()
    if suffix not in {".pdf", ".png", ".jpg", ".jpeg"}:
        raise HTTPException(400, detail="僅支援 PDF / JPG / PNG")

    job_id = uuid.uuid4().hex[:12]
    job_dir = UPLOAD_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    raw_path = job_dir / f"input{suffix}"
    content = await file.read()
    raw_path.write_bytes(content)

    try:
        if use_fixture:
            fixture = ROOT / "tests" / "fixtures" / use_fixture
            if not fixture.exists():
                raise HTTPException(400, detail=f"fixture 不存在: {use_fixture}")
            result = recognize_from_fixture(fixture)
            # still make a preview if possible
            try:
                pages = load_pages_as_png_bytes(raw_path, dpi=120)
                preview = save_preview_png(pages[0], job_dir / "preview.png")
            except Exception:
                preview = None
        else:
            pages = load_pages_as_png_bytes(raw_path, dpi=180)
            preview = save_preview_png(pages[0], job_dir / "preview.png")
            result = recognize_images(pages)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        # Keep message readable in the Web UI status line
        msg = str(exc)
        if len(msg) > 500:
            msg = msg[:500] + "…"
        raise HTTPException(500, detail=f"辨識失敗: {msg}") from exc

    JOBS[job_id] = {
        "result": result,
        "confirmed": False,
        "preview": str(preview) if preview else None,
        "filename": file.filename,
    }

    return {
        "job_id": job_id,
        "filename": file.filename,
        "preview_url": f"/api/jobs/{job_id}/preview" if preview else None,
        "warnings": result.warnings,
        "metadata": result.graph.metadata.model_dump(),
        "graph": json.loads(result.graph.model_dump_json(by_alias=True)),
        "rows": [r.as_dict() for r in result.rows],
    }


@app.get("/api/jobs/{job_id}/preview")
def job_preview(job_id: str):
    job = JOBS.get(job_id)
    if not job or not job.get("preview"):
        raise HTTPException(404, detail="找不到預覽圖")
    return FileResponse(job["preview"], media_type="image/png")


@app.post("/api/jobs/{job_id}/confirm")
def job_confirm(job_id: str, body: ConfirmRequest):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, detail="找不到工作")

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
    graph = (
        BifGraph.model_validate(body.graph)
        if body.graph
        else job["result"].graph
    )
    out_path = OUT_DIR / f"{job_id}_inventory.xlsx"
    write_inventory_excel(rows, out_path, graph=graph)
    job["confirmed"] = True
    job["rows"] = rows
    job["excel"] = str(out_path)
    return {
        "ok": True,
        "download_url": f"/api/jobs/{job_id}/download",
        "row_count": len(rows),
    }


@app.get("/api/jobs/{job_id}/download")
def job_download(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, detail="找不到工作")
    if not job.get("confirmed") or not job.get("excel"):
        raise HTTPException(400, detail="請先確認覆核結果後再下載")
    return FileResponse(
        job["excel"],
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename="個人資料盤點清冊.xlsx",
    )


def create_app() -> FastAPI:
    return app
