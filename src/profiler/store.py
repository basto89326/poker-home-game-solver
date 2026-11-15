"""Reading and writing player notes (Markdown) and profiles (JSON).

A notes file ``notes/<slug>.md`` looks like::

    # Dave

    Free-text notes...

    ## Session notes
    - 2026-11-08: Called three streets with second pair.

and the matching profile lives in ``profiles/<slug>.json``.

Your own notes use the reserved slug :data:`SELF_SLUG` ("you"). They are
stored the same way but never listed among your friends.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .schema import Profile

SELF_SLUG = "you"
SELF_NAME = "You"
SESSION_HEADING = "## Session notes"
_SESSION_LINE = re.compile(r"^- (\d{4}-\d{2}-\d{2}): (.+)$")


@dataclass
class SessionNote:
    date: str
    text: str


@dataclass
class PlayerNotes:
    name: str
    notes: str = ""
    sessions: list[SessionNote] = field(default_factory=list)

    @property
    def note_count(self) -> int:
        """Main notes (if any) plus one per session note."""
        return int(bool(self.notes.strip())) + len(self.sessions)

    def to_markdown(self) -> str:
        parts = [f"# {self.name}", "", self.notes.strip(), "", SESSION_HEADING]
        parts += [f"- {s.date}: {s.text}" for s in self.sessions]
        return "\n".join(parts).rstrip() + "\n"

    @classmethod
    def from_markdown(cls, text: str, fallback_name: str) -> "PlayerNotes":
        lines = text.splitlines()
        name = fallback_name
        if lines and lines[0].startswith("# "):
            name = lines[0][2:].strip() or fallback_name
            lines = lines[1:]
        body, sessions = [], []
        in_sessions = False
        for line in lines:
            if line.strip() == SESSION_HEADING:
                in_sessions = True
            elif in_sessions:
                match = _SESSION_LINE.match(line.strip())
                if match:
                    sessions.append(SessionNote(match[1], match[2].strip()))
            else:
                body.append(line)
        return cls(name, "\n".join(body).strip(), sessions)


def slugify(name: str) -> str:
    """File-safe id for a player name, e.g. ``"Mary Jane" -> "mary-jane"``."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    if not slug:
        raise ValueError("player name needs at least one letter or digit")
    return slug


class PlayerStore:
    """Notes and profiles on disk, under one data root."""

    def __init__(self, root: Path) -> None:
        self.notes_dir = root / "notes"
        self.profiles_dir = root / "profiles"

    def _notes_path(self, slug: str) -> Path:
        return self.notes_dir / f"{slug}.md"

    def _profile_path(self, slug: str) -> Path:
        return self.profiles_dir / f"{slug}.json"

    def slugs(self) -> list[str]:
        """Your friends' slugs (your own notes are not included)."""
        if not self.notes_dir.exists():
            return []
        return sorted(p.stem for p in self.notes_dir.glob("*.md") if p.stem != SELF_SLUG)

    def ensure_self(self) -> None:
        """Create your own (empty) notes file if it doesn't exist yet."""
        if not self.exists(SELF_SLUG):
            self.save_notes(SELF_SLUG, PlayerNotes(SELF_NAME))

    def exists(self, slug: str) -> bool:
        return self._notes_path(slug).exists()

    def load_notes(self, slug: str) -> PlayerNotes:
        path = self._notes_path(slug)
        if not path.exists():
            raise KeyError(slug)
        return PlayerNotes.from_markdown(path.read_text(encoding="utf-8"), slug.title())

    def save_notes(self, slug: str, notes: PlayerNotes) -> None:
        self.notes_dir.mkdir(parents=True, exist_ok=True)
        self._notes_path(slug).write_text(notes.to_markdown(), encoding="utf-8")

    def create(self, name: str) -> str:
        """Create an empty notes file for a new player; returns the slug."""
        slug = slugify(name)
        if slug == SELF_SLUG:
            raise ValueError(f"{name!r} is reserved for your own profile; pick another name")
        if self.exists(slug):
            raise ValueError(f"a player called {name!r} already exists")
        self.save_notes(slug, PlayerNotes(name.strip()))
        return slug

    def delete(self, slug: str) -> None:
        """Permanently remove a player's notes and profile."""
        if slug == SELF_SLUG:
            raise ValueError("your own profile can't be removed")
        if not self.exists(slug):
            raise KeyError(slug)
        self._notes_path(slug).unlink()
        self._profile_path(slug).unlink(missing_ok=True)

    def add_session_note(self, slug: str, text: str, date: str | None = None) -> PlayerNotes:
        text = " ".join(text.split())
        if not text:
            raise ValueError("session note is empty")
        date = date or dt.date.today().isoformat()
        dt.date.fromisoformat(date)  # raises ValueError if malformed
        notes = self.load_notes(slug)
        notes.sessions.append(SessionNote(date, text))
        notes.sessions.sort(key=lambda s: s.date, reverse=True)
        self.save_notes(slug, notes)
        return notes

    def load_profile(self, slug: str) -> Profile | None:
        path = self._profile_path(slug)
        if not path.exists():
            return None
        return Profile.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def save_profile(self, slug: str, profile: Profile) -> None:
        self.profiles_dir.mkdir(parents=True, exist_ok=True)
        self._profile_path(slug).write_text(json.dumps(profile.to_dict(), indent=2) + "\n",
                                            encoding="utf-8")
