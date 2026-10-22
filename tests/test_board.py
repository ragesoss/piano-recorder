"""The board (board/): the base with the player's pending edits, the edits appended, the live cards from the
other apps (and none when they are down), the home page's status, and its place among the apps."""

import json
import shutil
import threading
import time
import tomllib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from board import live as live_mod
from board.server import counts, create_app
from board import store as store_mod
from board.store import Store, load_config, merge

REPO = Path(__file__).resolve().parent.parent
FIXTURE = REPO / "tests" / "fixtures" / "board-cards.json"
DOWN = "http://127.0.0.1:9"  # nothing listens there


def make(tmp_path, review=DOWN, exercises=DOWN, ttl=10):
    d = tmp_path / "board-data"
    d.mkdir(exist_ok=True)
    shutil.copy(FIXTURE, d / "cards.json")
    cfg = {"data_dir": d, "port": 8007,
           "live": {"review": review, "exercises": exercises, "review_area": "dobro", "exercises_area": "dobro",
                    "timeout": 1.0, "ttl": ttl}}
    return TestClient(create_app(cfg)), d


def lines(d: Path, name="changes.jsonl") -> list[dict]:
    p = d / name
    return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []


def card(c, cid):
    return next(x for x in c.get("/api/board").json()["cards"] if x["id"] == cid)


def test_the_fixture_is_to_the_contract():
    raw = json.loads(FIXTURE.read_text())
    assert raw["version"] == 1 and len(raw["cards"]) >= 40 and raw["applied"] == []
    areas = {a["id"] for a in raw["areas"]}
    keys = {"id", "title", "area", "status", "importance", "who", "work", "effort_player", "effort_claude", "waiting_on",
            "blocked_by", "links", "details", "source", "created", "updated", "done"}
    for c in raw["cards"]:
        assert set(c) == keys, c["id"]
        assert c["area"] in areas and c["work"] in ("listen", "play", "agent", "people", "hardware")
        assert all(set(l) == {"label", "href", "repo"} and l["repo"] for l in c["links"]), c["id"]
    assert {c["status"] for c in raw["cards"]} == {"backlog", "next", "doing", "waiting", "done"}
    assert {w for c in raw["cards"] for w in c["who"]} == {"player", "claude", "others"}


def test_the_page_and_the_board_with_no_edits(tmp_path):
    c, d = make(tmp_path)
    with c:
        assert "<title>Board</title>" in c.get("/").text
        assert c.get("/icon.svg").status_code == 200 and c.get("/manifest.webmanifest").status_code == 200
        assert c.get("/fonts/public-sans.woff2").headers["content-type"] == "font/woff2"
        assert c.get("/fonts/nope.woff2").status_code == 404
        b = c.get("/api/board").json()
        assert b["has_base"] and len(b["cards"]) == 46 and b["pending"] == 0
        assert all(x["pending"] == 0 and x["notes"] == [] for x in b["cards"])
        assert b["areas"][0]["id"] == "dobro" and b["generated"].startswith("2026-10-19")


def test_pending_edits_are_shown_on_the_base(tmp_path):
    c, d = make(tmp_path)
    log = [
        {"id": "c1", "at": "2026-10-20T09:00:00-07:00", "by": "player", "card": "retro-record", "set": {"status": "doing", "importance": 1}},
        {"id": "c2", "at": "2026-10-20T09:01:00-07:00", "by": "player", "new": {
            "id": "tune-the-dulcimer-ab12", "title": "Tune the dulcimer", "area": "strings", "status": "backlog",
            "importance": 2, "who": ["player"], "work": "play"}},
        {"id": "c3", "at": "2026-10-20T09:02:00-07:00", "by": "player", "card": "tune-the-dulcimer-ab12", "set": {"status": "next"}},
        {"id": "c4", "at": "2026-10-20T09:03:00-07:00", "by": "player", "card": "retro-record", "note": "after the jam"},
        {"id": "c5", "at": "2026-10-20T09:04:00-07:00", "by": "player", "card": "no-such-card", "set": {"status": "done"}},
        {"id": "c6", "at": "2026-10-20T09:05:00-07:00", "by": "player", "card": "ab-takes", "set": {"status": "later"}},
        {"id": "c7", "at": "2026-10-20T09:06:00-07:00", "by": "player", "card": "ab-takes", "set": {"status": "done"}},
        {"id": "c8", "at": "2026-10-20T09:07:00-07:00", "by": "player", "card": "exercise-board", "set": {"status": "doing"}},
    ]
    (d / "changes.jsonl").write_text("".join(json.dumps(x) + "\n" for x in log) + '{"id": "torn", "card": "ab-ta')
    with c:
        r = card(c, "retro-record")
        assert r["status"] == "doing" and r["importance"] == 1 and r["updated"] == "2026-10-20"
        assert r["notes"] == [{"at": "2026-10-20T09:03:00-07:00", "by": "player", "text": "after the jam", "pending": True}]
        assert r["pending"] == 2 and r["pending_changes"] == ["c1", "c4"]
        new = card(c, "tune-the-dulcimer-ab12")
        assert new["status"] == "next" and new["new"] and new["pending"] == 2 and new["blocked_by"] == []
        ab = card(c, "ab-takes")  # the invalid status left out; done gets its date
        assert ab["status"] == "done" and ab["done"] == "2026-10-20" and ab["pending"] == 1
        assert card(c, "exercise-board")["done"] is None  # moved out of done
        b = c.get("/api/board").json()
        assert b["pending"] == 6 and len(b["cards"]) == 47  # c5 (no such card) and c6 (no such status) left out
        assert len(c.get("/api/changes").json()["changes"]) == 8  # the torn line skipped, the rest as written


