// The driver and circuit pop-ups' data (public/data/profiles.json, written by modules/profiles.py), the
// countries and flags (countries.json, public/flags/), driver photos (driverPhotos.json, public/drivers/),
// and the context any page uses to open a pop-up.

import { createContext, useContext } from "react";
import countryData from "./countries.json";
import photoData from "./driverPhotos.json";
import type { Columns } from "./data";

export interface ProfileDriver {
  driver: string; ref: string | null; name: string; number: string | null;
  /** ISO 3166 alpha-3, a key of countries.json. */
  country: string | null; nationality: string | null; born: string | null; wikipedia: string | null;
  team: string; color: string;
}
/** How a finished Grand Prix played (modules/profiles.race_analysis). */
export interface RaceAnalysis {
  starters: number; finishers: number; dnfs: number;
  places_moved: number | null; passes: number; passes_per_lap: number; lead_changes: number;
  winner: string | null; winner_grid: number | null;
  climber: string | null; climber_from: number | null; climber_to: number | null;
  stops: number | null; one_stop_share: number | null; top_plan: string | null; top_plan_n: number;
  deg: Record<string, number>;
  sc_laps: number; vsc_laps: number; red_laps: number; rain_laps: number; track_temp: number | null;
  fastest: { driver: string; time: number; lap: number } | null; total_laps: number;
}
export interface ProfileCircuit {
  round: number; event: string; ref: string | null; name: string; locality: string;
  country: string | null; country_name: string | null; wikipedia: string | null; circuit_key: number | null;
  pit_loss: number; pit_loss_known: boolean;
  analysis: RaceAnalysis | null;
  /** The outline (metres from the centre, rotated as F1 draws it), when MultiViewer or this season's telemetry has one. */
  x?: number[]; y?: number[]; length_m?: number; corners?: { n: string; x: number; y: number }[];
}
export interface ProfileResult {
  round: number; code: "R" | "S" | "Q" | "SQ"; driver: string; team: string;
  position: number | null; grid: number | null; quali: number | null; points: number;
  status: string; dnf: boolean; dns: boolean; gap: number | null; reached: number | null;
}
export interface Profiles {
  season: number; generated: string;
  drivers: ProfileDriver[]; results: Columns; circuits: ProfileCircuit[];
}

// ---------------------------------------------------------------- countries and flags

interface Country { a2: string | null; name: string; nationality: string; aliases?: string[] }
const COUNTRIES = countryData.countries as Record<string, Country>;

export const country = (a3: string | null | undefined): Country | null => (a3 ? COUNTRIES[a3] ?? null : null);
/** A country's name or alias as a source writes it ("UK", "Korea") -> alpha-3. */
export function countryByName(name: string | null | undefined): string | null {
  if (!name) return null;
  const k = name.toLowerCase();
  for (const [a3, c] of Object.entries(COUNTRIES)) {
    if (a3.toLowerCase() === k || c.name.toLowerCase() === k || c.aliases?.some((a) => a.toLowerCase() === k)) return a3;
  }
  return null;
}
export const flagUrl = (a3: string | null | undefined): string | null => {
  const c = country(a3);
  return c?.a2 ? `${import.meta.env.BASE_URL}flags/${c.a2}.svg` : null;
};

// ---------------------------------------------------------------- driver photos

export interface DriverPhoto { file: string; source: string; licence: string; author: string; page: string }
export const PHOTOS = photoData.drivers as Record<string, DriverPhoto>;
export const photoUrl = (ref: string | null | undefined): string | null =>
  ref && PHOTOS[ref] ? `${import.meta.env.BASE_URL}drivers/${PHOTOS[ref].file}` : null;

// ---------------------------------------------------------------- opening a pop-up

/** What a pop-up shows: a driver of this season (by code) or of the past (History ref), a circuit of
 * this season's calendar (by round) or of the past (History circuit ref). */
export type ProfileTarget =
  | { kind: "driver"; code: string }
  | { kind: "history-driver"; ref: string }
  | { kind: "circuit"; round: number }
  | { kind: "history-circuit"; ref: string };

export interface ProfileApi {
  open: (t: ProfileTarget) => void;
  /** "season": a bare driver code is one of this season's drivers. "history": codes repeat across
   * eras, so a chip opens a pop-up only when it carries a History ref. */
  scope: "season" | "history";
}

export const ProfileContext = createContext<ProfileApi | null>(null);
export const useProfiles = () => useContext(ProfileContext);

/** Age in whole years on `at` (default today). */
export function age(born: string | null | undefined, at: Date = new Date()): number | null {
  if (!born) return null;
  const b = new Date(born);
  let a = at.getFullYear() - b.getFullYear();
  if (at.getMonth() < b.getMonth() || (at.getMonth() === b.getMonth() && at.getDate() < b.getDate())) a--;
  return a;
}
