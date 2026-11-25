// Small DOM and formatting helpers shared by the views.

/** Create an element: el("div", {class: "x", onclick: fn}, child1, "text", ...). */
export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === undefined || value === null || value === false) continue;
    if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (key === "style" && typeof value === "object") Object.assign(node.style, value);
    else node.setAttribute(key, value === true ? "" : value);
  }
  node.append(...children.flat().filter((c) => c !== null && c !== undefined && c !== false));
  return node;
}

export const $ = (id) => document.getElementById(id);

export const pct = (x, digits = 0) => `${(x * 100).toFixed(digits)}%`;

/** "2026-11-08" -> "8 Nov" */
export function shortDate(iso) {
  if (!iso) return "";
  return new Date(`${iso}T00:00:00`).toLocaleDateString("en-AU", { day: "numeric", month: "short" });
}

export const STAT_LABELS = {
  vpip: "VPIP",
  pfr: "PFR",
  open_raise: "Open-raise",
  three_bet: "3-bet",
  call_open: "Call a raise",
  fold_to_3bet: "Fold to 3-bet",
  fold_to_cbet: "Fold to c-bet",
  aggression: "Aggression",
  river_bluff: "River bluff",
};

/** Badge for the AI's confidence in a stat (how well the notes support it), not the stat's size. */
export function confidenceBadge(level) {
  const title = "How much your notes support this number" +
    (level === "low" ? " (low usually means a placeholder default)" : "");
  return el("span", { class: `badge ${level}`, title }, `${level[0].toUpperCase()}${level.slice(1)} conf.`);
}

export function statBar(value) {
  return el("div", { class: "bar" }, el("i", { style: { width: pct(value) } }));
}