def test_changes_in_the_base_s_applied_are_not_laid_over_it(tmp_path):
    c, d = make(tmp_path)
    log = [{"id": "folded", "at": "2026-10-20T09:00:00", "by": "player", "card": "retro-record", "set": {"status": "done"}},
           {"id": "folded-note", "at": "2026-10-20T09:00:00", "by": "player", "card": "retro-record", "note": "in details now"},
           {"id": "later", "at": "2026-10-20T09:05:00", "by": "player", "card": "ab-takes", "set": {"status": "next"}}]
    (d / "changes.jsonl").write_text("".join(json.dumps(x) + "\n" for x in log))
    base = json.loads(FIXTURE.read_text())
    base["applied"] = ["folded", "folded-note"]
    (d / "cards.json").write_text(json.dumps(base))
    with c:
        r = card(c, "retro-record")
        assert r["status"] == "backlog" and r["pending"] == 0 and r["notes"] == []  # as the base has it
        assert card(c, "ab-takes")["status"] == "next"
        assert [x["id"] for x in c.get("/api/changes").json()["changes"]] == ["later"]
        assert c.get("/api/status").json()["pending"] == 1


def test_a_new_card_already_in_the_base_is_not_doubled(tmp_path):
    base = json.loads(FIXTURE.read_text())
    ch = [{"id": "c1", "at": "2026-10-20T09:00:00", "new": dict(base["cards"][0], title="an older copy")},
          {"id": "c2", "at": "2026-10-20T09:00:00", "card": base["cards"][0]["id"], "note": "seen"}]
    base["cards"][0]["notes"] = [{"at": "2026-10-20T09:00:00", "by": "player", "text": "seen"}]
    out = merge(base, ch)
    assert len(out["cards"]) == len(base["cards"])
    first = out["cards"][0]
    assert first["title"] == base["cards"][0]["title"] and len(first["notes"]) == 1 and first["pending"] == 0


def test_edits_are_appended_to_the_contract(tmp_path):
    c, d = make(tmp_path)
    with c:
        r = c.patch("/api/cards/retro-record", json={"status": "next", "who": ["claude", "player"]}).json()
        assert r["card"]["status"] == "next" and r["card"]["who"] == ["player", "claude"] and r["card"]["pending"] == 1
        ch = r["change"]
        assert set(ch) == {"id", "at", "by", "card", "set"} and ch["by"] == "player" and ch["card"] == "retro-record"
        assert ch["set"] == {"status": "next", "who": ["player", "claude"]} and len(ch["id"]) == 36 and ch["at"][10] == "T"
        # nothing changes, nothing appended
        assert c.patch("/api/cards/retro-record", json={"status": "next"}).json()["change"] is None
        for f in ({"work": "people"}, {"effort_player": "half-day", "effort_claude": "XL"}, {"waiting_on": "  the shop  "},
                  {"area": "gear"}, {"importance": 1}):
            assert c.patch("/api/cards/ab-takes", json=f).status_code == 200
        ab = card(c, "ab-takes")
        assert (ab["work"], ab["effort_player"], ab["effort_claude"], ab["waiting_on"], ab["area"], ab["importance"]) == \
            ("people", "half-day", "XL", "the shop", "gear", 1)
        n = c.post("/api/cards/ab-takes/notes", json={"note": "  try it on the iPad\nfirst "}).json()
        assert n["change"]["note"] == "try it on the iPad\nfirst" and n["card"]["notes"][-1]["pending"]
        q = c.post("/api/cards/band-chart-voice/claude-next").json()
        assert q["change"]["set"] == {"status": "next", "who": ["player", "claude"], "importance": 1}
        assert q["card"]["status"] == "next" and q["card"]["importance"] == 1
        assert c.post("/api/cards/band-chart-voice/claude-next").json()["change"] is None
        new = c.post("/api/cards", json={"title": "  Email the  jam's organiser ", "area": "band", "who": ["player"], "work": "people"})
        assert new.status_code == 201
        nc = new.json()["change"]["new"]
        assert nc["id"].startswith("email-the-jam-s-organiser-") and len(nc["id"]) == len("email-the-jam-s-organiser-") + 4
        assert (nc["title"], nc["status"], nc["importance"], nc["who"], nc["work"], nc["area"]) == \
            ("Email the jam's organiser", "backlog", 2, ["player"], "people", "band")
        assert nc["blocked_by"] == [] and nc["links"] == [] and nc["done"] is None and nc["created"] == nc["updated"]
        assert c.post("/api/cards", json={"title": "Defaults", "area": "band", "work": "play"}).json()["card"]["who"] == ["player"]
    log = lines(d)
    assert len(log) == 10 and [x.get("card") for x in log[:2]] == ["retro-record", "ab-takes"]
    assert all(set(x) - {"card", "set", "note", "new"} == {"id", "at", "by"} for x in log)


@pytest.mark.parametrize("path,body,code", [
    ("/api/cards/retro-record", {"status": "later"}, 422),
    ("/api/cards/retro-record", {"importance": 4}, 422),
    ("/api/cards/retro-record", {"importance": True}, 422),
    ("/api/cards/retro-record", {"who": []}, 422),
    ("/api/cards/retro-record", {"who": ["player", "bob"]}, 422),
    ("/api/cards/retro-record", {"kind": "software"}, 422),  # work, since 2026-10-19
    ("/api/cards/retro-record", {"work": "musical"}, 422),
    ("/api/cards/retro-record", {"title": "renamed"}, 422),  # not settable here
    ("/api/cards/retro-record", {"area": "nowhere"}, 422),
    ("/api/cards/retro-record", {"effort_player": "3h"}, 422),
    ("/api/cards/retro-record", {}, 422),
    ("/api/cards/no-such", {"status": "next"}, 404),
    ("/api/cards/live:exercises", {"status": "done"}, 409),
])
def test_edits_the_contract_does_not_allow_are_refused(tmp_path, path, body, code):
    c, d = make(tmp_path)
    with c:
        assert c.patch(path, json=body).status_code == code
    assert lines(d) == []


