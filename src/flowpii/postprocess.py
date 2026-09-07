"""Normalize BifGraph after LLM extraction: directions, node labels, punctuation."""

from __future__ import annotations

from .models import Action, BifGraph, Carry, Edge, MediaType, Node
from .normalize import normalize_method


# Methods that typically go "into" a system vs "out of" a system
_INTO_SYSTEM = {"登打", "上傳", "寫入", "輸入"}
_OUT_OF_SYSTEM = {"列印", "下載", "讀取", "查詢"}


def _is_system(node: Node | None) -> bool:
    if not node:
        return False
    lane = str(node.lane)
    if lane.endswith("system") or lane == "system":
        return True
    name = (node.name or "").lower()
    return any(k in name for k in ("系統", "erp", "sharepoint", "onedrive", "serp"))


def _normalize_node(node: Node) -> Node:
    name = (node.name or "").strip()
    note = (node.note or "").strip() if node.note else ""
    note = note.replace("、", "，")

    lower = name.lower()
    if "sharepoint" in lower or name in {"SharePoint", "Sharepoint"}:
        # Golden style for 支單: 資安室sharepoint
        if (not note) or ("資安" in note):
            return node.model_copy(
                update={"name": "資安室sharepoint", "note": None, "note_style": "none"}
            )
        return node.model_copy(
            update={"name": f"{note}sharepoint" if note else "sharepoint", "note": None, "note_style": "none"}
        )

    if note:
        return node.model_copy(update={"note": note})
    return node


def _split_opposite_system_edge(edge: Edge, nodes: dict[str, Node]) -> list[Edge]:
    """Split 登打+列印 (or 上傳+下載) on one edge into correct directions."""
    into = [a for a in edge.actions if normalize_method(a.method) in {normalize_method(m) for m in _INTO_SYSTEM} or a.method.strip() in _INTO_SYSTEM]
    out = [a for a in edge.actions if normalize_method(a.method) in {normalize_method(m) for m in _OUT_OF_SYSTEM} or a.method.strip() in _OUT_OF_SYSTEM]
    other = [
        a
        for a in edge.actions
        if a not in into and a not in out
    ]

    if not (into and out):
        return [edge]

    src = nodes.get(edge.from_id)
    dst = nodes.get(edge.to_id)
    # Determine which end is the system
    if _is_system(dst) and not _is_system(src):
        human_id, system_id = edge.from_id, edge.to_id
    elif _is_system(src) and not _is_system(dst):
        human_id, system_id = edge.to_id, edge.from_id
    else:
        # fallback: treat from→to as human→system for "into" methods
        human_id, system_id = edge.from_id, edge.to_id

    def _carry_for(methods: list[Action], prefer_digital: bool) -> list[Carry]:
        carries: list[Carry] = []
        for c in edge.carries:
            media = list(c.media) or [MediaType.digital]
            if prefer_digital and MediaType.digital in media:
                carries.append(Carry(code=c.code, media=[MediaType.digital]))
            elif (not prefer_digital) and MediaType.paper in media:
                carries.append(Carry(code=c.code, media=[MediaType.paper]))
            else:
                carries.append(c)
        return carries or list(edge.carries)

    edges: list[Edge] = [
        Edge(
            **{
                "from": human_id,
                "to": system_id,
                "actions": into,
                "carries": _carry_for(into, prefer_digital=True),
                "bidirectional": False,
            }
        ),
        Edge(
            **{
                "from": system_id,
                "to": human_id,
                "actions": out,
                "carries": _carry_for(out, prefer_digital=False),
                "bidirectional": False,
            }
        ),
    ]
    if other:
        edges.append(
            Edge(
                **{
                    "from": edge.from_id,
                    "to": edge.to_id,
                    "actions": other,
                    "carries": edge.carries,
                    "bidirectional": edge.bidirectional,
                }
            )
        )
    return edges


def normalize_graph(graph: BifGraph) -> BifGraph:
    nodes = [_normalize_node(n) for n in graph.nodes]
    node_map = {n.id: n for n in nodes}

    new_edges: list[Edge] = []
    for edge in graph.edges:
        new_edges.extend(_split_opposite_system_edge(edge, node_map))

    return graph.model_copy(update={"nodes": nodes, "edges": new_edges})
