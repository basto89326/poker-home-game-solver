// Players and Profile screens: notes editor, session notes and AI profile.
// The Profile tab is the same screen showing your own notes (slug "you")
// without the friends list.

import { api } from "./api.js";
import { $, confidenceBadge, el, pct, shortDate, statBar, STAT_LABELS } from "./dom.js";

const SAVE_DELAY_MS = 800;
const SELF = "you";
const SVG_NS = "http://www.w3.org/2000/svg";

function trashIcon() {
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("aria-hidden", "true");
  for (const d of ["M4 7h16", "M10 11v6", "M14 11v6", "M6 7l1 13h10l1-13", "M9 7V4h6v3"]) {
    const path = document.createElementNS(SVG_NS, "path");
    path.setAttribute("d", d);
    svg.append(path);
  }
  return svg;
}

export function createPlayersView(app) {
  const state = { slug: null, friendSlug: null, player: null, saveTimer: null, savedText: "" };
  const isSelf = () => state.slug === SELF;

  function renderList() {
    $("player-list").replaceChildren(...app.players.map((p) => el(
      "div", { class: "player-item" },
      el("button",
        { type: "button", class: `pick${p.slug === state.slug ? " active" : ""}`, onclick: () => select(p.slug) },
        el("strong", {}, p.name),
        el("small", {},
          `${p.archetype ?? "No profile yet"} \u00b7 ${p.note_count} note${p.note_count === 1 ? "" : "s"}`)),
      el("button",
        { type: "button", class: "trash", title: `Remove ${p.name}`, "aria-label": `Remove ${p.name}`,
          onclick: () => removePlayer(p) },
        trashIcon()),
    )));
  }

  function renderNotes() {
    const player = state.player;
    $("notes-heading").textContent = isSelf() ? "Notes on your game" : `Notes on ${player.name}`;
    $("notes-hint").textContent = isSelf()
      ? "Describe how you actually play, leaks and all. The AI turns this into your profile, which the "
        + "solver uses for your seat."
      : "Write the way you'd describe them to a friend. The AI turns this into the profile on the right.";
    $("notes-text").value = player.notes;
    state.savedText = player.notes;
    $("save-status").textContent = "";
    renderSessions();
  }

  function renderSessions() {
    const sessions = state.player.sessions;
    $("session-list").replaceChildren(...(sessions.length
      ? sessions.map((s) => el("li", {}, el("span", { class: "date" }, shortDate(s.date)), s.text))
      : [el("li", { class: "muted" }, "No session notes yet.")]));
  }

  function renderProfile() {
    const { profile, name } = state.player;
    $("solve-vs-btn").textContent = `Play a hand vs ${name}`;
    $("profile-empty").hidden = Boolean(profile);
    $("profile-body").hidden = !profile;
    $("profile-archetype").textContent = profile?.archetype ?? "";
    $("profile-updated").textContent = profile?.last_updated
      ? `Profile last updated ${shortDate(profile.last_updated)}` : "";
    if (!profile) return;
    $("stat-tiles").replaceChildren(...Object.keys(STAT_LABELS).map((key) => {
      const stat = profile.stats[key];
      return el("div", { class: "stat-tile" },
        el("div", { class: "top" },
          el("span", { title: app.statDefinitions[key] ?? "" }, STAT_LABELS[key]),
          confidenceBadge(stat.confidence)),
        el("div", { class: "value" }, pct(stat.estimate)),
        statBar(stat.estimate));
    }));
    $("tendencies").replaceChildren(...(profile.tendencies.length
      ? profile.tendencies.map((t) => el("li", {}, t))
      : [el("li", { class: "muted" }, "None in the notes yet.")]));
    $("modifiers").replaceChildren(...(profile.modifiers.length
      ? profile.modifiers.map((m) => el("span", { class: "chip" }, `${m.condition}: ${m.description}`))
      : [el("span", { class: "muted" }, "Nothing noted yet.")]));
  }

  async function select(slug) {
    await flushNotes();
    state.slug = slug;
    if (slug && slug !== SELF) state.friendSlug = slug;
    $("rebuild-error").hidden = true;
    $("view-players").classList.toggle("empty", !slug);
    $("view-players").classList.toggle("self", isSelf());
    renderList();
    if (!slug) {
      state.player = null;
      return;
    }
    try {
      state.player = await api.player(slug);
    } catch (err) {
      showError(err.message);
      return;
    }
    if (state.slug !== slug) return; // user clicked another player meanwhile
    renderNotes();
    renderProfile();
  }

  // ----- notes saving -----

  async function flushNotes() {
    clearTimeout(state.saveTimer);
    const text = $("notes-text").value;
    if (!state.player || text === state.savedText) return;
    const slug = state.slug;
    $("save-status").textContent = "Saving\u2026";
    try {
      await api.saveNotes(slug, text);
      state.savedText = text;
      state.player.notes = text;
      $("save-status").textContent = "Saved";
      await app.refreshPlayers();
      renderList();
    } catch (err) {
      $("save-status").textContent = `Not saved: ${err.message}`;
    }
  }

  function scheduleSave() {
    clearTimeout(state.saveTimer);
    $("save-status").textContent = "";
    state.saveTimer = setTimeout(flushNotes, SAVE_DELAY_MS);
  }

  // ----- actions -----

  function showError(message) {
    $("rebuild-error").textContent = message;
    $("rebuild-error").hidden = false;
  }

  async function addPlayer(event) {
    event.preventDefault();
    const input = $("add-player-name");
    try {
      const created = await api.createPlayer(input.value);
      input.value = "";
      await app.refreshPlayers();
      await select(created.slug);
    } catch (err) {
      showError(err.message);
    }
  }

  async function addSession(event) {
    event.preventDefault();
    const input = $("session-text");
    try {
      state.player.sessions = await api.addSession(state.slug, input.value);
      input.value = "";
      renderSessions();
      await app.refreshPlayers();
      renderList();
    } catch (err) {
      showError(err.message);
    }
  }

  async function rebuild() {
    const button = $("rebuild-btn");
    $("rebuild-error").hidden = true;
    button.disabled = true;
    button.textContent = "Rebuilding\u2026 (about 20 s)";
    try {
      await flushNotes();
      state.player.profile = await api.rebuildProfile(state.slug);
      renderProfile();
      await app.refreshPlayers();
      renderList();
    } catch (err) {
      showError(err.message);
    } finally {
      button.disabled = false;
      button.textContent = "Rebuild profile with AI";
    }
  }

  async function removePlayer(player) {
    const ok = window.confirm(`Remove ${player.name}? This permanently deletes their notes and profile.`);
    if (!ok) return;
    const removingSelected = player.slug === state.slug;
    if (removingSelected) {
      clearTimeout(state.saveTimer);
      state.player = null; // nothing left to save
    }
    try {
      await api.deletePlayer(player.slug);
    } catch (err) {
      showError(err.message);
      return;
    }
    if (state.friendSlug === player.slug) state.friendSlug = null;
    await app.refreshPlayers();
    if (removingSelected) await select(app.players[0]?.slug ?? null);
    else renderList();
  }

  $("notes-text").addEventListener("input", scheduleSave);
  $("notes-text").addEventListener("blur", flushNotes);
  $("add-player-form").addEventListener("submit", addPlayer);
  $("session-form").addEventListener("submit", addSession);
  $("rebuild-btn").addEventListener("click", rebuild);
  $("solve-vs-btn").addEventListener("click", () => app.playHandWith(state.slug));

  return {
    /** Players tab: select ``slug`` (or the last friend shown, or the first). */
    async show(slug) {
      const target = slug || state.friendSlug || app.players[0]?.slug || null;
      if (target !== state.slug || !state.player) await select(target);
      else renderList();
    },
    /** Profile tab: your own notes and profile. */
    async showSelf() {
      if (!isSelf() || !state.player) await select(SELF);
    },
    flush: flushNotes,
  };
}