def test_new_cards_and_notes_refused(tmp_path):
    c, d = make(tmp_path)
    with c:
        assert c.post("/api/cards", json={"title": " ", "area": "band", "work": "play"}).status_code == 422
        assert c.post("/api/cards", json={"title": "x" * 201, "area": "band", "work": "play"}).status_code == 422
        assert c.post("/api/cards", json={"title": "No area", "area": "", "work": "play"}).status_code == 422
        assert c.post("/api/cards", json={"title": "No work", "area": "band"}).status_code == 422
        assert c.post("/api/cards/retro-record/notes", json={"note": "   "}).status_code == 422
        assert c.post("/api/cards/no-such/notes", json={"note": "hi"}).status_code == 404
        assert c.post("/api/cards/live:review:x/claude-next").status_code == 409
    assert lines(d) == []


def test_appends_from_many_threads_are_whole_lines(tmp_path):
    d = tmp_path / "data"
    d.mkdir()
    shutil.copy(FIXTURE, d / "cards.json")
    (d / "changes.jsonl").write_text('{"id": "torn", "card": "retro-')  # a line cut short before
    stores = [Store(d) for _ in range(4)]  # as separate processes would: each its own lock file handle

    def go(n):
        s = stores[n % 4]
        for i in range(10):
            s.note("retro-record", f"note {n}-{i}")
    ts = [threading.Thread(target=go, args=(n,)) for n in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    raw = (d / "changes.jsonl").read_text().splitlines()
    assert raw[0] == '{"id": "torn", "card": "retro-'  # kept on its own line
    notes = [json.loads(l)["note"] for l in raw[1:]]
    assert sorted(notes) == sorted(f"note {n}-{i}" for n in range(8) for i in range(10))
    assert Store(d).card("retro-record")["pending"] == 80


def test_archive_moves_what_was_brought_in(tmp_path):
    c, d = make(tmp_path)
    with c:
        a = c.patch("/api/cards/retro-record", json={"status": "doing"}).json()["change"]["id"]
        b = c.post("/api/cards/retro-record/notes", json={"note": "one"}).json()["change"]["id"]
        later = c.post("/api/cards/retro-record/notes", json={"note": "made during the sync"}).json()["change"]["id"]
        s = Store(d)
        assert s.archive({a, b}) == (2, 1)
        assert [x["id"] for x in lines(d)] == [later]
        assert [x["id"] for x in lines(d, "changes.applied.jsonl")] == [a, b]
        assert s.archive({a, b}) == (0, 1)  # again: nothing more
        assert card(c, "retro-record")["pending"] == 1


def test_the_store_command_line(tmp_path, monkeypatch, capsys):
    from board import store
    d = tmp_path / "data"
    cfgp = tmp_path / "board.toml"
    cfgp.write_text(f'port = 8007\ndata_dir = "{d}"\n')
    monkeypatch.setenv("ROOM_BOARD_CONFIG", str(cfgp))
    store.main(["datadir"])
    assert capsys.readouterr().out.strip() == str(d)
    d.mkdir()
    shutil.copy(FIXTURE, d / "cards.json")
    s = Store(d)
    _, ch = s.note("retro-record", "hello")
    with open(d / "changes.jsonl", "a") as f:
        f.write('{"id": "half')
    store.main(["pending"])
    assert [json.loads(l)["id"] for l in capsys.readouterr().out.splitlines()] == [ch["id"]]
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO(ch["id"] + "\n"))
    store.main(["archive"])
    assert "archived 1 change; 1 still pending" in capsys.readouterr().out


class Apps:
    """The review app and the exercise app as far as the board reads them."""

    def __init__(self):
        self.reviews = [
            {"id": "take-check-1", "title": "Check the take: Red Wing", "kind": "take-check", "subtitle": "dobro, 100 bpm",
             "created": "2026-10-19", "demo": False, "questions": 8, "answered": 0, "last_answer": "",
             "origin": "pipeline", "origin_note": "", "archived": False},
            {"id": "free-1", "title": "Free recordings", "kind": "free", "subtitle": "", "created": "2026-10-19",
             "demo": False, "questions": 33, "answered": 9, "last_answer": "2026-10-19T19:33:32-07:00",
             "origin": "agent", "origin_note": "the harvest review", "archived": False},
            {"id": "done-1", "title": "All answered", "demo": False, "questions": 2, "answered": 2},
            {"id": "demo-1", "title": "A demo", "demo": True, "questions": 3, "answered": 0},
            {"id": "arch-1", "title": "Done with it", "kind": "free", "demo": False, "questions": 5, "answered": 2,
             "origin": "agent", "archived": True, "archived_at": "2026-10-20T21:40:00-07:00"},
            {"id": "arch-2", "title": "Done with, all answered", "kind": "grid", "demo": False, "questions": 2,
             "answered": 2, "origin": "pipeline", "archived": True},
        ]  # arch-1 and arch-2 archived (the player done with them): no live card of their own
        self.exercises = (
            [{"id": f"dobro-{i}", "instrument": "dobro", "status": "needed", "to_review": 0} for i in range(3)]
            + [{"id": "dobro-9", "instrument": "dobro", "status": "wanted", "to_review": 1}]
            + [{"id": f"banjo-{i}", "instrument": "banjo", "status": "wanted", "to_review": 0} for i in range(2)]
            + [{"id": "g-1", "instrument": "generic", "status": "needed", "to_review": 0},
               {"id": "bass-1", "instrument": "bass", "status": "good", "to_review": 0}])
        apps = self

        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                body = {"/api/reviews": {"reviews": apps.reviews}, "/api/exercises": apps.exercises}.get(self.path)
                self.send_response(200 if body is not None else 404)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(body).encode())

            def log_message(self, *a):
                pass
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()


@pytest.fixture
def apps():
    a = Apps()
    yield a
    a.close()


