// App entry: shared state and hash routing (#solver, #players, #players/<slug>, #profile).

import { api } from "./api.js";
import { $ } from "./dom.js";
import { createPlayersView } from "./players.js";
import { createSolverView } from "./solver.js";

const app = {
  players: [],
  statDefinitions: {},
  config: null, // home-game settings in dollars, plus seat names by table size

  /** Reload the player list (after adding, removing or renaming). */
  async refreshPlayers() {
    app.players = await api.players();
    solverView?.playersChanged();
  },
  async playHandWith(slug) {
    await playersView.flush();
    solverView.seat(slug);
    location.hash = "#solver";
  },
};

let solverView = null; // created once the config has loaded
const playersView = createPlayersView(app);

async function route() {
  const [view, slug] = location.hash.replace(/^#/, "").split("/");
  const name = ["players", "profile"].includes(view) ? view : "solver";
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.view === name));
  $("view-solver").hidden = name !== "solver";
  $("view-players").hidden = name === "solver"; // the Profile tab reuses the Players screen
  if (name === "players") {
    await playersView.show(slug ? decodeURIComponent(slug) : null);
  } else if (name === "profile") {
    await playersView.showSelf();
  } else {
    await playersView.flush();
    solverView.show();
  }
}

async function start() {
  [app.statDefinitions, app.config] = await Promise.all([api.statDefinitions(), api.config()]);
  await app.refreshPlayers();
  solverView = createSolverView(app);
  window.addEventListener("hashchange", route);
  await route();
}

start();
