// Solver screen: choose who's in the hand, then play preflop seat by seat.
// The API works in big blinds; everything shown here is in dollars.

import { api } from "./api.js";
import { $, confidenceBadge, el, pct, statBar, STAT_LABELS } from "./dom.js";

const YOU = "you";
const COLUMN = { fold: 0, call: 1, raise: 2 }; // order of the API's frequency arrays
const DISPLAY_ORDER = ["raise", "call", "fold"];
const CSS_CLASS = { raise: "threebet", call: "call", fold: "fold" };
const ACTOR_STATS = ["open_raise", "vpip", "three_bet", "call_open", "fold_to_3bet"];

export function createSolverView(app) {
  const state = {
    table: [], // [{player, stack}] with stack in dollars
    actions: [], // [{kind, to}] with "to" in big blinds
    mode: "predicted",
    raiseTo: null, // big blinds; null = the server's default size
    result: null,
    selected: "AA",
    loading: false,
    requestId: 0,
  };
  const cells = [];

  // ----- money -----

  const bb = () => app.config.big_blind;
  const toBB = (dollars) => dollars / bb();
  function money(amountBB, signed = false) {
    const dollars = amountBB * bb();
    const text = `$${Math.abs(dollars).toFixed(2)}`;
    return signed ? `${dollars >= 0 ? "+" : "-"}${text}` : text;
  }

  // ----- table setup -----

  const positionsFor = (count) => app.config.positions[count];
  const maxSeats = () => Math.min(app.players.length + 1, 9);

  function defaultTable(count, first = null) {
    const friends = app.players.map((p) => p.slug);
    if (first) friends.sort((a, b) => (a === first ? -1 : b === first ? 1 : 0));
    const order = positionsFor(count);
    const youAt = order.includes("BTN") ? order.indexOf("BTN") : order.length - 1;
    return order.map((_, i) => ({
      player: i === youAt ? YOU : friends.shift(),
      stack: app.config.stack,
    }));
  }

  function renderSetup() {
    const count = state.table.length;
    $("seat-count").replaceChildren(...Array.from({ length: Math.max(maxSeats() - 1, 0) }, (_, i) => {
      const n = i + 2;
      return el("option", { value: n, selected: n === count }, String(n));
    }));
    const options = [{ slug: YOU, name: "You" }, ...app.players];
    $("seat-rows").replaceChildren(...positionsFor(count).map((position, i) => el(
      "div", { class: "seat-row" },
      el("span", { class: "pos" }, position),
      el("select", {
        "aria-label": `Player in ${position}`,
        onchange: (e) => { state.table[i].player = e.target.value; },
      }, options.map((p) => el("option", { value: p.slug, selected: p.slug === state.table[i].player }, p.name))),
      el("label", {}, "$", el("input", {
        type: "number", step: "0.05", min: "0.10", value: state.table[i].stack,
        "aria-label": `Stack for ${position} in dollars`,
        onchange: (e) => { state.table[i].stack = Number(e.target.value); },
      })),
    )));
    const c = app.config;
    $("blinds-text").textContent =
      `Blinds $${c.small_blind.toFixed(2)} / $${c.big_blind.toFixed(2)} \u00b7 usual open $${c.open_size.toFixed(2)}`;
  }

  function tableIsValid() {
    return state.table.length >= 2
      && state.table.every((s) => s.player === YOU || app.players.some((p) => p.slug === s.player));
  }

  function showSetup(message = null) {
    if (!tableIsValid()) state.table = app.players.length ? defaultTable(maxSeats()) : [];
    $("setup").hidden = false;
    $("play").hidden = true;
    $("setup-error").hidden = !message;
    $("setup-error").textContent = message ?? "";
    $("deal-btn").disabled = !app.players.length;
    if (!app.players.length) {
      $("seat-rows").replaceChildren(el("p", { class: "muted" }, "Add your friends on the Players screen first."));
      return;
    }
    renderSetup();
  }

  async function deal() {
    state.actions = [];
    state.raiseTo = null;
    state.mode = "predicted";
    const error = await refresh();
    if (error) {
      showSetup(error);
      return;
    }
    $("setup").hidden = true;
    $("play").hidden = false;
  }

  // ----- talking to the server -----

  /** Re-analyse the hand; returns an error message or null. */
  async function refresh() {
    const id = ++state.requestId;
    state.loading = true;
    renderStatus();
    let error = null;
    try {
      const result = await api.hand({
        seats: state.table.map((s) => ({ player: s.player, stack: toBB(s.stack) })),
        actions: state.actions,
        mode: state.mode,
        raise_to: state.raiseTo,
      });
      if (id !== state.requestId) return null; // a newer request has started
      state.result = result;
      render();
    } catch (err) {
      if (id !== state.requestId) return null;
      error = err.message;
    }
    state.loading = false;
    renderStatus();
    return error;
  }

  async function act(kind, to = null) {
    state.actions.push(to === null ? { kind } : { kind, to });
    state.raiseTo = null;
    const error = await refresh();
    if (error) {
      state.actions.pop();
      showActionError(error);
    }
  }

  function showActionError(message) {
    $("action-error").textContent = message ?? "";
    $("action-error").hidden = !message;
  }

  // ----- rendering -----

  function renderStatus() {
    $("grid-status").hidden = !state.loading;
    $("grid-status").textContent = state.mode === "best" ? "Simulating\u2026" : "Working\u2026";
    document.querySelectorAll("#action-card button").forEach((b) => { b.disabled = state.loading; });
  }

  function buildGrid() {
    for (let i = 0; i < 169; i += 1) {
      const cell = el("button", { type: "button", class: "cell", onclick: () => selectHand(cell.dataset.name) });
      $("hand-grid").append(cell);
      cells.push(cell);
    }
  }

  function cellBackground(freqs) {
    let start = 0;
    const stops = DISPLAY_ORDER.map((action) => {
      const end = start + freqs[COLUMN[action]] * 100;
      const stop = `var(--${CSS_CLASS[action]}) ${start}% ${end}%`;
      start = end;
      return stop;
    });
    return `linear-gradient(to right, ${stops.join(", ")})`;
  }

  function actionLabel(action, analysis) {
    if (action === "raise") return `Raise to ${money(analysis.raise_to)}`;
    if (action === "call") return analysis.can_check ? "Check" : `Call ${money(state.result.state.legal.to_call)}`;
    return "Fold";
  }

  function renderGrid(analysis) {
    analysis.hands.forEach((hand, i) => {
      const cell = cells[i];
      cell.textContent = hand.name;
      cell.dataset.name = hand.name;
      cell.style.background = cellBackground(hand.freqs);
      cell.classList.toggle("out", !hand.in_range);
      cell.classList.toggle("close", hand.close);
      cell.classList.toggle("selected", hand.name === state.selected);
    });
  }

  function renderHeader(analysis) {
    const toggle = $("mode-toggle");
    if (!analysis) {
      $("actor-title").textContent = "Preflop is over";
      toggle.hidden = true;
      $("mode-caption").textContent = "";
      return;
    }
    $("actor-title").textContent = `${analysis.position} \u00b7 ${analysis.name} to act`;
    toggle.hidden = analysis.is_you;
    toggle.querySelectorAll(".seg").forEach((b) => b.classList.toggle("active", b.dataset.mode === analysis.mode));
    $("legend-call").textContent = analysis.can_check ? "Check" : "Call";
    $("legend-close").hidden = analysis.mode !== "best";
    $("mode-caption").textContent = analysis.mode === "predicted"
      ? `Predicted: what ${analysis.name}'s profile says they'll do with each hand, given what they've done so far.`
      : `Best play: the most profitable choice for ${analysis.is_you ? "you" : analysis.name} with each hand, `
        + "assuming everyone else plays "
        + "like their profile. Simulated with one round of responses (re-raises count as calls), hands scored by "
        + "all-in equity (no postflop play) and no side pots. ~ marks hands where the best two choices are within "
        + "the simulation's margin of error.";
  }

  function renderSeats() {
    const hand = state.result.state;
    $("seats").replaceChildren(...hand.seats.map((seat, i) => el(
      "div",
      { class: `seat${seat.folded ? " folded" : ""}${i === hand.actor ? " acting" : ""}` },
      el("div", { class: "pos" }, seat.position),
      el("div", { class: "stack" }, money(seat.stack)),
      el("div", { class: "who" }, seat.name),
      el("div", { class: "bet" }, seat.folded ? "Fold" : seat.all_in ? "All-in" : seat.bet > 0 ? money(seat.bet) : ""),
    )));
    const toCall = hand.legal?.to_call ?? 0;
    $("pot").textContent = money(hand.pot);
    $("to-call").textContent = money(toCall);
    $("pot-odds").textContent = toCall > 0 ? pct(toCall / (hand.pot + toCall)) : "-";
  }

  function renderActions(analysis) {
    const hand = state.result.state;
    $("action-buttons").hidden = !analysis;
    $("hand-over").hidden = Boolean(analysis);
    $("undo-btn").hidden = state.actions.length === 0;
    showActionError(null);
    if (!analysis) {
      $("action-heading").textContent = "Hand summary";
      const left = hand.players_left;
      $("hand-over").textContent = left.length === 1
        ? `${left[0]} wins the pot of ${money(hand.pot)}.`
        : `${left.join(", ")} see the flop with ${money(hand.pot)} in the pot. `
          + "Choosing the flop cards comes next (not built yet).";
      return;
    }
    $("action-heading").textContent = analysis.is_you ? "Choose your action" : `Choose ${analysis.name}'s action`;
    const call = $("call-btn");
    call.dataset.kind = analysis.can_check ? "check" : "call";
    call.textContent = actionLabel("call", analysis);
    const legal = hand.legal;
    $("raise-btn").parentElement.hidden = !legal.can_raise;
    const input = $("raise-input");
    input.min = (legal.min_raise_to * bb()).toFixed(2);
    input.max = (legal.max_raise_to * bb()).toFixed(2);
    input.value = (analysis.raise_to * bb()).toFixed(2);
    $("raise-btn").textContent = analysis.raise_to >= legal.max_raise_to ? "All-in" : "Raise to";
  }

  function renderLog() {
    const { log, state: hand } = state.result;
    const c = app.config;
    const [sb, bigBlind] = hand.seats.slice(-2);
    const blinds = el("li", { class: "muted" },
      `Blinds: ${sb.name} $${c.small_blind.toFixed(2)}, ${bigBlind.name} $${c.big_blind.toFixed(2)}`);
    const verbs = { fold: "folds", check: "checks", call: "calls" };
    $("action-log").replaceChildren(blinds, ...log.map((entry) => el("li", {},
      `${entry.position} ${entry.name} `,
      entry.kind === "raise" ? `raises to ${money(entry.to)}` : verbs[entry.kind],
      entry.fits_profile ? null : el("span", { class: "warn" }, " (not what their profile predicts)"))));
  }

  function renderStats(analysis) {
    const profile = analysis.profile;
    const slug = state.table[analysis.seat].player;
    $("stats-heading").textContent = analysis.is_you ? "You" : `${analysis.name}'s profile`;
    $("edit-notes-link").hidden = analysis.is_you;
    $("edit-notes-link").href = `#players/${encodeURIComponent(slug)}`;
    $("actor-stats").replaceChildren(...(analysis.is_you ? [] : ACTOR_STATS.map((key) => {
      const stat = profile.stats[key];
      return el("div", { class: "stat-row" },
        el("span", { title: app.statDefinitions[key] ?? "" }, STAT_LABELS[key]),
        statBar(stat.estimate),
        el("span", { class: "value" }, pct(stat.estimate)),
        confidenceBadge(stat.confidence));
    })));
    let note = "";
    if (analysis.is_you) {
      note = "There are no notes on you. When others' best play is worked out, your range is narrowed "
        + "using typical-player placeholder stats.";
    } else if (!analysis.has_profile) {
      note = `No AI profile for ${analysis.name} yet, so these are placeholder defaults.`;
    } else if (profile.summary) {
      note = `From your notes: \u201c${profile.summary}\u201d`;
    }
    $("actor-note").textContent = note;
  }

  function renderRange(analysis) {
    $("range-heading").textContent = analysis.is_you ? "Your range here" : `${analysis.name}'s range here`;
    $("range-tiles").replaceChildren(...DISPLAY_ORDER.map((action) => {
      const { share, combos } = analysis.summary[action];
      return el("div", { class: `range-tile ${CSS_CLASS[action]}` },
        el("span", { class: "label" }, actionLabel(action, analysis)),
        el("span", { class: "pct" }, pct(share, 1)),
        el("span", { class: "combos" }, `${combos} combos`));
    }));
    $("range-bar").replaceChildren(...DISPLAY_ORDER.map((action) =>
      el("i", { class: CSS_CLASS[action], style: { width: pct(analysis.summary[action].share, 2) } })));
  }

  function renderHand(analysis) {
    const hand = analysis.hands.find((h) => h.name === state.selected);
    const kind = hand.name.length === 2 ? "pair" : hand.name.endsWith("s") ? "suited" : "offsuit";
    const combos = { pair: 6, suited: 4, offsuit: 12 }[kind];
    $("hand-name").textContent = hand.name;
    $("hand-meta").textContent = `${combos} combos \u00b7 ${kind}`;
    $("hand-bars").replaceChildren(...DISPLAY_ORDER.map((action) => {
      const freq = hand.freqs[COLUMN[action]];
      const label = action === "call" && analysis.can_check ? "Check" : action[0].toUpperCase() + action.slice(1);
      return el("div", { class: "hand-bar" },
        el("span", {}, label),
        el("div", { class: "bar" }, el("i", { class: CSS_CLASS[action], style: { width: pct(freq) } })),
        el("span", { class: "value" }, pct(freq)));
    }));
    $("hand-evs").textContent = hand.evs
      ? "Average result: " + DISPLAY_ORDER
        .filter((action) => hand.evs[COLUMN[action]] !== null)
        .map((action) => `${actionLabel(action, analysis)} ${money(hand.evs[COLUMN[action]], action !== "fold")}`)
        .join(" \u00b7 ")
      : "";
    $("hand-why").textContent = hand.why;
  }

  function render() {
    const analysis = state.result.analysis;
    renderHeader(analysis);
    renderSeats();
    renderActions(analysis);
    renderLog();
    // The grid and side panels keep showing the last decision once the hand is over.
    if (!analysis) return;
    renderGrid(analysis);
    renderStats(analysis);
    renderRange(analysis);
    renderHand(analysis);
  }

  function selectHand(name) {
    const analysis = state.result?.analysis;
    if (!analysis) return;
    state.selected = name;
    renderGrid(analysis);
    renderHand(analysis);
  }

  // ----- wiring -----

  buildGrid();
  $("seat-count").addEventListener("change", (e) => {
    state.table = defaultTable(Number(e.target.value));
    renderSetup();
  });
  $("deal-btn").addEventListener("click", deal);
  $("mode-toggle").querySelectorAll(".seg").forEach((b) => b.addEventListener("click", () => {
    state.mode = b.dataset.mode;
    refresh();
  }));
  $("action-buttons").querySelectorAll("button[data-kind]").forEach((b) => b.addEventListener("click", () => {
    const kind = b.dataset.kind;
    act(kind, kind === "raise" ? toBB(Number($("raise-input").value)) : null);
  }));
  $("raise-input").addEventListener("change", (e) => {
    state.raiseTo = toBB(Number(e.target.value));
    refresh();
  });
  $("undo-btn").addEventListener("click", () => {
    state.actions.pop();
    state.raiseTo = null;
    refresh();
  });
  $("redeal-btn").addEventListener("click", deal);
  $("edit-table-btn").addEventListener("click", () => showSetup());

  return {
    /** Show the screen: the hand in progress, or the table setup. */
    show() {
      if ($("play").hidden || !state.result) showSetup();
    },
    /** Open the table setup with ``slug`` seated (used by "Play a hand"). */
    seat(slug) {
      if (!state.table.some((s) => s.player === slug)) {
        state.table = defaultTable(Math.max(state.table.length, 2), slug);
      }
      showSetup();
    },
    /** Players were added or removed: go back to setup if the table is no longer valid. */
    playersChanged() {
      if (!tableIsValid()) showSetup();
    },
  };
}