def test_live_cards_from_the_review_and_exercise_apps(tmp_path, apps):
    c, d = make(tmp_path, review=apps.url, exercises=apps.url)
    with c:
        b = c.get("/api/board").json()
        assert b["live_up"] == {"review": True, "exercises": True}
        live = {x["id"]: x for x in b["live"]}
        assert set(live) == {"live:review:take-check-1", "live:review:free-1", "live:exercises"}
        r = live["live:review:take-check-1"]
        port = apps.srv.server_address[1]
        assert (r["work"], r["status"], r["who"], r["summary"], r["open_questions"]) == ("listen", "next", ["player"], "8 questions to answer", 8)
        assert r["links"] == [{"label": "Open the review", "href": f"http://testserver:{port}/#/r/take-check-1", "repo": ""}]
        assert live["live:review:free-1"]["status"] == "doing" and live["live:review:free-1"]["summary"] == "24 questions to answer (9 of 33 answered)"
        e = live["live:exercises"]
        assert e["work"] == "play" and e["title"] == "Exercises to record: 6" and e["summary"] == "Dobro 3 · Banjo 2 · Any instrument 1"
        assert [l["href"] for l in e["links"]] == [f"http://testserver:{port}{p}" for p in ("/", "/#dobro", "/#banjo", "/#generic")]
        assert all(x["live"] and x["area"] == "dobro" and x["pending"] == 0 for x in live.values())
        s = c.get("/api/status").json()
        assert s["live"] == 3 and s["by_work"]["listen"] == 5 + 2 and s["by_work"]["play"] == 2 + 1


def test_a_review_a_card_links_to_is_shown_on_that_card(tmp_path, apps):
    c, d = make(tmp_path, review=apps.url, exercises=DOWN)
    base = json.loads(FIXTURE.read_text())
    by = {x["id"]: x for x in base["cards"]}
    by["retro-record"]["links"].append({"label": "Its review", "href": "http://practice.local:8005/#/r/take-check-1", "repo": "dobro-transcription"})
    by["exercise-board"]["links"].append({"label": "Done with", "href": "http://practice.local:8005/#/r/free-1", "repo": "dobro-transcription"})
    (d / "cards.json").write_text(json.dumps(base))
    port = apps.srv.server_address[1]
    with c:
        b = c.get("/api/board").json()
        assert {x["id"] for x in b["live"]} == {"live:review:free-1"}  # the done card does not carry it
        r = card(c, "retro-record")
        assert r["live_summary"] == "8 questions to answer"
        assert r["live_link"] == {"label": "Open the review", "href": f"http://testserver:{port}/#/r/take-check-1", "repo": ""}
        assert "live_summary" not in card(c, "exercise-board")
        assert c.get("/api/status").json()["live"] == 1


def test_who_made_a_review_and_an_archived_one(tmp_path, apps):
    """A live review card says who made the review (the review app's `origin`); an archived review has no card of
    its own, and a card that links to it says "Archived" in place of a live count."""
    c, d = make(tmp_path, review=apps.url, exercises=DOWN)
    base = json.loads(FIXTURE.read_text())
    by = {x["id"]: x for x in base["cards"]}
    by["retro-record"]["links"].append({"label": "Its review", "href": "http://practice.local:8005/#/r/arch-1", "repo": "dobro-transcription"})
    (d / "cards.json").write_text(json.dumps(base))
    with c:
        b = c.get("/api/board").json()
        live = {x["id"]: x for x in b["live"]}
        assert set(live) == {"live:review:take-check-1", "live:review:free-1"}  # neither archived one
        assert live["live:review:take-check-1"]["origin"] == "pipeline"
        assert (live["live:review:free-1"]["origin"], live["live:review:free-1"]["origin_note"]) == ("agent", "the harvest review")
        r = card(c, "retro-record")
        assert (r["live_archived"], r["live_summary"], r["live_detail"], r["live_origin"]) == (
            True, "Archived: 2 of 5 answered", "2 of 5 answered", "agent")
        assert c.get("/api/status").json()["live"] == 2  # an archived review is not counted
    cards = live_mod.review_cards(apps.reviews, 8005, "dobro")
    assert {x["review_id"]: x["status"] for x in cards if x["archived"]} == {"arch-1": "done", "arch-2": "done"}
    assert all(x["open_questions"] == 0 for x in cards if x["archived"])
    assert [x["review_id"] for x in live_mod.fold_reviews([], cards)] == ["take-check-1", "free-1"]
    old = [{"id": "x", "title": "From before the origin", "questions": 3, "answered": 0}]  # a review app not yet updated
    assert live_mod.review_cards(old, 8005, "dobro")[0]["origin"] == "" and not live_mod.review_cards(old, 8005, "dobro")[0]["archived"]


