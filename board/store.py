"""The board's data on the control unit: the cards pushed from the desktop, and the player's edits.

    data_dir/cards.json             the base: dobro-transcription's `board/board.py json`, put here by
                                    tools/push-board (written as cards.json.new, then renamed)
    data_dir/changes.jsonl          the player's edits not yet on the desktop, one JSON object a line
    data_dir/changes.applied.jsonl  the edits the desktop has applied to cards.toml (moved here by
                                    tools/pull-board once the new base is pushed)
    data_dir/changes.lock           taken (flock) for every append and every archive

The app shows the base with the pending edits applied in order, so an edit shows at once.

    python -m board.store datadir             print the data folder (what the tools write to)
    python -m board.store pending             print the pending changes (whole lines, read under the lock)
    python -m board.store archive < IDS       move the changes with these ids (one a line) to
                                              changes.applied.jsonl
Both read $ROOM_BOARD_CONFIG, else /etc/room-board.toml (--config another).
"""

from __future__ import annotations

import argparse
import copy
import fcntl
import json
import os
import re
import secrets
import sys
import tomllib
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SYSTEM_CONFIG = Path("/etc/room-board.toml")

# The data contract (dobro-transcription board/board.py)
STATUSES = ("backlog", "next", "doing", "waiting", "done")
WHO = ("player", "claude", "others")
# the kind of work it is for the player (2026-10-19): listening and evaluating; playing an instrument and/or
# singing; managing an AI agent (decisions, priorities, reviewing what it built, and all of Claude's software work);
# communicating with other people; working with the hardware
WORK = ("listen", "play", "agent", "people", "hardware")
EFFORT_PLAYER = ("", "5m", "15m", "30m", "1h", "2h", "half-day", "day+")
EFFORT_CLAUDE = ("", "S", "M", "L", "XL")
IMPORTANCE = (1, 2, 3)
SETTABLE = ("status", "importance", "who", "work", "effort_player", "effort_claude", "waiting_on", "area")
TITLE_MAX = 200
TEXT_MAX = 300  # waiting_on
NOTE_MAX = 4000


class Invalid(ValueError):
    """An edit the contract does not allow (the server answers 422)."""


class NotFound(KeyError):
    pass


def load_config(path: Path | str | None = None) -> dict:
    """The board's config: port, data_dir, and the apps it reads live cards from ([live])."""
    path = Path(path or os.environ.get("ROOM_BOARD_CONFIG") or SYSTEM_CONFIG)
    raw = tomllib.loads(path.read_text())
    d = Path(str(raw.get("data_dir", "~/board-data"))).expanduser()
    raw["data_dir"] = d if d.is_absolute() else REPO / d
    raw.setdefault("port", 8007)
    raw.setdefault("host", "0.0.0.0")
    raw.setdefault("live", {})
    return raw


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def slug(title: str) -> str:
    """A card's id from its title: lower case, words joined by hyphens, a short random suffix."""
    s = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    if len(s) > 40:  # at a word's end
        s = s[:41].rsplit("-", 1)[0] if "-" in s[:41] else s[:40]
    s = s.strip("-") or "card"
    return f"{s}-{secrets.token_hex(2)}"


def _str_list(v) -> list[str]:
    return [str(x) for x in v] if isinstance(v, list) else []


def normalize(card: dict) -> dict:
    """A card with every field of the contract (defaults where the base leaves one out)."""
    c = dict(card)
    c["id"] = str(c.get("id", ""))
    c["title"] = str(c.get("title", "")) or c["id"]
    c["area"] = str(c.get("area", ""))
    c["status"] = c.get("status") if c.get("status") in STATUSES else "backlog"
    c["importance"] = c.get("importance") if c.get("importance") in IMPORTANCE else 2
    who = _str_list(c.get("who", ["player"]))
    c["who"] = [w for w in WHO if w in who]  # the contract's order
    c["work"] = c.get("work") if c.get("work") in WORK else ""
    c["effort_player"] = c.get("effort_player") if c.get("effort_player") in EFFORT_PLAYER else ""
    c["effort_claude"] = c.get("effort_claude") if c.get("effort_claude") in EFFORT_CLAUDE else ""
    c["waiting_on"] = str(c.get("waiting_on") or "")
    c["blocked_by"] = _str_list(c.get("blocked_by"))
    c["links"] = [l for l in c.get("links", []) if isinstance(l, dict) and l.get("href")] if isinstance(c.get("links"), list) else []
    c["details"] = str(c.get("details") or "")
    c["source"] = str(c.get("source") or "")
    for k in ("created", "updated"):
        c[k] = str(c.get(k) or "")
    c["done"] = c.get("done") or None
    c["notes"] = [n for n in c.get("notes", []) if isinstance(n, dict)] if isinstance(c.get("notes"), list) else []
    c["pending"] = 0
    c["pending_changes"] = []
    return c


