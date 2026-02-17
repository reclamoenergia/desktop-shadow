from __future__ import annotations

import json
import math
import os
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np
import rasterio
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from pyproj import CRS, Transformer
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

ROME_TZ = "Europe/Rome"
TYPICAL_YEAR = 2025
MAX_TURBINES = 20
MAX_AREA_M = 12_000
DEFAULT_BUFFER = 2_000
CELLSIZE_ALLOWED = {8, 10, 20, 25, 50}


class Turbine(BaseModel):
    id: str
    x: float
    y: float
    hub_height_m: float
    rotor_diameter_m: float


class OutputConfig(BaseModel):
    format: str = Field(default="both", pattern="^(asc|geotiff|both)$")


class RunRequest(BaseModel):
    project_path: str
    epsg: str
    cellsize_m: float = 10
    buffer_m: float = DEFAULT_BUFFER
    terrain_aware: bool = False
    dem_path: str
    turbines: list[Turbine]
    output: OutputConfig = OutputConfig()


@dataclass
class JobState:
    id: str
    status: str = "queued"
    progress_pct: int = 0
    progress_message: str = "Queued"
    error: str | None = None
    logs: list[str] = field(default_factory=list)
    outputs: dict[str, str] = field(default_factory=dict)
    overlay_bounds: list[list[float]] | None = None
    stats: dict[str, float] | None = None


app = FastAPI(title="Wind Shadow Engine")
JOBS: dict[str, JobState] = {}