def test_a_cards_own_questions_and_its_reviews_each_by_its_link(tmp_path, apps):
    """A card's questions (dobro-transcription's `card` review, card-<id>, linked by "Answer") fold onto it as any
    review a card links to; a review all answered too ("All answered", live_done), never a card of its own; each
    review the card links to in its live_reviews; the card's line the most pressing, its own questions first. The
    base's questions (board.py's) pass through to the page as they are."""
    apps.reviews += [
        {"id": "card-retro-record", "title": "A retroactive record", "kind": "card", "subtitle": "Card #12",
         "created": "2026-10-21", "demo": False, "questions": 3, "answered": 1, "origin": "agent", "archived": False},
        {"id": "card-done-all", "title": "All answered, linked by none", "kind": "card", "demo": False, "questions": 2,
         "answered": 2, "origin": "agent", "archived": False}]
    c, d = make(tmp_path, review=apps.url, exercises=DOWN)
    base = json.loads(FIXTURE.read_text())
    by = {x["id"]: x for x in base["cards"]}
    by["retro-record"]["links"] += [
        {"label": "Its review", "href": "http://practice.local:8005/#/r/take-check-1", "repo": "dobro-transcription"},
        {"label": "Answer", "href": "http://practice.local:8005/#/r/card-retro-record", "repo": "dobro-transcription"},
        {"label": "Answered", "href": "http://practice.local:8005/#/r/done-1", "repo": "dobro-transcription"}]
    by["retro-record"]["questions"] = [
        {"id": "first", "ask": "Which first?", "kind": "choice", "options": ["A", "B"], "for": "", "answer": None,
         "answered": None}]
    by["exercise-board"]["links"].append({"label": "Answered", "href": "http://practice.local:8005/#/r/done-1",
                                           "repo": "dobro-transcription"})
    (d / "cards.json").write_text(json.dumps(base))
    with c:
        b = c.get("/api/board").json()
        assert {x["id"] for x in b["live"]} == {"live:review:free-1"}  # neither all-answered one, nor the folded
        r = card(c, "retro-record")
        assert r["live_summary"] == "2 questions to answer (1 of 3 answered)" and r["live_link"]["href"].endswith("#/r/card-retro-record")
        assert not r["live_done"] and r["questions"][0]["options"] == ["A", "B"]
        assert r["live_reviews"] == {
            "take-check-1": {"summary": "8 questions to answer", "archived": False, "done": False, "origin": "pipeline", "left": 8},
            "card-retro-record": {"summary": "2 questions to answer (1 of 3 answered)", "archived": False, "done": False,
                                  "origin": "agent", "left": 2},
            "done-1": {"summary": "All answered", "archived": False, "done": True, "origin": "", "left": 0}}
        e = card(c, "exercise-board")  # done: carries none
        assert "live_reviews" not in e
    cards = live_mod.review_cards(apps.reviews, 8005, "dobro")
    done = [x for x in cards if x["all_answered"]]
    assert {x["review_id"] for x in done} == {"done-1", "card-done-all"} and all(x["status"] == "done" for x in done)
    # an open card that links only to one all answered: "All answered", its line not on the board's tile
    one = [{"id": "x", "status": "next", "links": [{"href": "http://practice.local:8005/#/r/done-1"}]}]
    live_mod.fold_reviews(one, cards)
    assert (one[0]["live_summary"], one[0]["live_done"]) == ("All answered", True)


def test_the_other_apps_down_leave_their_cards_out(tmp_path, apps):
    c, d = make(tmp_path, review=apps.url, exercises=DOWN)
    with c:
        b = c.get("/api/board").json()
        assert b["live_up"] == {"review": True, "exercises": False}
        assert {x["live"] for x in b["live"]} == {"review"}
    apps.close()
    c, d = make(tmp_path, review=apps.url, exercises=DOWN, ttl=0)
    with c:
        b = c.get("/api/board").json()
        assert b["live"] == [] and b["live_up"] == {"review": False, "exercises": False} and len(b["cards"]) == 46


def test_the_status_never_waits_on_the_other_apps(tmp_path):
    class Slow(BaseHTTPRequestHandler):
        def do_GET(self):
            time.sleep(3)

        def log_message(self, *a):
            pass
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Slow)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        c, d = make(tmp_path, review=f"http://127.0.0.1:{srv.server_address[1]}", exercises=DOWN)
        with c:
            t = time.monotonic()
            s = c.get("/api/status").json()
            assert time.monotonic() - t < 0.5  # the home page asks with a 1.5 s timeout
            assert s["state"] == "5 to listen · 2 to play · 2 for the agent"
    finally:
        srv.shutdown()


def test_status_counts_by_kind_of_work(tmp_path):
    c, d = make(tmp_path)
    with c:
        s = c.get("/api/status").json()
        assert s["by_work"] == {"listen": 5, "play": 2, "agent": 2, "people": 1, "hardware": 2}
        assert (s["doing"], s["next"], s["waiting"], s["cards"], s["pending"]) == (4, 8, 6, 46, 0)
        assert s["for_player"] == 10 and s["for_claude"] == 4
        c.post("/api/cards/retro-record/claude-next")
        s = c.get("/api/status").json()
        assert s["by_work"]["agent"] == 3 and s["pending"] == 1 and s["state"] == "5 to listen · 2 to play · 3 for the agent"
    assert counts([], [])["state"] == "Nothing in Next or Doing"
    (d / "cards.json").unlink()
    c2 = TestClient(create_app({"data_dir": d, "live": {}}))
    with c2:
        assert c2.get("/api/status").json()["state"] == "No cards yet"
        assert c2.get("/api/board").json()["has_base"] is False


def test_live_counts_as_the_exercise_board_counts():
    # no status file at all: what has no take here
    ex = [{"instrument": "dobro", "takes": 0}, {"instrument": "dobro", "takes": 2}, {"instrument": "voice", "takes": 0}]
    assert live_mod.exercise_counts(ex) == [{"kind": "dobro", "name": "Dobro", "n": 1}, {"kind": "voice", "name": "Voice", "n": 1}]
    assert live_mod.exercise_card([{"instrument": "dobro", "status": "good"}], 8002, "dobro") is None


def test_the_board_is_installed_beside_the_others_and_takes_no_turns():
    cfg = load_config(REPO / "config" / "board.toml")
    assert cfg["port"] == 8007 and cfg["data_dir"] == Path("~/board-data").expanduser()
    assert cfg["live"]["review"].endswith(":8005") and cfg["live"]["exercises"].endswith(":8002")
    assert "peers" not in tomllib.loads((REPO / "config" / "board.toml").read_text())
    for p in (REPO / "config").glob("*.toml"):
        raw = tomllib.loads(p.read_text())
        assert not any(":8007" in peer for peer in raw.get("peers", [])), p.name
    used = {tomllib.loads(p.read_text()).get("web", {}).get("port") for p in (REPO / "config").glob("*.toml")}
    assert 8007 not in used
    home = tomllib.loads((REPO / "config" / "home.toml").read_text())
    tile = next(a for a in home["apps"] if a["port"] == 8007)
    assert tile["id"] == "board" and tile["stop"] == []
    install = (REPO / "setup" / "install.sh").read_text()
    assert "room-board" in install and "/etc/room-board.toml" in install
    unit = (REPO / "setup" / "room-board.service").read_text()
    assert "-m board.server" in unit and "sound.target" not in unit


def test_the_board_answers_no_yield(tmp_path):
    c, d = make(tmp_path)
    with c:
        assert c.post("/api/yield").status_code in (404, 405)


