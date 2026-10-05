// Loading the JSON that scripts/export_site.py writes to public/data/, and the shapes it has.

/** A column-wise table as exported: {"col": [values...]}. */
export type Columns = Record<string, unknown[]>;
export type Row = Record<string, any>;

/** Column-wise -> one object per row. */
export function rows<T = Row>(cols: Columns | undefined | null): T[] {
  if (!cols) return [];
  const keys = Object.keys(cols);
  const n = keys.length ? cols[keys[0]].length : 0;
  const out: T[] = [];
  for (let i = 0; i < n; i++) {
    const r: Row = {};
    for (const k of keys) r[k] = cols[k][i];
    out.push(r as T);
  }
  return out;
}

export interface CalendarEvent {
  round: number;
  event: string;
  location: string;
  country: string;
  format: string;
  race_utc: string | null;
  sprint_utc: string | null;
  /** The circuit's position, for the live rain radar (absent in files from before it). */
  lat?: number | null;
  lon?: number | null;
  done_R: boolean;
  done_S: boolean;
  winner: string | null;
  winner_color: string | null;
}

export interface SessionRef { id: string; round: number; code: "R" | "S"; complete: boolean }

export interface DriverStanding {
  position: number; driver: string; name: string; team: string; color: string;
  points: number; wins: number; podiums: number; dnfs: number; starts: number;
}
export interface TeamStanding { position: number; team: string; color: string; points: number; wins: number }
export interface TitleOdds { driver?: string; team: string; color: string; points: number; exp_points: number; p_title: number }

export interface Meta {
  season: number;
  generated: string;
  calendar: CalendarEvent[];
  sessions: SessionRef[];
  pending: string[];
  next_round: number | null;
  forecasts: number[];
  drivers: Columns;
  teams: Columns;
  progression: Columns;
  title_history: Columns;
  title_odds?: { drivers: Columns; teams: Columns };
}

export interface ResultRow {
  driver: string; name: string | null; number: string | null; team: string; color: string;
  second_driver: boolean; grid: number | null; pit_lane_start: boolean; position: number | null;
  classified: boolean; status: string; points: number; laps: number; gap: number | null;
  best_lap: number | null; pace: number | null; dnf: boolean; dns: boolean;
}
export interface LapRow {
  driver: string; lap: number; pos: number; gap: number | null; lap_time: number | null;
  compound: string; tyre_life: number | null; pit: boolean;
}
export interface StintRow {
  driver: string; stint: number; compound: string; first: number; last: number; laps: number;
  deg: number | null; clean_laps: number; cliff_lap: number | null; cliff_cause: "tyre" | "weather" | null;
  cliff_lost: number | null;
}
export interface DegRow { driver: string; compound: string; base_pace: number; deg_rate: number; r_squared: number; n_laps: number; sample: string }
export interface CompoundModel { compound: string; base_pace: number; deg_rate: number; deg_iqr: number; n_drivers: number }

export interface Race {
  id: string; round: number; code: "R" | "S"; event: string; location: string; country: string;
  start_utc: string | null; session_name: string; total_laps: number; complete: boolean;
  neutralised: { SC: number[]; VSC: number[]; RED: number[] };
  rain_laps: number[];
  weather: { air?: [number, number]; track?: [number, number]; track_mean?: number; rain?: boolean };
  /** The track sensors lap by lap (lap, air, track, humidity, wind, rain), or null without a weather feed. */
  weather_laps?: Columns | null;
  fastest: { driver: string; time: number; lap: number } | null;
  results: Columns; laps: Columns; stints: Columns; deg: Columns;
  compounds: CompoundModel[];
  insights: string[];
}

export interface ForecastDriver {
  driver: string; team: string; color: string; form: number; p_win: number; p_podium: number;
  p_points: number; p_dnf: number; exp_pos: number; exp_points: number;
}
export interface StrategyPlan {
  name: string; plan: string; stops: number; pit_laps: number[]; total: number; delta: number;
  mean: number; p10: number; p90: number; win_prob: number; stints: { compound: string; laps: number }[];
  /** Monte Carlo mean without the weather scenarios. */
  mean_dry?: number;
}
export interface WeatherLap { lap: number; air: number | null; track: number | null; humidity: number | null; wind: number | null; rain: boolean }
export interface WeatherHour { time: string; air: number | null; track: number | null; rain_mm: number | null; rain_prob: number | null }
/** A race's weather outlook (modules/weather.py via site_export.weather_forecast). */
export interface WeatherForecast {
  source: "ensemble" | "climate"; model: string; samples: number;
  rain_chance: number; heavy_chance: number; rain_lap_median: number | null;
  air: number | null; track: number | null; track_ref: number | null; temp_delta: number;
  reference: string | null; reference_track: number | null; track_expected: number | null;
  hourly?: Columns;
}
export interface StrategyForecast {
  weather?: WeatherForecast | null;
  calibrated_on: string | null; severity: number; season_factor: number; total_laps: number;
  pit_loss: number; pit_loss_known: boolean; deg: Record<string, number>;
  strategies: StrategyPlan[]; trace: Columns;
}
export interface Forecast { round: number; event: string; based_on: number[]; drivers: Columns; strategy?: StrategyForecast }

const cache = new Map<string, Promise<unknown>>();

/** A file from public/data/, fetched once. Resolves to null if it doesn't exist. */
export function load<T>(path: string): Promise<T | null> {
  if (!cache.has(path)) {
    cache.set(path, fetch(`./data/${path}`, { cache: "no-cache" }).then((r) => (r.ok ? r.json() : null)).catch(() => null));
  }
  return cache.get(path)! as Promise<T | null>;
}

export const raceFile = (round: number, code: "R" | "S") => `races/r${String(round).padStart(2, "0")}-${code}.json`;
export const forecastFile = (round: number) => `forecasts/r${String(round).padStart(2, "0")}.json`;
