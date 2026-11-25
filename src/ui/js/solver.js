// Solver screen: choose who's in the hand, then play it seat by seat and street by street
// (picking the flop, turn and river cards in between).
// The API works in big blinds; everything shown here is in dollars.

import { api } from "./api.js";
import { $, confidenceBadge, el, pct, statBar, STAT_LABELS } from "./dom.js";

const YOU = "you";
const COLUMN = { fold: 0, call: 1, raise: 2 }; // order of the API's frequency arrays
const DISPLAY_ORDER = ["raise", "call", "fold"];
const CSS_CLASS = { raise: "threebet", call: "call", fold: "fold" };
const ACTOR_STATS = {
  preflop: ["open_raise", "vpip", "three_bet", "call_open", "fold_to_3bet"],
  postflop: ["aggression", "fold_to_cbet", "river_bluff"],
};
const SUIT_SYMBOL = { c: "\u2663", d: "\u2666", h: "\u2665", s: "\u2660" };
const PICKER_SUITS = ["s", "h", "d", "c"];
const PICKER_RANKS = "AKQJT98765432";
const SOMETIMES = 0.05; // predicted actions taken at least this often are tagged "Sometimes"

export function createSolverView(app) {
  const state = {
    table: [], // [{player, stack}] with stack in dollars
    actions: [], // [{kind, to}] with "to" in big blinds
    boardSteps: [], // [{cards, at}]: board cards dealt once `at` actions had been taken
    picked: [], // cards chosen in the picker but not dealt yet
    winners: [], // once the hand is over: the seats picked as winners of each pot
    mode: "predicted", // on friends' turns
    yourMode: "best", // on your turn
    raiseTo: null, // big blinds; null = the server's default size
    result: null,
    selected: "AA",
    loading: false,
    requestId: 0,
  };
  const cells = [];

  // ----- money -----

  const bb = () => app.config.big_blind;
  // Every amount is a whole number of the smallest chip, the small blind ($0.05).
  const toChips = (dollars) => Math.round(dollars / app.config.small_blind) * app.config.small_blind;
  const toBB = (dollars) => toChips(dollars) / bb();
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
        onchange: (e) => {
          state.table[i].stack = Number(toChips(Number(e.target.value)).toFixed(2));
          e.target.value = state.table[i].stack;
        },
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

  function showSetup(message = null, note = null) {
    if (!tableIsValid()) state.table = app.players.length ? defaultTable(maxSeats()) : [];
    $("setup").hidden = false;
    $("setup-note").hidden = !note;
    $("setup-note").textContent = note ?? "";
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
    state.boardSteps = [];
    state.picked = [];
    state.raiseTo = null;
    const error = await refresh();
    if (error) {
      showSetup(error);
      return;
    }
    $("setup").hidden = true;
    $("play").hidden = false;
  }

  // ----- talking to the server -----

  function handRequest() {
    return {
      seats: state.table.map((s) => ({ player: s.player, stack: toBB(s.stack) })),
      actions: state.actions,
      board: board(),
      mode: state.mode,
      your_mode: state.yourMode,
      raise_to: state.raiseTo,
    };
  }

  /** Re-analyse the hand; returns an error message or null. */
  async function refresh() {
    const id = ++state.requestId;
    state.loading = true;
    renderStatus();
    let error = null;
    try {
      const result = await api.hand(handRequest());
      if (id !== state.requestId) return null; // a newer request has started
      state.result = result;
      // A pot only one player can win needs no choice.
      state.winners = (result.state.pots ?? []).map((pot) => (pot.eligible.length === 1 ? [...pot.eligible] : []));
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

  async function dealStreet() {
    state.boardSteps.push({ cards: [...state.picked], at: state.actions.length });
    state.picked = [];
    const error = await refresh();
    if (error) {
      state.picked = state.boardSteps.pop().cards;
      renderPicker();
      showActionError(error);
    }
  }

  function undo() {
    const last = state.boardSteps.at(-1);
    if (last && last.at === state.actions.length) state.boardSteps.pop();
    else state.actions.pop();
    state.picked = [];
    state.raiseTo = null;
    refresh();
  }

  const board = () => state.boardSteps.flatMap((step) => step.cards);

  /** Pay out the pots to the chosen winners, then set up the next hand (button moved on). */
  async function nextHand() {
    const seats = state.result.state.seats;
    $("next-hand-btn").disabled = true; // no double clicks: each would move the button again
    let settlement;
    try {
      settlement = (await api.hand({ ...handRequest(), winners: state.winners })).settlement;
    } catch (err) {
      showActionError(err.message);
      renderStatus();
      return;
    }
    const toDollars = (amountBB) => Number(toChips(amountBB * bb()).toFixed(2));
    const results = seats
      .map((seat, i) => ({ name: seat.name, won: toDollars(settlement.payouts[i]) }))
      .filter((r) => r.won > 0)
      .map((r) => `${r.name} won $${r.won.toFixed(2)}`);
    const table = state.table.map((s, i) => ({ player: s.player, stack: toDollars(settlement.stacks[i]) }));
    // The button moves one seat: everyone shifts one position (last hand's SB is the new BTN).
    state.table = [...table.slice(1), table[0]];
    state.result = null;
    const broke = state.table.filter((s) => s.stack <= 0).map((s) => nameOf(s.player));
    const note = `${results.join(", ")}. Stacks are updated and everyone has moved one seat (the button `
      + "passed on). Check the stacks, then deal the next hand."
      + (broke.length ? ` ${broke.join(" and ")} ${broke.length === 1 ? "is" : "are"} out of chips: `
        + "enter a rebuy amount or change the seat." : "");
    showSetup(null, note);
  }

  const nameOf = (slug) => (slug === YOU ? "You" : app.players.find((p) => p.slug === slug)?.name ?? slug);

  function showActionError(message) {
    $("action-error").textContent = message ?? "";
    $("action-error").hidden = !message;
  }

  // ----- rendering -----

  function renderStatus() {
    $("grid-status").hidden = !state.loading;
    $("grid-status").textContent = state.result?.analysis?.mode === "best" ? "Simulating\u2026" : "Working\u2026";
    document.querySelectorAll("#action-buttons button, .action-footer button").forEach((b) => {
      b.disabled = state.loading;
    });
    $("deal-street-btn").disabled = state.loading || state.picked.length !== cardsNeeded();
    $("next-hand-btn").disabled = state.loading || state.winners.some((w) => w.length === 0);
  }

  // ----- board cards -----

  const cardsNeeded = () => state.result?.state.cards_needed ?? 0;
  const cardText = (card) => `${card[0]}${SUIT_SYMBOL[card[1]]}`;

  function cardChip(card) {
    return el("span", { class: `playing-card suit-${card[1]}` }, cardText(card));
  }

  function renderPicker() {
    const hand = state.result.state;
    const used = new Set(hand.board);
    const needed = cardsNeeded();
    $("card-picker").replaceChildren(...PICKER_SUITS.flatMap((suit) => [...PICKER_RANKS].map((rank) => {
      const card = rank + suit;
      const picked = state.picked.includes(card);
      return el("button", {
        type: "button",
        class: `pick-card suit-${suit}${picked ? " picked" : ""}`,
        disabled: used.has(card),
        "aria-pressed": String(picked),
        onclick: () => togglePick(card),
      }, cardText(card));
    })));
    $("picker-chosen").textContent = `${state.picked.length} of ${needed} chosen`;
    $("deal-street-btn").textContent = `Deal the ${hand.needs_street}`;
    $("deal-street-btn").disabled = state.loading || state.picked.length !== needed;
  }

  function togglePick(card) {
    if (state.picked.includes(card)) state.picked = state.picked.filter((c) => c !== card);
    else if (state.picked.length < cardsNeeded()) state.picked.push(card);
    renderPicker();
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
    if (action === "raise") return `${analysis.opening_bet ? "Bet" : "Raise to"} ${money(analysis.raise_to)}`;
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
      const street = state.result.state.needs_street;
      $("actor-title").textContent = street ? `Choose the ${street}` : "Hand over";
      toggle.hidden = true;
      $("mode-caption").textContent = "";
      return;
    }
    const street = analysis.street[0].toUpperCase() + analysis.street.slice(1);
    $("actor-title").textContent = `${street} \u00b7 ${analysis.position} \u00b7 ${analysis.name} to act`;
    toggle.hidden = false;
    toggle.querySelectorAll(".seg").forEach((b) => b.classList.toggle("active", b.dataset.mode === analysis.mode));
    $("legend-call").textContent = analysis.can_check ? "Check" : "Call";
    $("legend-raise").textContent = analysis.opening_bet ? "Bet" : "Raise";
    $("legend-close").hidden = analysis.mode !== "best";
    const who = analysis.is_you ? "your" : `${analysis.name}'s`;
    $("mode-caption").textContent = analysis.mode === "predicted"
      ? `Predicted: what ${who} profile says ${analysis.is_you ? "you" : "they"}'ll do with each hand, `
        + "given the action so far. The amount next to each action is what it would win or lose on average, "
        + "assuming everyone else plays like their profile."
      : `Best play: the most profitable choice for ${analysis.is_you ? "you" : analysis.name} with each hand, `
        + "assuming everyone else plays "
        + "like their profile. Simulated with one round of responses (later bets and raises count as checks and "
        + "calls), then the rest of the board is dealt at random and hands are scored by all-in equity (no "
        + "betting on later streets) with no side pots. "
        + (analysis.street === "preflop" ? "" : "Each hand is averaged over its combos, so a specific suit "
          + "(e.g. a flush draw) can play differently. ")
        + "~ marks close calls: the best two choices are within 0.1 BB of each other.";
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
    $("board").hidden = hand.board.length === 0;
    $("board").replaceChildren(...hand.board.map(cardChip));
    const toCall = hand.legal?.to_call ?? 0;
    $("pot").textContent = money(hand.pot);
    $("to-call").textContent = money(toCall);
    $("pot-odds").textContent = toCall > 0 ? pct(toCall / (hand.pot + toCall)) : "-";
  }

  function renderShowdown() {
    const hand = state.result.state;
    const pots = hand.pots;
    const names = (seats) => seats.map((i) => hand.seats[i].name).join(", ");
    $("pot-winners").replaceChildren(...pots.map((pot, k) => {
      const title = pots.length === 1 ? "Pot" : k === 0 ? "Main pot" : `Side pot ${k}`;
      const head = el("div", { class: "pot-title" }, `${title} ${money(pot.amount)}`);
      if (pot.eligible.length === 1) {
        const text = hand.players_left.length === 1 ? `${names(pot.eligible)} wins it.`
          : `Goes back to ${names(pot.eligible)} (nobody else could match it).`;
        return el("div", { class: "pot-row" }, head, el("p", { class: "muted" }, text));
      }
      return el("div", { class: "pot-row" }, head,
        el("p", { class: "muted" }, `Who won? Tick more than one for a split pot. In it: ${names(pot.eligible)}.`),
        el("div", { class: "pot-choices" }, pot.eligible.map((seat) => el("label", {},
          el("input", {
            type: "checkbox",
            checked: state.winners[k].includes(seat),
            onchange: (e) => {
              state.winners[k] = e.target.checked ? [...state.winners[k], seat]
                : state.winners[k].filter((s) => s !== seat);
              renderStatus();
            },
          }),
          hand.seats[seat].name))));
    }));
    renderStatus();
  }

  function renderActions(analysis) {
    const hand = state.result.state;
    $("action-buttons").hidden = !analysis;
    $("hand-over").hidden = Boolean(analysis);
    $("board-picker").hidden = Boolean(analysis) || !hand.needs_street;
    $("showdown").hidden = !hand.pots;
    $("undo-btn").hidden = state.actions.length === 0 && state.boardSteps.length === 0;
    showActionError(null);
    if (!analysis) {
      const left = hand.players_left;
      if (hand.needs_street) {
        const needed = hand.cards_needed;
        $("action-heading").textContent = `Choose the ${hand.needs_street}`;
        $("hand-over").textContent = `${left.join(", ")} see the ${hand.needs_street} with ${money(hand.pot)} `
          + `in the pot. Pick ${needed === 1 ? "1 card" : `${needed} cards`}.`;
        renderPicker();
        return;
      }
      $("action-heading").textContent = "Hand summary";
      if (left.length === 1) $("hand-over").textContent = `${left[0]} wins the pot of ${money(hand.pot)}.`;
      else {
        $("hand-over").textContent = `${left.join(", ")} go to showdown with ${money(hand.pot)} in the pot`
          + (hand.street === "river" ? "." : " (at most one of them has chips left, so there's no more betting).")
          + " Pick who won, then deal the next hand. Profiles aren't changed.";
      }
      renderShowdown();
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
    $("raise-btn").textContent = analysis.raise_to >= legal.max_raise_to ? "All-in"
      : analysis.opening_bet ? "Bet" : "Raise to";
  }

  function renderLog() {
    const { log, state: hand } = state.result;
    const c = app.config;
    const [sb, bigBlind] = hand.seats.slice(-2);
    const blinds = el("li", { class: "muted" },
      `Blinds: ${sb.name} $${c.small_blind.toFixed(2)}, ${bigBlind.name} $${c.big_blind.toFixed(2)}`);
    const verbs = { fold: "folds", check: "checks", call: "calls" };
    $("action-log").replaceChildren(blinds, ...log.map((entry) => {
      if (entry.kind === "board") {
        const street = entry.street[0].toUpperCase() + entry.street.slice(1);
        return el("li", { class: "street" }, `${street}: ${entry.cards.map(cardText).join(" ")} `
          + `(pot ${money(entry.pot)})`);
      }
      const verb = entry.kind === "raise" ? `raises to ${money(entry.to)}`
        : entry.kind === "bet" ? `bets ${money(entry.to)}` : verbs[entry.kind];
      return el("li", {}, `${entry.position} ${entry.name} ${verb}`,
        entry.fits_profile ? null : el("span", { class: "warn" }, " (not what their profile predicts)"));
    }));
  }

  function renderStats(analysis) {
    const profile = analysis.profile;
    const slug = state.table[analysis.seat].player;
    $("stats-heading").textContent = analysis.is_you ? "Your profile" : `${analysis.name}'s profile`;
    $("edit-notes-link").href = analysis.is_you ? "#profile" : `#players/${encodeURIComponent(slug)}`;
    const keys = ACTOR_STATS[analysis.street === "preflop" ? "preflop" : "postflop"];
    $("actor-stats").replaceChildren(...keys.map((key) => {
      const stat = profile.stats[key];
      return el("div", { class: "stat-row" },
        el("span", { title: app.statDefinitions[key] ?? "" }, STAT_LABELS[key]),
        statBar(stat.estimate),
        el("span", { class: "value" }, pct(stat.estimate)),
        confidenceBadge(stat.confidence));
    }));
    let note = "";
    if (analysis.is_you && !analysis.has_profile) {
      note = "No profile for you yet, so these are placeholder 'typical player' stats. Add notes on the "
        + "Profile tab and rebuild to use your own.";
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
    const total = { pair: 6, suited: 4, offsuit: 12 }[kind];
    const combos = hand.combos === total ? `${total} combos` : `${hand.combos} of ${total} combos left`;
    $("hand-name").textContent = hand.name;
    $("hand-meta").textContent = `${combos} \u00b7 ${kind}`;
    // One action per hand: "Best" (best play) or "Likely" (predicted), plus
    // "Sometimes" for a predicted hand on the edge of two parts of their range.
    const top = DISPLAY_ORDER.reduce((a, b) => (hand.freqs[COLUMN[b]] > hand.freqs[COLUMN[a]] ? b : a));
    $("hand-bars").replaceChildren(...DISPLAY_ORDER.map((action) => {
      const freq = hand.freqs[COLUMN[action]];
      let tag = null;
      if (action === top) tag = analysis.mode === "best" ? "Best" : "Likely";
      else if (analysis.mode === "predicted" && freq >= SOMETIMES) tag = "Sometimes";
      const ev = hand.evs ? hand.evs[COLUMN[action]] : null;
      return el("div", { class: `hand-action${tag ? " on" : ""}` },
        el("i", { class: `swatch ${CSS_CLASS[action]}` }),
        el("span", {}, actionLabel(action, analysis)),
        el("span", { class: "ev", title: "Average result" }, ev === null ? "" : evText(ev, action !== "fold")),
        el("span", {}, tag ? el("span", { class: `tag${action === top ? "" : " secondary"}` }, tag) : null));
    }));
    $("hand-why").textContent = hand.why;
  }

  /** Average result in dollars and big blinds, e.g. "+$0.04 (+0.41 BB)". */
  function evText(amountBB, signed) {
    const rounded = Math.round(amountBB * 100) / 100 || 0; // "|| 0" turns -0 into 0, so no "-0.00 BB"
    const sign = signed ? (rounded >= 0 ? "+" : "-") : "";
    return `${money(rounded, signed)} (${sign}${Math.abs(rounded).toFixed(2)} BB)`;
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
    if (state.result?.analysis?.is_you) state.yourMode = b.dataset.mode;
    else state.mode = b.dataset.mode;
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
  $("undo-btn").addEventListener("click", undo);
  $("deal-street-btn").addEventListener("click", dealStreet);
  $("next-hand-btn").addEventListener("click", nextHand);
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
