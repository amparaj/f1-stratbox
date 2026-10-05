// Live rain radar around the circuit while a session (qualifying, sprint, race) is on (RainViewer's free API).
// RainViewer keeps only the last two hours of radar (a frame every 10 minutes, no forecast
// frames), so this is live only: it can't show a finished race. Drawn from map tiles at zoom 7
// (the free tier's limit) on OpenStreetMap's own tiles (free, no key, credit required; CARTO's
// now need a key), no map library: the circuit sits in the middle. Dark mode darkens the map
// with a CSS filter (.radar-dark), as OpenStreetMap has no dark style.

import { useEffect, useMemo, useRef, useState } from "react";

const API = "https://api.rainviewer.com/public/weather-maps.json";
const ZOOM = 7;
const TILE = 256;
const COLOR_SCHEME = 2;            // RainViewer's "Universal Blue"
const REFRESH_MS = 5 * 60_000;     // new radar frames every 10 minutes

/** When the radar shows: from LEAD_MIN before the start until the session is surely over. */
export const RADAR_LEAD_MIN = 30;
export const RADAR_HOURS = { R: 3, S: 1.5, Q: 1.5, SQ: 1.25 } as const;

/** The live session at `now`, if any: a session's start within the radar window. */
export function liveSession(ev: { race_utc: string | null; sprint_utc: string | null; quali_utc?: string | null; sprint_quali_utc?: string | null }, now: number):
  { code: "R" | "S" | "Q" | "SQ"; start: number } | null {
  for (const [code, iso] of [["SQ", ev.sprint_quali_utc], ["S", ev.sprint_utc], ["Q", ev.quali_utc], ["R", ev.race_utc]] as const) {
    if (!iso) continue;
    const start = Date.parse(iso);
    if (now >= start - RADAR_LEAD_MIN * 60_000 && now <= start + RADAR_HOURS[code] * 3_600_000) return { code, start };
  }
  return null;
}

/** The time now, ticking every `ms`. */
export function useNow(ms = 30_000): number {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), ms);
    return () => window.clearInterval(id);
  }, [ms]);
  return now;
}

interface Frame { time: number; path: string }

function useFrames(): { host: string; frames: Frame[] } | null | undefined {
  const [data, setData] = useState<{ host: string; frames: Frame[] } | null | undefined>(undefined);
  useEffect(() => {
    let alive = true;
    const load = () =>
      fetch(API, { cache: "no-cache" })
        .then((r) => (r.ok ? r.json() : null))
        .then((d) => { if (alive) setData(d ? { host: d.host, frames: d.radar?.past ?? [] } : null); })
        .catch(() => { if (alive) setData(null); });
    load();
    const id = window.setInterval(load, REFRESH_MS);
    return () => { alive = false; window.clearInterval(id); };
  }, []);
  return data;
}

function useDark(): boolean {
  const query = "(prefers-color-scheme: dark)";
  const read = () => {
    const theme = document.documentElement.dataset.theme;
    return theme ? theme === "dark" : window.matchMedia(query).matches;
  };
  const [dark, setDark] = useState(read);
  useEffect(() => {
    const m = window.matchMedia(query);
    const on = () => setDark(read());
    m.addEventListener("change", on);
    const obs = new MutationObserver(on);
    obs.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    return () => { m.removeEventListener("change", on); obs.disconnect(); };
  }, []);
  return dark;
}

/** A tile that didn't load (rate limit, no data) is left blank, not a broken-image icon. */
const hide = (e: { currentTarget: HTMLImageElement }) => { e.currentTarget.style.visibility = "hidden"; };

/** World pixel of a point at the zoom (Web Mercator). */
function worldPx(lat: number, lon: number): [number, number] {
  const n = TILE * 2 ** ZOOM;
  const s = Math.sin((lat * Math.PI) / 180);
  return [((lon + 180) / 360) * n, (0.5 - Math.log((1 + s) / (1 - s)) / (4 * Math.PI)) * n];
}

