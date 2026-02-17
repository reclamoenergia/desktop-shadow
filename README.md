# Wind Shadow Studio

Desktop Windows app standalone (Tauri v2 + React) con engine Python locale sidecar per il calcolo delle ore annue di ombreggiamento WTG.

## Architettura
- **Desktop UI**: React + Leaflet, pacchettizzata con Tauri v2.
- **Engine locale**: FastAPI in esecuzione su `127.0.0.1` con **porta dinamica**.
- Scoperta porta tramite `port.json` runtime (`WSS_RUNTIME_DIR`) e stampa `ENGINE_PORT=` su stdout.
- Timezone: `Europe/Rome`; anno tipo fisso `2025`.

## Funzioni principali
- Progetto locale su cartella disco con `project.wssproj.json`.
- Apertura/creazione progetto e ricarica impostazioni.
- CSV turbine con separatore `;`.
- EPSG configurabile, DEM GeoTIFF/ASC, terrain-aware opzionale.
- Limiti: max 20 turbine; area clamp 12x12 km con messaggio log.
- Output: ASC/GeoTIFF/Both + preview PNG + report PDF in `outputs/`.
- Log live job in UI.
- Mappa OSM con attribution sempre visibile: `© OpenStreetMap contributors`.

## Demo mode
Cartella `/demo` include:
- `demo_dem.asc` sintetico (2km x 2km, 10m)
- `demo_turbines.csv`
- `demo_project.wssproj.json`
- `README_demo.md`

In app: pulsante **Apri demo** copia i file demo in `Documents/WindShadowStudio/Demo` e carica il progetto.

## Sviluppo locale
```bash
npm install
python -m pip install -r engine/requirements.txt
npm run tauri dev
```

## CI / Installer Windows
Workflow: `.github/workflows/windows-desktop.yml`
1. Installa dipendenze engine
2. Esegue smoke test demo
3. Build `engine.exe` via PyInstaller
4. Build installer Tauri (MSI/NSIS)
5. Upload artifact + checksum
6. Su tag `v*`, prepara release draft via tauri-action

## Note GDAL/PROJ
Se richiesto in runtime bundle Windows, impostare `PROJ_LIB` e `GDAL_DATA` verso risorse incluse accanto a `engine.exe`.

## Performance
- Timestep: 15 minuti.
- Terrain-aware usa ray-march passo = `cellsize_m`.
- Terrain-aware è più lento del modello piano.
