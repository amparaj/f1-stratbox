// Chart colours come from the CSS tokens in styles.css, so light and dark mode each get their own
// validated steps. SVG presentation attributes can't take var(), so read the values at draw time.

const read = (name: string) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

export const color = {
  get s1() { return read("--s1"); },
  get s2() { return read("--s2"); },
  get s3() { return read("--s3"); },
  get neutral() { return read("--neutral"); },
  get ink() { return read("--ink"); },
  get ink2() { return read("--ink-2"); },
  get muted() { return read("--muted"); },
  get grid() { return read("--grid"); },
  get surface() { return read("--surface"); },
  get good() { return read("--good"); },
  get bad() { return read("--bad"); },
};
