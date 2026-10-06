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

/** A weekend's competitive sessions (the ones with forecasts): Grand Prix, Sprint, Qualifying, Sprint Qualifying. */
export type SessionCode = "R" | "S" | "Q" | "SQ";
/** Free practice: analysed, not forecast. */
export type PracticeCode = "FP1" | "FP2" | "FP3";
export type AnySession = SessionCode | PracticeCode;
export const SESSION_LABEL: Record<AnySession, string> = { R: "Grand Prix", S: "Sprint", Q: "Qualifying", SQ: "Sprint Qualifying", FP1: "Practice 1", FP2: "Practice 2", FP3: "Practice 3" };
/** Short labels for chips and phones. */
export const SESSION_SHORT: Record<AnySession, string> = { R: "GP", S: "Sprint", Q: "Quali", SQ: "Sprint Quali", FP1: "FP1", FP2: "FP2", FP3: "FP3" };
/** Running order on a sprint weekend (a conventional one has Q and R only). */
export const WEEKEND_ORDER: SessionCode[] = ["SQ", "S", "Q", "R"];
export const PRACTICE: PracticeCode[] = ["FP1", "FP2", "FP3"];
export const isQuali = (c: AnySession) => c === "Q" || c === "SQ";
export const isPractice = (c: AnySession): c is PracticeCode => c === "FP1" || c === "FP2" || c === "FP3";
const UTC_KEY = { R: "race_utc", S: "sprint_utc", Q: "quali_utc", SQ: "sprint_quali_utc", FP1: "fp1_utc", FP2: "fp2_utc", FP3: "fp3_utc" } as const;

/** The weekend's sessions in running order (files from before qualifying was exported know only S and R). */
export function weekendSessions(e: CalendarEvent): SessionCode[] {
  return WEEKEND_ORDER.filter((c) => (c === "R" ? true : !!e[UTC_KEY[c]]));
}
export const sessionStart = (e: CalendarEvent, c: AnySession) => e[UTC_KEY[c]] ?? null;
const DONE_KEY = { R: "done_R", S: "done_S", Q: "done_Q", SQ: "done_SQ", FP1: "done_FP1", FP2: "done_FP2", FP3: "done_FP3" } as const;
export const sessionDone = (e: CalendarEvent, c: AnySession) => !!e[DONE_KEY[c]];
/** The URL of a finished session: #races/16 is the Grand Prix, #races/16/S the sprint. */
export const sessionHash = (round: number, c: AnySession) => `races/${round}${c === "R" ? "" : `/${c}`}`;
/** Every session of a weekend, practice included, in the order they run. */
export function weekendAll(e: CalendarEvent): AnySession[] {
  const fp = PRACTICE.filter((c) => !!e[UTC_KEY[c]]);
  const all: AnySession[] = [...fp, ...weekendSessions(e)];
  return all.sort((a, b) => (sessionStart(e, a) ?? "").localeCompare(sessionStart(e, b) ?? ""));
}

export interface CalendarEvent {
  round: number;
  event: string;
  location: string;
  country: string;
  format: string;
  race_utc: string | null;
  sprint_utc: string | null;
  quali_utc?: string | null;
  sprint_quali_utc?: string | null;
  fp1_utc?: string | null;
  fp2_utc?: string | null;
  fp3_utc?: string | null;
  done_FP1?: boolean;
  done_FP2?: boolean;
  done_FP3?: boolean;
  /** The circuit's position, for the live rain radar (absent in files from before it). */
  lat?: number | null;
  lon?: number | null;
  done_R: boolean;
  done_S: boolean;
  done_Q?: boolean;
  done_SQ?: boolean;
  winner: string | null;
  winner_color: string | null;
  sprint_winner?: string | null;
  sprint_winner_color?: string | null;
  pole?: string | null;
  pole_color?: string | null;
  sprint_pole?: string | null;
  sprint_pole_color?: string | null;
}

export interface SessionRef { id: string; round: number; code: AnySession; complete: boolean }

