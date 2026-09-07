from __future__ import annotations

import json
from pathlib import Path

import pytest

from flowpii.expand import expand_graph
from flowpii.models import BifGraph
from flowpii.normalize import row_match_key

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
GOLDEN = ROOT / "tests" / "golden"


def _load_golden(name: str) -> list[dict]:
    data = json.loads((GOLDEN / f"{name}.json").read_text(encoding="utf-8"))
    return data["rows"]


def _compare(actual_rows, expected_rows):
    actual_keys = sorted(row_match_key(r.as_dict()) for r in actual_rows)
    expected_keys = sorted(row_match_key(r) for r in expected_rows)
    missing = [k for k in expected_keys if k not in actual_keys]
    extra = [k for k in actual_keys if k not in expected_keys]
    return missing, extra, actual_keys, expected_keys


@pytest.mark.parametrize("name", ["demo", "expense"])
def test_fixture_matches_golden(name: str):
    graph = BifGraph.model_validate(
        json.loads((FIXTURES / f"{name}_graph.json").read_text(encoding="utf-8"))
    )
    rows = expand_graph(graph)
    expected = _load_golden(name)
    missing, extra, _, _ = _compare(rows, expected)
    assert not missing, f"missing rows: {missing[:5]}"
    assert not extra, f"extra rows: {extra[:5]}"
    assert len(rows) == len(expected)
