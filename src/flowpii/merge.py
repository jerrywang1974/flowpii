"""Merge per-page recognition results into one inventory sheet.

Keep rows grouped by process_id; dedupe only within the same process_id.
"""

from __future__ import annotations

from .models import Asset, BifGraph, Edge, InventoryRow, Node
from .normalize import row_match_key


def prefix_graph_ids(graph: BifGraph, prefix: str) -> BifGraph:
    """Avoid node id collisions across pages."""
    id_map = {n.id: f"{prefix}{n.id}" for n in graph.nodes}
    nodes = [n.model_copy(update={"id": id_map[n.id]}) for n in graph.nodes]
    edges: list[Edge] = []
    for e in graph.edges:
        edges.append(
            Edge(
                **{
                    "from": id_map.get(e.from_id, f"{prefix}{e.from_id}"),
                    "to": id_map.get(e.to_id, f"{prefix}{e.to_id}"),
                    "actions": e.actions,
                    "carries": e.carries,
                    "bidirectional": e.bidirectional,
                }
            )
        )
    return graph.model_copy(update={"nodes": nodes, "edges": edges})


def merge_page_results(
    page_graphs: list[BifGraph],
    page_rows: list[list[InventoryRow]],
    *,
    page_numbers: list[int] | None = None,
) -> tuple[BifGraph, list[InventoryRow], list[str]]:
    """Merge graphs/rows from multiple pages."""
    warnings: list[str] = []
    if not page_graphs:
        return BifGraph(), [], ["沒有可合併的分頁結果"]

    pages = page_numbers or list(range(1, len(page_graphs) + 1))
    merged_nodes: list[Node] = []
    merged_assets: list[Asset] = []
    merged_edges: list[Edge] = []
    asset_codes: set[str] = set()

    base_meta = page_graphs[0].metadata.model_copy()
    process_ids = sorted(
        {g.metadata.process_id for g in page_graphs if g.metadata.process_id}
    )
    if len(process_ids) > 1:
        shown = ",".join(process_ids[:3])
        if len(process_ids) > 3:
            shown += f"…(+{len(process_ids) - 3})"
        base_meta.process_id = shown
        base_meta.process_name = f"多頁合併（{len(process_ids)} 個作業）"

    for g, page_no in zip(page_graphs, pages):
        g2 = prefix_graph_ids(g, f"p{page_no}_")
        merged_nodes.extend(g2.nodes)
        merged_edges.extend(g2.edges)
        for a in g2.assets:
            if a.code not in asset_codes:
                asset_codes.add(a.code)
                merged_assets.append(a)

    tagged: list[InventoryRow] = []
    for rows, page_no in zip(page_rows, pages):
        for r in rows:
            tagged.append(r.model_copy(update={"source_page": page_no}))

    by_pid: dict[str, list[InventoryRow]] = {}
    for r in tagged:
        by_pid.setdefault(r.process_id or "", []).append(r)

    merged_rows: list[InventoryRow] = []
    for pid in sorted(by_pid.keys()):
        seen: set[tuple] = set()
        for r in by_pid[pid]:
            key = row_match_key(r.as_dict())
            if key in seen:
                continue
            seen.add(key)
            merged_rows.append(r)

    dropped = len(tagged) - len(merged_rows)
    if dropped:
        warnings.append(f"同檔案編號內去除重複傳輸列 {dropped} 筆")

    graph = BifGraph(
        metadata=base_meta,
        nodes=merged_nodes,
        assets=merged_assets,
        edges=merged_edges,
    )
    return graph, merged_rows, warnings