def check_set(fields: dict, areas: set[str]) -> dict:
    """The fields of a "set" change, checked against the contract (raises Invalid)."""
    if not isinstance(fields, dict) or not fields:
        raise Invalid("nothing to set")
    out = {}
    for k, v in fields.items():
        if k not in SETTABLE:
            raise Invalid(f"{k} cannot be set here (only {', '.join(SETTABLE)})")
        if k == "status" and v not in STATUSES:
            raise Invalid(f"status must be one of {', '.join(STATUSES)}")
        if k == "importance" and (isinstance(v, bool) or v not in IMPORTANCE):
            raise Invalid("importance must be 1, 2 or 3")
        if k == "who":
            if not isinstance(v, list) or not v or any(w not in WHO for w in v) or len(set(v)) != len(v):
                raise Invalid(f"who must be one or more of {', '.join(WHO)}")
            v = [w for w in WHO if w in v]  # the contract's order
        if k == "work" and v not in WORK:
            raise Invalid(f"work must be one of {', '.join(WORK)}")
        if k == "effort_player" and v not in EFFORT_PLAYER:
            raise Invalid(f"effort_player must be one of {', '.join(repr(e) for e in EFFORT_PLAYER)}")
        if k == "effort_claude" and v not in EFFORT_CLAUDE:
            raise Invalid(f"effort_claude must be one of {', '.join(repr(e) for e in EFFORT_CLAUDE)}")
        if k == "waiting_on":
            if not isinstance(v, str) or len(v) > TEXT_MAX:
                raise Invalid(f"waiting_on: text of {TEXT_MAX} characters at most")
            v = v.strip()
        if k == "area" and v not in areas:
            raise Invalid("no such area")
        out[k] = v
    return out


def _date(at: str) -> str:
    return str(at)[:10]


def _apply_set(card: dict, fields: dict, at: str) -> None:
    for k, v in fields.items():
        if k in SETTABLE:
            card[k] = copy.copy(v)
    if "status" in fields:
        # the contract's "done" date: shown here at once (board.py keeps its own when it applies the change)
        if fields["status"] == "done" and not card.get("done"):
            card["done"] = _date(at)
        elif fields["status"] != "done":
            card["done"] = None
    card["updated"] = _date(at)


def merge(base: dict, changes: list[dict]) -> dict:
    """The base with the pending changes applied in order: those whose ids are not in the base's "applied" (the
    changes board.py has folded into these cards; the archive after a sync may lag). Each card says how many are
    pending on it ("pending", "pending_changes"); a change for a card not there is left out (not lost: still in
    the log)."""
    folded = set(base.get("applied") or []) if isinstance(base.get("applied"), list) else set()
    cards: dict[str, dict] = {}
    for raw in base.get("cards", []) if isinstance(base.get("cards"), list) else []:
        if isinstance(raw, dict) and raw.get("id"):
            c = normalize(raw)
            cards[c["id"]] = c
    in_base = set(cards)
    areas = {a["id"] for a in base.get("areas", []) if isinstance(a, dict) and a.get("id")}
    for ch in changes:
        if ch.get("id") in folded:
            continue
        at = str(ch.get("at", ""))
        if isinstance(ch.get("new"), dict):
            c = normalize(ch["new"])
            if not c["id"] or c["id"] in in_base:  # already brought into the base (applied, not yet archived)
                continue
            c["new"] = True
            cards[c["id"]] = c
        elif "set" in ch and ch.get("card") in cards:
            c = cards[ch["card"]]
            try:
                fields = check_set(ch["set"], areas | {c["area"]})
            except Invalid:
                continue
            _apply_set(c, fields, at)
        elif "note" in ch and ch.get("card") in cards:
            c = cards[ch["card"]]
            text = str(ch["note"])
            if any(n.get("text") == text and n.get("at") == at for n in c["notes"]):
                continue  # the base has it already
            c["notes"].append({"at": at, "by": ch.get("by", "player"), "text": text, "pending": True})
        else:
            continue
        c["pending"] += 1
        c["pending_changes"].append(ch.get("id"))
    return {**base, "cards": list(cards.values())}


