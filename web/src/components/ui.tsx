// Small shared pieces (from xP-FPL): stat tiles, legends, the chart wrapper and the sortable table.

import * as Plot from "@observablehq/plot";
import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore, type ReactNode } from "react";

// ---------------------------------------------------------------- stat tiles

export interface TileProps { label: string; value: ReactNode; note?: ReactNode }

export function Tiles({ tiles }: { tiles: TileProps[] }) {
  return (
    <div className="tiles">
      {tiles.map((t) => (
        <div className="tile" key={t.label}>
          <div className="tile-label">{t.label}</div>
          <div className="tile-value">{t.value}</div>
          {t.note !== undefined && <div className="tile-note">{t.note}</div>}
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------- legend

export function Legend({ items }: { items: { label: string; color: string; kind?: "rect" | "line" | "dot" }[] }) {
  return (
    <div className="legend">
      {items.map((i) => (
        <span key={i.label} className="legend-item">
          <span className={`key key-${i.kind ?? "rect"}`} style={{ background: i.color }} />
          {i.label}
        </span>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------- chart

/** Renders an Observable Plot chart at the container's width, redrawn when it resizes. */
export function Chart({ make, height, ariaLabel }: {
  make: (width: number) => (SVGSVGElement | HTMLElement);
  height?: number;
  ariaLabel: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const observer = new ResizeObserver(([entry]) => setWidth(Math.floor(entry.contentRect.width)));
    observer.observe(el);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    const el = ref.current;
    if (!el || width === 0) return;
    const chart = make(width);
    el.replaceChildren(chart);
    return () => chart.remove();
  }, [make, width]);
  return <div ref={ref} className="chart" style={{ minHeight: height }} role="img" aria-label={ariaLabel} />;
}

/** Chart defaults: recessive axes and grid in the text tokens, the site's font. */
export function plotDefaults(width: number): Plot.PlotOptions {
  return {
    width,
    style: { fontFamily: "inherit", fontSize: "12px", background: "transparent", color: "var(--muted)", overflow: "visible" },
    marginLeft: 44,
    // Room between the tick labels and the axis label under them (Plot puts it at marginBottom - 3).
    marginBottom: 42,
  };
}

/** Band padding that keeps bars narrow (about 24px) whatever the chart width and bar count. Capped at
 * 0.8: with few bars on a wide screen (0.88 for five gameweeks at desktop width) the chart came out blank. */
export function barPadding(width: number, bars: number, margins = 64): number {
  const band = (width - margins) / Math.max(bars, 1);
  return Math.min(0.8, Math.max(0.35, 1 - 24 / band));
}

// ---------------------------------------------------------------- table

export interface Column<T> {
  key: string;
  label: ReactNode;
  value: (r: T) => number | string | null | undefined;
  render?: (r: T) => ReactNode;
  numeric?: boolean;
  title?: string;
  /** A heading over this column and its neighbours with the same group (e.g. "Market" over Home/Draw/Away). */
  group?: string;
  /** Long text: wrap it onto a few lines rather than widening the table. */
  wrap?: boolean;
  /** A shorter label for the phone cards, where the group heading isn't shown ("Mkt home"). */
  short?: string;
  /** Whether it can be sorted on. Default: when its values are numbers; names and descriptions can't
   * (set true for text that sorts by meaning, such as a date or a season). */
  sortable?: boolean;
}

/** A column's label as plain text, with its group in front ("xP GW6"): for the phone's sort list and detail sheet. */
function labelText<T>(c: Column<T>): string {
  const label = typeof c.label === "string" && c.label ? c.label : c.title ?? c.key;
  return c.group ? `${c.group} ${label}` : label;
}

// ---------------------------------------------------------------- phone layout

/** Below this width the site lays itself out for a phone: tables become cards, details open in a sheet. */
export const PHONE = "(max-width: 640px)";

/** Whether a media query matches, kept up to date as the window changes. */
export function useMedia(query: string): boolean {
  const subscribe = useCallback((notify: () => void) => {
    const list = window.matchMedia(query);
    list.addEventListener("change", notify);
    return () => list.removeEventListener("change", notify);
  }, [query]);
  return useSyncExternalStore(subscribe, () => window.matchMedia(query).matches, () => false);
}

export const usePhone = () => useMedia(PHONE);

/** A panel over the page: slides up from the bottom on a phone, a centred box on a wide screen.
 * The native <dialog> gives Escape to close, focus kept inside and the dimmed backdrop. */
export function Sheet({ open, onClose, title, children }: {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) d.showModal();
    if (!open && d.open) d.close();
  }, [open]);
  return (
    <dialog ref={ref} className="sheet" onClose={onClose}
            onClick={(e) => { if (e.target === ref.current) onClose(); }}>   {/* a tap on the backdrop */}
      {open && (
        <div className="sheet-inner">
          <div className="sheet-handle" aria-hidden />
          <div className="sheet-head">
            <div className="sheet-title">{title}</div>
            <button className="sheet-close" onClick={onClose} aria-label="Close">×</button>
          </div>
          <div className="sheet-body">{children}</div>
        </div>
      )}
    </dialog>
  );
}

/** Label-value pairs in two columns: a row's every number, in the sheet. */
export function Stats({ items }: { items: { label: ReactNode; value: ReactNode; title?: string }[] }) {
  return (
    <dl className="stats">
      {items.map((s, i) => (
        <div key={i} className="stat" title={s.title}>
          <dt>{s.label}</dt>
          <dd>{s.value}</dd>
        </div>
      ))}
    </dl>
  );
}

/** The group heading row: one cell per run of neighbouring columns with the same group. */
function groupRuns<T>(columns: Column<T>[]): { group?: string; span: number; start: number }[] {
  const runs: { group?: string; span: number; start: number }[] = [];
  columns.forEach((c, i) => {
    const last = runs[runs.length - 1];
    if (last && c.group && last.group === c.group) last.span++;
    else runs.push({ group: c.group, span: 1, start: i });
  });
  return runs;
}

export interface TableProps<T> {
  columns: Column<T>[];
  data: T[];
  sort?: string;
  desc?: boolean;
  limit?: number;
  rowKey: (r: T) => string | number;
  /** Clicking a row does this instead of opening the row's detail sheet (on a phone). */
  onRow?: (r: T) => void;
  selected?: string | number | null;
  /** Phone cards: the columns shown as numbers on each card (default: the sorted column and the last numeric ones). */
  cardStats?: string[];
  /** Phone cards and the sheet: the row's title, when the first column alone doesn't say it (a bare gameweek number). */
  cardTitle?: (r: T) => ReactNode;
  /** Phone cards: small columns shown under the card's title (default: the short text columns next to the first). */
  cardSub?: string[];
  /** Extra content for a row's detail sheet, above its full list of columns ("Why this projection?"). */
  detail?: (r: T) => ReactNode;
  /** Cards on a phone (default: when the table has more than four columns, or a detail panel). */
  cards?: boolean;
}

export function Table<T>(props: TableProps<T>) {
  const { columns, data, sort: initialSort, desc: initialDesc = true, limit, rowKey, onRow, selected } = props;
  const [sort, setSort] = useState(initialSort ?? null);
  const [desc, setDesc] = useState(initialDesc);
  const [all, setAll] = useState(false);
  const [open, setOpen] = useState<T | null>(null);
  const phone = usePhone();
  const sorted = useMemo(() => {
    const col = columns.find((c) => c.key === sort);
    if (!col) return data;
    const nulls = (v: unknown) => v === null || v === undefined || Number.isNaN(v);
    return [...data].sort((a, b) => {
      const x = col.value(a), y = col.value(b);
      if (nulls(x)) return nulls(y) ? 0 : 1;
      if (nulls(y)) return -1;
      const c = typeof x === "number" && typeof y === "number" ? x - y : String(x).localeCompare(String(y));
      return desc ? -c : c;
    });
  }, [columns, data, sort, desc]);
  const shown = limit && !all ? sorted.slice(0, limit) : sorted;
  const more = limit && sorted.length > limit ? (
    <button className="link" onClick={() => setAll(!all)}>
      {all ? "Show fewer" : `Show all ${sorted.length}`}
    </button>
  ) : null;
  const sortable = useMemo(() => new Set(columns.filter((c) =>
    c.sortable ?? (c.numeric || data.some((r) => typeof c.value(r) === "number"))).map((c) => c.key)), [columns, data]);
  const sortBy = (key: string) => {
    if (sort === key) setDesc(!desc);
    else { setSort(key); setDesc(!!columns.find((c) => c.key === key)?.numeric); }
  };

  // A row with more to show opens it in a sheet: every column on a phone, plus the page's own detail panel.
  const sheet = (
    <Sheet open={open !== null} onClose={() => setOpen(null)} title={open !== null && (props.cardTitle ? props.cardTitle(open) : cell(columns[0], open))}>
      {open !== null && (
        <>
          {props.detail?.(open)}
          {phone && <RowDetail columns={columns.slice(1)} row={open} />}
        </>
      )}
    </Sheet>
  );
  const tap = onRow ?? (props.detail ? setOpen : undefined);

  if (phone && (props.cards ?? (columns.length > 4 || !!props.detail))) {
    return <>
      <Cards {...props} shown={shown} sort={sort} desc={desc} sortBy={sortBy} sortable={sortable} setDesc={setDesc} more={more} tap={onRow ?? setOpen} />
      {sheet}
    </>;
  }

  const runs = columns.some((c) => c.group) ? groupRuns(columns) : null;
  // The first column of each group gets a rule down its left edge, in every row.
  const starts = new Set(runs?.filter((r) => r.group).map((r) => r.start));
  const cls = (c: Column<T>, i: number) => [c.numeric && "num", c.wrap && "wrap", starts.has(i) && "group-start"].filter(Boolean).join(" ") || undefined;
  return (
    <div className="table-wrap">
      <table>
        <thead>
          {runs && (
            <tr className="group-row">
              {runs.map((r) => (
                <th key={r.start} colSpan={r.span} className={r.group ? "group-start" : undefined} scope={r.group ? "colgroup" : undefined}>
                  {r.group}
                </th>
              ))}
            </tr>
          )}
          <tr>
            {columns.map((c, i) => (
              <th key={c.key} className={cls(c, i)} title={c.title}
                  aria-sort={sort === c.key ? (desc ? "descending" : "ascending") : undefined}>
                {sortable.has(c.key) ? (
                  <button onClick={() => sortBy(c.key)}>
                    {c.label}
                    <span className="sort-mark">{sort === c.key ? (desc ? "▼" : "▲") : ""}</span>
                  </button>
                ) : <span className="th-label">{c.label}</span>}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {shown.map((r) => {
            const key = rowKey(r);
            return (
              <tr key={key} onClick={tap ? () => tap(r) : undefined}
                  className={`${tap ? "clickable" : ""} ${selected === key ? "selected" : ""}`}>
                {columns.map((c, i) => (
                  <td key={c.key} className={cls(c, i)}>
                    {cell(c, r)}
                  </td>
                ))}
              </tr>
            );
          })}
        </tbody>
      </table>
      {more}
      {sheet}
    </div>
  );
}

const cell = <T,>(c: Column<T>, r: T): ReactNode => (c.render ? c.render(r) : String(c.value(r) ?? "–"));

/** The table on a phone: one card per row (its name, a few small columns, two to four numbers), a
 * sort control instead of the headings, and every column in a sheet when a card is tapped. */
function Cards<T>({ columns, rowKey, selected, cardStats, cardSub, cardTitle, shown, sort, desc, sortBy, sortable, setDesc, more, tap }: Omit<TableProps<T>, "sort"> & {
  shown: T[]; sort: string | null; desc: boolean; sortBy: (key: string) => void; sortable: Set<string>; setDesc: (d: boolean) => void; more: ReactNode;
  tap: (r: T) => void;
}) {
  const [title, ...rest] = columns;
  const subs = cardSub
    ? columns.filter((c) => cardSub.includes(c.key))
    : rest.slice(0, 2).filter((c) => !c.numeric && !c.wrap);
  let stats: Column<T>[];
  if (cardStats) {
    stats = cardStats.map((k) => columns.find((c) => c.key === k)).filter((c): c is Column<T> => !!c);
  } else {
    // The last numeric columns: totals and the headline number usually come last.
    stats = rest.filter((c) => c.numeric).slice(-3);
  }
  // The sorted column shows on every card, even when it isn't one of the card's usual numbers.
  const sortCol = columns.find((c) => c.key === sort);
  if (sortCol && sortCol !== title && !stats.includes(sortCol) && !subs.includes(sortCol)) stats = [sortCol, ...stats.slice(0, 2)];
  return (
    <div className="cards-wrap">
      <div className="cards-sort">
        <label>
          Sort by
          <select value={sort ?? ""} onChange={(e) => sortBy(e.target.value)}>
            {!sort && <option value="">–</option>}
            {columns.filter((c) => sortable.has(c.key) || c.key === sort).map((c) => <option key={c.key} value={c.key}>{labelText(c)}</option>)}
          </select>
        </label>
        <button className="cards-dir" onClick={() => setDesc(!desc)}>
          {desc ? "▼ High to low" : "▲ Low to high"}
        </button>
      </div>
      <ul className="cards">
        {shown.map((r) => {
          const key = rowKey(r);
          return (
            <li key={key}>
              <div className={`card-row${selected === key ? " selected" : ""}`} role="button" tabIndex={0}
                   onClick={() => tap(r)}
                   onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); tap(r); } }}>
                <div className="card-main">
                  <div className="card-title">{cardTitle ? cardTitle(r) : cell(title, r)}</div>
                  {subs.length > 0 && <div className="card-sub">{subs.map((c) => <span key={c.key}>{cell(c, r)}</span>)}</div>}
                </div>
                <div className="card-stats">
                  {stats.map((c) => (
                    <div key={c.key} className={`card-stat${c.key === sort ? " sorted" : ""}`}>
                      <small>{c.short ?? (typeof c.label === "string" ? c.label : labelText(c))}</small>
                      <b>{cell(c, r)}</b>
                    </div>
                  ))}
                </div>
                <span className="card-chevron" aria-hidden>›</span>
              </div>
            </li>
          );
        })}
      </ul>
      {more}
    </div>
  );
}

/** Every column of one row as label-value pairs, under its group's heading. */
function RowDetail<T>({ columns, row }: { columns: Column<T>[]; row: T }) {
  const groups: { group?: string; columns: Column<T>[] }[] = [];
  for (const c of columns) {
    const last = groups[groups.length - 1];
    if (last && last.group === c.group) last.columns.push(c);
    else groups.push({ group: c.group, columns: [c] });
  }
  return (
    <>
      {groups.map((g, i) => (
        <section key={i} className="sheet-section">
          {g.group && <h4>{g.group}</h4>}
          <Stats items={g.columns.map((c) => ({
            label: typeof c.label === "string" && c.label ? c.label : labelText(c), value: cell(c, row), title: c.title,
          }))} />
        </section>
      ))}
    </>
  );
}

// ---------------------------------------------------------------- misc

export function Segmented<T extends string | number>({ options, value, onChange, label }: {
  options: { value: T; label: string }[];
  value: T;
  onChange: (v: T) => void;
  label: string;
}) {
  return (
    <div className="segmented" role="radiogroup" aria-label={label}>
      {options.map((o) => (
        <button key={String(o.value)} role="radio" aria-checked={o.value === value}
                className={o.value === value ? "on" : undefined} onClick={() => onChange(o.value)}>
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function Loading() {
  return <p className="muted">Loading…</p>;
}

export function Note({ children }: { children: ReactNode }) {
  return <p className="note">{children}</p>;
}
