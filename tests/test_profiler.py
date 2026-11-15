import json
import subprocess

import pytest

from src.profiler import builder
from src.profiler.builder import BuilderError, build_profile
from src.profiler.schema import STAT_DEFINITIONS, Profile, ProfileError, default_profile
from src.profiler.store import PlayerNotes, PlayerStore, slugify


def valid_profile_dict(**overrides) -> dict:
    data = {
        "name": "Dave",
        "archetype": "Calling station",
        "summary": "Calls everything.",
        "stats": {s: {"estimate": 0.3, "confidence": "medium"} for s in STAT_DEFINITIONS},
        "tendencies": ["Never folds top pair"],
        "modifiers": [{"condition": "Drinking", "description": "Looser", "adjustments": {"vpip": 0.1}}],
        "last_updated": "2026-11-15",
    }
    data.update(overrides)
    return data


class TestSchema:
    def test_round_trip(self):
        profile = Profile.from_dict(valid_profile_dict())
        assert Profile.from_dict(profile.to_dict()) == profile

    @pytest.mark.parametrize("change", [
        {"stats": {}},
        {"archetype": ""},
        {"tendencies": "not a list"},
        {"last_updated": "15 Nov"},
        {"modifiers": [{"condition": "x", "description": "y", "adjustments": {"made_up": 0.1}}]},
    ])
    def test_rejects_invalid(self, change):
        with pytest.raises(ProfileError):
            Profile.from_dict(valid_profile_dict(**change))

    def test_rejects_out_of_range_and_bad_confidence(self):
        data = valid_profile_dict()
        data["stats"]["vpip"] = {"estimate": 1.5, "confidence": "high"}
        with pytest.raises(ProfileError):
            Profile.from_dict(data)
        data["stats"]["vpip"] = {"estimate": 0.5, "confidence": "certain"}
        with pytest.raises(ProfileError):
            Profile.from_dict(data)

    def test_pfr_cannot_exceed_vpip(self):
        data = valid_profile_dict()
        data["stats"]["pfr"] = {"estimate": 0.9, "confidence": "low"}
        with pytest.raises(ProfileError):
            Profile.from_dict(data)

    def test_default_profile_is_valid_and_low_confidence(self):
        profile = default_profile("New")
        assert Profile.from_dict(profile.to_dict()) == profile
        assert {s.confidence for s in profile.stats.values()} == {"low"}


class TestStore:
    def test_markdown_round_trip(self):
        notes = PlayerNotes("Dave", "Calls a lot.\n\nSecond paragraph.")
        parsed = PlayerNotes.from_markdown(notes.to_markdown(), "dave")
        assert parsed == notes

    def test_create_save_and_sessions(self, tmp_path):
        store = PlayerStore(tmp_path)
        slug = store.create("Mary Jane")
        assert slug == "mary-jane"
        with pytest.raises(ValueError):
            store.create("mary jane")
        store.add_session_note(slug, "Bluffed twice", date="2026-11-01")
        notes = store.add_session_note(slug, "Folded a lot", date="2026-11-08")
        assert [s.date for s in notes.sessions] == ["2026-11-08", "2026-11-01"]
        assert notes.note_count == 2  # main notes are empty
        with pytest.raises(ValueError):
            store.add_session_note(slug, "   ")

    def test_profile_round_trip(self, tmp_path):
        store = PlayerStore(tmp_path)
        store.create("Dave")
        assert store.load_profile("dave") is None
        profile = Profile.from_dict(valid_profile_dict())
        store.save_profile("dave", profile)
        assert store.load_profile("dave") == profile

    def test_slugify_rejects_empty(self):
        with pytest.raises(ValueError):
            slugify("!!!")


class FakeCompleted:
    def __init__(self, stdout: str, returncode: int = 0, stderr: str = ""):
        self.stdout, self.returncode, self.stderr = stdout, returncode, stderr


class TestBuilder:
    @pytest.fixture(autouse=True)
    def no_api_key(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setattr(builder.shutil, "which", lambda exe: "/fake/claude")

    def fake_cli(self, monkeypatch, reply: str, **kwargs):
        calls = []

        def run(cmd, **run_kwargs):
            calls.append((cmd, run_kwargs))
            return FakeCompleted(json.dumps({"is_error": False, "result": reply}), **kwargs)

        monkeypatch.setattr(builder.subprocess, "run", run)
        return calls

    def test_builds_profile_from_cli_reply(self, monkeypatch):
        reply = "```json\n" + json.dumps(valid_profile_dict(name="Whoever")) + "\n```"
        calls = self.fake_cli(monkeypatch, reply)
        profile = build_profile("Dave", "Calls a lot", [])
        assert profile.name == "Dave"  # stored name wins
        cmd, kwargs = calls[0]
        assert "-p" in cmd and "--tools" in cmd and "--bare" not in cmd
        assert "Calls a lot" in kwargs["input"]

    def test_refuses_when_api_key_set(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        with pytest.raises(BuilderError, match="ANTHROPIC_API_KEY"):
            build_profile("Dave", "notes", [])

    def test_reports_invalid_reply(self, monkeypatch):
        self.fake_cli(monkeypatch, "Sorry, I can't do that.")
        with pytest.raises(BuilderError, match="JSON"):
            build_profile("Dave", "notes", [])

    def test_reports_invalid_profile(self, monkeypatch):
        self.fake_cli(monkeypatch, json.dumps(valid_profile_dict(stats={})))
        with pytest.raises(BuilderError, match="invalid"):
            build_profile("Dave", "notes", [])

    def test_reports_cli_failure(self, monkeypatch):
        self.fake_cli(monkeypatch, "", returncode=1, stderr="not logged in")
        with pytest.raises(BuilderError, match="not logged in"):
            build_profile("Dave", "notes", [])

    def test_reports_timeout(self, monkeypatch):
        def run(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd, 1)
        monkeypatch.setattr(builder.subprocess, "run", run)
        with pytest.raises(BuilderError, match="within"):
            build_profile("Dave", "notes", [])

    def test_needs_notes(self):
        with pytest.raises(BuilderError, match="no notes"):
            build_profile("Dave", "  ", [])


class TestSelf:
    def test_self_is_hidden_reserved_and_permanent(self, tmp_path):
        store = PlayerStore(tmp_path)
        store.ensure_self()
        store.create("Dave")
        assert store.slugs() == ["dave"]  # your own notes aren't a friend
        assert store.load_notes("you").name == "You"
        with pytest.raises(ValueError, match="reserved"):
            store.create("You")
        with pytest.raises(ValueError, match="can't be removed"):
            store.delete("you")

    def test_ensure_self_keeps_existing_notes(self, tmp_path):
        store = PlayerStore(tmp_path)
        store.ensure_self()
        store.save_notes("you", PlayerNotes("You", "I call too much."))
        store.ensure_self()
        assert store.load_notes("you").notes == "I call too much."