def log(job: JobState, msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    job.logs.append(line)
    job.progress_message = msg


def save_project_state(project_path: Path, payload: dict[str, Any]) -> None:
    path = project_path / "project.wssproj.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def read_dem(dem_path: Path):
    with rasterio.open(dem_path) as ds:
        arr = ds.read(1)
        transform = ds.transform
        crs = ds.crs
        nodata = ds.nodata
        bounds = ds.bounds
    return arr, transform, crs, nodata, bounds


def rasterize(job: JobState, req: RunRequest) -> None:
    project = Path(req.project_path)
    outputs = project / "outputs"
    outputs.mkdir(parents=True, exist_ok=True)

    if len(req.turbines) > MAX_TURBINES:
        raise ValueError(f"Too many turbines ({len(req.turbines)}), max {MAX_TURBINES}")
    if req.cellsize_m not in CELLSIZE_ALLOWED:
        raise ValueError("cellsize_m must be one of 8,10,20,25,50")

    dem, dem_tr, dem_crs, dem_nodata, dem_bounds = read_dem(Path(req.dem_path))
    model_crs = CRS.from_user_input(req.epsg)
    if dem_crs and dem_crs != model_crs:
        log(job, "Warning: DEM CRS differs from selected EPSG. Continuing with selected EPSG.")

    xs = [t.x for t in req.turbines]
    ys = [t.y for t in req.turbines]
    minx, maxx = min(xs) - req.buffer_m, max(xs) + req.buffer_m
    miny, maxy = min(ys) - req.buffer_m, max(ys) + req.buffer_m

    width = min(maxx - minx, MAX_AREA_M)
    height = min(maxy - miny, MAX_AREA_M)
    if width < (maxx - minx) or height < (maxy - miny):
        log(job, "Area clamped to max 12km x 12km")

    maxx = minx + width
    maxy = miny + height

    cell = req.cellsize_m
    ncols = max(1, int(math.ceil(width / cell)))
    nrows = max(1, int(math.ceil(height / cell)))
    grid = np.zeros((nrows, ncols), dtype=np.float32)

    steps = []
    d = datetime(TYPICAL_YEAR, 1, 1, 6, 0)
    while d.year == TYPICAL_YEAR:
        steps.append(d)
        d += timedelta(minutes=15)

    total_ops = max(1, len(steps) * len(req.turbines))
    op = 0

    for t in req.turbines:
        if not (dem_bounds.left <= t.x <= dem_bounds.right and dem_bounds.bottom <= t.y <= dem_bounds.top):
            log(job, f"Turbine {t.id} outside DEM, ignored")
            continue
        z_ground = sample_dem(dem, dem_tr, t.x, t.y)
        hub_z = z_ground + t.hub_height_m

        for dt in steps:
            elev, azim = approx_solar(dt)
            if elev <= 0:
                op += 1
                continue
            length = min(20_000, t.hub_height_m / math.tan(math.radians(elev)))
            if req.terrain_aware:
                length = terrain_adjusted_length(dem, dem_tr, t.x, t.y, hub_z, elev, azim, cell, length)

            draw_shadow(grid, minx, miny, cell, t.x, t.y, azim, length, t.rotor_diameter_m)
            op += 1
            if op % 500 == 0:
                job.progress_pct = int(op * 100 / total_ops)

    job.progress_pct = 85
    valid = grid[grid >= 0]
    stats = {
        "min": float(np.min(valid)) if valid.size else 0,
        "max": float(np.max(valid)) if valid.size else 0,
        "mean": float(np.mean(valid)) if valid.size else 0,
    }
    job.stats = stats

    asc_path = outputs / "shadow_hours.asc"
    tif_path = outputs / "shadow_hours.tif"
    png_path = outputs / "preview.png"
    pdf_path = outputs / "report.pdf"

    if req.output.format in {"asc", "both"}:
        write_asc(asc_path, grid, minx, miny, cell)
        job.outputs["asc"] = str(asc_path)
    if req.output.format in {"geotiff", "both"}:
        write_tif(tif_path, grid, minx, miny, cell, model_crs)
        job.outputs["geotiff"] = str(tif_path)

    make_preview(png_path, grid)
    make_pdf(pdf_path, req, stats, job.outputs)
    job.outputs["preview_png"] = str(png_path)
    job.outputs["pdf"] = str(pdf_path)

    b = to_wgs84_bounds(minx, miny, maxx, maxy, model_crs)
    job.overlay_bounds = b

    save_project_state(project, req.model_dump())
    job.progress_pct = 100
    job.status = "done"
    log(job, "Completed")


def approx_solar(dt: datetime) -> tuple[float, float]:
    h = dt.hour + dt.minute / 60
    if h < 6 or h > 18:
        return -5, 0
    elev = max(0.1, 60 * math.sin((h - 6) / 12 * math.pi))
    azim = 90 + ((h - 6) / 12) * 180
    return elev, azim


def sample_dem(arr, tr, x, y):
    col = int((x - tr.c) / tr.a)
    row = int((tr.f - y) / abs(tr.e))
    row = np.clip(row, 0, arr.shape[0] - 1)
    col = np.clip(col, 0, arr.shape[1] - 1)
    return float(arr[row, col])


def terrain_adjusted_length(dem, tr, x, y, hub_z, elev, azim, step, max_len):
    rad = math.radians((azim + 180) % 360)
    tan_e = math.tan(math.radians(elev))
    d = step
    while d <= max_len:
        px = x + d * math.sin(rad)
        py = y + d * math.cos(rad)
        z_ray = hub_z - d * tan_e
        z_dem = sample_dem(dem, tr, px, py)
        if z_ray <= z_dem:
            return d
        d += step
    return max_len


def draw_shadow(grid, minx, miny, cell, x, y, azim, length, width):
    rad = math.radians((azim + 180) % 360)
    steps = int(max(1, length / cell))
    spread = max(1, int((width / 2) / cell))
    for i in range(steps):
        d = i * cell
        px = x + d * math.sin(rad)
        py = y + d * math.cos(rad)
        col = int((px - minx) / cell)
        row = int((py - miny) / cell)
        if 0 <= row < grid.shape[0] and 0 <= col < grid.shape[1]:
            for s in range(-spread, spread + 1):
                c2 = col + s
                if 0 <= c2 < grid.shape[1]:
                    grid[row, c2] += 0.25


def write_asc(path: Path, grid: np.ndarray, minx: float, miny: float, cell: float):
    with path.open("w", encoding="utf-8") as f:
        f.write(f"ncols {grid.shape[1]}\n")
        f.write(f"nrows {grid.shape[0]}\n")
        f.write(f"xllcorner {minx}\n")
        f.write(f"yllcorner {miny}\n")
        f.write(f"cellsize {cell}\n")
        f.write("NODATA_value -9999\n")
        np.savetxt(f, np.flipud(grid), fmt="%.2f")


def write_tif(path: Path, grid: np.ndarray, minx: float, miny: float, cell: float, crs: CRS):
    transform = rasterio.transform.from_origin(minx, miny + grid.shape[0] * cell, cell, cell)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=grid.shape[0],
        width=grid.shape[1],
        count=1,
        dtype=grid.dtype,
        crs=crs,
        transform=transform,
    ) as dst:
        dst.write(np.flipud(grid), 1)