# --- the goals (2026-10-20): each at a stage from 1 to 10, its history and notes; a card serves any of them

GOALS = REPO / "tests" / "fixtures" / "board-goals.json"
STAGE_LABELS = ["Idea", "Idea with a plan", "Testable prototype", "Tested prototype", "Rough but usable", "Decent",
                "Getting pretty good", "Almost release-ready", "Release-ready", "Released"]


def with_goals(tmp_path, base=None):
    c, d = make(tmp_path)
    (d / "cards.json").write_text(json.dumps(base) if base is not None else GOALS.read_text())
    return c, d


def goal(c, gid):
    return next(g for g in c.get("/api/board").json()["goals"] if g["id"] == gid)


def test_the_goals_fixture_is_to_the_contract():
    raw = json.loads(GOALS.read_text())
    plain = json.loads(FIXTURE.read_text())
    assert raw["stages"] == STAGE_LABELS and len(raw["goals"]) == 6
    assert [c["id"] for c in raw["cards"]] == [c["id"] for c in plain["cards"]]
    ids = {g["id"] for g in raw["goals"]}
    colors = [g["color"] for g in raw["goals"]]
    assert len(set(colors)) == len(colors) and set(colors) <= set(store_mod.GOAL_COLORS)
    for g in raw["goals"]:
        assert set(g) == {"id", "name", "about", "release", "shipped", "color", "history", "notes"}, g["id"]
        assert g["history"] and all(set(h) == {"date", "stage", "note"} and type(h["stage"]) is int and 1 <= h["stage"] <= 10
                                    for h in g["history"])
        assert [h["date"] for h in g["history"]] == sorted(h["date"] for h in g["history"])
        assert all(set(n) == {"date", "text", "by"} and n["by"] in ("player", "claude") for n in g["notes"])
    assert sum(len(g["history"]) > 2 for g in raw["goals"]) >= 4  # several stage changes each, mostly
    for c in raw["cards"]:
        # the goals' fixture is board.py's JSON of 2026-10-21: the goals, and each card's number and "ready" too; and
        # of 2026-10-22, its questions for the player ([] for none: one card has three)
        assert set(c) - {"goals", "number", "ready", "questions"} == set(next(x for x in plain["cards"] if x["id"] == c["id"]))
        assert set(c["goals"]) <= ids and len(set(c["goals"])) == len(c["goals"])
        assert type(c["number"]) is int and type(c["ready"]) is bool
        assert all(set(q) == {"id", "ask", "kind", "options", "for", "answer", "answered"} for q in c["questions"])
    assert [(c["id"], len(c["questions"])) for c in raw["cards"] if c["questions"]] == [("choose-mandolin-sound", 3)]
    assert {len(c["goals"]) for c in raw["cards"]} == {0, 1, 2, 3}
    assert sorted(c["number"] for c in raw["cards"]) == list(range(1, 47))
    assert [c["id"] for c in raw["cards"] if c["ready"]] == ["liva-t-tests", "lineup-separate-process"]


def test_the_goals_are_on_the_board_with_their_stage(tmp_path):
    c, d = with_goals(tmp_path)
    with c:
        b = c.get("/api/board").json()
        assert b["stages"] == STAGE_LABELS and len(b["goals"]) == 6 and b["pending"] == 0
        band = goal(c, "band-in-a-box")
        assert band["stage"] == 6 and band["color"] == "gold" and len(band["history"]) == 6 and band["pending"] == 0
        assert band["shipped"].startswith("The band's tunes") and [n["by"] for n in band["notes"]] == ["claude", "player"]
        assert goal(c, "ai-detector")["stage"] == 1
        assert card(c, "bass-line-octaves")["goals"] == ["band-in-a-box", "solo-acoustic"]  # in its order
        assert card(c, "records-loudness")["goals"] == []
        s = c.get("/api/status").json()
        assert s["goals"][0] == {"id": "band-in-a-box", "name": "UD as a high-quality band-in-a-box", "stage": 6,
                                 "stage_name": "Decent"}
        assert [g["stage"] for g in s["goals"]] == [6, 5, 4, 2, 7, 1]
        assert s["state"] == "5 to listen · 2 to play · 2 for the agent"  # the tile's words as before


def test_a_board_pushed_before_the_goals_is_as_before(tmp_path):
    c, d = make(tmp_path)
    with c:
        b = c.get("/api/board").json()
        assert b["goals"] == [] and b["stages"] is None and all(x["goals"] == [] for x in b["cards"])
        assert c.get("/api/status").json()["goals"] == []
        assert c.post("/api/goals/solo-dobro/notes", json={"note": "hi"}).status_code == 404
        assert c.patch("/api/cards/retro-record", json={"goals": ["solo-dobro"]}).status_code == 422
        assert c.patch("/api/cards/retro-record", json={"goals": []}).json()["change"] is None


