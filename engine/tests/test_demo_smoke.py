from __future__ import annotations

import json
import time
from pathlib import Path

from fastapi.testclient import TestClient

from windshadow_engine.main import app


def test_demo_smoke(tmp_path: Path):
    demo_project = Path(__file__).resolve().parents[2] / "demo" / "demo_project.wssproj.json"
    cfg = json.loads(demo_project.read_text(encoding="utf-8"))
    project_dir = tmp_path / "demo_project"
    project_dir.mkdir()

    dem_src = Path(__file__).resolve().parents[2] / "demo" / cfg["dem_path"]
    dem_dst = project_dir / "demo_dem.asc"
    dem_dst.write_text(dem_src.read_text(encoding="utf-8"), encoding="utf-8")

    req = cfg.copy()
    req["project_path"] = str(project_dir)
    req["dem_path"] = str(dem_dst)

    client = TestClient(app)
    job_id = client.post("/jobs/run", json=req).json()["id"]

    for _ in range(300):
        state = client.get(f"/jobs/{job_id}").json()
        if state["status"] in {"done", "error"}:
            break
        time.sleep(0.05)

    assert state["status"] == "done", state.get("error")
    assert (project_dir / "outputs" / "preview.png").exists()
    assert (project_dir / "outputs" / "report.pdf").exists()
