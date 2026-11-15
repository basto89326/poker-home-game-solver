// App entry: shared state and hash routing (#solver, #players, #players/<slug>).

import { api } from "./api.js";
import { $ } from "./dom.js";
import { createPlayersView } from "./players.js";
import { createSolverView } from "./solver.js";

const app = {
  players: [],
  statDefinitions: {},
  opponent: null,
  spot: { opener: "CO", hero: "BTN", open_size: 2.5, three_bet_to: 8.5, stack: 100 },

  async refreshPlayers() {
    app.players = await api.players();
  },
  playerName(slug) {
    return app.players.find((p) => p.slug === slug)?.name ?? "";
  },
  setOpponent(slug) {
    app.opponent = slug;
    solverView.solve();
  },
  async solveAgainst(slug) {
    await playersView.flush();
    app.opponent = slug;
    location.hash = "#solver";
    solverView.solve();
  },
};

const solverView = createSolverView(app);
const playersView = createPlayersView(app);

async function route() {
  const [view, slug] = location.hash.replace(/^#/, "").split("/");
  const name = view === "players" ? "players" : "solver";
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.view === name));
  $("view-solver").hidden = name !== "solver";
  $("view-players").hidden = name !== "players";
  $("spot-header").hidden = name !== "solver";
  if (name === "players") {
    await playersView.show(slug ? decodeURIComponent(slug) : null);
  } else {
    await playersView.flush();
    solverView.render();
  }
}

async function start() {
  [app.statDefinitions] = await Promise.all([api.statDefinitions(), app.refreshPlayers()]);
  app.opponent = app.players[0]?.slug ?? null;
  window.addEventListener("hashchange", route);
  await route();
  solverView.solve();
}

start();
