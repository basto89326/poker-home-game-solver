// Thin wrapper around the Flask JSON API.

async function request(method, url, body) {
  const options = { method };
  if (body !== undefined) {
    options.headers = { "Content-Type": "application/json" };
    options.body = JSON.stringify(body);
  }
  const res = await fetch(url, options);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
  return data;
}

const playerUrl = (slug) => `/api/players/${encodeURIComponent(slug)}`;

export const api = {
  players: () => request("GET", "/api/players"),
  statDefinitions: () => request("GET", "/api/stats"),
  player: (slug) => request("GET", playerUrl(slug)),
  createPlayer: (name) => request("POST", "/api/players", { name }),
  saveNotes: (slug, notes) => request("PUT", `${playerUrl(slug)}/notes`, { notes }),
  addSession: (slug, text) => request("POST", `${playerUrl(slug)}/sessions`, { text }),
  rebuildProfile: (slug) => request("POST", `${playerUrl(slug)}/profile`),
  solvePreflop: (spot) => request("POST", "/api/solve/preflop", spot),
};
