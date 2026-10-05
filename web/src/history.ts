// The History pages' data (public/data/history/, written by modules/history.py from Jolpica's dump).

import type { Columns } from "./data";

export interface HistorySeason {
  year: number; rounds: number; drivers: number;
  champion: string | null; champion_ref: string | null; champion_name: string | null;
  champion_team: string | null; champion_color: string | null; champion_points: number | null;
  champion_wins: number | null; runner_up: string | null; margin: number | null;
  constructor: string | null; constructor_color: string | null;
}
export interface HistoryTeam {
  team: string; color: string; country: string | null; first: number; last: number; seasons: number;
  races: number; wins: number; podium_races: number; poles: number; titles: number; driver_titles: number;
}
export interface HistoryIndex {
  generated: string;
  source: { name: string; licence: string; uploaded_at: string | null };
  first_year: number; last_year: number; races: number; drivers: number;
  seasons: HistorySeason[];
  teams: Columns;
}

export interface HistoryRound {
  round: number; event: string; date: string; circuit: string; circuit_name: string; locality: string; country: string;
  winner: string | null; winner_name: string | null; winner_team: string | null; winner_color: string | null;
  pole: string | null; fastest: string | null; laps: number | null; scheduled_laps: number | null;
  starters: number; finishers: number; sprint: boolean; has_laps: boolean; wikipedia: string | null;
}
export interface HistoryResult {
  round: number; session: "R" | "S"; pos: number | null; driver: string; ref: string; name: string; team: string;
  color: string; second_driver: boolean; number: number | null; grid: number | null; laps: number | null;
  time: number | null; gap: number | null; status: string; started: boolean; points: number; fl_rank: number | null;
}
export interface HistoryDriverStanding {
  position: number; driver: string; ref: string; name: string; team: string; color: string | null;
  points: number; wins: number; podiums: number; poles: number; starts: number; best: number | null; points_scored: number;
}
export interface HistoryTeamStanding { position: number; team: string; color: string; points: number; wins: number }
export interface SeasonFile {
  year: number;
  rounds: HistoryRound[];
  drivers: HistoryDriverStanding[];
  teams: HistoryTeamStanding[];
  progression: Columns;
  results: Columns;
}

export interface HistoryLap { driver: string; lap: number; pos: number | null; lap_time: number | null; pit: boolean }
export interface HistoryPit { driver: string; stop: number; lap: number | null; duration: number | null }
export interface RaceLapsFile { laps: Columns; pits: Columns }

export interface CareerRow {
  ref: string; driver: string; name: string; country: string | null; born: string | null; wikipedia: string | null;
  first: number; last: number; seasons: number; starts: number; wins: number; podiums: number; poles: number;
  fastest: number; titles: number; points: number; team: string; color: string;
}
export interface DriverSeason {
  ref: string; year: number; team: string; color: string; position: number | null; points: number | null;
  starts: number; wins: number; podiums: number; poles: number; best: number | null;
}
export interface DriversFile { drivers: Columns & { title_years: number[][]; teams: string[][] }; seasons: Columns }

export interface CircuitRow {
  circuit: string; name: string; locality: string; country: string; lat: number; lon: number;
  races: number; first: number; last: number; top_winner: string; top_wins: number;
}
export interface CircuitRace {
  circuit: string; year: number; round: number; event: string; winner: string; winner_ref: string;
  winner_name: string; team: string; color: string; pole: string | null;
}
export interface CircuitsFile { circuits: Columns; races: Columns }

export const H = {
  index: "history/index.json",
  drivers: "history/drivers.json",
  circuits: "history/circuits.json",
  season: (year: number) => `history/seasons/${year}.json`,
  race: (year: number, round: number, code: "R" | "S") => `history/races/${year}-${String(round).padStart(2, "0")}-${code}.json`,
};

/** Hash links inside the History page. */
export const link = {
  season: (year: number) => `#history/${year}`,
  race: (year: number, round: number, code: "R" | "S" = "R") => `#history/${year}/${round}${code === "S" ? "/S" : ""}`,
  driver: (ref: string) => `#history/driver/${ref}`,
  circuit: (ref: string) => `#history/circuit/${ref}`,
};
