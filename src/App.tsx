import { useMemo, useState } from 'react';
import { invoke } from '@tauri-apps/api/core';
import { messages } from './i18n/messages';
import type { ProjectConfig, Turbine } from './types';
import { MapContainer, Marker, TileLayer, ImageOverlay } from 'react-leaflet';
import 'leaflet/dist/leaflet.css';

const defaultTurbine = (): Turbine => ({ id: 'T1', x: 500100, y: 5000100, hub_height_m: 120, rotor_diameter_m: 140 });

const defaultCfg: ProjectConfig = {
  project_path: '',
  epsg: 'EPSG:32632',
  cellsize_m: 10,
  buffer_m: 2000,
  terrain_aware: false,
  dem_path: '',
  turbines: [defaultTurbine()],
  output: { format: 'both' }
};

export default function App() {
  const [lang, setLang] = useState<'it' | 'en'>('it');
  const t = messages[lang];
  const [cfg, setCfg] = useState<ProjectConfig>(defaultCfg);
  const [engineBase, setEngineBase] = useState('http://127.0.0.1:8000');
  const [job, setJob] = useState<any>(null);
  const [logs, setLogs] = useState<string[]>([]);

  useMemo(async () => {
    const p = await invoke<number>('get_engine_port');
    setEngineBase(`http://127.0.0.1:${p}`);
  }, []);

  async function chooseProject(mode: 'new' | 'open' | 'demo') {
    const data = await invoke<any>('choose_project', { mode });
    if (!data) return;
    setCfg(data as ProjectConfig);
  }

  async function chooseDem() {
    const p = await invoke<string>('pick_dem');
    if (p) setCfg((s) => ({ ...s, dem_path: p }));
  }

  async function importCsv() {
    const rows = await invoke<Turbine[]>('import_csv_turbines');
    if (rows?.length) setCfg((s) => ({ ...s, turbines: rows.slice(0, 20) }));
  }

  async function run() {
    const res = await fetch(`${engineBase}/jobs/run`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(cfg) });
    const json = await res.json();
    const id = json.id;
    const timer = setInterval(async () => {
      const state = await (await fetch(`${engineBase}/jobs/${id}`)).json();
      setJob(state);
      setLogs(state.logs || []);
      if (state.status === 'done' || state.status === 'error') clearInterval(timer);
    }, 900);
  }

  const overlayUrl = job?.outputs?.preview_png ? `${engineBase}/jobs/${job.id}/files/preview_png` : '';

  return (
    <div className="app">
      <header>
        <h1>Wind Shadow Studio</h1>
        <select value={lang} onChange={(e) => setLang(e.target.value as 'it' | 'en')}><option value="it">IT</option><option value="en">EN</option></select>
      </header>
      <div className="row">
        <button onClick={() => chooseProject('new')}>{t.newProject}</button>
        <button onClick={() => chooseProject('open')}>{t.openProject}</button>
        <button onClick={() => chooseProject('demo')}>{t.openDemo}</button>
      </div>
      <section className="grid">
        <div>
          <label>EPSG <input value={cfg.epsg} onChange={(e) => setCfg({ ...cfg, epsg: e.target.value })} /></label>
          <label>Cellsize <input type="number" value={cfg.cellsize_m} onChange={(e) => setCfg({ ...cfg, cellsize_m: Number(e.target.value) })} /></label>
          <label>Buffer m <input type="number" value={cfg.buffer_m} onChange={(e) => setCfg({ ...cfg, buffer_m: Number(e.target.value) })} /></label>
          <label>DEM <input value={cfg.dem_path} readOnly /><button onClick={chooseDem}>Pick</button></label>
          <label>Terrain-aware <input type="checkbox" checked={cfg.terrain_aware} onChange={(e) => setCfg({ ...cfg, terrain_aware: e.target.checked })} /></label>
          <label>Output
            <select value={cfg.output.format} onChange={(e) => setCfg({ ...cfg, output: { format: e.target.value as any } })}>
              <option value="both">both</option><option value="asc">asc</option><option value="geotiff">geotiff</option>
            </select>
          </label>
          <button onClick={importCsv}>Import CSV (;)</button>
          <button onClick={run}>{t.run}</button>
        </div>
        <div>
          <MapContainer center={[45, 10]} zoom={7} style={{ height: 360 }}>
            <TileLayer attribution="&copy; OpenStreetMap contributors" url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" />
            {overlayUrl && job?.overlay_bounds && <ImageOverlay bounds={job.overlay_bounds} url={overlayUrl} opacity={0.6} />}
            {cfg.turbines.map((tb) => (
              <Marker key={tb.id} position={[45 + (tb.y % 1000) / 10000, 10 + (tb.x % 1000) / 10000]} />
            ))}
          </MapContainer>
          <div className="legend">Legend min/max: {job?.stats?.min ?? '-'} / {job?.stats?.max ?? '-'}</div>
        </div>
      </section>
      <section>
        <h3>Logs</h3>
        <pre>{logs.join('\n')}</pre>
      </section>
    </div>
  );
}