/** One finished practice session (site_export.export_practice). */
export interface PracticeRow {
  position: number; driver: string; name: string | null; team: string; color: string; second_driver: boolean;
  best: number | null; gap: number | null; compound: string | null; laps: number;
  one_lap: number | null; long_run: number | null; long_laps: number;
}
export interface RunRow { driver: string; stint: number; compound: string; first: number; last: number; laps: number; median: number; deg: number | null; pace: number }
export interface Practice {
  id: string; round: number; code: PracticeCode; label: string; event: string; location: string; country: string;
  start_utc: string | null; session_name: string; complete: boolean; sources?: Record<string, string>;
  results: Columns; runs: Columns; trace: Columns; insights: string[];
  penalties?: WeekendPenalties; upgrades?: Columns;
}
/** One upgrade item from the FIA's Car Presentation Submissions. */
export interface UpgradeRow { round?: number; team: string; team_name: string; item: number; component: string; reason: string; text: string }
/** news.json: the scanned F1 news and the season's upgrades. */
export interface NewsItem {
  id: string; source: string; title: string; link: string; published: string; summary: string;
  topics: string[]; drivers: string[]; teams: string[]; rounds: number[];
}
export interface News {
  generated: string; feeds: string[]; items: Columns; upgrades: Columns; upgrade_effect: Columns;
}

export interface DriverStanding {
  position: number; driver: string; name: string; team: string; color: string;
  points: number; wins: number; podiums: number; dnfs: number; starts: number;
  gp_points?: number; sprint_points?: number; sprint_wins?: number; poles?: number;
}
export interface TeamStanding { position: number; team: string; color: string; points: number; wins: number; sprint_points?: number }
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
  /** Where they qualified for this race (its Qualifying, or Sprint Qualifying for a sprint). */
  quali?: number | null;
}

/** One stewards' decision (f1penalties.com). */
export interface PenaltyRow {
  session: string; driver: string; code: string | null; team: string; kind: "pu" | "parc_ferme" | "other";
  allegation: string; detail: string | null; involving: string | null; outcome: string | null;
  time_s: number | null; grid: number | string | null; points: number | null; fine: number | null; notes: string | null;
}
/** A round's stewards' decisions and new power-unit elements, on every session file of the weekend. */
export interface WeekendPenalties {
  decisions: Columns;
  /** Each car that fitted new elements: the elements (one entry each), its count of each after, and the grid drop. */
  pu_fitted: { driver: string; elements: string[]; drop: number | string | null; after: Record<string, number> }[];
  limits: Record<string, number>;
  pu_doc: boolean;
  /** The last round of the season f1penalties.com has. */
  stewards_round: number | null;
}
/** power-units.json: elements used against the allocation, and the chance of a penalty at each Grand Prix left. */
export interface PowerUnitDriver {
  driver: string; team: string | null; color: string | null; short: number; at_limit: string[];
  penalties: number; penalty_rounds: number[];
  p_next: number | null; p_season: number | null; likely_round: number | null; likely_p: number | null;
  announced: number | string | null;
  /** Reported plans: a team's statement (kept by hand) or the news (2+ sites), each one penalty. */
  plans: { rounds: number[]; kind: "team" | "news" | null; note: string | null; links: { source: string; title?: string | null; link: string; published?: string }[] }[];
  [element: string]: unknown;
}
export interface PowerUnits {
  season: number; generated: string; next_round: number | null;
  elements: string[]; names: Record<string, string>; limits: Record<string, number>;
  fia_round: number | null; fia_sources: string[]; stewards_round: number | null;
  drivers: Columns; risk: Columns; circuits: Record<string, number>;
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
  id: string; round: number; code: "R" | "S"; label?: string; event: string; location: string; country: string;
  start_utc: string | null; session_name: string; total_laps: number; complete: boolean;
  /** Data filled from another source where OpenF1 had a gap: endpoint -> source. */
  sources?: Record<string, string>;
  neutralised: { SC: number[]; VSC: number[]; RED: number[] };
  rain_laps: number[];
  weather: { air?: [number, number]; track?: [number, number]; track_mean?: number; rain?: boolean };
  /** The track sensors lap by lap (lap, air, track, humidity, wind, rain), or null without a weather feed. */
  weather_laps?: Columns | null;
  fastest: { driver: string; time: number; lap: number } | null;
  results: Columns; laps: Columns; stints: Columns; deg: Columns;
  compounds: CompoundModel[];
  insights: string[];
  /** The weekend's stewards' decisions and new power-unit elements (newer files). */
  penalties?: WeekendPenalties;
  /** The weekend's upgrades (the FIA's Car Presentation Submissions). */
  upgrades?: Columns;
}