def make_preview(path: Path, grid: np.ndarray):
    plt.figure(figsize=(8, 6))
    plt.imshow(grid, cmap="inferno")
    plt.colorbar(label="Annual shadow hours")
    plt.title("Wind Shadow Studio Preview")
    plt.tight_layout()
    plt.savefig(path, dpi=120)
    plt.close()


def make_pdf(path: Path, req: RunRequest, stats: dict[str, float], outputs: dict[str, str]):
    c = canvas.Canvas(str(path), pagesize=A4)
    y = 800
    c.drawString(30, y, "Wind Shadow Studio - Report")
    y -= 24
    c.drawString(30, y, f"EPSG: {req.epsg} | Cellsize: {req.cellsize_m} | Buffer: {req.buffer_m}")
    y -= 18
    c.drawString(30, y, f"Terrain-aware: {req.terrain_aware} | DEM: {req.dem_path}")
    y -= 18
    c.drawString(30, y, f"Output format: {req.output.format}")
    y -= 18
    c.drawString(30, y, f"Stats min/max/mean: {stats['min']:.2f}/{stats['max']:.2f}/{stats['mean']:.2f}")
    y -= 24
    c.drawString(30, y, "Output files:")
    for k, v in outputs.items():
        y -= 16
        c.drawString(40, y, f"- {k}: {v}")
    c.save()


def to_wgs84_bounds(minx, miny, maxx, maxy, from_crs: CRS):
    t = Transformer.from_crs(from_crs, CRS.from_epsg(4326), always_xy=True)
    w, s = t.transform(minx, miny)
    e, n = t.transform(maxx, maxy)
    return [[s, w], [n, e]]


@app.get("/health")
def health():
    return {"status": "ok", "timezone": ROME_TZ, "year": TYPICAL_YEAR}


@app.post("/jobs/run")
def run_job(req: RunRequest):
    job_id = str(uuid.uuid4())
    job = JobState(id=job_id, status="running", progress_message="Starting")
    JOBS[job_id] = job

    def _worker():
        try:
            rasterize(job, req)
        except Exception as exc:  # noqa: BLE001
            job.status = "error"
            job.error = str(exc)
            log(job, f"Error: {exc}")

    threading.Thread(target=_worker, daemon=True).start()
    return {"id": job_id}


@app.get("/jobs/{job_id}")
def get_job(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    return {
        "id": job.id,
        "status": job.status,
        "progress_pct": job.progress_pct,
        "progress_message": job.progress_message,
        "error": job.error,
        "logs": job.logs[-400:],
        "outputs": job.outputs,
        "overlay_bounds": job.overlay_bounds,
        "stats": job.stats,
    }


@app.get("/jobs/{job_id}/files/{kind}")
def get_file(job_id: str, kind: str):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    p = job.outputs.get(kind)
    if not p:
        raise HTTPException(404, "file kind not available")
    return FileResponse(p)


def find_free_port() -> int:
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def write_port_file(runtime_dir: Path, port: int) -> None:
    runtime_dir.mkdir(parents=True, exist_ok=True)
    (runtime_dir / "port.json").write_text(json.dumps({"port": port}), encoding="utf-8")


def run():
    import uvicorn

    runtime = Path(os.environ.get("WSS_RUNTIME_DIR", Path.home() / ".windshadowstudio"))
    port = find_free_port()
    write_port_file(runtime, port)
    print(f"ENGINE_PORT={port}", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")


if __name__ == "__main__":
    run()
