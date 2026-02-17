#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use serde::{Deserialize, Serialize};
use std::{fs, path::PathBuf, process::Command, sync::Mutex, thread, time::Duration};
use tauri::{Manager, State};

#[derive(Default)]
struct EngineState(Mutex<Option<u16>>);

#[derive(Serialize, Deserialize)]
struct Turbine {
    id: String,
    x: f64,
    y: f64,
    hub_height_m: f64,
    rotor_diameter_m: f64,
}

#[derive(Serialize, Deserialize)]
struct ProjectConfig {
    project_path: String,
    epsg: String,
    cellsize_m: f64,
    buffer_m: f64,
    terrain_aware: bool,
    dem_path: String,
    turbines: Vec<Turbine>,
    output: serde_json::Value,
}

#[tauri::command]
fn get_engine_port(state: State<EngineState>) -> Result<u16, String> {
    state.0.lock().map_err(|e| e.to_string())?.ok_or_else(|| "engine port unavailable".to_string())
}

#[tauri::command]
fn pick_dem() -> Option<String> {
    rfd::FileDialog::new().add_filter("DEM", &["tif", "tiff", "asc"]).pick_file().map(|p| p.display().to_string())
}

#[tauri::command]
fn import_csv_turbines() -> Result<Vec<Turbine>, String> {
    let Some(path) = rfd::FileDialog::new().add_filter("CSV", &["csv"]).pick_file() else {
        return Ok(vec![]);
    };
    let mut rdr = csv::ReaderBuilder::new().delimiter(b';').from_path(path).map_err(|e| e.to_string())?;
    let mut out = vec![];
    for rec in rdr.deserialize() {
        let t: Turbine = rec.map_err(|e| e.to_string())?;
        out.push(t);
    }
    Ok(out)
}

#[tauri::command]
fn choose_project(mode: &str) -> Result<ProjectConfig, String> {
    if mode == "demo" {
        let base = dirs::document_dir().unwrap_or(PathBuf::from(".")).join("WindShadowStudio").join("Demo");
        fs::create_dir_all(&base).map_err(|e| e.to_string())?;
        for f in ["demo_dem.asc", "demo_turbines.csv", "demo_project.wssproj.json"] {
            let src = PathBuf::from("../demo").join(f);
            let dst = base.join(f);
            fs::copy(src, dst).map_err(|e| e.to_string())?;
        }
        let mut cfg: ProjectConfig = serde_json::from_str(&fs::read_to_string(base.join("demo_project.wssproj.json")).map_err(|e| e.to_string())?).map_err(|e| e.to_string())?;
        cfg.project_path = base.display().to_string();
        cfg.dem_path = base.join("demo_dem.asc").display().to_string();
        return Ok(cfg);
    }

    let Some(folder) = rfd::FileDialog::new().pick_folder() else {
        return Err("no folder selected".to_string());
    };
    let proj_file = folder.join("project.wssproj.json");
    if mode == "open" && proj_file.exists() {
        let mut cfg: ProjectConfig = serde_json::from_str(&fs::read_to_string(proj_file).map_err(|e| e.to_string())?).map_err(|e| e.to_string())?;
        cfg.project_path = folder.display().to_string();
        return Ok(cfg);
    }
    Ok(ProjectConfig {
        project_path: folder.display().to_string(),
        epsg: "EPSG:32632".to_string(),
        cellsize_m: 10.0,
        buffer_m: 2000.0,
        terrain_aware: false,
        dem_path: String::new(),
        turbines: vec![Turbine { id: "T1".into(), x: 500100.0, y: 5000100.0, hub_height_m: 120.0, rotor_diameter_m: 140.0 }],
        output: serde_json::json!({"format":"both"}),
    })
}

fn start_engine(app: &tauri::AppHandle, state: &EngineState) {
    let runtime = app.path().app_data_dir().unwrap_or(PathBuf::from(".")).join("runtime");
    fs::create_dir_all(&runtime).ok();
    let port_file = runtime.join("port.json");
    let dev_sidecar = PathBuf::from("../engine/dist/engine.exe");
    if dev_sidecar.exists() {
        let _ = Command::new(dev_sidecar).env("WSS_RUNTIME_DIR", runtime.display().to_string()).spawn();
    } else {
        let _ = Command::new("python").arg("../engine/run_engine.py").env("WSS_RUNTIME_DIR", runtime.display().to_string()).spawn();
    }
    for _ in 0..50 {
        if let Ok(raw) = fs::read_to_string(&port_file) {
            if let Ok(v) = serde_json::from_str::<serde_json::Value>(&raw) {
                if let Some(p) = v["port"].as_u64() {
                    let mut lock = state.0.lock().unwrap();
                    *lock = Some(p as u16);
                    break;
                }
            }
        }
        thread::sleep(Duration::from_millis(200));
    }
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .manage(EngineState::default())
        .setup(|app| {
            let handle = app.handle().clone();
            let state = app.state::<EngineState>();
            start_engine(&handle, &state);
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![get_engine_port, choose_project, pick_dem, import_csv_turbines])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
