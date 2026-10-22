"""tools/push-board and tools/pull-board against a temporary data folder: locally (--to, --from) and through
ssh (a fake ssh that runs the command in a temporary home laid out as the unit's), with a fake board.py that
keeps its cards as JSON and does what dobro-transcription's does with apply (each change once, applied.txt;
a refusal reported and the rest applied, exit 1; the goals' changes too), fmt and json ("applied"); and with the
real board.py on a copy of its cards, when that checkout is here."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from board.store import Store

REPO = Path(__file__).resolve().parent.parent
FIXTURE = REPO / "tests" / "fixtures" / "board-cards.json"
GOALS = REPO / "tests" / "fixtures" / "board-goals.json"
PUSH, PULL = REPO / "tools" / "push-board", REPO / "tools" / "pull-board"

FAKE_BOARD_PY = r'''
"""A stand-in for dobro-transcription's board/board.py: cards.toml is JSON here, applied.txt beside it."""
import json, os, sys
from pathlib import Path
here = Path(__file__).parent
state, calls, done = here / "cards.toml", here / "calls.log", here / "applied.txt"
what = sys.argv[1]
with open(calls, "a") as f:
    f.write(" ".join(sys.argv[1:]) + "\n")
if os.environ.get("FAKE_FAIL") == what:
    sys.exit(f"{what} went wrong")
board = json.loads(state.read_text())
applied = done.read_text().split() if done.exists() else []
if what == "apply":
    (here / "seen.jsonl").write_text(Path(sys.argv[2]).read_text())  # what apply was given, as given
    by = {c["id"]: c for c in board["cards"]}
    goals = {g["id"]: g for g in board.get("goals", [])}
    problems = []
    for line in Path(sys.argv[2]).read_text().splitlines():
        ch = json.loads(line)
        if ch["id"] in applied:
            continue
        if "goal" in ch:  # the goals' ops: a stage (a history entry), a note
            if ch["goal"] not in goals:
                problems.append(f"no goal {ch['goal']!r}"); continue
            g = goals[ch["goal"]]
            if "stage" in ch:
                g["history"].append({"date": ch["at"][:10], "stage": ch["stage"], "note": ch["note"]})
            else:
                g["notes"].append({"date": ch["at"][:10], "text": ch["note"], "by": ch["by"]})
        elif "new" in ch:
            by[ch["new"]["id"]] = ch["new"]; board["cards"].append(ch["new"])
        elif ch.get("card") not in by:
            problems.append(f"no card {ch.get('card')!r}"); continue
        elif "set" in ch:
            by[ch["card"]].update(ch["set"])
        elif "note" in ch:
            by[ch["card"]]["details"] += "\n\nNotes:\n\n- " + ch["note"]
        applied.append(ch["id"])
    state.write_text(json.dumps(board))
    done.write_text("".join(i + "\n" for i in applied))
    for p in problems:
        print(f"not applied: {p}", file=sys.stderr)
    print(f"{len(applied)} applied, {len(problems)} not applied")
    if problems:
        sys.exit(1)
    during = os.environ.get("FAKE_EDIT_DURING")  # the player taps something while the sync runs
    if during:
        with open(Path(during) / "changes.jsonl", "a") as f:
            f.write(json.dumps({"id": "during-the-sync", "at": "2026-10-20T10:00:00-07:00", "by": "player",
                                "card": "retro-record", "set": {"status": "doing"}}) + "\n")
elif what == "json":
    if os.environ.get("FAKE_BAD_JSON"):
        board["cards"][0]["status"] = "someday"
    print(json.dumps(board | {"applied": applied}))