def test_pending_goal_changes_are_laid_over_the_base(tmp_path):
    base = json.loads(GOALS.read_text())
    base["applied"] = ["folded"]
    c, d = with_goals(tmp_path, base)
    at = "2026-10-20T09:0{}:00-07:00".format
    log = [
        {"id": "folded", "at": at(0), "by": "player", "goal": "solo-dobro", "stage": 9, "note": "in the base"},
        {"id": "g1", "at": at(1), "by": "player", "goal": "solo-dobro", "stage": 6, "note": "Vibrato keys"},
        {"id": "g2", "at": at(2), "by": "player", "goal": "solo-dobro", "note": "A note on it"},
        {"id": "g3", "at": at(3), "by": "player", "goal": "ai-detector", "stage": 3, "note": ""},
        {"id": "g4", "at": at(4), "by": "player", "goal": "solo-dobro", "stage": 11, "note": "no such stage"},
        {"id": "g5", "at": at(4), "by": "player", "goal": "solo-dobro", "stage": "7", "note": "a word, not a number"},
        {"id": "g6", "at": at(4), "by": "player", "goal": "solo-dobro", "stage": True, "note": "not a number"},
        {"id": "g7", "at": at(4), "by": "player", "goal": "upstream", "stage": 2, "note": "no such goal"},
        {"id": "g8", "at": at(4), "by": "player", "goal": "solo-dobro"},  # neither a stage nor a note
        {"id": "c1", "at": at(5), "by": "player", "card": "retro-record", "set": {"goals": ["practice-room", "solo-dobro"]}},
        {"id": "c2", "at": at(6), "by": "player", "card": "ab-takes", "set": {"goals": ["nowhere"]}},
        {"id": "c3", "at": at(7), "by": "player", "card": "bass-line-octaves", "set": {"goals": []}},
        {"id": "g9", "at": "2026-10-12T10:00:00-07:00", "by": "player", "goal": "solo-dobro", "stage": 5,
         "note": "Coyote Trail's key answered; the pipeline's keys need a few fixes each"},  # the base has it
    ]
    (d / "changes.jsonl").write_text("".join(json.dumps(x) + "\n" for x in log))
    with c:
        g = goal(c, "solo-dobro")
        assert g["stage"] == 6 and g["pending"] == 2 and g["pending_changes"] == ["g1", "g2"]
        assert g["history"][-1] == {"date": "2026-10-20", "stage": 6, "note": "Vibrato keys", "at": at(1), "by": "player",
                                    "pending": True}
        assert len(g["history"]) == 6 and g["notes"][-1]["text"] == "A note on it" and g["notes"][-1]["by"] == "player"
        assert goal(c, "ai-detector")["stage"] == 3 and goal(c, "ai-detector")["history"][-1]["note"] == ""
        r = card(c, "retro-record")
        assert r["goals"] == ["practice-room", "solo-dobro"] and r["pending"] == 1 and r["updated"] == "2026-10-20"
        assert card(c, "ab-takes")["goals"] == [] and card(c, "ab-takes")["pending"] == 0  # no such goal: left out
        assert card(c, "bass-line-octaves")["goals"] == []
        b = c.get("/api/board").json()
        assert b["pending"] == 5 and c.get("/api/status").json()["pending"] == 5
        assert len(c.get("/api/changes").json()["changes"]) == len(log) - 1  # all kept in the log but the folded one


def test_goal_edits_are_appended_to_the_contract(tmp_path):
    c, d = with_goals(tmp_path)
    with c:
        r = c.post("/api/goals/band-in-a-box/stage", json={"stage": 7, "note": "  The voices are in "}).json()
        ch = r["change"]
        assert set(ch) == {"id", "at", "by", "goal", "stage", "note"} and ch["by"] == "player"
        assert (ch["goal"], ch["stage"], ch["note"]) == ("band-in-a-box", 7, "The voices are in")
        assert r["goal"]["stage"] == 7 and r["goal"]["pending"] == 1 and r["goal"]["history"][-1]["pending"]
        assert c.post("/api/goals/band-in-a-box/stage", json={"stage": 7}).json()["change"] is None  # there already
        back = c.post("/api/goals/band-in-a-box/stage", json={"stage": 5}).json()  # a goal can go back
        assert back["change"]["note"] == "" and back["goal"]["stage"] == 5
        n = c.post("/api/goals/ai-detector/notes", json={"note": " Ask the review app's players "}).json()
        assert set(n["change"]) == {"id", "at", "by", "goal", "note"} and n["change"]["note"] == "Ask the review app's players"
        assert n["goal"]["notes"][-1]["by"] == "player" and n["goal"]["notes"][-1]["pending"]
        s = c.patch("/api/cards/retro-record", json={"goals": ["solo-dobro", "band-in-a-box"]}).json()
        assert s["change"]["set"] == {"goals": ["solo-dobro", "band-in-a-box"]} and s["card"]["goals"] == ["solo-dobro", "band-in-a-box"]
        assert c.patch("/api/cards/retro-record", json={"goals": ["solo-dobro", "band-in-a-box"]}).json()["change"] is None
        assert c.patch("/api/cards/retro-record", json={"goals": []}).json()["card"]["goals"] == []
        assert c.get("/api/status").json()["pending"] == 5
    log = lines(d)
    assert [("goal" in x, x.get("stage")) for x in log] == [(True, 7), (True, 5), (True, None), (False, None), (False, None)]


@pytest.mark.parametrize("path,body,code", [
    ("/api/goals/solo-dobro/stage", {"stage": 0}, 422),
    ("/api/goals/solo-dobro/stage", {"stage": 11}, 422),
    ("/api/goals/solo-dobro/stage", {"stage": "6"}, 422),
    ("/api/goals/solo-dobro/stage", {"stage": 6.0}, 422),
    ("/api/goals/solo-dobro/stage", {"stage": True}, 422),
    ("/api/goals/solo-dobro/stage", {"note": "no stage"}, 422),
    ("/api/goals/solo-dobro/stage", {"stage": 6, "note": "x" * 4001}, 422),
    ("/api/goals/upstream/stage", {"stage": 6}, 404),  # not a goal (an area)
    ("/api/goals/solo-dobro/notes", {"note": "  "}, 422),
    ("/api/goals/no-such/notes", {"note": "hi"}, 404),
])
def test_goal_edits_the_contract_does_not_allow_are_refused(tmp_path, path, body, code):
    c, d = with_goals(tmp_path)
    with c:
        assert c.post(path, json=body).status_code == code
    assert lines(d) == []


@pytest.mark.parametrize("goals", [["nowhere"], ["solo-dobro", "solo-dobro"], "solo-dobro", [1], None])
def test_a_card_s_goals_the_contract_does_not_allow_are_refused(tmp_path, goals):
    c, d = with_goals(tmp_path)
    with c:
        assert c.patch("/api/cards/retro-record", json={"goals": goals}).status_code == 422
        assert c.patch("/api/cards/live:exercises", json={"goals": ["solo-dobro"]}).status_code == 409
    assert lines(d) == []