export function Radar({ lat, lon, place }: { lat: number; lon: number; place: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const obs = new ResizeObserver(([e]) => setWidth(Math.floor(e.contentRect.width)));
    obs.observe(el);
    return () => obs.disconnect();
  }, []);
  const data = useFrames();
  const dark = useDark();
  const frames = data?.frames ?? [];
  const [index, setIndex] = useState<number | null>(null);    // null = follow the latest frame
  const [playing, setPlaying] = useState(false);
  const shown = index ?? frames.length - 1;

  useEffect(() => {
    if (!playing || frames.length < 2) return;
    const id = window.setInterval(() => setIndex((i) => ((i ?? frames.length - 1) + 1) % frames.length), 700);
    return () => window.clearInterval(id);
  }, [playing, frames.length]);

  const height = Math.round(Math.min(Math.max(width * 0.7, 260), 440));
  const tiles = useMemo(() => {
    if (!width) return [];
    const [cx, cy] = worldPx(lat, lon);
    const left = cx - width / 2, top = cy - height / 2;
    const max = 2 ** ZOOM;
    const out: { key: string; x: number; y: number; left: number; top: number }[] = [];
    for (let tx = Math.floor(left / TILE); tx <= Math.floor((left + width) / TILE); tx++) {
      for (let ty = Math.floor(top / TILE); ty <= Math.floor((top + height) / TILE); ty++) {
        if (ty < 0 || ty >= max) continue;
        out.push({ key: `${tx}/${ty}`, x: ((tx % max) + max) % max, y: ty,
                   left: Math.round(tx * TILE - left), top: Math.round(ty * TILE - top) });
      }
    }
    return out;
  }, [lat, lon, width, height]);

  const fmt = (t: number) => new Date(t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  const latest = frames.at(-1);
  const ageMin = latest ? Math.round((Date.now() / 1000 - latest.time) / 60) : null;

  return (
    <div className="radar">
      <div ref={ref} className={`radar-map${dark ? " radar-dark" : ""}`} style={{ height }} role="img"
           aria-label={`Rain radar around ${place}${frames[shown] ? ` at ${fmt(frames[shown].time)}` : ""}`}>
        <div className="radar-base">
          {tiles.map((t) => (
            <img key={`b${t.key}`} className="radar-tile" alt="" draggable={false} style={{ left: t.left, top: t.top }}
                 onError={hide} src={`https://tile.openstreetmap.org/${ZOOM}/${t.x}/${t.y}.png`} />
          ))}
        </div>
        {data && frames.map((f, i) => tiles.map((t) => (
          <img key={`${f.path}${t.key}`} className="radar-tile radar-rain" alt="" draggable={false}
               style={{ left: t.left, top: t.top, opacity: i === shown ? 0.8 : 0 }} onError={hide}
               src={`${data.host}${f.path}/${TILE}/${ZOOM}/${t.x}/${t.y}/${COLOR_SCHEME}/1_1.png`} />
        )))}
        <span className="radar-pin" style={{ left: width / 2, top: height / 2 }} />
        <span className="radar-label" style={{ left: width / 2, top: height / 2 }}>{place}</span>
        {data === null && <span className="radar-msg">Radar unavailable right now.</span>}
      </div>
      {frames.length > 0 && (
        <div className="radar-controls">
          <button type="button" className="radar-play" onClick={() => { if (!playing && index === null) setIndex(0); setPlaying(!playing); }}
                  aria-label={playing ? "Pause the radar loop" : "Play the last two hours"}>
            {playing ? "Pause" : "Play"}
          </button>
          <input type="range" min={0} max={frames.length - 1} value={shown} aria-label="Radar time"
                 onChange={(e) => { setPlaying(false); setIndex(Number(e.target.value)); }} />
          <span className="radar-time">
            {fmt(frames[shown].time)}{shown === frames.length - 1 ? ` · latest${ageMin !== null ? `, ${ageMin} min ago` : ""}` : ""}
          </span>
          {index !== null && !playing && (
            <button type="button" className="radar-play" onClick={() => setIndex(null)}>Latest</button>
          )}
        </div>
      )}
      <p className="note">
        The last two hours of rain radar around the circuit, a frame every 10 minutes; the deeper the blue, the
        heavier the rain. Your local time. Radar <a href="https://www.rainviewer.com/">RainViewer</a> · map ©{" "}
        <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors.
      </p>
    </div>
  );
}
