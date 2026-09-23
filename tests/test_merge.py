"""Unit tests for multipage merge by process_id."""

from __future__ import annotations

import json
from pathlib import Path

from flowpii.expand import expand_graph
from flowpii.merge import merge_page_results, prefix_graph_ids
from flowpii.models import BifGraph, InventoryRow
from flowpii.normalize import row_match_key
from flowpii.postprocess import normalize_graph

ROOT = Path(__file__).resolve().parents[1]


def _load_fixture(name: str) -> BifGraph:
    return normalize_graph(
        BifGraph.model_validate(
            json.loads((ROOT / "tests/fixtures" / name).read_text(encoding="utf-8"))
        )
    )


def test_prefix_graph_ids_unique():
    g = _load_fixture("demo_graph.json")
    g2 = prefix_graph_ids(g, "p1_")
    assert all(n.id.startswith("p1_") for n in g2.nodes)
    assert all(e.from_id.startswith("p1_") for e in g2.edges)


def test_merge_keeps_different_process_ids():
    demo = _load_fixture("demo_graph.json")
    expense = _load_fixture("expense_graph.json")
    rows1 = expand_graph(demo)
    rows2 = expand_graph(expense)
    _, merged, warnings = merge_page_results(
        [demo, expense], [rows1, rows2], page_numbers=[1, 2]
    )
    pids = {r.process_id for r in merged}
    assert "1-1-1" in pids
    assert "1-1-8" in pids
    assert len(merged) == len(rows1) + len(rows2)
    assert all(r.source_page in (1, 2) for r in merged)


def test_merge_dedupes_within_same_process_id():
    demo = _load_fixture("demo_graph.json")
    rows = expand_graph(demo)
    # duplicate the same page results
    _, merged, warnings = merge_page_results(
        [demo, demo], [rows, rows], page_numbers=[1, 2]
    )
    assert len(merged) == len(rows)
    assert any("去除重複" in w for w in warnings)
    # keys unique
    keys = [row_match_key(r.as_dict()) for r in merged]
    assert len(keys) == len(set(keys))


def test_inventory_row_page_in_as_dict():
    r = InventoryRow(
        process_id="1-1-1",
        process_name="x",
        file_name="002_a",
        file_type="2. 電子檔",
        source="A",
        target="B",
        transfer_method="Email",
        source_page=3,
    )
    d = r.as_dict()
    assert d["page"] == "3"
    assert "A" in d


def test_lion_pdf_loads_all_nine_pages():
    from flowpii.images import load_pages

    lion = ROOT / "samples" / "LION_管理本部-資安暨個資管理室_BIF_v1.4_20250919.pdf"
    assert lion.is_file()
    loaded = load_pages(lion, dpi=72, max_pages=50)
    assert loaded.total_in_file == 9
    assert len(loaded.pages) == 9
    assert loaded.truncated is False