class Store:
    def __init__(self, data_dir: Path | str):
        self.dir = Path(data_dir)
        self.base_path = self.dir / "cards.json"
        self.changes_path = self.dir / "changes.jsonl"
        self.applied_path = self.dir / "changes.applied.jsonl"
        self.lock_path = self.dir / "changes.lock"
        self._base_key = None
        self._base = None

    # --- reading
    def base(self) -> dict | None:
        """The pushed cards.json (read again only when it changes), or None before the first push."""
        try:
            st = self.base_path.stat()
        except FileNotFoundError:
            return None
        key = (st.st_mtime_ns, st.st_size)
        if key != self._base_key:
            try:
                raw = json.loads(self.base_path.read_text())
            except (OSError, ValueError):
                return self._base  # being replaced: the last one read
            self._base, self._base_key = raw if isinstance(raw, dict) else None, key
        return self._base

    def changes(self) -> list[dict]:
        """The pending changes, in order; a line that is not a JSON object is skipped."""
        out = []
        try:
            text = self.changes_path.read_text()
        except FileNotFoundError:
            return out
        for line in text.splitlines():
            try:
                ch = json.loads(line)
            except ValueError:
                continue
            if isinstance(ch, dict):
                out.append(ch)
        return out

    def pending(self) -> list[dict]:
        """The changes not yet folded into the base (not in its "applied")."""
        folded = set((self.base() or {}).get("applied") or [])
        return [ch for ch in self.changes() if ch.get("id") not in folded]

    def board(self) -> dict:
        base = self.base() or {"version": 1, "generated": None, "areas": [], "cards": []}
        out = merge(base, self.changes())
        out["has_base"] = self.base() is not None
        return out

    def card(self, card_id: str) -> dict:
        for c in self.board()["cards"]:
            if c["id"] == card_id:
                return c
        raise NotFound(card_id)

    def areas(self) -> set[str]:
        return {a["id"] for a in (self.base() or {}).get("areas", []) if isinstance(a, dict) and a.get("id")}

    # --- writing
    @contextmanager
    def locked(self):
        self.dir.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def _append(self, change: dict) -> None:
        """One line on the end of changes.jsonl, in one write, synced (the lock held)."""
        line = (json.dumps(change, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
        fd = os.open(self.changes_path, os.O_RDWR | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            size = os.fstat(fd).st_size
            if size and os.pread(fd, 1, size - 1) != b"\n":
                line = b"\n" + line  # a torn last line stays on its own line, and this one whole
            while line:
                line = line[os.write(fd, line):]
            os.fsync(fd)
        finally:
            os.close(fd)

    def _change(self, **body) -> dict:
        return {"id": str(uuid.uuid4()), "at": now_iso(), "by": "player", **body}

    def _set(self, card: dict, fields: dict) -> tuple[dict, dict | None]:
        fields = check_set(fields, self.areas() | {card["area"]})
        fields = {k: v for k, v in fields.items() if card.get(k) != v}
        if not fields:
            return card, None
        change = self._change(card=card["id"], set=fields)
        self._append(change)
        return self.card(card["id"]), change

    def set(self, card_id: str, fields: dict) -> tuple[dict, dict | None]:
        """Set fields on a card. Returns the card as now shown and the change (None when nothing changed)."""
        with self.locked():
            return self._set(self.card(card_id), fields)

    def claude_next(self, card_id: str) -> tuple[dict, dict | None]:
        """"Claude: do this next": status next, Claude among who, importance 1 (one change)."""
        with self.locked():
            card = self.card(card_id)
            who = [w for w in WHO if w in card["who"] or w == "claude"]
            return self._set(card, {"status": "next", "who": who, "importance": 1})

    def note(self, card_id: str, text: str) -> tuple[dict, dict]:
        text = str(text or "").strip()
        if not text:
            raise Invalid("an empty note")
        if len(text) > NOTE_MAX:
            raise Invalid(f"a note of {NOTE_MAX} characters at most")
        with self.locked():
            self.card(card_id)
            change = self._change(card=card_id, note=text)
            self._append(change)
            return self.card(card_id), change

    def new(self, body: dict) -> tuple[dict, dict]:
        """A new card from the page: title, area, who and work; the rest defaults."""
        title = " ".join(str(body.get("title") or "").split())
        if not title:
            raise Invalid("a card needs a title")
        if len(title) > TITLE_MAX:
            raise Invalid(f"a title of {TITLE_MAX} characters at most")
        with self.locked():
            areas = self.areas()
            fields = {"status": body.get("status", "backlog"), "importance": body.get("importance", 2),
                      "who": body.get("who") or ["player"], "area": body.get("area", "")}
            if not body.get("work"):
                raise Invalid(f"a card needs its work: {', '.join(WORK)}")
            for k in ("work", "effort_player", "effort_claude", "waiting_on"):
                if body.get(k):
                    fields[k] = body[k]
            fields = check_set(fields, areas)
            details = str(body.get("details") or "")
            if len(details) > NOTE_MAX:
                raise Invalid(f"details of {NOTE_MAX} characters at most")
            taken = {c["id"] for c in self.board()["cards"]}
            card_id = slug(title)
            while card_id in taken:
                card_id = slug(title)
            today = now_iso()[:10]
            card = {"id": card_id, "title": title, "area": fields["area"], "status": fields["status"],
                    "importance": fields["importance"], "who": fields["who"], "work": fields["work"],
                    "effort_player": fields.get("effort_player", ""), "effort_claude": fields.get("effort_claude", ""),
                    "waiting_on": fields.get("waiting_on", ""), "blocked_by": [], "links": [], "details": details,
                    "source": "", "created": today, "updated": today,  # board.py names the source
                    "done": today if fields["status"] == "done" else None}
            change = self._change(new=card)
            self._append(change)
            return self.card(card_id), change

    def pending_text(self) -> str:
        """changes.jsonl as it is, whole lines only (what tools/pull-board takes to the desktop)."""
        with self.locked():
            try:
                text = self.changes_path.read_text()
            except FileNotFoundError:
                return ""
        return "".join(l for l in text.splitlines(keepends=True) if l.endswith("\n") and l.strip())

    def archive(self, ids: set[str]) -> tuple[int, int]:
        """Move the changes with these ids from changes.jsonl to changes.applied.jsonl (after a sync).
        The applied file is written (and synced) first, so a crash between leaves a change in both, never in
        neither. Returns (moved, left)."""
        ids = {str(i) for i in ids}
        with self.locked():
            try:
                lines = self.changes_path.read_text().splitlines(keepends=True)
            except FileNotFoundError:
                return 0, 0
            moved, kept = [], []
            for line in lines:
                try:
                    cid = json.loads(line).get("id")
                except (ValueError, AttributeError):
                    cid = None
                (moved if cid in ids else kept).append(line if line.endswith("\n") else line + "\n")
            if not moved:
                return 0, len(kept)
            fd = os.open(self.applied_path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
            try:
                data = "".join(moved).encode()
                while data:
                    data = data[os.write(fd, data):]
                os.fsync(fd)
            finally:
                os.close(fd)
            tmp = self.changes_path.with_name("changes.jsonl.new")
            with open(tmp, "w") as f:
                f.write("".join(kept))
                f.flush()
                os.fsync(f.fileno())
            tmp.replace(self.changes_path)
            return len(moved), len(kept)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("what", choices=["datadir", "pending", "archive"])
    ap.add_argument("--config", type=Path, help="default: $ROOM_BOARD_CONFIG or /etc/room-board.toml")
    a = ap.parse_args(argv)
    cfg = load_config(a.config)
    if a.what == "datadir":
        print(cfg["data_dir"])
        return
    if a.what == "pending":
        sys.stdout.write(Store(cfg["data_dir"]).pending_text())
        return
    ids = {line.strip() for line in sys.stdin if line.strip()}
    moved, left = Store(cfg["data_dir"]).archive(ids)
    print(f"archived {moved} change{'s' if moved != 1 else ''}; {left} still pending")


if __name__ == "__main__":
    main()
