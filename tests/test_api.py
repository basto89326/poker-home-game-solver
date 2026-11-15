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


def test_config_is_home_game(client):
    body = client.get("/api/config").get_json()
    assert {k: body[k] for k in ("small_blind", "big_blind", "open_size", "stack")} == {
        "small_blind": 0.05, "big_blind": 0.10, "open_size": 0.50, "stack": 20.0}
    assert body["positions"]["6"] == ["UTG", "HJ", "CO", "BTN", "SB", "BB"]


def test_hand_play_through(client):
    client.post("/api/players", json={"name": "Dave"})
    seats = [{"player": "dave", "stack": 200}, {"player": "you", "stack": 200}]
    body = client.post("/api/hand", json={"seats": seats, "actions": [], "mode": "predicted"}).get_json()
    assert body["state"]["actor"] == 0  # heads-up: the small blind acts first
    assert body["analysis"]["mode"] == "predicted"
    assert body["analysis"]["has_profile"] is False  # no profile yet: placeholder defaults
    done = client.post("/api/hand", json={"seats": seats, "actions": [{"kind": "fold"}]}).get_json()
    assert done["state"]["complete"] and done["analysis"] is None


@pytest.mark.parametrize("seats, actions", [
    ([{"player": "dave"}, {"player": "dave"}], []),  # nobody is you
    ([{"player": "you"}, {"player": "nobody"}], []),  # unknown player
    ([{"player": "you"}, {"player": "dave"}], [{"kind": "check"}]),  # can't check facing the BB
    ([{"player": "you"}, {"player": "dave"}], [{"to": 5}]),  # missing kind
])
def test_hand_rejects_bad_requests(client, seats, actions):
    client.post("/api/players", json={"name": "Dave"})
    res = client.post("/api/hand", json={"seats": seats, "actions": actions})
    assert res.status_code in (400, 404)
    assert "error" in res.get_json()


def test_river_validation(client):
    res = client.post("/api/solve/river", json={"board": "Ks7d2c"})
    assert res.status_code == 400
    res = client.post("/api/solve/river", data=json.dumps({
        "board": "Ks7d2c9h3s", "hero_range": "AA", "villain_range": "KQ", "pot": 10, "stack": 20,
    }), content_type="application/json")
    assert res.status_code == 200


def test_delete_player(client, tmp_path):
    client.post("/api/players", json={"name": "Dave"})
    assert client.delete("/api/players/dave").status_code == 204
    assert client.get("/api/players").get_json() == []
    assert not (tmp_path / "notes" / "dave.md").exists()
    assert client.delete("/api/players/dave").status_code == 404


def test_your_profile_endpoints(client):
    assert client.get("/api/players").get_json() == []  # you're not listed as a friend
    client.put("/api/players/you/notes", json={"notes": "I limp too much."})
    assert client.get("/api/players/you").get_json()["notes"] == "I limp too much."
    assert client.delete("/api/players/you").status_code == 400


def test_your_seat_uses_your_profile_and_mode(client, tmp_path):
    from src.profiler.store import PlayerStore

    client.post("/api/players", json={"name": "Dave"})
    mine = Profile.from_dict({
        "name": "You", "archetype": "Nit",
        "stats": {s: {"estimate": 0.1, "confidence": "high"} for s in STAT_DEFINITIONS},
    })
    PlayerStore(tmp_path).save_profile("you", mine)
    seats = [{"player": "you"}, {"player": "dave"}]  # heads-up: you're the SB and act first
    body = client.post("/api/hand", json={"seats": seats, "actions": [], "your_mode": "predicted"}).get_json()
    analysis = body["analysis"]
    assert analysis["is_you"] and analysis["mode"] == "predicted" and analysis["has_profile"]
    assert analysis["profile"]["archetype"] == "Nit"