/** A qualifying session: Q1/Q2/Q3, sectors, ideal laps, track evolution, cut-offs. */
export interface QualiRow {
  position: number; driver: string; name: string | null; team: string; color: string; second_driver: boolean;
  q1: number | null; q2: number | null; q3: number | null; best: number | null; reached: 1 | 2 | 3;
  gap_to_pole: number | null; q1_margin: number | null; q2_margin: number | null; teammate_gap: number | null;
  pace: number | null;
}
export interface SectorRow {
  driver: string; best_lap: number | null; segment: number | null; compound: string; s1: number | null; s2: number | null;
  s3: number | null; ideal: number | null; lost: number | null; top_speed: number | null; push_laps: number;
}
export interface QualiLap { driver: string; segment: number | null; minute: number; lap_time: number; compound: string }
export interface Quali {
  id: string; round: number; code: "Q" | "SQ"; label: string; event: string; location: string; country: string;
  start_utc: string | null; session_name: string; complete: boolean;
  sources?: Record<string, string>;
  results: Columns; sectors: Columns; laps: Columns;
  evolution: { Q1_Q2: number | null; Q1_Q2_pct: number | null; Q2_Q3: number | null; Q2_Q3_pct: number | null };
  /** Track gain within each segment, s per minute (negative = quicker later). */
  gain?: Record<"Q1" | "Q2" | "Q3", number | null>;
  cutoff: { q1: number | null; q2: number | null }; to_q2: number;
  weather: { air?: [number, number]; track?: [number, number]; rain?: boolean };
  insights: string[];
  penalties?: WeekendPenalties;
  upgrades?: Columns;
}

export interface ForecastDriver extends SplitTerms {
  driver: string; team: string; color: string; p_win: number; p_podium: number;
  p_points: number; p_dnf: number; exp_pos: number; exp_points: number;
  /** Expected pace (% against the field, negative = faster) before the circuit and grid terms. */
  pace?: number;
  /** Older files: form. */
  form?: number;
  /** Last season's team offset at this circuit (%), and the grid once qualifying is in. */
  circuit?: number | null; grid?: number | null;
  /** What makes `pace` (newer files): race form, the qualifying figure and its share of the blend,
   * then the circuit, grid and grid-penalty terms (%), all summing to `pace`. */
  race_form?: number | null; quali_form?: number | null; quali_share?: number;
  circuit_term?: number; grid_term?: number; penalty_places?: number; penalty_term?: number;
  /** An announced grid penalty: places back, or "back" / "pit". */
  penalty?: number | string | null;
  /** Power-unit penalty risk (Grand Prix, before the grid): the chance of one here, the places it
   * would cost, and the expected term (%, in `pace`). */
  pu_risk?: number; pu_places?: number; pu_term?: number;
  /** Performance parts the team brings (the FIA's list) and their term (%); whether this weekend's practice is in. */
  upgrade_items?: number; upgrade_term?: number; practice?: boolean;
}
/** Car + driver form (sessions in config.SPLIT_FORM): the form part of `pace` as the car's form, the
 * driver's rating against teammates and their streak, each at its share, plus what this weekend's
 * practice moved it (%). Absent or null: the plain race / qualifying form rows. */
