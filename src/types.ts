export type Turbine = {
  id: string;
  x: number;
  y: number;
  hub_height_m: number;
  rotor_diameter_m: number;
};

export type ProjectConfig = {
  project_path: string;
  epsg: string;
  cellsize_m: number;
  buffer_m: number;
  terrain_aware: boolean;
  dem_path: string;
  turbines: Turbine[];
  output: { format: 'asc' | 'geotiff' | 'both' };
};
