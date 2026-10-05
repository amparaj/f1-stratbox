// Diagrams for the About pages, built from HTML boxes rather than images so they reflow: boxes that
// sit side by side on a wide screen stack two to a row on a phone (the layout is xP-FPL's).

import type { ReactNode } from "react";

export interface FlowNode {
  title: string;
  text?: ReactNode;
  /** source: data in; model: a fitted model; key: the headline result; out: what the site shows; drop: thrown away. */
  kind?: "source" | "model" | "key" | "out" | "drop";
  /** A section of the technical documentation (#about/docs/<section>). */
  section?: string;
}

export interface FlowStage {
  label: string;
  nodes: FlowNode[];
  /** Text on the arrow to the next stage ("" for a bare arrow; leave out on the last stage). */
  join?: string;
  note?: ReactNode;
}

function Box({ node }: { node: FlowNode }) {
  const body = (
    <>
      <span className="flow-title">{node.title}</span>
      {node.text && <span className="flow-text">{node.text}</span>}
    </>
  );
  const cls = `flow-node${node.kind ? ` flow-${node.kind}` : ""}`;
  return node.section
    ? <a className={cls} href={`#about/docs/${node.section}`}>{body}</a>
    : <div className={cls}>{body}</div>;
}

/** Stages top to bottom, each a row of boxes with its label on the left, joined by labelled arrows. */
export function Flow({ stages, label }: { stages: FlowStage[]; label: string }) {
  return (
    <figure className="flow" aria-label={label}>
      {stages.map((s) => (
        <div className="flow-stage" key={s.label}>
          <div className="flow-label">{s.label}</div>
          <div className="flow-row" style={{ ["--n" as string]: s.nodes.length }}>
            {s.nodes.map((n) => <Box key={n.title} node={n} />)}
          </div>
          {s.note && <div className="flow-note">{s.note}</div>}
          {s.join !== undefined && <div className="flow-arrow" aria-hidden><span>{s.join}</span></div>}
        </div>
      ))}
    </figure>
  );
}

/** A chain of tests: pass one and go down to the next, fail and leave to the right with that outcome. */
export function Checks({ start, checks, pass, label }: {
  start: ReactNode;
  checks: { test: ReactNode; fail: ReactNode }[];
  pass: ReactNode;
  label: string;
}) {
  return (
    <figure className="checks" aria-label={label}>
      <div className="checks-row">
        <div className="flow-node flow-source">{start}</div>
      </div>
      {checks.map((c, i) => (
        <div key={i}>
          <div className="checks-down" aria-hidden><span>{i === 0 ? "" : "yes"}</span></div>
          <div className="checks-row">
            <div className="flow-node checks-test"><span className="checks-n">{i + 1}</span>{c.test}</div>
            <div className="checks-no" aria-hidden><span>no</span></div>
            <div className="flow-node flow-drop">{c.fail}</div>
          </div>
        </div>
      ))}
      <div className="checks-down" aria-hidden><span>yes</span></div>
      <div className="checks-row">
        <div className="flow-node flow-key">{pass}</div>
      </div>
    </figure>
  );
}