export interface SplitTerms {
  form_car?: number | null; form_driver?: number | null; form_streak?: number | null; form_practice?: number | null;
}
export interface QualiForecastDriver extends SplitTerms {
  driver: string; team: string; color: string; pace: number; circuit?: number | null;
  p_pole: number; p_front_row: number; p_top3: number; p_q3: number; p_q1_out: number; exp_pos: number;
  quali_form?: number | null; quali_share?: number; circuit_term?: number;
}
/** One session that counts towards a driver's form: its pace, the pace used (outliers capped) and its share. */
export interface FormInput { driver: string; session: string; pace: number; used: number; share: number }
/** What's behind a forecast: the sessions in each driver's form, and where the qualifying figure and grid came from. */
export interface ForecastWhy {
  form: Columns;
  quali_source: "form" | "weekend";
  grid_source: "official" | "starting_grid" | "qualifying" | null;
}
/** One session's odds: before the weekend, and after its earlier sessions (and grid) once there are some. */
export interface SessionForecast {
  pre: Columns; latest: Columns | null; latest_after: string[];
  why?: { pre: ForecastWhy; latest?: ForecastWhy };
}
export interface QualiStrategy {
  reference: {
    event: string; pole: number; to_q2: number; Q1_Q2: number | null; Q1_Q2_pct: number | null;
    Q2_Q3: number | null; Q2_Q3_pct: number | null; q1_cut_pct: number | null; q2_cut_pct: number | null;
    gain?: Record<"Q1" | "Q2" | "Q3", number | null>;
  } | null;
  rain: { chance: number; source: string; model: string } | null;
  q1_edge: string[]; q3_edge: string[];
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
  code?: "R" | "S";
  weather?: WeatherForecast | null;
  calibrated_on: string | null; severity: number; season_factor: number; total_laps: number;
  pit_loss: number; pit_loss_known: boolean; deg: Record<string, number>;
  strategies: StrategyPlan[]; trace: Columns;
}
export interface Forecast {
  round: number; event: string; based_on: number[]; drivers: Columns; strategy?: StrategyForecast;
  /** How many rounds ahead the "pre" forecasts were made (1 = the next round). */
  rounds_ahead?: number;
  sessions?: Partial<Record<SessionCode, SessionForecast>>;
  sprint_strategy?: StrategyForecast;
  quali_strategy?: Partial<Record<"Q" | "SQ", QualiStrategy>>;
}

/** A session's forecast table: the latest version, or the one from before the weekend. */
export function sessionOdds(f: Forecast | null | undefined, code: SessionCode, which: "latest" | "pre" = "latest"): Columns | null {
  const s = f?.sessions?.[code];
  if (!s) return code === "R" ? f?.drivers ?? null : null;
  return which === "latest" ? s.latest ?? s.pre : s.pre;
}

/** What's behind a session's forecast (newer files only), matching sessionOdds. */
export function sessionWhy(f: Forecast | null | undefined, code: SessionCode, which: "latest" | "pre" = "latest"): ForecastWhy | null {
  const w = f?.sessions?.[code]?.why;
  if (!w) return null;
  return which === "latest" ? w.latest ?? w.pre : w.pre;
}

const cache = new Map<string, Promise<unknown>>();

/** A file from public/data/, fetched once. Resolves to null if it doesn't exist. */
export function load<T>(path: string): Promise<T | null> {
  if (!cache.has(path)) {
    cache.set(path, fetch(`./data/${path}`, { cache: "no-cache" }).then((r) => (r.ok ? r.json() : null)).catch(() => null));
  }
  return cache.get(path)! as Promise<T | null>;
}

export const raceFile = (round: number, code: AnySession) => `races/r${String(round).padStart(2, "0")}-${code}.json`;
export const forecastFile = (round: number) => `forecasts/r${String(round).padStart(2, "0")}.json`;

/** One driver's fastest lap on the session's shared distance axis (every `step` metres). */
export interface TelLap {
  lap: number; time: number; compound: string | null; sectors: (number | null)[];
  t: number[]; speed: number[]; throttle: number[]; brake: number[]; gear: number[]; rpm: number[]; drs?: number[];
  /** [start, end] m where the car data has a hole (frozen or dropped samples): no reading there. */
  gaps?: number[][];
}
/** A session's lap telemetry (modules/site_telemetry.py): track outline, corners and every driver's fastest lap. */
export interface Telemetry {
  step: number; length: number; reference: string;
  x: number[]; y: number[];
  corners: { label: string; d: number; x: number; y: number }[];
  sector_d: number[] | null; drs: boolean;
  drivers: Record<string, TelLap>;
}
export const telemetryFile = (round: number, code: SessionCode) => `telemetry/r${String(round).padStart(2, "0")}-${code}.json`;
