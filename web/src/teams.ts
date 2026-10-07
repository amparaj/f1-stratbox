// Team badges: a logo where a freely licensed one exists (web/src/teamLogos.json, files in public/logos/),
// else the team colour with a short code. modules/teams.py applies the same rules for the dashboard.

import manifest from "./teamLogos.json";

export interface TeamLogo { file: string; since?: number; until?: number; source?: string; archive?: string; licence?: string; author?: string; page?: string }
interface TeamEntry { key: string; name: string; names: string[]; years?: number[]; short?: string; logos: TeamLogo[] }

export const TEAMS = manifest.teams as TeamEntry[];
const STOP = new Set(["team", "f1", "racing", "grand", "prix", "gp", "formula", "one", "the", "scuderia"]);

const norm = (n: string) =>
  n.normalize("NFKD").replace(/[\u0300-\u036f]/g, "").replace(/&amp;/g, "&").toLowerCase().replace(/\s+/g, " ").trim();

/** The manifest entry for a team: its full name, then its chassis ("Lotus-Climax" -> "lotus"), in a season if given. */
function entry(name: string, year?: number | null): TeamEntry | undefined {
  const full = norm(name);
  const tries = [full, full.split("-")[0].trim()];
  const inYears = (t: TeamEntry) => year == null || !t.years || (year >= t.years[0] && year <= t.years[1]);
  for (const n of tries) {
    const hit = TEAMS.find((t) => t.names.includes(n) && inYears(t));
    if (hit) return hit;
  }
  return undefined;
}

/** The logo for that season (no season: the latest), or null. */
export function teamLogo(name: string, year?: number | null): TeamLogo | null {
  const logos = entry(name, year)?.logos ?? [];
  if (year == null) return logos[logos.length - 1] ?? null;
  return logos.find((l) => (l.since ?? 0) <= year && year <= (l.until ?? 9999)) ?? null;
}

/** Short code: the manifest's, else three letters of a one-word name or the initials of a longer one. */
export function teamShort(name: string, year?: number | null): string {
  const e = entry(name, year);
  if (e?.short) return e.short;
  const words = name.split("-")[0].replace(/&amp;/g, "&").split(/\s+/).filter(Boolean);
  const kept = words.filter((w) => !STOP.has(norm(w)));
  const use = kept.length ? kept : words;
  return (use.length === 1 ? use[0].slice(0, 3) : use.slice(0, 3).map((w) => w[0]).join("")).toUpperCase();
}

export const logoUrl = (l: TeamLogo) => `${import.meta.env.BASE_URL}logos/${l.file}`;
