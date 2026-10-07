// The driver and circuit pop-ups: click a driver chip or name, or a circuit name, anywhere on the site.
// This file is the light part every page loads (the host, the links, flags); the pop-ups themselves are in
// ProfileViews.tsx, loaded the first time one opens.

import { lazy, Suspense, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { country, flagUrl, ProfileContext, useProfiles, type ProfileTarget } from "../profiles";
import { DriverChip } from "./f1";
import { Loading, Sheet } from "./ui";

const Views = lazy(() => import("./ProfileViews"));
const LazyAnalysis = lazy(() => import("./ProfileViews").then((m) => ({ default: m.CircuitAnalysis })));

/** How one finished Grand Prix played, ranked against the season (the Races page's "How the Circuit Played"). */
export function CircuitAnalysis({ round }: { round: number }) {
  return <Suspense fallback={<Loading />}><LazyAnalysis round={round} /></Suspense>;
}

// ---------------------------------------------------------------- the host: state, back button, the sheet

/** Holds which pop-up is open and shows it. Opening pushes a history entry, so the phone's Back closes it;
 * following a link inside it (to a race, the History page) closes it too. */
export function ProfileHost({ scope, children }: { scope: "season" | "history"; children: ReactNode }) {
  const [target, setTarget] = useState<ProfileTarget | null>(null);
  const pushed = useRef(false);
  useEffect(() => {
    const onPop = () => { pushed.current = false; setTarget(null); };
    const onHash = () => { pushed.current = false; setTarget(null); };
    window.addEventListener("popstate", onPop);
    window.addEventListener("hashchange", onHash);
    return () => { window.removeEventListener("popstate", onPop); window.removeEventListener("hashchange", onHash); };
  }, []);
  const api = useMemo(() => ({
    scope,
    open: (t: ProfileTarget) => {
      if (!pushed.current) { window.history.pushState({ profile: true }, ""); pushed.current = true; }
      setTarget(t);
    },
  }), [scope]);
  const close = () => {
    if (pushed.current) window.history.back();      // popstate clears it
    else setTarget(null);
  };
  return (
    <ProfileContext.Provider value={api}>
      {children}
      <Sheet key={target ? JSON.stringify(target) : "closed"} open={target !== null} onClose={close} wide
             title={target ? <ProfileTitle target={target} /> : null}>
        {target && <Suspense fallback={<Loading />}><Views target={target} /></Suspense>}
      </Sheet>
    </ProfileContext.Provider>
  );
}

function ProfileTitle({ target }: { target: ProfileTarget }) {
  return <>{target.kind === "driver" || target.kind === "history-driver" ? "Driver profile" : "Circuit guide"}</>;
}

// ---------------------------------------------------------------- small pieces anyone can use

export function Flag({ a3, title }: { a3: string | null | undefined; title?: string }) {
  const url = flagUrl(a3);
  const c = country(a3);
  if (!url) return null;
  return <img className="flag" src={url} alt={c?.name ?? ""} title={title ?? c?.name} width={20} height={15} loading="lazy" />;
}

/** A driver's chip and name, both opening their pop-up. */
export function DriverName({ code, color: c, name, historyRef }: {
  code: string; color?: string | null; name?: string | null; historyRef?: string | null;
}) {
  const p = useProfiles();
  const target: ProfileTarget | null = !p ? null : historyRef ? { kind: "history-driver", ref: historyRef }
    : p.scope === "season" ? { kind: "driver", code } : null;
  if (!target) return <><DriverChip code={code} color={c} plain /> {name}</>;
  return (
    <button type="button" className="profile-link driver-name" title={`${name ?? code}: profile`}
            onClick={(e) => { e.stopPropagation(); p!.open(target); }} onKeyDown={(e) => e.stopPropagation()}>
      <DriverChip code={code} color={c} plain />{name ? <span> {name}</span> : null}
    </button>
  );
}

/** A circuit's name (or any text) that opens its pop-up: by this season's round, or a History circuit ref. */
export function CircuitLink({ round, historyRef, children }: { round?: number; historyRef?: string; children: ReactNode }) {
  const p = useProfiles();
  if (!p || (round === undefined && !historyRef)) return <>{children}</>;
  const target: ProfileTarget = round !== undefined ? { kind: "circuit", round } : { kind: "history-circuit", ref: historyRef! };
  return (
    <button type="button" className="profile-link circuit-link" title="Circuit guide"
            onClick={(e) => { e.stopPropagation(); p.open(target); }} onKeyDown={(e) => e.stopPropagation()}>
      {children}
    </button>
  );
}

