"""Flask app: JSON endpoints under ``/api`` and the UI from ``src/ui``."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from flask import Flask, abort, jsonify, request, send_from_directory

from ..config import Config
from ..profiler.builder import BuilderError, build_profile
from ..profiler.schema import STAT_DEFINITIONS, Profile, ProfileError, default_profile
from ..profiler.store import PlayerStore
from ..solver.preflop import PreflopSpot, solve_spot
from ..solver.river import RiverSpot, solve_river

UI_DIR = Path(__file__).resolve().parents[1] / "ui"


def _json_body() -> dict[str, Any]:
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        abort(400, description="expected a JSON object")
    return body


def create_app(config: Config) -> Flask:
    """Build the app; all state lives on disk under ``config.data_root``."""
    app = Flask(__name__, static_folder=None)
    store = PlayerStore(config.data_root)

    def require_player(slug: str) -> None:
        if not store.exists(slug):
            abort(404, description=f"no player {slug!r}")

    def profile_or_default(slug: str) -> tuple[Profile, bool]:
        """The player's profile, or placeholder defaults (second value True) if none."""
        profile = store.load_profile(slug)
        if profile is not None:
            return profile, False
        return default_profile(store.load_notes(slug).name), True

    def player_summary(slug: str) -> dict[str, Any]:
        notes = store.load_notes(slug)
        profile = store.load_profile(slug)
        return {"slug": slug, "name": notes.name, "note_count": notes.note_count,
                "archetype": profile.archetype if profile else None}

    @app.errorhandler(400)
    @app.errorhandler(404)
    def client_error(err):
        return jsonify(error=err.description), err.code

    # ----- UI ---------------------------------------------------------------

    @app.get("/")
    def index():
        return send_from_directory(UI_DIR, "index.html")

    @app.get("/ui/<path:filename>")
    def ui_file(filename: str):
        return send_from_directory(UI_DIR, filename)

    # ----- players ------------------------------------------------------------

    @app.get("/api/players")
    def list_players():
        return jsonify([player_summary(s) for s in store.slugs()])

    @app.post("/api/players")
    def create_player():
        name = str(_json_body().get("name", "")).strip()
        try:
            slug = store.create(name)
        except ValueError as exc:
            abort(400, description=str(exc))
        return jsonify(player_summary(slug)), 201

    @app.get("/api/players/<slug>")
    def get_player(slug: str):
        require_player(slug)
        notes = store.load_notes(slug)
        profile = store.load_profile(slug)
        return jsonify({
            "slug": slug,
            "name": notes.name,
            "notes": notes.notes,
            "sessions": [asdict(s) for s in notes.sessions],
            "profile": profile.to_dict() if profile else None,
        })

    @app.put("/api/players/<slug>/notes")
    def save_notes(slug: str):
        require_player(slug)
        text = _json_body().get("notes")
        if not isinstance(text, str):
            abort(400, description="'notes' must be text")
        notes = store.load_notes(slug)
        notes.notes = text
        store.save_notes(slug, notes)
        return jsonify(player_summary(slug))

    @app.post("/api/players/<slug>/sessions")
    def add_session(slug: str):
        require_player(slug)
        body = _json_body()
        try:
            notes = store.add_session_note(slug, str(body.get("text", "")), body.get("date") or None)
        except ValueError as exc:
            abort(400, description=str(exc))
        return jsonify([asdict(s) for s in notes.sessions]), 201

    @app.post("/api/players/<slug>/profile")
    def rebuild_profile(slug: str):
        require_player(slug)
        notes = store.load_notes(slug)
        try:
            profile = build_profile(notes.name, notes.notes,
                                    [f"{s.date}: {s.text}" for s in notes.sessions],
                                    executable=config.claude_executable,
                                    model=config.claude_model, timeout=config.claude_timeout)
        except BuilderError as exc:
            return jsonify(error=str(exc)), 502
        store.save_profile(slug, profile)
        return jsonify(profile.to_dict())

    @app.get("/api/stats")
    def stat_definitions():
        return jsonify(STAT_DEFINITIONS)

    # ----- solver -------------------------------------------------------------

    @app.post("/api/solve/preflop")
    def preflop():
        body = _json_body()
        slug = str(body.get("opponent", ""))
        require_player(slug)
        try:
            spot = PreflopSpot(
                opener=str(body.get("opener", "CO")),
                hero=str(body.get("hero", "BTN")),
                open_size=float(body.get("open_size", 2.5)),
                three_bet_to=float(body.get("three_bet_to", 8.5)),
                stack=float(body.get("stack", 100)),
            )
            profile, using_defaults = profile_or_default(slug)
            result = solve_spot(spot, profile)
            return jsonify({**result, "profile": profile.to_dict(), "using_defaults": using_defaults})
        except (TypeError, ValueError, ProfileError) as exc:
            abort(400, description=str(exc))

    @app.post("/api/solve/river")
    def river():
        body = _json_body()
        slug = body.get("opponent")
        if slug:
            require_player(slug)
        try:
            spot = RiverSpot(
                board=str(body["board"]),
                hero_range=str(body["hero_range"]),
                villain_range=str(body["villain_range"]),
                pot=float(body["pot"]),
                stack=float(body["stack"]),
                hero_first=bool(body.get("hero_first", False)),
                bet_fractions=[float(f) for f in body.get("bet_fractions", [0.75])],
            )
            profile = profile_or_default(slug)[0] if slug else None
            return jsonify(solve_river(spot, profile))
        except KeyError as exc:
            abort(400, description=f"missing field {exc}")
        except (TypeError, ValueError, ProfileError) as exc:
            abort(400, description=str(exc))

    return app
