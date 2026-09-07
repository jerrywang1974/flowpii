"""Tests for unlabeled third-party（各單位）arrow auto-generation."""

from __future__ import annotations

import json
from pathlib import Path

from flowpii.expand import expand_graph
from flowpii.models import BifGraph
from flowpii.normalize import row_match_key
from flowpii.postprocess import normalize_graph
from flowpii.recognize import recognize_from_fixture
from flowpii.unlabeled_third_party import (
    augment_unlabeled_units,
    detect_unlabeled_third_party_arrow,
)

ROOT = Path(__file__).resolve().parents[1]
EXPENSE_PDF = ROOT / "samples" / "BIF圖 支單DEMO.pdf"
DEMO_PDF = ROOT / "samples" / "BIF圖 DEMO.pdf"
EXPENSE_FIXTURE = ROOT / "tests" / "fixtures" / "expense_graph.json"
EXPENSE_GOLDEN = ROOT / "tests" / "golden" / "expense.json"


def _expense_graph_without_units() -> BifGraph:
    """Simulate LLM output that missed the unlabeled 各單位 arrow."""
    g = BifGraph.model_validate(
        json.loads(EXPENSE_FIXTURE.read_text(encoding="utf-8"))
    )
    g.nodes = [n for n in g.nodes if n.id != "units"]
    g.edges = [e for e in g.edges if e.from_id != "units" and e.to_id != "units"]
    return normalize_graph(g)


def test_detect_unlabeled_on_expense_not_demo():
    assert detect_unlabeled_third_party_arrow(EXPENSE_PDF) is True
    # DEMO already has labeled 「各單位」text → must not treat as unlabeled
    assert detect_unlabeled_third_party_arrow(DEMO_PDF) is False


def test_augment_restores_expense_golden_units_rows():
    g = _expense_graph_without_units()
    assert len(expand_graph(g)) == 10
    g2, warnings = augment_unlabeled_units(g, pdf_path=EXPENSE_PDF)
    assert any("各單位" in w for w in warnings)
    assert any(n.name == "各單位" for n in g2.nodes)

    rows = expand_graph(g2)
    golden = json.loads(EXPENSE_GOLDEN.read_text(encoding="utf-8"))["rows"]
    actual = {row_match_key(r.as_dict()) for r in rows}
    expected = {row_match_key(r) for r in golden}
    assert actual == expected
    assert len(rows) == 18


def test_augment_skips_when_units_already_present():
    g = normalize_graph(
        BifGraph.model_validate(
            json.loads(EXPENSE_FIXTURE.read_text(encoding="utf-8"))
        )
    )
    before = len(g.edges)
    g2, warnings = augment_unlabeled_units(g, pdf_path=EXPENSE_PDF)
    assert warnings == []
    assert len(g2.edges) == before


def test_recognize_fixture_with_expense_pdf_augments():
    """Fixture without units + real 支單 PDF should yield 18 golden rows."""
    # Build a temp incomplete fixture? Use expense fixture which HAS units —
    # instead call augment path via recognize_from_fixture on incomplete graph
    # by writing a temporary fixture.
    incomplete = json.loads(EXPENSE_FIXTURE.read_text(encoding="utf-8"))
    incomplete["nodes"] = [n for n in incomplete["nodes"] if n["id"] != "units"]
    incomplete["edges"] = [
        e
        for e in incomplete["edges"]
        if e.get("from") != "units" and e.get("to") != "units"
    ]
    tmp = ROOT / "tmp" / "expense_incomplete.json"
    tmp.parent.mkdir(exist_ok=True)
    tmp.write_text(json.dumps(incomplete, ensure_ascii=False), encoding="utf-8")

    result = recognize_from_fixture(tmp, pdf_path=EXPENSE_PDF)
    assert len(result.rows) == 18
    assert any("各單位" in w for w in result.warnings)
    golden = json.loads(EXPENSE_GOLDEN.read_text(encoding="utf-8"))["rows"]
    actual = {row_match_key(r.as_dict()) for r in result.rows}
    expected = {row_match_key(r) for r in golden}
    assert actual == expected
