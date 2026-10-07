// Floating page navigation: "Sections" lists the page's section headings (its h3s) and jumps to one,
// "Top" returns to the top once the page has been scrolled. Long pages are a lot of thumb-scrolling on a phone.

import { useEffect, useState } from "react";
import { Sheet } from "./ui";

interface Heading { el: HTMLElement; label: string; badge?: { text: string; className: string } }

/** A page needs at least this many sections before the "Sections" button shows. */
const MIN_SECTIONS = 3;

/** The page's section headings: every visible h3 in <main>, not those in a pop-up or a closed <details>. */
function scan(root: HTMLElement): Heading[] {
  return [...root.querySelectorAll<HTMLElement>("h3")]
    .filter((el) => !el.closest("dialog") && el.getClientRects().length > 0)
    .map((el) => {
      // The heading's own words: without the Docs section number, the live dot or the session badge.
      const copy = el.cloneNode(true) as HTMLElement;
      copy.querySelectorAll(".report-n, [aria-hidden], .session-badge").forEach((n) => n.remove());
      const badge = el.querySelector<HTMLElement>(".session-badge");
      return {
        el, label: copy.textContent?.trim() ?? "",
        badge: badge ? { text: badge.textContent ?? "", className: badge.className } : undefined,
      };
    })
    .filter((h) => h.label);
}

const same = (a: Heading[], b: Heading[]) =>
  a.length === b.length && a.every((h, i) => h.el === b[i].el && h.label === b[i].label && h.badge?.text === b[i].badge?.text);

/** The last heading at or above the top of the visible page (under the pinned tabs): the section being read. */
function current(headings: Heading[]): number {
  const pinned = parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--pinned")) || 0;
  let at = -1;
  headings.forEach((h, i) => { if (h.el.getBoundingClientRect().top <= pinned + 40) at = i; });
  return at;
}

export default function PageJump({ page }: { page: string }) {
  const [headings, setHeadings] = useState<Heading[]>([]);
  const [scrolled, setScrolled] = useState(false);
  const [open, setOpen] = useState(false);
  const [at, setAt] = useState(-1);

  // Pages load their data and fill in after the first render, and sections come and go with the picks
  // on the page: rescan whenever <main> changes (once a frame at most).
  useEffect(() => {
    const main = document.querySelector("main");
    if (!main) return;
    let frame = 0;
    const update = () => {
      frame = 0;
      const found = scan(main);
      setHeadings((hs) => same(hs, found) ? hs : found);
    };
    const observer = new MutationObserver(() => { if (!frame) frame = requestAnimationFrame(update); });
    observer.observe(main, { childList: true, subtree: true, attributes: true, attributeFilter: ["open"] });
    update();
    return () => { observer.disconnect(); cancelAnimationFrame(frame); };
  }, [page]);

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > window.innerHeight * 0.8);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  const smooth = () => (matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth") as ScrollBehavior;
  const jump = (h: Heading) => {
    setOpen(false);
    // After the sheet has closed and given the page its scrolling back.
    requestAnimationFrame(() => h.el.scrollIntoView({ behavior: smooth(), block: "start" }));
  };
  const sections = headings.length >= MIN_SECTIONS;
  if (!sections && !scrolled) return null;

  return (
    <>
      <div className="page-jump">
        {sections && (
          <button className="page-jump-btn" onClick={() => { setAt(current(headings)); setOpen(true); }}
                  aria-haspopup="dialog">
            <span aria-hidden>☰</span> Sections
          </button>
        )}
        {scrolled && (
          <button className="page-jump-btn" onClick={() => window.scrollTo({ top: 0, behavior: smooth() })}
                  aria-label="Back to top">
            <span aria-hidden>↑</span> Top
          </button>
        )}
      </div>
      <Sheet open={open} onClose={() => setOpen(false)} title="Jump to section">
        <ul className="page-jump-list">
          {headings.map((h, i) => (
            <li key={i}>
              <button className={i === at ? "on" : undefined} aria-current={i === at ? "location" : undefined}
                      onClick={() => jump(h)}>
                {h.label}
                {h.badge && <span className={h.badge.className}>{h.badge.text}</span>}
              </button>
            </li>
          ))}
        </ul>
        {scrolled && (
          <button className="link page-jump-top" onClick={() => { setOpen(false); window.scrollTo({ top: 0, behavior: smooth() }); }}>
            ↑ Back to top
          </button>
        )}
      </Sheet>
    </>
  );
}
