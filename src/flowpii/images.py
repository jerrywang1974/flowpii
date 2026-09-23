from __future__ import annotations

import io
import os
from dataclasses import dataclass
from pathlib import Path

import pymupdf
from PIL import Image


SUPPORTED_IMAGE = {".jpg", ".jpeg", ".png"}
SUPPORTED_PDF = {".pdf"}
SUPPORTED_PPT = {".ppt", ".pptx"}

DEFAULT_MAX_PAGES = int(os.getenv("FLOWPII_MAX_PAGES", "50"))


@dataclass
class LoadedPages:
    pages: list[bytes]
    total_in_file: int
    truncated: bool

    @property
    def warnings(self) -> list[str]:
        if not self.truncated:
            return []
        return [
            f"檔案共 {self.total_in_file} 頁，本次只處理前 {len(self.pages)} 頁"
            f"（上限 FLOWPII_MAX_PAGES={DEFAULT_MAX_PAGES}）"
        ]


def load_pages_as_png_bytes(
    path: str | Path,
    *,
    dpi: int = 200,
    max_pages: int | None = None,
) -> list[bytes]:
    """Rasterize PDF or load image files to PNG bytes list."""
    return load_pages(path, dpi=dpi, max_pages=max_pages).pages


def load_pages(
    path: str | Path,
    *,
    dpi: int = 200,
    max_pages: int | None = None,
) -> LoadedPages:
    path = Path(path)
    limit = DEFAULT_MAX_PAGES if max_pages is None else max_pages
    suffix = path.suffix.lower()
    if suffix in SUPPORTED_PDF:
        return _pdf_to_pngs(path, dpi=dpi, max_pages=limit)
    if suffix in SUPPORTED_IMAGE:
        return LoadedPages(pages=[_image_to_png_bytes(path)], total_in_file=1, truncated=False)
    if suffix in SUPPORTED_PPT:
        raise ValueError(
            "PPT/PPTX 需先匯出為 PDF 或 PNG 再上傳（第一版僅接受 PDF/JPG/PNG）"
        )
    raise ValueError(f"不支援的檔案格式：{suffix}")


def extract_single_page_pdf(src: str | Path, page_index: int, dest: str | Path) -> Path:
    """Write a one-page PDF (0-based page_index) for per-page unlabeled detection."""
    src = Path(src)
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open(src)
    try:
        if page_index < 0 or page_index >= doc.page_count:
            raise ValueError(f"頁碼超出範圍: {page_index + 1}")
        out = pymupdf.open()
        out.insert_pdf(doc, from_page=page_index, to_page=page_index)
        out.save(dest)
        out.close()
        return dest
    finally:
        doc.close()


def _pdf_to_pngs(path: Path, *, dpi: int, max_pages: int) -> LoadedPages:
    doc = pymupdf.open(path)
    try:
        total = doc.page_count
        zoom = dpi / 72.0
        matrix = pymupdf.Matrix(zoom, zoom)
        pages: list[bytes] = []
        for i, page in enumerate(doc):
            if i >= max_pages:
                break
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            pages.append(pix.tobytes("png"))
        if not pages:
            raise ValueError("PDF 沒有可讀取的頁面")
        return LoadedPages(
            pages=pages,
            total_in_file=total,
            truncated=total > len(pages),
        )
    finally:
        doc.close()


def _image_to_png_bytes(path: Path) -> bytes:
    with Image.open(path) as im:
        im = im.convert("RGB")
        buf = io.BytesIO()
        im.save(buf, format="PNG")
        return buf.getvalue()


def save_preview_png(png_bytes: bytes, dest: str | Path) -> Path:
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(png_bytes)
    return dest


def detail_crops(png_bytes: bytes) -> list[tuple[str, bytes]]:
    """Return labeled detail crops to help the model read dense A:/C: labels."""
    with Image.open(io.BytesIO(png_bytes)) as im:
        im = im.convert("RGB")
        w, h = im.size
        regions = [
            ("top-center（常見：部門↔第三方雙向多通道 A:）", (0.22, 0.08, 0.72, 0.48)),
            ("top-right（第三方泳道）", (0.50, 0.05, 0.98, 0.40)),
            ("middle-systems（系統列：ERP/Sharepoint）", (0.00, 0.35, 0.55, 0.75)),
        ]
        out: list[tuple[str, bytes]] = []
        for label, (x0, y0, x1, y1) in regions:
            crop = im.crop((int(w * x0), int(h * y0), int(w * x1), int(h * y1)))
            buf = io.BytesIO()
            crop.save(buf, format="PNG")
            out.append((label, buf.getvalue()))
        return out
