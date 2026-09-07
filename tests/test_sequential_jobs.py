"""Sequential / consecutive recognition job isolation.

Scenario: a user finishes (or starts) recognizing file A, then uploads file B
without refreshing the page. Backend must keep both jobs isolated; confirming
or downloading A must not affect B and vice versa.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from flowpii.api import app
from flowpii.jobs import UPLOAD_DIR, load_job

ROOT = Path(__file__).resolve().parents[1]
DEMO_PDF = ROOT / "samples" / "BIF圖 DEMO.pdf"
EXPENSE_PDF = ROOT / "samples" / "BIF圖 支單DEMO.pdf"


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
    pytest.fail(f"job {job_id} not done in time: {last}")


@pytest.fixture()
def client():
    return TestClient(app)


def test_sequential_uploads_create_distinct_jobs(client: TestClient):
    """Uploading A then B must yield two different job_ids and tokens."""
    with DEMO_PDF.open("rb") as f:
        r1 = client.post(
            "/api/recognize?use_fixture=demo_graph.json",
            files={"file": ("demo.pdf", f, "application/pdf")},
        )
    assert r1.status_code == 200
    j1 = r1.json()

    with EXPENSE_PDF.open("rb") as f:
        r2 = client.post(
            "/api/recognize?use_fixture=expense_graph.json",
            files={"file": ("expense.pdf", f, "application/pdf")},
        )
    assert r2.status_code == 200
    j2 = r2.json()

    assert j1["job_id"] != j2["job_id"]
    assert j1["access_token"] != j2["access_token"]
    assert (UPLOAD_DIR / j1["job_id"]).is_dir()
    assert (UPLOAD_DIR / j2["job_id"]).is_dir()


def test_sequential_results_do_not_overwrite_each_other(client: TestClient):
    """After both jobs finish, each token only sees its own rows / process id."""
    with DEMO_PDF.open("rb") as f:
        a = client.post(
            "/api/recognize?use_fixture=demo_graph.json",
            files={"file": ("demo.pdf", f, "application/pdf")},
        ).json()
    with EXPENSE_PDF.open("rb") as f:
        b = client.post(
            "/api/recognize?use_fixture=expense_graph.json",
            files={"file": ("expense.pdf", f, "application/pdf")},
        ).json()

    sa = _wait_done(client, a["job_id"], a["access_token"])
    sb = _wait_done(client, b["job_id"], b["access_token"])
    assert sa["status"] == "done"
    assert sb["status"] == "done"

    # DEMO fixture → 資安演練；expense fixture → 支單
    assert sa["metadata"]["process_id"] == "1-1-1"
    assert sb["metadata"]["process_id"] == "1-1-8"
    assert len(sa["rows"]) == 4
    assert len(sb["rows"]) == 18
    assert sa["rows"][0]["G"] != sb["rows"][0]["G"]

    # Cross-token access must be denied
    assert (
        client.get(
            f"/api/jobs/{a['job_id']}",
            params={"access_token": b["access_token"]},
        ).status_code
        == 403
    )


def test_confirm_first_job_after_second_started(client: TestClient):
    """User may still confirm/download job A after starting job B."""
    with DEMO_PDF.open("rb") as f:
        a = client.post(
            "/api/recognize?use_fixture=demo_graph.json",
            files={"file": ("demo.pdf", f, "application/pdf")},
        ).json()
    sa = _wait_done(client, a["job_id"], a["access_token"])

    with EXPENSE_PDF.open("rb") as f:
        b = client.post(
            "/api/recognize?use_fixture=expense_graph.json",
            files={"file": ("expense.pdf", f, "application/pdf")},
        ).json()
    # Do not wait for B — confirm A immediately while B may still be running
    r = client.post(
        f"/api/jobs/{a['job_id']}/confirm",
        json={
            "rows": sa["rows"],
            "graph": sa["graph"],
            "access_token": a["access_token"],
        },
    )
    assert r.status_code == 200, r.text
    dl = client.get(r.json()["download_url"])
    assert dl.status_code == 200
    assert dl.content[:2] == b"PK"

    # B remains intact and independent
    sb = _wait_done(client, b["job_id"], b["access_token"])
    assert sb["status"] == "done"
    assert sb["metadata"]["process_id"] == "1-1-8"
    job_a = load_job(a["job_id"])
    job_b = load_job(b["job_id"])
    assert job_a and job_a.confirmed is True
    assert job_b and job_b.confirmed is False
    assert job_a.excel and Path(job_a.excel).exists()
