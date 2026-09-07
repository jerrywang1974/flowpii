from __future__ import annotations

import base64
import json
import os
import re
from pathlib import Path

from openai import OpenAI

from .expand import expand_graph
from .images import detail_crops, load_pages_as_png_bytes
from .models import BifGraph, RecognizeResult
from .postprocess import normalize_graph
from .unlabeled_third_party import augment_unlabeled_units

PROMPT_PATH = Path(__file__).parent / "prompts" / "bif_extract.txt"
REPAIR_PATH = Path(__file__).parent / "prompts" / "bif_repair.txt"
DEFAULT_MODEL = os.getenv("FLOWPII_MODEL", "grok-4.5")


def _load_api_key() -> str:
    key = os.getenv("XAI_API_KEY", "").strip()
    if key:
        return key
    env_path = Path(__file__).resolve().parents[2] / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("XAI_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("未設定 XAI_API_KEY（請在環境變數或專案 .env 提供）")


def _client() -> OpenAI:
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


def _image_contents(png_pages: list[bytes], *, include_crops: bool = True) -> list[dict]:
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
                "text": f"這是 BIF 圖第 {i + 1}/{len(png_pages)} 頁（全圖）。",
            }
        )
        if include_crops:
            for label, crop in detail_crops(png):
                cb64 = base64.b64encode(crop).decode("ascii")
                content.append(
                    {
                        "type": "input_image",
                        "image_url": f"data:image/png;base64,{cb64}",
                        "detail": "high",
                    }
                )
                content.append(
                    {
                        "type": "input_text",
                        "text": (
                            f"局部放大：{label}。請逐字讀取此區所有 A:/C: 標註；"
                            "若箭頭進入「第三方」泳道且沒有框名，端點命名為「各單位」。"
                            "特別注意形如 A:親送(專人),Line(X),Fax(X),Email(X) 的多通道標註，"
                            "必須拆成多個 actions，不可只保留親送。"
                        ),
                    }
                )
    return content


def _needs_repair(graph: BifGraph) -> bool:
    names = " ".join(n.name or "" for n in graph.nodes)
    if "各單位" not in names:
        return True
    node_map = graph.node_map()
    for edge in graph.edges:
        a = node_map.get(edge.from_id)
        b = node_map.get(edge.to_id)
        if (a and "各單位" in (a.name or "")) or (b and "各單位" in (b.name or "")):
            # Ensure multi-channel actions if bidirectional to 各單位
            if edge.bidirectional and len(edge.actions) < 2:
                return True
            return False
    return True


def _call_extract(client: OpenAI, model: str, png_pages: list[bytes]) -> BifGraph:
    system_prompt = PROMPT_PATH.read_text(encoding="utf-8")
    content = _image_contents(png_pages)
    content.append({"type": "input_text", "text": "請依系統說明輸出完整 JSON。"})
    resp = client.responses.create(
        model=model,
        input=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": content},
        ],
    )
    raw = getattr(resp, "output_text", None) or str(resp)
    return normalize_graph(BifGraph.model_validate(_extract_json(raw)))


def _call_repair(
    client: OpenAI, model: str, png_pages: list[bytes], graph: BifGraph
) -> BifGraph:
    repair_prompt = REPAIR_PATH.read_text(encoding="utf-8")
    content = _image_contents(png_pages)
    content.append(
        {
            "type": "input_text",
            "text": "第一輪 JSON 如下，請補漏後輸出完整修正 JSON：\n"
            + graph.model_dump_json(by_alias=True),
        }
    )
    resp = client.responses.create(
        model=model,
        input=[
            {"role": "system", "content": repair_prompt},
            {"role": "user", "content": content},
        ],
    )
    raw = getattr(resp, "output_text", None) or str(resp)
    return normalize_graph(BifGraph.model_validate(_extract_json(raw)))


def _finalize_graph(
    graph: BifGraph,
    *,
    pdf_path: str | Path | None = None,
    extra_warnings: list[str] | None = None,
) -> RecognizeResult:
    """Normalize → auto-add unlabeled 各單位 → expand rows."""
    warnings = list(extra_warnings or [])
    graph, unit_warnings = augment_unlabeled_units(graph, pdf_path=pdf_path)
    warnings.extend(unit_warnings)

    rows = expand_graph(graph)
    if not graph.edges:
        warnings.append("未識別到任何傳輸邊，請人工補齊")
    if not rows:
        warnings.append("展開後沒有清冊列")

    node_map = graph.node_map()

    def _touches_units(edge) -> bool:
        a = node_map.get(edge.from_id)
        b = node_map.get(edge.to_id)
        return bool(
            (a and "各單位" in (a.name or ""))
            or (b and "各單位" in (b.name or ""))
        )

    if any("各單位" in (n.name or "") for n in graph.nodes):
        if not any(_touches_units(e) for e in graph.edges):
            warnings.append("偵測到「各單位」節點但缺少相關傳輸邊，請人工補齊雙向通道")
    elif not unit_warnings:
        warnings.append("未偵測到「各單位」；若圖上有未標註第三方箭頭請回報以便調整偵測")

    return RecognizeResult(graph=graph, rows=rows, warnings=warnings)


def recognize_images(
    png_pages: list[bytes],
    *,
    model: str | None = None,
    repair: bool = True,
    pdf_path: str | Path | None = None,
) -> RecognizeResult:
    model_name = model or DEFAULT_MODEL
    client = _client()
    graph = _call_extract(client, model_name, png_pages)
    warnings: list[str] = []

    if repair and _needs_repair(graph):
        try:
            repaired = _call_repair(client, model_name, png_pages, graph)
            if len(repaired.edges) >= len(graph.edges) or len(repaired.nodes) >= len(
                graph.nodes
            ):
                graph = repaired
                warnings.append("已執行第二輪補漏辨識（方向／各單位／多通道）")
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"第二輪補漏失敗，沿用第一輪結果：{exc}")

    return _finalize_graph(graph, pdf_path=pdf_path, extra_warnings=warnings)


def recognize_file(
    path: str | Path,
    *,
    model: str | None = None,
    dpi: int = 200,
) -> RecognizeResult:
    path = Path(path)
    pages = load_pages_as_png_bytes(path, dpi=dpi)
    pdf = path if path.suffix.lower() == ".pdf" else None
    return recognize_images(pages, model=model, pdf_path=pdf)


def recognize_from_fixture(
    fixture_path: str | Path,
    *,
    pdf_path: str | Path | None = None,
) -> RecognizeResult:
    data = json.loads(Path(fixture_path).read_text(encoding="utf-8"))
    graph = normalize_graph(BifGraph.model_validate(data))
    return _finalize_graph(graph, pdf_path=pdf_path)
