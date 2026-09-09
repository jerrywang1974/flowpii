"""Regression tests for review bugs: token auth, fixture path, confirm 422, CLI pdf_path."""

from __future__ import annotations

import json
import time
from pathlib import Path

from fastapi.testclient import TestClient

from flowpii.api import app
from flowpii.cli import main as cli_main
from flowpii.expand import expand_graph
from flowpii.recognize import recognize_from_fixture

ROOT = Path(__file__).resolve().parents[1]
DEMO_PDF = ROOT / "samples" / "BIF圖 DEMO.pdf"
EXPENSE_PDF = ROOT / "samples" / "BIF圖 支單DEMO.pdf"
INCOMPLETE_FIXTURE = ROOT / "tmp" / "expense_incomplete_cli.json"


def _wait_done(client: TestClient, job_id: str, token: str, timeout: float = 30.0):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        r = client.get(f"/api/jobs/{job_id}", params={"access_token": token})
        assert r.status_code == 200, r.text
        last = r.json()
        if last["status"] in {"done", "error", "confirmed"}:
            return last
        time.sleep(0.05)
    raise AssertionError(f"job not done: {last}")


def _start_demo_job(client: TestClient) -> tuple[str, str, dict]:
    with DEMO_PDF.open("rb") as f:
        r = client.post(
            "/api/recognize?use_fixture=demo_graph.json",
            files={"file": ("demo.pdf", f, "application/pdf")},
        )
    assert r.status_code == 200, r.text
    data = r.json()
    status = _wait_done(client, data["job_id"], data["access_token"])
    assert status["status"] == "done"
    return data["job_id"], data["access_token"], status


def test_missing_token_forbidden():
    client = TestClient(app)
    job_id, token, _ = _start_demo_job(client)
    assert client.get(f"/api/jobs/{job_id}").status_code == 403
    assert client.get(f"/api/jobs/{job_id}", params={"access_token": ""}).status_code == 403
    assert client.get(f"/api/jobs/{job_id}/preview").status_code == 403
    # valid token still works
    assert (
        client.get(f"/api/jobs/{job_id}", params={"access_token": token}).status_code
        == 200
    )


def test_status_does_not_echo_access_token_in_urls():
    client = TestClient(app)
    job_id, token, status = _start_demo_job(client)
    assert "access_token=" not in (status.get("preview_url") or "")
    # preview still works when client supplies token
    prev = client.get(
        status["preview_url"],
        params={"access_token": token},
    )
    assert prev.status_code == 200
    assert prev.headers["content-type"].startswith("image/")


def test_fixture_path_traversal_rejected():
    client = TestClient(app)
    with DEMO_PDF.open("rb") as f:
        r = client.post(
            "/api/recognize?use_fixture=../../.env.example",
            files={"file": ("demo.pdf", f, "application/pdf")},
        )
    assert r.status_code == 400
    assert "fixture" in r.json()["detail"].lower() or "無效" in r.json()["detail"]


def test_fixture_missing_basename_rejected():
    client = TestClient(app)
    with DEMO_PDF.open("rb") as f:
        r = client.post(
            "/api/recognize?use_fixture=no_such_graph.json",
            files={"file": ("demo.pdf", f, "application/pdf")},
        )
    assert r.status_code == 400


def test_confirm_invalid_file_type_returns_422():
    client = TestClient(app)
    job_id, token, status = _start_demo_job(client)
    bad_rows = [dict(status["rows"][0])]
    bad_rows[0]["H"] = "INVALID"
    r = client.post(
        f"/api/jobs/{job_id}/confirm",
        json={
            "rows": bad_rows,
            "graph": status["graph"],
            "access_token": token,
        },
    )
    assert r.status_code == 422


def test_cli_fixture_passes_pdf_for_units_augmentation(tmp_path: Path):
    incomplete = json.loads(
        (ROOT / "tests/fixtures/expense_graph.json").read_text(encoding="utf-8")
    )
    incomplete["nodes"] = [n for n in incomplete["nodes"] if n["id"] != "units"]
    incomplete["edges"] = [
        e
        for e in incomplete["edges"]
        if e.get("from") != "units" and e.get("to") != "units"
    ]
    INCOMPLETE_FIXTURE.parent.mkdir(exist_ok=True)
    INCOMPLETE_FIXTURE.write_text(
        json.dumps(incomplete, ensure_ascii=False), encoding="utf-8"
    )

    # Without PDF: no unlabeled detection → 10 rows
    bare = recognize_from_fixture(INCOMPLETE_FIXTURE)
    assert len(bare.rows) == 10

    out = tmp_path / "cli_out.xlsx"
    rc = cli_main(
        [
            "recognize",
            str(EXPENSE_PDF),
            "-o",
            str(out),
            "--fixture",
            str(INCOMPLETE_FIXTURE),
        ]
    )
    assert rc == 0
    assert out.exists()
    # With PDF via CLI, unlabeled units should restore 18 rows
    with_pdf = recognize_from_fixture(INCOMPLETE_FIXTURE, pdf_path=EXPENSE_PDF)
    assert len(with_pdf.rows) == 18
    assert len(expand_graph(with_pdf.graph)) == 18
