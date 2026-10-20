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