def test_a_goal_s_color_and_the_stages_labels_from_the_base(tmp_path):
    base = json.loads(GOALS.read_text())
    base["goals"][1]["color"] = "plum"  # not one of the ten: the first not taken
    del base["goals"][2]["color"]
    base["goals"].append({"id": "empty", "name": "", "history": [{"date": "2026-10-19", "stage": "release"}]})
    base["stages"] = [f"Step {n}" for n in range(1, 11)]
    out = merge(base, [])
    assert [g["color"] for g in out["goals"]] == ["gold", "teal", "orange", "red", "violet", "sky", "pink"]
    empty = out["goals"][-1]
    assert (empty["name"], empty["history"], empty["stage"], empty["shipped"]) == ("empty", [], 1, "")
    assert out["stages"][5] == "Step 6"
    c, d = with_goals(tmp_path, base)
    with c:
        assert c.get("/api/status").json()["goals"][0]["stage_name"] == "Step 6"
    base["stages"] = STAGE_LABELS[:9]  # not all ten: the board's own
    assert merge(base, [])["stages"] is None
    assert counts([], [], merge(base, [])["goals"])["goals"][0]["stage_name"] == "Decent"


# --- each card's number and the player's mark "ready to go" (2026-10-21)

def test_numbers_and_who_claude_can_take_up(tmp_path):
    c, d = with_goals(tmp_path)
    with c:
        cards = {x["id"]: x for x in c.get("/api/board").json()["cards"]}
        assert cards["guitalele-key"]["number"] == 1 and cards["first-dobro-keys"]["number"] == 46
        assert cards["liva-t-tests"]["ready"] and cards["lineup-separate-process"]["ready"]
        assert not cards["loop-section"]["ready"]
        ready = {i for i, x in cards.items() if x["claude_ready"]}
        # Claude among who, in Backlog or Next, nothing open blocking it
        assert ready == {"liva-t-tests", "retro-record", "loop-section", "ab-takes", "lineup-separate-process",
                         "backing-out-desktop", "piano-pedal-key", "band-swing", "dropbox-copy", "alphatab-voice-issue"}
        # not: doing, waiting, done, the player's alone, or blocked by a card not done
        for cid in ("melodic-line-deploy", "mandolin-soundfont", "exercise-board", "record-pi-bass-takes",
                    "generic-transcriber-deploy", "dobro-slides-study"):
            assert not cards[cid]["claude_ready"], cid
    # pushed by a board.py from before the numbers and "ready": none, and "Ready" offered on no card
    (tmp_path / "plain").mkdir()
    c, d = make(tmp_path / "plain")
    with c:
        b = c.get("/api/board").json()
        assert all(x["number"] is None and x["ready"] is False and x["claude_ready"] is False for x in b["cards"])
        r = c.patch("/api/cards/retro-record", json={"ready": True})
        assert r.status_code == 422 and "push them again" in r.json()["detail"]
    assert lines(d) == []


def test_ready_marked_and_cleared(tmp_path):
    c, d = with_goals(tmp_path)
    with c:
        r = c.patch("/api/cards/loop-section", json={"ready": True}).json()
        assert r["change"]["set"] == {"ready": True} and r["card"]["ready"] and r["card"]["pending"] == 1
        assert c.patch("/api/cards/loop-section", json={"ready": True}).json()["change"] is None
        assert c.patch("/api/cards/lineup-separate-process", json={"ready": False}).json()["card"]["ready"] is False
        # a marked card moved to Doing (Claude takes it up) loses the mark, as board.py does
        moved = c.patch("/api/cards/liva-t-tests", json={"status": "doing"}).json()
        assert moved["change"]["set"] == {"status": "doing"} and moved["card"]["ready"] is False
        assert not moved["card"]["claude_ready"]
        # and Claude out of who
        c.patch("/api/cards/loop-section", json={"who": ["player"]})
        assert card(c, "loop-section")["ready"] is False
        # a card Claude cannot take up now is not marked
        for cid in ("generic-transcriber-deploy", "melodic-line-deploy", "guitalele-key", "mandolin-soundfont",
                    "liva-t-tests"):
            r = c.patch(f"/api/cards/{cid}", json={"ready": True})
            assert r.status_code == 422 and "Claude can take up now" in r.json()["detail"], cid
        assert c.patch("/api/cards/ab-takes", json={"ready": "yes"}).status_code == 422
        # its blocker done (in the app too): the blocked card can be marked
        c.patch("/api/cards/melodic-line-deploy", json={"status": "done"})
        assert card(c, "generic-transcriber-deploy")["claude_ready"]
        assert c.patch("/api/cards/generic-transcriber-deploy", json={"ready": True}).status_code == 200
    log = lines(d)
    assert [x["set"] for x in log if "ready" in x["set"]] == [{"ready": True}, {"ready": False}, {"ready": True}]
    assert len(log) == 6


def test_a_ready_mark_logged_for_a_card_taken_up_since_is_shown_off(tmp_path):
    c, d = with_goals(tmp_path)
    at = "2026-10-21T09:0{}:00-07:00".format
    log = [{"id": "r1", "at": at(1), "by": "player", "card": "melodic-line-deploy", "set": {"ready": True}},  # doing
           {"id": "r2", "at": at(2), "by": "player", "card": "ab-takes", "set": {"ready": True}},
           {"id": "r3", "at": at(3), "by": "player", "card": "ab-takes", "set": {"status": "waiting"}},
           {"id": "r4", "at": at(4), "by": "player", "card": "retro-record", "set": {"ready": True}}]
    (d / "changes.jsonl").write_text("".join(json.dumps(x) + "\n" for x in log))
    with c:
        assert card(c, "melodic-line-deploy")["ready"] is False
        assert card(c, "ab-takes")["ready"] is False and card(c, "ab-takes")["status"] == "waiting"
        assert card(c, "retro-record")["ready"] is True and card(c, "retro-record")["pending"] == 1
