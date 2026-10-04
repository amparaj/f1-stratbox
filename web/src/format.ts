// Number and date formats, shared so every page shows the same kind of number the same way.

const missing = (v: unknown): v is null | undefined => v === null || v === undefined || Number.isNaN(v as number);
export const isMissing = missing;

export const int = (v: number | null | undefined) => (missing(v) ? "–" : Math.round(v).toLocaleString());
export const dec = (v: number | null | undefined, digits = 2) => (missing(v) ? "–" : v.toFixed(digits));
export const signed = (v: number | null | undefined, digits = 2) => {
  if (missing(v)) return "–";
  const r = Number(v.toFixed(digits));          // so -0.04 at one decimal is "0.0", not "−0.0"
  return `${r > 0 ? "+" : r < 0 ? "−" : ""}${Math.abs(r).toFixed(digits)}`;
};
/** A chance: "34%", "<1%" for a sliver, "–" for none. */
export const pct = (v: number | null | undefined, digits = 0) => {
  if (missing(v)) return "–";
  if (v > 0 && v < 0.005 && digits === 0) return "<1%";
  return `${(v * 100).toFixed(digits)}%`;
};

/** 92.456 -> "1:32.456". */
export const lapTime = (s: number | null | undefined) => {
  if (missing(s)) return "–";
  const m = Math.floor(s / 60);
  return `${m}:${(s - m * 60).toFixed(3).padStart(6, "0")}`;
};
/** A gap in seconds: "+12.345". */
export const gap = (s: number | null | undefined) => (missing(s) ? "–" : `+${s.toFixed(3)}`);
/** Race time differences in seconds, long ones as minutes: "+4.2 s", "+1:02.3". */
export const delta = (s: number | null | undefined) => {
  if (missing(s)) return "–";
  if (Math.abs(s) < 60) return `${s >= 0 ? "+" : "−"}${Math.abs(s).toFixed(1)} s`;
  const m = Math.floor(Math.abs(s) / 60);
  return `${s >= 0 ? "+" : "−"}${m}:${(Math.abs(s) - m * 60).toFixed(1).padStart(4, "0")}`;
};

export const when = (iso: string | null | undefined) =>
  iso
    ? new Date(iso).toLocaleString(undefined, { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", hourCycle: "h23" })
    : "–";
export const day = (iso: string | null | undefined) =>
  iso ? new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short" }) : "–";
export const dayYear = (iso: string | null | undefined) =>
  iso ? new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }) : "–";

/** "Bahrain Grand Prix" -> "Bahrain GP". */
export const shortEvent = (name: string) => name.replace(/ Grand Prix$/, " GP");
