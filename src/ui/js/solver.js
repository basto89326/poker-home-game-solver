// Solver screen: hand grid, opponent picker, spot bar, range summary, hand panel.

import { api } from "./api.js";
import { $, confidenceBadge, el, pct, statBar, STAT_LABELS } from "./dom.js";

const ACTION_LABELS = { "3bet": "3-Bet", call: "Call", fold: "Fold" };
const ACTION_ORDER = ["3bet", "call", "fold"];
const PANEL_STATS = ["open_raise", "fold_to_3bet", "vpip", "fold_to_cbet", "river_bluff"];
const POSITIONS = ["UTG", "HJ", "CO", "BTN", "SB", "BB"];
// CSS class names can't start with a digit, so "3bet" maps to "threebet".
const CSS_CLASS = { "3bet": "threebet", call: "call", fold: "fold" };

export function createSolverView(app) {
  const state = { result: null, mode: "exploit", selected: "AA", requestId: 0, loading: false, error: null };
  const cells = new Map();

  function buildGrid() {
    const grid = $("hand-grid");
    for (let i = 0; i < 169; i += 1) {
      const cell = el("button", { type: "button", class: "cell", onclick: () => selectHand(cell.dataset.name) });
      grid.append(cell);
      cells.set(i, cell);
    }
  }

  const strategy = () => state.result?.[state.mode];
  const otherMode = () => (state.mode === "exploit" ? "baseline" : "exploit");

  function cellBackground(freqs) {
    // Left-to-right split: 3-bet, then call, then fold.
    let start = 0;
    const stops = ACTION_ORDER.map((action) => {
      const end = start + freqs[action] * 100;
      const stop = `var(--${CSS_CLASS[action]}) ${start}% ${end}%`;
      start = end;
      return stop;
    });
    return `linear-gradient(to right, ${stops.join(", ")})`;
  }

  function renderGrid() {
    const hands = strategy()?.hands;
    hands?.forEach((hand, i) => {
      const cell = cells.get(i);
      cell.textContent = hand.name;
      cell.dataset.name = hand.name;
      cell.style.background = cellBackground(hand.freqs);
      cell.classList.toggle("selected", hand.name === state.selected);
    });
  }

  function renderStatus() {
    const status = $("grid-status");
    status.hidden = !(state.loading || state.error);
    status.textContent = state.error || "Solving…";
  }

  function renderOpponent() {
    const result = state.result;
    const name = app.playerName(app.opponent);
    $("mode-exploit").textContent = `Exploit ${name}`;
    $("edit-notes-link").href = `#players/${encodeURIComponent(app.opponent ?? "")}`;
    $("opponent-picker").replaceChildren(...app.players.map((p) => el(
      "button",
      { type: "button", class: p.slug === app.opponent ? "active" : null, onclick: () => app.setOpponent(p.slug) },
      el("strong", {}, p.name),
      el("small", {}, p.archetype ?? "No profile yet"),
    )));
    if (!result) return;
    $("opponent-heading").textContent = `Opponent in the ${result.spot.opener}`;
    const profile = result.profile;
    $("opponent-stats").replaceChildren(...PANEL_STATS.map((key) => {
      const stat = profile.stats[key];
      return el("div", { class: "stat-row" },
        el("span", { title: app.statDefinitions[key] ?? "" }, STAT_LABELS[key]),
        statBar(stat.estimate),
        el("span", { class: "value" }, pct(stat.estimate)),
        confidenceBadge(stat.confidence));
    }));
    $("opponent-quote").textContent = result.using_defaults
      ? `No AI profile for ${name} yet, so these are placeholder defaults. Add notes on the Players screen.`
      : profile.summary ? `From your notes: “${profile.summary}”` : "";
  }

  function renderSpot() {
    const spot = state.result?.spot ?? app.spot;
    $("spot-text").textContent =
      `Spot: ${spot.opener} opens ${spot.open_size} BB, you're on the ${spot.hero} · ${spot.stack} BB deep`;
    if (!state.result) return;
    $("seats").replaceChildren(...spot.seats.map((seat) => el(
      "div",
      { class: `seat${seat.position === spot.opener ? " villain" : ""}${seat.position === spot.hero ? " hero" : ""}` },
      el("div", { class: "pos" }, seat.position),
      el("div", { class: "stack" }, String(+seat.stack.toFixed(1))),
      el("div", { class: "who" }, seat.label),
    )));
    $("pot").textContent = `${spot.start_pot} BB`;
    $("to-call").textContent = `${spot.to_call} BB`;
    $("pot-odds").textContent = pct(spot.pot_odds);
  }

  function renderRange() {
    const current = strategy();
    if (!current) return;
    const spot = state.result.spot;
    const sizes = { "3bet": spot.three_bet_to, call: spot.to_call, fold: null };
    $("range-heading").textContent = `Your whole range here · vs ${state.result.opponent}`;
    $("range-tiles").replaceChildren(...ACTION_ORDER.map((action) => {
      const { share, combos } = current.summary[action];
      return el("div", { class: `range-tile ${CSS_CLASS[action]}` },
        el("span", { class: "label" }, [ACTION_LABELS[action], sizes[action]].filter(Boolean).join(" ")),
        el("span", { class: "pct" }, pct(share, 1)),
        el("span", { class: "combos" }, `${combos} combos`));
    }));
    $("range-bar").replaceChildren(...ACTION_ORDER.map((action) =>
      el("i", { class: CSS_CLASS[action], style: { width: pct(current.summary[action].share, 2) } })));
  }

  function renderHand() {
    const current = strategy();
    const hand = current?.hands.find((h) => h.name === state.selected);
    if (!hand) return;
    const other = state.result[otherMode()].hands.find((h) => h.name === hand.name);
    $("hand-name").textContent = hand.name;
    $("hand-meta").textContent = `${hand.combos} combos · ${hand.kind}`;
    $("hand-bars").replaceChildren(...ACTION_ORDER.map((action) => el("div", { class: "hand-bar" },
      el("span", {}, ACTION_LABELS[action]),
      el("div", { class: "bar" }, el("i", { class: CSS_CLASS[action], style: { width: pct(hand.freqs[action]) } })),
      el("span", { class: "value" }, pct(hand.freqs[action])))));
    $("hand-why").textContent = hand.why;
    const label = state.mode === "exploit" ? "Compared with GTO:" : `Compared with exploiting ${state.result.opponent}:`;
    $("hand-compare").replaceChildren(label, ...ACTION_ORDER.map((action) => {
      const delta = Math.round((hand.freqs[action] - other.freqs[action]) * 100);
      const sign = delta > 0 ? "+" : delta < 0 ? "−" : "±";
      return el("span", {}, ` ${ACTION_LABELS[action]} `, el("strong", {}, `${sign}${Math.abs(delta)}%`));
    }));
  }

  function render() {
    renderStatus();
    renderOpponent();
    renderSpot();
    renderGrid();
    renderRange();
    renderHand();
  }

  function selectHand(name) {
    state.selected = name;
    renderGrid();
    renderHand();
  }

  function setMode(mode) {
    state.mode = mode;
    document.querySelectorAll(".seg").forEach((b) => b.classList.toggle("active", b.dataset.mode === mode));
    render();
  }

  async function solve() {
    if (!app.opponent) {
      state.error = "Add a player on the Players screen first.";
      render();
      return;
    }
    const id = ++state.requestId;
    state.loading = true;
    state.error = null;
    render();
    try {
      const result = await api.solvePreflop({ ...app.spot, opponent: app.opponent });
      if (id !== state.requestId) return; // a newer request has started
      state.result = result;
    } catch (err) {
      if (id !== state.requestId) return;
      state.error = err.message;
    }
    state.loading = false;
    render();
  }

  // ----- New spot dialog -----

  function setupSpotDialog() {
    const dialog = $("spot-dialog");
    const form = $("spot-form");
    for (const name of ["opener", "hero"]) {
      form.elements[name].append(...POSITIONS.map((p) => el("option", { value: p }, p)));
    }
    $("new-spot-btn").addEventListener("click", () => {
      for (const [key, value] of Object.entries(app.spot)) form.elements[key].value = value;
      $("spot-error").hidden = true;
      dialog.showModal();
    });
    $("spot-cancel").addEventListener("click", () => dialog.close());
    form.addEventListener("submit", (event) => {
      const spot = {
        opener: form.elements.opener.value,
        hero: form.elements.hero.value,
        open_size: Number(form.elements.open_size.value),
        three_bet_to: Number(form.elements.three_bet_to.value),
        stack: Number(form.elements.stack.value),
      };
      const problem = validateSpot(spot);
      if (problem) {
        event.preventDefault();
        $("spot-error").textContent = problem;
        $("spot-error").hidden = false;
        return;
      }
      app.spot = spot;
      solve();
    });
  }

  function validateSpot(spot) {
    if (POSITIONS.indexOf(spot.hero) <= POSITIONS.indexOf(spot.opener)) return "You must act after the opener.";
    if (spot.opener === "BB") return "The big blind can't open.";
    if (!(spot.open_size > 1 && spot.three_bet_to > spot.open_size && spot.three_bet_to <= spot.stack)) {
      return "Need: open size > 1, 3-bet bigger than the open, and no bigger than the stacks.";
    }
    return null;
  }

  buildGrid();
  setupSpotDialog();
  document.querySelectorAll(".seg").forEach((b) => b.addEventListener("click", () => setMode(b.dataset.mode)));

  return { solve, render };
}