'''


@pytest.fixture
def desk(tmp_path):
    """A dobro-transcription checkout with the fake board.py, and the unit's data folder with the fixture."""
    repo = tmp_path / "dobro-transcription"
    (repo / "board").mkdir(parents=True)
    (repo / "board" / "board.py").write_text(FAKE_BOARD_PY)
    shutil.copy(FIXTURE, repo / "board" / "cards.toml")
    data = tmp_path / "home" / "board-data"
    data.mkdir(parents=True)
    shutil.copy(FIXTURE, data / "cards.json")
    return repo, data


@pytest.fixture
def unit(tmp_path, desk, monkeypatch):
    """A fake ssh: `ssh HOST CMD` runs CMD in a home laid out as the unit's (practice-recorder/ with the board
    package and its venv's python), its config in ROOM_BOARD_CONFIG."""
    home = tmp_path / "home"
    tree = home / "practice-recorder"
    (tree / ".venv" / "bin").mkdir(parents=True)
    (tree / "board").symlink_to(REPO / "board")
    (tree / ".venv" / "bin" / "python").symlink_to(sys.executable)
    cfg = tmp_path / "room-board.toml"
    cfg.write_text(f'port = 8007\ndata_dir = "{desk[1]}"\n')
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    (bin_ / "ssh").write_text(f'#!/bin/sh\nhost=$1; shift\ncd "{home}" && exec sh -c "$*"\n')
    (bin_ / "ssh").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("ROOM_BOARD_CONFIG", str(cfg))
    return home


def run(*args, env=None, ok=True):
    r = subprocess.run([str(a) for a in args], capture_output=True, text=True, env={**os.environ, **(env or {})})
    if ok:
        assert r.returncode == 0, r.stderr + r.stdout
    return r


def calls(repo):
    p = repo / "board" / "calls.log"
    return p.read_text().splitlines() if p.exists() else []


def test_push_board_to_a_folder(tmp_path):
    to = tmp_path / "data"
    r = run(PUSH, FIXTURE, "--to", to)
    assert "46 cards in 7 areas" in r.stdout
    assert json.loads((to / "cards.json").read_text()) == json.loads(FIXTURE.read_text())
    assert not (to / "cards.json.new").exists()
    piped = subprocess.run([str(PUSH), "--to", str(to)], input=FIXTURE.read_text(), capture_output=True, text=True)
    assert piped.returncode == 0


@pytest.mark.parametrize("change", [
    lambda b: b["cards"][0].update(status="someday"),
    lambda b: b["cards"][0].update(work="musical"),
    lambda b: b["cards"][1].update(id=b["cards"][0]["id"]),
    lambda b: b["cards"][0].update(area="nowhere"),
    lambda b: b["cards"][0].update(who=[]),
    lambda b: b.update(version=2),
])
def test_push_board_refuses_what_the_board_cannot_read(tmp_path, change):
    to = tmp_path / "data"
    run(PUSH, FIXTURE, "--to", to)
    before = (to / "cards.json").read_text()
    bad = json.loads(FIXTURE.read_text())
    change(bad)
    r = subprocess.run([str(PUSH), "--to", str(to)], input=json.dumps(bad), capture_output=True, text=True)
    assert r.returncode != 0 and r.stderr
    assert (to / "cards.json").read_text() == before


def test_push_board_with_goals(tmp_path):
    to = tmp_path / "data"
    r = run(PUSH, GOALS, "--to", to)
    assert "46 cards in 7 areas, 6 goals" in r.stdout and not r.stderr
    shared = json.loads(GOALS.read_text())
    shared["goals"][1]["color"] = shared["goals"][0]["color"]
    r = subprocess.run([str(PUSH), "--to", str(to)], input=json.dumps(shared), capture_output=True, text=True)
    assert r.returncode == 0 and "share the color gold" in r.stderr  # each is named, so allowed
    unnumbered = json.loads(GOALS.read_text())
    unnumbered["cards"][0]["number"] = None  # a card added by hand, not yet through board.py fmt
    del unnumbered["cards"][1]["number"]
    r = subprocess.run([str(PUSH), "--to", str(to)], input=json.dumps(unnumbered), capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


@pytest.mark.parametrize("change", [
    lambda b: b["goals"][0].update(color="plum"),
    lambda b: b["goals"][0]["history"][0].update(stage="idea"),  # the stages are numbers since 2026-10-20
    lambda b: b["goals"][0]["history"][0].update(stage=11),
    lambda b: b["goals"][0]["history"][0].update(stage=True),
    lambda b: b["goals"][0]["history"].reverse(),
    lambda b: b["goals"][0]["history"][0].pop("date"),
    lambda b: b["goals"][1].update(id=b["goals"][0]["id"]),
    lambda b: b["goals"][0]["notes"][0].update(by="bob"),
    lambda b: b["goals"][0].update(shipped=["a list"]),
    lambda b: b["cards"][0].update(goals=["nowhere"]),
    lambda b: b["cards"][0].update(goals="solo-dobro"),
    lambda b: b["cards"][0].update(goals=["solo-dobro", "solo-dobro"]),
    lambda b: b.update(stages=b["stages"][:9]),
    lambda b: b.update(goals={"solo-dobro": {}}),
    # the cards' numbers and the mark "ready" (2026-10-21)
    lambda b: b["cards"][1].update(number=b["cards"][0]["number"]),
    lambda b: b["cards"][0].update(number=0),
    lambda b: b["cards"][0].update(number="7"),
    lambda b: b["cards"][0].update(number=True),
    lambda b: b["cards"][0].update(ready="yes"),
])
def test_push_board_refuses_goals_the_board_cannot_read(tmp_path, change):
    to = tmp_path / "data"
    run(PUSH, GOALS, "--to", to)
    before = (to / "cards.json").read_text()
    bad = json.loads(GOALS.read_text())
    change(bad)
    r = subprocess.run([str(PUSH), "--to", str(to)], input=json.dumps(bad), capture_output=True, text=True)
    assert r.returncode != 0 and "not as the board reads them" in r.stderr, r.stderr
    assert (to / "cards.json").read_text() == before


def test_push_board_through_ssh(tmp_path, desk, unit):
    repo, data = desk
    base = json.loads(FIXTURE.read_text())
    base["generated"] = "2026-10-20T08:00:00-07:00"
    r = subprocess.run([str(PUSH), "--host", "fake"], input=json.dumps(base), capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert f"on fake in {data}" in r.stdout
    assert json.loads((data / "cards.json").read_text())["generated"] == "2026-10-20T08:00:00-07:00"
    assert not (data / "cards.json.new").exists()


def edits(data):
    s = Store(data)
    a = s.set("retro-record", {"status": "next", "importance": 1})[1]
    b = s.new({"title": "Tune the dulcimer", "area": "strings", "work": "play"})[1]
    c = s.note("ab-takes", "try it on the iPad")[1]
    return [a["id"], b["id"], c["id"]], b["new"]["id"]


@pytest.mark.parametrize("via", ["ssh", "local"])
def test_pull_board_brings_the_edits_in_and_archives_them(desk, unit, via):
    repo, data = desk
    ids, new_id = edits(data)
    where = ["--host", "fake"] if via == "ssh" else ["--from", data]
    r = run(PULL, *where, "--repo", repo, "--python", sys.executable, env={"FAKE_EDIT_DURING": str(data)})
    assert "brought in 3 edits" in r.stdout and "(1 new, 1 note, 1 set)" in r.stdout
    assert "archived 3" in r.stdout and "1 still pending" in r.stdout
    assert [c.split()[0] for c in calls(repo)] == ["apply", "fmt", "json"]
    # cards.toml has them, and so has the base pushed back, with their ids in "applied"
    toml = json.loads((repo / "board" / "cards.toml").read_text())
    base = json.loads((data / "cards.json").read_text())
    assert base == toml | {"applied": ids}
    by = {c["id"]: c for c in base["cards"]}
    assert by["retro-record"]["status"] == "next" and by["retro-record"]["importance"] == 1
    assert by[new_id]["title"] == "Tune the dulcimer" and by["ab-takes"]["details"].endswith("try it on the iPad")
    # the three moved by their ids; the edit made during the sync is still pending, and shown
    applied = [json.loads(l)["id"] for l in (data / "changes.applied.jsonl").read_text().splitlines()]
    left = [json.loads(l)["id"] for l in (data / "changes.jsonl").read_text().splitlines()]
    assert applied == ids and left == ["during-the-sync"]
    s = Store(data)
    assert s.card("retro-record")["status"] == "doing" and s.card("retro-record")["pending"] == 1
    assert s.card(new_id)["pending"] == 0 and "new" not in s.card(new_id)


@pytest.mark.parametrize("via", ["ssh", "local"])
def test_pull_board_passes_the_goals_edits_to_apply(desk, unit, via):
    """A goal moved, a note on a goal, a card's goals: given to board.py apply as the app wrote them, applied there,
    pushed back and archived."""
    repo, data = desk
    shutil.copy(GOALS, repo / "board" / "cards.toml")
    shutil.copy(GOALS, data / "cards.json")
    s = Store(data)
    made = [s.move_goal("solo-dobro", 6, "Vibrato keys")[1], s.goal_note("ai-detector", "Ask the players")[1],
            s.set("retro-record", {"goals": ["practice-room", "solo-dobro"]})[1]]
    ids = [ch["id"] for ch in made]
    where = ["--host", "fake"] if via == "ssh" else ["--from", data]
    r = run(PULL, *where, "--repo", repo, "--python", sys.executable)
    assert "brought in 3 edits" in r.stdout and "(1 goal note, 1 goal stage, 1 set)" in r.stdout
    assert [json.loads(l) for l in (repo / "board" / "seen.jsonl").read_text().splitlines()] == made
    toml = json.loads((repo / "board" / "cards.toml").read_text())
    goals = {g["id"]: g for g in toml["goals"]}
    assert goals["solo-dobro"]["history"][-1] == {"date": made[0]["at"][:10], "stage": 6, "note": "Vibrato keys"}
    assert goals["ai-detector"]["notes"][-1] == {"date": made[1]["at"][:10], "text": "Ask the players", "by": "player"}
    assert next(c for c in toml["cards"] if c["id"] == "retro-record")["goals"] == ["practice-room", "solo-dobro"]
    base = json.loads((data / "cards.json").read_text())
    assert base["applied"] == ids and pending_ids(data) == []
    s = Store(data)
    g = s.goal("solo-dobro")
    assert g["stage"] == 6 and g["pending"] == 0 and len(g["history"]) == 6  # the base's own entry, not laid over again
    assert s.card("retro-record")["goals"] == ["practice-room", "solo-dobro"] and s.card("retro-record")["pending"] == 0


def test_pull_board_reports_a_goal_apply_refuses(desk, unit):
    repo, data = desk
    shutil.copy(GOALS, data / "cards.json")
    shutil.copy(GOALS, repo / "board" / "cards.toml")
    gone = Store(data).goal_note("ai-detector", "Ask the players")[1]["id"]
    cards = json.loads((repo / "board" / "cards.toml").read_text())  # the goal taken off the desktop meanwhile
    cards["goals"] = [g for g in cards["goals"] if g["id"] != "ai-detector"]
    for c in cards["cards"]:
        c["goals"] = [x for x in c["goals"] if x != "ai-detector"]
    (repo / "board" / "cards.toml").write_text(json.dumps(cards))
    r = run(PULL, "--from", data, "--repo", repo, "--python", sys.executable, ok=False)
    assert r.returncode == 1 and "no goal 'ai-detector'" in r.stderr and kept_ids(r) == [gone]
    assert pending_ids(data) == [gone]  # still pending; and not shown, its goal gone from the base
    assert "ai-detector" not in {g["id"] for g in Store(data).board()["goals"]}


def pending_ids(data):
    return [json.loads(l)["id"] for l in (data / "changes.jsonl").read_text().splitlines()]


def kept_ids(r):
    assert "kept in" in r.stderr, r.stderr
    kept = Path(r.stderr.split("kept in ")[-1].strip())
    out = [json.loads(l)["id"] for l in kept.read_text().splitlines()]
    kept.unlink()
    return out


@pytest.mark.parametrize("fail", [{"FAKE_FAIL": "fmt"}, {"FAKE_FAIL": "json"}, {"FAKE_BAD_JSON": "1"}])
def test_pull_board_archives_nothing_unless_the_push_succeeded(desk, unit, fail):
    repo, data = desk
    ids, _ = edits(data)
    before = (data / "cards.json").read_text()
    r = run(PULL, "--host", "fake", "--repo", repo, "--python", sys.executable, env=fail, ok=False)
    assert r.returncode != 0 and kept_ids(r) == ids
    assert (data / "cards.json").read_text() == before
    assert pending_ids(data) == ids and not (data / "changes.applied.jsonl").exists()
    # the next pull takes up from there: apply skips what it applied, and the rest follows
    r = run(PULL, "--host", "fake", "--repo", repo, "--python", sys.executable)
    assert "brought in 3 edits" in r.stdout and pending_ids(data) == []
    assert json.loads((data / "cards.json").read_text())["applied"] == ids


def test_pull_board_pushes_and_archives_the_rest_when_apply_refuses_one(desk, unit):
    repo, data = desk
    ids, _ = edits(data)
    gone = Store(data).set("ab-takes", {"status": "doing"})[1]["id"]
    cards = json.loads((repo / "board" / "cards.toml").read_text())  # the card deleted on the desktop meanwhile
    cards["cards"] = [c for c in cards["cards"] if c["id"] != "ab-takes" and "ab-takes" not in c["blocked_by"]]
    (repo / "board" / "cards.toml").write_text(json.dumps(cards))
    r = run(PULL, "--host", "fake", "--repo", repo, "--python", sys.executable, ok=False)
    assert r.returncode == 1 and "not applied: no card 'ab-takes'" in r.stderr
    assert f"2 not applied, still pending on fake" in r.stderr  # the note on ab-takes, and the move
    assert "brought in 2 edits" in r.stdout
    assert set(kept_ids(r)) == set(ids) | {gone}
    assert pending_ids(data) == [ids[2], gone]  # the rest archived
    base = json.loads((data / "cards.json").read_text())
    assert base["applied"] == ids[:2] and "ab-takes" not in {c["id"] for c in base["cards"]}


def test_pull_board_when_apply_fails_outright(desk, unit):
    repo, data = desk
    ids, _ = edits(data)
    r = run(PULL, "--host", "fake", "--repo", repo, "--python", sys.executable, env={"FAKE_FAIL": "apply"}, ok=False)
    assert r.returncode == 1 and "3 not applied" in r.stderr and kept_ids(r) == ids
    assert pending_ids(data) == ids and not (data / "changes.applied.jsonl").exists()
    assert json.loads((data / "cards.json").read_text())["applied"] == []  # the same cards, pushed again


def test_pull_board_with_nothing_to_bring_in(desk, unit):
    repo, data = desk
    r = run(PULL, "--host", "fake", "--repo", repo, "--python", sys.executable)
    assert "no edits on fake" in r.stdout and calls(repo) == []
    (data / "changes.jsonl").write_text('{"id": "half a li')  # a line being written is not taken
    r = run(PULL, "--from", data, "--repo", repo, "--python", sys.executable)
    assert "no edits" in r.stdout and calls(repo) == []


def test_pull_board_needs_the_repo(tmp_path):
    r = run(PULL, "--from", tmp_path, "--repo", tmp_path / "nowhere", ok=False)
    assert r.returncode != 0 and "no board/board.py" in r.stderr


def real_board() -> Path | None:
    root = Path(os.environ.get("DOBRO_TRANSCRIPTION") or REPO.parent / "dobro-transcription")
    return root / "board" if (root / "board" / "board.py").is_file() else None


@pytest.mark.skipif(real_board() is None, reason="needs dobro-transcription's board/board.py ($DOBRO_TRANSCRIPTION)")
def test_the_real_board_py_round_trip_on_a_copy(tmp_path):
    """dobro-transcription's own board.py and cards, copied (the checkout's are not touched): its JSON pushed and
    shown, three edits made in the app, pulled, applied, pushed back and archived."""
    from fastapi.testclient import TestClient
    from board.server import create_app
    src = real_board()
    repo = tmp_path / "dobro-transcription"
    (repo / "board").mkdir(parents=True)
    for name in ("board.py", "cards.toml", "applied.txt"):
        if (src / name).exists():
            shutil.copy(src / name, repo / "board" / name)
    data = tmp_path / "board-data"
    made = subprocess.run([sys.executable, "board/board.py", "json"], cwd=repo, capture_output=True, text=True, check=True)
    pushed = subprocess.run([str(PUSH), "--to", str(data)], input=made.stdout, capture_output=True, text=True)
    assert pushed.returncode == 0, pushed.stderr
    base = json.loads((data / "cards.json").read_text())
    with TestClient(create_app({"data_dir": data, "live": {}})) as c:
        b = c.get("/api/board").json()
        assert len(b["cards"]) == len(base["cards"]) and b["pending"] == 0
        assert c.get("/api/status").json()["state"]
        card = next(x for x in b["cards"] if x["status"] == "backlog")
        a = c.patch(f"/api/cards/{card['id']}", json={"status": "doing", "importance": 1}).json()["change"]["id"]
        n = c.post(f"/api/cards/{card['id']}/notes", json={"note": "From the board's test"}).json()["change"]["id"]
        new = c.post("/api/cards", json={"title": "A card from the board's test", "area": base["areas"][0]["id"],
                                         "who": ["player"], "work": "play"}).json()
    r = run(PULL, "--from", data, "--repo", repo, "--python", sys.executable)
    assert "brought in 3 edits" in r.stdout and "archived 3; 0 still pending" in r.stdout
    after = json.loads((data / "cards.json").read_text())
    assert set(after["applied"]) >= {a, n, new["change"]["id"]}
    by = {x["id"]: x for x in after["cards"]}
    assert by[card["id"]]["status"] == "doing" and "From the board's test" in by[card["id"]]["details"]
    assert by[new["card"]["id"]]["title"] == "A card from the board's test" and by[new["card"]["id"]]["work"] == "play"
    s = Store(data)
    assert s.pending() == [] and s.card(card["id"])["pending"] == 0
    assert (src / "cards.toml").read_text() != (repo / "board" / "cards.toml").read_text()  # the copy changed, not the checkout


def real_goals() -> bool:
    src = real_board()
    if src is None:
        return False
    made = subprocess.run([sys.executable, str(src / "board.py"), "json"], cwd=src.parent, capture_output=True, text=True)
    try:
        return bool(json.loads(made.stdout).get("goals"))
    except ValueError:
        return False


@pytest.mark.skipif(not real_goals(), reason="needs dobro-transcription's board.py with the goals (2026-10-20)")
def test_the_real_board_py_takes_the_goals_edits(tmp_path):
    """dobro-transcription's own board.py with its goals, on a copy: a goal moved a stage, a note on a goal, a card's
    goals, made in the app, pulled, applied, pushed back and archived."""
    from fastapi.testclient import TestClient
    from board.server import create_app
    src = real_board()
    repo = tmp_path / "dobro-transcription"
    (repo / "board").mkdir(parents=True)
    for name in ("board.py", "cards.toml", "applied.txt"):
        if (src / name).exists():
            shutil.copy(src / name, repo / "board" / name)
    data = tmp_path / "board-data"
    made = subprocess.run([sys.executable, "board/board.py", "json"], cwd=repo, capture_output=True, text=True, check=True)
    assert subprocess.run([str(PUSH), "--to", str(data)], input=made.stdout, capture_output=True, text=True).returncode == 0
    with TestClient(create_app({"data_dir": data, "live": {}})) as c:
        b = c.get("/api/board").json()
        g = b["goals"][0]
        to = 1 if g["stage"] != 1 else 2
        a = c.post(f"/api/goals/{g['id']}/stage", json={"stage": to, "note": "From the board's test"}).json()["change"]["id"]
        n = c.post(f"/api/goals/{g['id']}/notes", json={"note": "A note from the board's test"}).json()["change"]["id"]
        card = next(x for x in b["cards"] if g["id"] not in x["goals"])
        s = c.patch(f"/api/cards/{card['id']}", json={"goals": [g["id"]] + card["goals"]}).json()["change"]["id"]
    r = run(PULL, "--from", data, "--repo", repo, "--python", sys.executable)
    assert "brought in 3 edits" in r.stdout and "archived 3; 0 still pending" in r.stdout
    after = json.loads((data / "cards.json").read_text())
    assert set(after["applied"]) >= {a, n, s}
    goal = next(x for x in after["goals"] if x["id"] == g["id"])
    assert goal["history"][-1]["stage"] == to and goal["history"][-1]["note"] == "From the board's test"
    assert goal["notes"][-1]["text"] == "A note from the board's test" and goal["notes"][-1]["by"] == "player"
    assert next(x for x in after["cards"] if x["id"] == card["id"])["goals"][0] == g["id"]
    st = Store(data)
    assert st.pending() == [] and st.goal(g["id"])["stage"] == to and st.goal(g["id"])["pending"] == 0


def real_ready() -> bool:
    src = real_board()
    if src is None:
        return False
    made = subprocess.run([sys.executable, str(src / "board.py"), "json"], cwd=src.parent, capture_output=True, text=True)
    try:
        return all("ready" in c and "number" in c for c in json.loads(made.stdout)["cards"])
    except (ValueError, KeyError):
        return False


@pytest.mark.skipif(not real_ready(), reason="needs dobro-transcription's board.py with numbers and ready (2026-10-21)")
def test_the_real_board_py_takes_ready_and_numbers_the_app_s_new_card(tmp_path):
    """dobro-transcription's own board.py, on a copy: a card the app offers "Ready" marked, another cleared, and a
    new card; pulled, applied, pushed back: the marks as made, every card numbered, the new card the next number."""
    from fastapi.testclient import TestClient
    from board.server import create_app
    src = real_board()
    repo = tmp_path / "dobro-transcription"
    (repo / "board").mkdir(parents=True)
    for name in ("board.py", "cards.toml", "applied.txt"):
        if (src / name).exists():
            shutil.copy(src / name, repo / "board" / name)
    data = tmp_path / "board-data"
    made = subprocess.run([sys.executable, "board/board.py", "json"], cwd=repo, capture_output=True, text=True, check=True)
    assert subprocess.run([str(PUSH), "--to", str(data)], input=made.stdout, capture_output=True, text=True).returncode == 0
    with TestClient(create_app({"data_dir": data, "live": {}})) as c:
        b = c.get("/api/board").json()
        offered = [x for x in b["cards"] if x["claude_ready"] and not x["ready"]]
        assert len(offered) >= 2
        a = c.patch(f"/api/cards/{offered[0]['id']}", json={"ready": True}).json()["change"]["id"]
        c.patch(f"/api/cards/{offered[1]['id']}", json={"ready": True})
        cl = c.patch(f"/api/cards/{offered[1]['id']}", json={"ready": False}).json()["change"]["id"]
        new = c.post("/api/cards", json={"title": "A card from the board's ready test", "area": b["areas"][0]["id"],
                                         "who": ["claude"], "work": "agent"}).json()
        assert new["card"]["number"] is None  # the desktop gives it one
    r = run(PULL, "--from", data, "--repo", repo, "--python", sys.executable)
    assert "brought in 4 edits" in r.stdout and "archived 4; 0 still pending" in r.stdout
    after = json.loads((data / "cards.json").read_text())
    assert set(after["applied"]) >= {a, cl, new["change"]["id"]}
    by = {x["id"]: x for x in after["cards"]}
    assert by[offered[0]["id"]]["ready"] is True and by[offered[1]["id"]]["ready"] is False
    nums = [x["number"] for x in after["cards"]]
    assert all(type(n) is int for n in nums) and len(set(nums)) == len(nums)
    assert by[new["card"]["id"]]["number"] == max(nums)
    with TestClient(create_app({"data_dir": data, "live": {}})) as c:
        shown = {x["id"]: x for x in c.get("/api/board").json()["cards"]}
        assert shown[offered[0]["id"]]["ready"] and shown[new["card"]["id"]]["number"] == max(nums)
    ready = subprocess.run([sys.executable, "board/board.py", "ready"], cwd=repo, capture_output=True, text=True, check=True)
    assert f"#{by[offered[0]['id']]['number']}" in ready.stdout and offered[0]["id"] in ready.stdout
