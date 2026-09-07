from __future__ import annotations

import base64
import json
import os
import re
from pathlib import Path

from openai import OpenAI

from .expand import expand_graph
from .images import load_pages_as_png_bytes
from .models import BifGraph, RecognizeResult

PROMPT_PATH = Path(__file__).parent / "prompts" / "bif_extract.txt"
DEFAULT_MODEL = os.getenv("FLOWPII_MODEL", "grok-4.5")


def _load_api_key() -> str:
    key = os.getenv("XAI_API_KEY", "").strip()
    if key:
        return key
    # Fallback: project .env
    env_path = Path(__file__).resolve().parents[2] / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("XAI_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("未設定 XAI_API_KEY（請在環境變數或專案 .env 提供）")


def _client() -> OpenAI:
    # Complex BIF diagrams (e.g. Korea) can take several minutes.
    import httpx

    timeout = httpx.Timeout(600.0, connect=30.0)
    return OpenAI(
        api_key=_load_api_key(),
        base_url="https://api.x.ai/v1",
        timeout=timeout,
    )


def _extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]*\}", text)
        if not m:
            raise
        return json.loads(m.group(0))


def recognize_images(
    png_pages: list[bytes],
    *,
    model: str | None = None,
) -> RecognizeResult:
    system_prompt = PROMPT_PATH.read_text(encoding="utf-8")
    content: list[dict] = []
    for i, png in enumerate(png_pages):
        b64 = base64.b64encode(png).decode("ascii")
        content.append(
            {
                "type": "input_image",
                "image_url": f"data:image/png;base64,{b64}",
                "detail": "high",
            }
        )
        content.append(
            {
                "type": "input_text",
                "text": f"這是 BIF 圖第 {i + 1}/{len(png_pages)} 頁。",
            }
        )
    content.append(
        {
            "type": "input_text",
            "text": "請依系統說明輸出完整 JSON。",
        }
    )

    client = _client()
    resp = client.responses.create(
        model=model or DEFAULT_MODEL,
        input=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": content},
        ],
    )
    raw = getattr(resp, "output_text", None) or ""
    if not raw:
        # Fallback walk
        raw = str(resp)

    data = _extract_json(raw)
    graph = BifGraph.model_validate(data)
    rows = expand_graph(graph)
    warnings: list[str] = []
    if not graph.edges:
        warnings.append("未識別到任何傳輸邊，請人工補齊")
    if not rows:
        warnings.append("展開後沒有清冊列")
    return RecognizeResult(graph=graph, rows=rows, warnings=warnings)


def recognize_file(
    path: str | Path,
    *,
    model: str | None = None,
    dpi: int = 200,
) -> RecognizeResult:
    pages = load_pages_as_png_bytes(path, dpi=dpi)
    return recognize_images(pages, model=model)


def recognize_from_fixture(fixture_path: str | Path) -> RecognizeResult:
    data = json.loads(Path(fixture_path).read_text(encoding="utf-8"))
    graph = BifGraph.model_validate(data)
    return RecognizeResult(graph=graph, rows=expand_graph(graph), warnings=[])
