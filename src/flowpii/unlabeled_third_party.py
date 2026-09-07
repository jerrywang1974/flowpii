"""Detect unlabeled arrows into the Third-Party swimlane and auto-add 各單位 flows.

Company BIF charts sometimes draw a bidirectional connector into the purple
「第三方」band without an A:/C: text label (channels confirmed in interviews).
When that happens, inventory golden sheets still expect 各單位 transfer rows.

Defaults (overridable via env):
- methods: 親送 / Line / Fax / Email
- carry: assets on the hub that are tagged both (D) and (P), else hub's
  most common carry code with both media if present on the asset list
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import pymupdf

from .models import Action, BifGraph, Carry, Edge, Lane, MediaType, Node

# Default multi-channel set used when the arrow has no A: label
_DEFAULT_METHODS = os.getenv(
    "FLOWPII_UNITS_DEFAULT_METHODS",
    "親送:專人,Line:X,Fax:X,Email:X",
)


@dataclass
class TextBox:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2

    def inflate(self, pad: float) -> tuple[float, float, float, float]:
        return (self.x0 - pad, self.y0 - pad, self.x1 + pad, self.y1 + pad)


def _parse_default_actions() -> list[Action]:
    actions: list[Action] = []
    for part in _DEFAULT_METHODS.split(","):
        part = part.strip()
        if not part:
            continue
        if ":" in part:
            method, prot = part.split(":", 1)
        else:
            method, prot = part, None
        actions.append(Action(method=method.strip(), protection=prot.strip() if prot else None))
    return actions or [Action(method="親送", protection="專人")]


def _page_text_boxes(page: pymupdf.Page) -> list[TextBox]:
    boxes: list[TextBox] = []
    for block in page.get_text("dict").get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            text = "".join(s["text"] for s in line.get("spans", [])).strip()
            if not text:
                continue
            x0, y0, x1, y1 = line["bbox"]
            boxes.append(TextBox(text=text, x0=x0, y0=y0, x1=x1, y1=y1))
    return boxes


def _third_party_band(boxes: list[TextBox]) -> tuple[float, float, float, float] | None:
    """Return approximate third-party content band (x0,y0,x1,y1).

    Swimlanes are horizontal strips. 「第三方」header marks the top band; connectors
    into empty purple space often end just below that strip (or leftward under it).
    """
    header = next((b for b in boxes if "第三方" in b.text or "Third Party" in b.text), None)
    if not header:
        return None
    # Top band: from header top down toward first department node labels
    y0 = header.y0 - 5
    y1 = header.y1 + 70
    x0 = min(b.x0 for b in boxes) - 10
    x1 = max(b.x1 for b in boxes) + 10
    return (x0, y0, x1, y1)


def _line_segments(page: pymupdf.Page) -> list[tuple[float, float, float, float]]:
    segs: list[tuple[float, float, float, float]] = []
    for drawing in page.get_drawings():
        for item in drawing.get("items") or []:
            if item[0] != "l":
                continue
            p1, p2 = item[1], item[2]
            segs.append((p1.x, p1.y, p2.x, p2.y))
    return segs


def _point_in(x: float, y: float, box: tuple[float, float, float, float]) -> bool:
    x0, y0, x1, y1 = box
    return x0 <= x <= x1 and y0 <= y <= y1


def _segment_enters_band(
    seg: tuple[float, float, float, float],
    band: tuple[float, float, float, float],
) -> bool:
    x1, y1, x2, y2 = seg
    return _point_in(x1, y1, band) or _point_in(x2, y2, band)


def _near_a_label(x: float, y: float, boxes: list[TextBox], radius: float = 45.0) -> bool:
    for b in boxes:
        if not (b.text.startswith("A:") or b.text.startswith("C:")):
            continue
        cx, cy = b.cx, b.cy
        if abs(cx - x) <= radius and abs(cy - y) <= radius:
            return True
    return False


def _hub_candidate(graph: BifGraph, boxes: list[TextBox]) -> Node | None:
    """Prefer 資安暨個資管理室; else the department node with most incident edges."""
    by_name = {n.name: n for n in graph.nodes}
    if "資安暨個資管理室" in by_name:
        return by_name["資安暨個資管理室"]
    counts: dict[str, int] = {n.id: 0 for n in graph.nodes}
    for e in graph.edges:
        counts[e.from_id] = counts.get(e.from_id, 0) + 1
        counts[e.to_id] = counts.get(e.to_id, 0) + 1
    if not counts:
        # fall back to any department-looking text box matched into graph
        return graph.nodes[0] if graph.nodes else None
    hub_id = max(counts, key=counts.get)
    return next((n for n in graph.nodes if n.id == hub_id), None)


def _hub_box(hub: Node, boxes: list[TextBox]) -> TextBox | None:
    """Match the swimlane node label, not the page-header department string."""
    if not hub.name:
        return None
    # Prefer exact label (e.g. box text 「資安暨個資管理室」)
    exact = [b for b in boxes if b.text.strip() == hub.name]
    if exact:
        return max(exact, key=lambda b: (b.x1 - b.x0) * (b.y1 - b.y0))
    cands = [
        b
        for b in boxes
        if hub.name in b.text
        and not b.text.strip().startswith("管理本部")
        and "更新日期" not in b.text
    ]
    if cands:
        return min(cands, key=lambda b: abs(len(b.text) - len(hub.name)))
    return None


def _hub_shape_rect(
    page: pymupdf.Page, hub_box: TextBox
) -> tuple[float, float, float, float]:
    """Expand text label to its surrounding Visio rounded-rect if present."""
    label = (hub_box.x0, hub_box.y0, hub_box.x1, hub_box.y1)
    best = None
    best_area = None
    for drawing in page.get_drawings():
        r = drawing.get("rect")
        if not r:
            continue
        # rectangle-like shapes that fully contain the label
        if r.x0 <= label[0] and r.y0 <= label[1] and r.x1 >= label[2] and r.y1 >= label[3]:
            area = (r.x1 - r.x0) * (r.y1 - r.y0)
            # ignore page-sized backgrounds
            if area > (page.rect.width * page.rect.height) * 0.5:
                continue
            if best_area is None or area < best_area:
                best_area = area
                best = (r.x0, r.y0, r.x1, r.y1)
    if best:
        return best
    # Fallback: generous inflate around text
    return hub_box.inflate(30)


def _connector_from_hub_to_third_party(
    page: pymupdf.Page,
    boxes: list[TextBox],
    hub: Node,
) -> bool:
    """True if a vector segment leaves the hub box toward third-party band
    without an A:/C: label near the third-party-facing end.

    Also treats a horizontal stub leaving the hub *top* toward the left/right
    under the 「第三方」header (common Visio elbow that then continues elsewhere)
    as evidence of an unlabeled third-party link when that stub end has no A:.
    """
    band = _third_party_band(boxes)
    if not band:
        return False
    hub_box = _hub_box(hub, boxes)
    if not hub_box:
        return False
    hx0, hy0, hx1, hy1 = _hub_shape_rect(page, hub_box)
    # slight pad so line endpoints on the stroke still count
    hx0, hy0, hx1, hy1 = hx0 - 2, hy0 - 2, hx1 + 2, hy1 + 2
    segs = _line_segments(page)
    tp_header = next((b for b in boxes if "第三方" in b.text or "Third Party" in b.text), None)

    for x1, y1, x2, y2 in segs:
        ends = [(x1, y1), (x2, y2)]
        in_hub = [_point_in(x, y, (hx0, hy0, hx1, hy1)) for x, y in ends]
        in_band = [_point_in(x, y, band) for x, y in ends]
        # One end on hub, other in third-party band
        if any(in_hub) and any(in_band) and not (all(in_hub) or all(in_band)):
            tp_pt = ends[0] if in_band[0] else ends[1]
            if not _near_a_label(tp_pt[0], tp_pt[1], boxes, radius=50):
                return True
        # Elbow stub: from hub toward third-party header x-range near hub top
        if any(in_hub) and not all(in_hub):
            other = ends[1] if in_hub[0] else ends[0]
            ox, oy = other
            near_top = hy0 - 5 <= oy <= hy0 + 30
            under_tp_header = False
            if tp_header:
                under_tp_header = tp_header.x0 - 30 <= ox <= tp_header.x1 + 50
            if near_top and under_tp_header and not _near_a_label(ox, oy, boxes, radius=55):
                return True
    return False


def _has_units(graph: BifGraph) -> bool:
    for n in graph.nodes:
        if "各單位" in (n.name or ""):
            return True
    return False


def _infer_carries(graph: BifGraph, hub: Node) -> list[Carry]:
    both = [
        a
        for a in graph.assets
        if MediaType.digital in a.media and MediaType.paper in a.media
    ]
    if both:
        # Prefer codes already used on edges from/to hub
        hub_codes: set[str] = set()
        for e in graph.edges:
            if e.from_id == hub.id or e.to_id == hub.id:
                for c in e.carries:
                    hub_codes.add(c.code)
        preferred = [a for a in both if a.code in hub_codes] or both
        return [
            Carry(code=a.code, media=[MediaType.digital, MediaType.paper])
            for a in preferred
        ]

    # Fallback: any asset mentioned on hub edges, expand media from asset list
    codes: list[str] = []
    for e in graph.edges:
        if e.from_id == hub.id or e.to_id == hub.id:
            for c in e.carries:
                if c.code not in codes:
                    codes.append(c.code)
    asset_map = graph.asset_map()
    carries: list[Carry] = []
    for code in codes:
        asset = asset_map.get(code)
        media = list(asset.media) if asset else [MediaType.digital, MediaType.paper]
        if MediaType.digital in media and MediaType.paper in media:
            carries.append(Carry(code=code, media=[MediaType.digital, MediaType.paper]))
    if carries:
        return carries
    # Last resort
    if graph.assets:
        a = graph.assets[0]
        return [Carry(code=a.code, media=list(a.media) or [MediaType.digital])]
    return [Carry(code="007", media=[MediaType.digital, MediaType.paper])]


def detect_unlabeled_third_party_arrow(pdf_path: str | Path) -> bool:
    """Public helper for tests: whether the PDF looks like it has an unlabeled TP arrow."""
    path = str(pdf_path)
    doc = pymupdf.open(path)
    try:
        page = doc[0]
        boxes = _page_text_boxes(page)
        if any("各單位" in b.text for b in boxes):
            return False  # already labeled on the chart
        if not any("第三方" in b.text or "Third Party" in b.text for b in boxes):
            return False
        if not any(b.text.strip() == "資安暨個資管理室" for b in boxes):
            return False
        fake_hub = Node(id="hub", name="資安暨個資管理室", lane=Lane.department)
        return _connector_from_hub_to_third_party(page, boxes, fake_hub)
    finally:
        doc.close()


def augment_unlabeled_units(
    graph: BifGraph,
    *,
    pdf_path: str | Path | None = None,
    force: bool = False,
) -> tuple[BifGraph, list[str]]:
    """Add 各單位 bidirectional multi-channel edge when unlabeled TP arrow detected.

    Returns (possibly updated graph, warning messages).
    """
    warnings: list[str] = []
    if _has_units(graph) and not force:
        return graph, warnings

    detected = False
    hub: Node | None = None

    if pdf_path:
        try:
            doc = pymupdf.open(pdf_path)
            try:
                page = doc[0]
                boxes = _page_text_boxes(page)
                if any("各單位" in b.text for b in boxes) and not force:
                    return graph, warnings
                hub = _hub_candidate(graph, boxes)
                if hub and _connector_from_hub_to_third_party(page, boxes, hub):
                    detected = True
            finally:
                doc.close()
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"未標註第三方箭頭偵測略過（PDF 解析失敗：{exc}）")

    # Graph-only fallback: third-party lane unused + hub exists + force/heuristic
    if not detected and force:
        hub = hub or _hub_candidate(graph, [])
        detected = hub is not None

    if not detected or hub is None:
        # Soft heuristic without PDF: if assets have D+P and no 各單位, do not invent
        # unless pdf_path detection succeeded.
        return graph, warnings

    units = Node(
        id="auto_units",
        name="各單位",
        lane=Lane.third_party,
        note=None,
        note_style="none",
    )
    actions = _parse_default_actions()
    carries = _infer_carries(graph, hub)
    edge = Edge(
        **{
            "from": hub.id,
            "to": units.id,
            "actions": actions,
            "carries": carries,
            "bidirectional": True,
        }
    )

    nodes = list(graph.nodes) + [units]
    edges = list(graph.edges) + [edge]
    methods = "、".join(a.method for a in actions)
    codes = "、".join(c.code for c in carries)
    warnings.append(
        "偵測到通往「第三方」泳道且無 A:/C: 標註的連線，"
        f"已自動產生「各單位」雙向傳輸列（預設 {methods}；資料 {codes}）。請人工確認。"
    )
    return graph.model_copy(update={"nodes": nodes, "edges": edges}), warnings
