// The data every page shares (meta.json), and hooks for the rest.

import { createContext, useContext, useEffect, useMemo, useState } from "react";
import { useNow } from "./components/Radar";
import { currentRound, load, rows, type CalendarEvent, type DriverStanding, type Meta, type TeamStanding } from "./data";

export interface Site {
  meta: Meta;
  drivers: DriverStanding[];
  teams: TeamStanding[];
  driver: Map<string, DriverStanding>;
  event: Map<number, CalendarEvent>;
}

export const SiteContext = createContext<Site | null>(null);

export function useSite(): Site {
  const site = useContext(SiteContext);
  if (!site) throw new Error("useSite outside SiteContext");
  return site;
}

/** The round whose weekend is on (data.currentRound), checked every minute: it has its own page, Current Round. */
export function useCurrentRound(site: Site | null | undefined): CalendarEvent | undefined {
  const now = useNow(60_000);
  return site ? currentRound(site.meta.calendar, now) : undefined;
}

/** A data file, loaded on first use: undefined while loading, null if it's missing. */
export function useData<T>(path: string | null): T | null | undefined {
  const [value, setValue] = useState<{ path: string | null; data: T | null } | undefined>(undefined);
  useEffect(() => {
    let live = true;
    if (path === null) return;
    load<T>(path).then((data) => live && setValue({ path, data }));
    return () => {
      live = false;
    };
  }, [path]);
  return useMemo(() => (value && value.path === path ? value.data : undefined), [value, path]);
}

export function useSiteData(): Site | null | undefined {
  const meta = useData<Meta>("meta.json");
  return useMemo(() => {
    if (meta === undefined) return undefined;
    if (!meta) return null;
    const drivers = rows<DriverStanding>(meta.drivers);
    return {
      meta,
      drivers,
      teams: rows<TeamStanding>(meta.teams),
      driver: new Map(drivers.map((d) => [d.driver, d])),
      event: new Map(meta.calendar.map((e) => [e.round, e])),
    };
  }, [meta]);
}

/** The URL hash without the "#": "races/16/S". */
export function useHash(): string {
  const [hash, setHash] = useState(() => window.location.hash.slice(1));
  useEffect(() => {
    const onChange = () => {
      setHash(window.location.hash.slice(1));
      window.scrollTo({ top: 0 });
    };
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return hash;
}
