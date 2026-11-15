// Players screen: player list, notes editor, session notes and AI profile.

import { api } from "./api.js";
import { $, confidenceBadge, el, pct, shortDate, statBar, STAT_LABELS } from "./dom.js";

const SAVE_DELAY_MS = 800;

export function createPlayersView(app) {
  const state = { slug: null, player: null, saveTimer: null, savedText: "" };

  function renderList() {
    $("player-list").replaceChildren(...app.players.map((p) => el(
      "button",
      { type: "button", class: p.slug === state.slug ? "active" : null, onclick: () => select(p.slug) },
      el("strong", {}, p.name),
      el("small", {}, `${p.archetype ?? "No profile yet"} \u00b7 ${p.note_count} note${p.note_count === 1 ? "" : "s"}`),
    )));
  }

  function renderNotes() {
    const player = state.player;
    $("notes-heading").textContent = `Notes on ${player.name}`;
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
    $("rebuild-error").hidden = true;
    $("view-players").classList.toggle("empty", !slug);
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

  async function removePlayer() {
    const { name } = state.player;
    const ok = window.confirm(`Remove ${name}? This permanently deletes their notes and profile.`);
    if (!ok) return;
    clearTimeout(state.saveTimer);
    const slug = state.slug;
    try {
      await api.deletePlayer(slug);
    } catch (err) {
      showError(err.message);
      return;
    }
    state.player = null; // nothing left to save
    await app.refreshPlayers();
    await select(app.players[0]?.slug ?? null);
  }

  $("notes-text").addEventListener("input", scheduleSave);
  $("notes-text").addEventListener("blur", flushNotes);
  $("add-player-form").addEventListener("submit", addPlayer);
  $("session-form").addEventListener("submit", addSession);
  $("rebuild-btn").addEventListener("click", rebuild);
  $("remove-player-btn").addEventListener("click", removePlayer);
  $("solve-vs-btn").addEventListener("click", () => app.playHandWith(state.slug));

  return {
    /** Show the screen, selecting ``slug`` (or keep the current / first player). */
    async show(slug) {
      const target = slug || state.slug || app.players[0]?.slug || null;
      if (target !== state.slug || !state.player) await select(target);
      else renderList();
    },
    flush: flushNotes,
  };
}
