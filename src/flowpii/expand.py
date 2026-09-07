from __future__ import annotations

from .models import (
    BifGraph,
    Carry,
    Edge,
    InventoryRow,
    MediaType,
    Node,
)
from .symbols import (
    DIGITAL_METHODS,
    FILE_TYPE_DIGITAL,
    FILE_TYPE_PAPER,
    PAPER_METHODS,
)


def _method_prefers_paper(method: str) -> bool | None:
    from .normalize import normalize_method

    key = normalize_method(method)
    raw = method.strip()
    if raw in PAPER_METHODS or key in {normalize_method(m) for m in PAPER_METHODS}:
        return True
    if raw in DIGITAL_METHODS or key in {normalize_method(m) for m in DIGITAL_METHODS}:
        return False
    # Heuristics
    if key in {"fax", "列印", "親送", "親取", "內郵", "臨櫃", "郵寄", "快遞"}:
        return True
    if key in {"email", "line", "上傳", "下載", "登打", "系統拋轉", "ftp"}:
        return False
    return None


def resolve_media_for_action(method: str, carry: Carry) -> list[MediaType]:
    """Pick which media types apply for this action given C: media tags."""
    available = list(carry.media) or [MediaType.digital]
    if len(available) == 1:
        return available

    prefer_paper = _method_prefers_paper(method)
    if prefer_paper is True and MediaType.paper in available:
        return [MediaType.paper]
    if prefer_paper is False and MediaType.digital in available:
        return [MediaType.digital]
    # Ambiguous: emit all tagged media
    return available


def media_to_file_type(media: MediaType) -> str:
    return FILE_TYPE_PAPER if media == MediaType.paper else FILE_TYPE_DIGITAL


def _node_label(nodes: dict[str, Node], node_id: str) -> str:
    node = nodes.get(node_id)
    if not node:
        return node_id
    return node.display_name()


def _asset_name(graph: BifGraph, code: str) -> str:
    asset = graph.asset_map().get(code)
    if asset:
        return asset.inventory_name()
    return code


def expand_edge(
    graph: BifGraph,
    edge: Edge,
    *,
    reverse: bool = False,
) -> list[InventoryRow]:
    nodes = graph.node_map()
    meta = graph.metadata
    src_id = edge.to_id if reverse else edge.from_id
    dst_id = edge.from_id if reverse else edge.to_id
    source = _node_label(nodes, src_id)
    target = _node_label(nodes, dst_id)

    rows: list[InventoryRow] = []
    for action in edge.actions:
        for carry in edge.carries:
            for media in resolve_media_for_action(action.method, carry):
                rows.append(
                    InventoryRow(
                        process_id=meta.process_id,
                        process_name=meta.process_name,
                        file_name=_asset_name(graph, carry.code),
                        file_type=media_to_file_type(media),  # type: ignore[arg-type]
                        source=source,
                        target=target,
                        transfer_method=action.method,
                    )
                )
    return rows


def expand_graph(graph: BifGraph) -> list[InventoryRow]:
    rows: list[InventoryRow] = []
    for edge in graph.edges:
        rows.extend(expand_edge(graph, edge, reverse=False))
        if edge.bidirectional:
            rows.extend(expand_edge(graph, edge, reverse=True))
    return rows
