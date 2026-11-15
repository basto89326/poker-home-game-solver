import json

import pytest

from src.api import app as app_module
from src.config import Config
from src.profiler.schema import Profile, STAT_DEFINITIONS


@pytest.fixture
def client(tmp_path):
    app = app_module.create_app(Config(data_root=tmp_path))
    return app.test_client()


def test_player_lifecycle(client):
    assert client.get("/api/players").get_json() == []
    res = client.post("/api/players", json={"name": "Dave"})
    assert res.status_code == 201
    assert client.post("/api/players", json={"name": "Dave"}).status_code == 400

    client.put("/api/players/dave/notes", json={"notes": "Calls everything."})
    client.post("/api/players/dave/sessions", json={"text": "Called three streets", "date": "2026-11-08"})
    player = client.get("/api/players/dave").get_json()
    assert player["notes"] == "Calls everything."
    assert player["sessions"] == [{"date": "2026-11-08", "text": "Called three streets"}]
    assert player["profile"] is None
    assert client.get("/api/players").get_json()[0]["note_count"] == 2


def test_unknown_player_and_bad_body(client):
    assert client.get("/api/players/nobody").status_code == 404
    res = client.post("/api/players", data="not json", content_type="application/json")
    assert res.status_code == 400
    assert "error" in res.get_json()


def test_rebuild_profile_reports_builder_errors(client, monkeypatch):
    client.post("/api/players", json={"name": "Dave"})

    def failing(*args, **kwargs):
        raise app_module.BuilderError("CLI missing")

    monkeypatch.setattr(app_module, "build_profile", failing)
    res = client.post("/api/players/dave/profile")
    assert res.status_code == 502
    assert res.get_json()["error"] == "CLI missing"


def test_rebuild_profile_saves_result(client, monkeypatch):
    client.post("/api/players", json={"name": "Dave"})
    profile = Profile.from_dict({
        "name": "Dave", "archetype": "Calling station",
        "stats": {s: {"estimate": 0.3, "confidence": "low"} for s in STAT_DEFINITIONS},
    })
    monkeypatch.setattr(app_module, "build_profile", lambda *a, **k: profile)
    assert client.post("/api/players/dave/profile").status_code == 200
    assert client.get("/api/players/dave").get_json()["profile"]["archetype"] == "Calling station"


def test_preflop_uses_defaults_without_profile(client):
    client.post("/api/players", json={"name": "Dave"})
    res = client.post("/api/solve/preflop", json={"opponent": "dave"})
    body = res.get_json()
    assert res.status_code == 200
    assert body["using_defaults"] is True
    assert len(body["exploit"]["hands"]) == 169
    bad = client.post("/api/solve/preflop", json={"opponent": "dave", "hero": "UTG"})
    assert bad.status_code == 400


def test_river_validation(client):
    res = client.post("/api/solve/river", json={"board": "Ks7d2c"})
    assert res.status_code == 400
    res = client.post("/api/solve/river", data=json.dumps({
        "board": "Ks7d2c9h3s", "hero_range": "AA", "villain_range": "KQ", "pot": 10, "stack": 20,
    }), content_type="application/json")
    assert res.status_code == 200
