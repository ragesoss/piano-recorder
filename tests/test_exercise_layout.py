"""Where an exercise's bars and notes are on its pages (exercises/layout.py, made by tools/push-exercise): the trim's
offset from each page's staff lines, a page that does not line up refused, the beats in play order with their bars;
the store and the route that serve it; MuseScore's own positions for a real exercise and a pickup, when MuseScore is
here (skipped without)."""

import json
import os
import shutil
import subprocess
import sys
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from exercises.layout import LayoutError, layout
from exercises.server import create_app
from exercises.store import ExerciseNotFound, Exercises
from recorder.config import REPO, load_config

FIXTURE = REPO / "tests" / "fixtures" / "dobro-002-g-scale.musicxml"


def positions_file(path, elements, events):
    els = "".join(f'<element id="{i}" x="{x}" y="{y}" sx="{w}" sy="{h}" page="{p}"></element>'
                  for i, (x, y, w, h, p) in enumerate(elements))
    evs = "".join(f'<event elid="{e}" position="{ms}"></event>' for ms, e in events)
    path.write_text(f'<?xml version="1.0"?><score><elements>{els}</elements><events>{evs}</events></score>')


def page_svg(path, systems, w=1000.0, h=800.0):
    """A trimmed page: each system's top staff line starting at (x, y)."""
    lines = "".join(f'<polyline class="StaffLines" points="{x},{y + k * 10} {w - 10},{y + k * 10}" />'
                    for x, y in systems for k in range(5))
    path.write_text(f'<svg width="1mm" height="1mm" viewBox="0 0 {w} {h}">{lines}</svg>')


def test_the_trim_offset_and_the_notes_in_play_order(tmp_path):
    # two systems on one page, two bars each; the page trimmed by (100, 50): MuseScore's units twelve to the SVG's
    measures = [(1200 * 3, 1200 * 2, 1200 * 4, 600, 0), (1200 * 7, 1200 * 2, 1200 * 4, 600, 0),
                (1200 * 2, 1200 * 5, 1200 * 5, 600, 0), (1200 * 7, 1200 * 5, 1200 * 4, 600, 0)]
    positions_file(tmp_path / "m.mpos", measures, [(0, 0), (2000, 1), (4000, 2), (6000, 3), (8000, 0)])  # a repeat
    segments = [(1200 * 4, 1200 * 2, 10, 600, 0), (1200 * 8, 1200 * 2, 10, 600, 0), (1200 * 3, 1200 * 5, 10, 600, 0),
                (1200 * 8, 1200 * 5, 10, 600, 0)]
    positions_file(tmp_path / "s.spos", segments, [(0, 0), (2000, 1), (4000, 2), (6000, 3), (8000, 0)])
    page_svg(tmp_path / "page-1.svg", [(300 - 100, 200 - 50), (200 - 100, 500 - 50)])
    out = layout([tmp_path / "page-1.svg"], tmp_path / "m.mpos", tmp_path / "s.spos", bpm=120)
    assert out["pages"] == [{"w": 1000.0, "h": 800.0}]
    assert out["bars"][0] == {"page": 0, "x": 200.0, "y": 150.0, "w": 400.0, "h": 50.0}
    assert out["bars"][2] == {"page": 0, "x": 100.0, "y": 450.0, "w": 500.0, "h": 50.0}
    # 2000 ms at 120 bpm: 4 beats; the repeat back to bar 0 after bar 3
    assert out["notes"] == [[0.0, 0, 300.0], [4.0, 1, 700.0], [8.0, 2, 200.0], [12.0, 3, 700.0], [16.0, 0, 300.0]]
    # a second system that is not where its measure says: refused, not a cursor in the wrong place
    page_svg(tmp_path / "page-1.svg", [(200, 150), (100, 470)])
    with pytest.raises(LayoutError, match="does not line up"):
        layout([tmp_path / "page-1.svg"], tmp_path / "m.mpos", tmp_path / "s.spos", bpm=120)
    with pytest.raises(LayoutError, match="no tempo"):
        layout([tmp_path / "page-1.svg"], tmp_path / "m.mpos", tmp_path / "s.spos", bpm=0)


def exercise_with_positions(root, ex_id="dobro-009-x", pages=1, positions=True):
    d = root / ex_id
    d.mkdir(parents=True)
    names = [f"page-{n}.svg" for n in range(1, pages + 1)]
    for n in names:
        (d / n).write_text('<svg viewBox="0 0 10 10"></svg>')
    meta = {"title": ex_id, "instrument": "dobro", "bars": 1, "pages": names,
            "tempo": {"bpm": 90, "pickup_beats": 0, "beats_per_bar": 4}, "beats": 4}
    if positions:
        meta["positions"] = "positions.json"
        (d / "positions.json").write_text(json.dumps({"version": 1, "pages": [{"w": 10, "h": 10}] * pages,
                                                      "bars": [{"page": 0, "x": 1, "y": 1, "w": 8, "h": 4}],
                                                      "notes": [[0, 0, 2]]}))
    (d / "exercise.json").write_text(json.dumps(meta))


def test_the_store_and_the_route_serve_the_positions(tmp_path):
    exercise_with_positions(tmp_path, "dobro-009-x")
    exercise_with_positions(tmp_path, "dobro-010-y", positions=False)
    exercise_with_positions(tmp_path, "dobro-011-z", pages=2)
    (tmp_path / "dobro-011-z" / "exercise.json").write_text(
        (tmp_path / "dobro-011-z" / "exercise.json").read_text().replace('"page-2.svg"', '"page-2.svg", "page-3.svg"'))
    (tmp_path / "dobro-011-z" / "page-3.svg").write_text("<svg/>")
    store = Exercises(tmp_path)
    assert [(e.id, e.positions) for e in store.list()] == [("dobro-009-x", True), ("dobro-010-y", False),
                                                           ("dobro-011-z", True)]
    assert store.positions("dobro-009-x")["notes"] == [[0, 0, 2]]
    with pytest.raises(ExerciseNotFound, match="no positions"):
        store.positions("dobro-010-y")
    with pytest.raises(ExerciseNotFound, match="other pages"):  # three pages now, positions for two
        store.positions("dobro-011-z")
    cfg = replace(load_config(REPO / "config" / "exercises-dev.toml"), takes_dir=tmp_path / "takes",
                  backing_dir=tmp_path / "backing", exercises_dir=tmp_path)
    with TestClient(create_app(cfg)) as c:
        assert c.get("/api/exercises/dobro-009-x/positions").json()["bars"][0]["w"] == 8
        assert c.get("/api/exercises/dobro-010-y/positions").status_code == 404
        assert next(e for e in c.get("/api/exercises").json() if e["id"] == "dobro-009-x")["positions"] is True


def mscore() -> str | None:
    for c in (os.environ.get("MSCORE"), shutil.which("mscore4portable"), shutil.which("mscore")):
        if c and os.path.exists(c):
            return c
    return None


PICKUP = """<?xml version="1.0" encoding="UTF-8"?>
<score-partwise version="3.1"><work><work-title>Pickup</work-title></work>
<part-list><score-part id="P1"><part-name>Dobro</part-name></score-part></part-list>
<part id="P1">
<measure number="0" implicit="yes"><attributes><divisions>2</divisions><key><fifths>1</fifths></key>
<time><beats>4</beats><beat-type>4</beat-type></time><clef><sign>G</sign><line>2</line></clef></attributes>
<direction placement="above"><direction-type><metronome><beat-unit>quarter</beat-unit><per-minute>100</per-minute></metronome></direction-type><sound tempo="100"/></direction>
<note><pitch><step>D</step><octave>4</octave></pitch><duration>2</duration><type>quarter</type></note></measure>
<measure number="1">
<note><pitch><step>G</step><octave>4</octave></pitch><duration>4</duration><type>half</type></note>
<note><pitch><step>B</step><octave>4</octave></pitch><duration>2</duration><type>quarter</type></note>
<note><pitch><step>A</step><octave>4</octave></pitch><duration>2</duration><type>quarter</type></note></measure>
<measure number="2">
<note><pitch><step>G</step><octave>4</octave></pitch><duration>8</duration><type>whole</type></note></measure>
</part></score-partwise>
"""


@pytest.mark.skipif(mscore() is None, reason="needs MuseScore (mscore)")
def test_push_exercise_writes_the_positions_musescore_gives(tmp_path):
    (tmp_path / "pickup.musicxml").write_text(PICKUP)
    out = tmp_path / "exercises"
    out.mkdir()
    for score, ex_id in ((FIXTURE, "dobro-002-g-scale"), (tmp_path / "pickup.musicxml", "dobro-099-pickup")):
        r = subprocess.run([sys.executable, str(REPO / "tools" / "push-exercise"), str(score), "--id", ex_id, "--no-audio",
                            "--no-readme", "--to", str(out)], capture_output=True, text=True, timeout=300)
        assert r.returncode == 0 and "warning" not in r.stderr, r.stdout + r.stderr
    meta = json.loads((out / "dobro-002-g-scale" / "exercise.json").read_text())
    pos = json.loads((out / "dobro-002-g-scale" / "positions.json").read_text())
    assert meta["positions"] == "positions.json" and len(pos["pages"]) == len(meta["pages"]) >= 2
    assert len(pos["bars"]) == meta["bars"] == 33
    beats = [n[0] for n in pos["notes"]]
    assert beats[0] == 0 and beats == sorted(beats) and beats[-1] == 128.0
    # two half notes a bar, the last a whole note: each note's bar is its beat over four; the bars left to right
    # on a system, and every note inside its bar
    assert all(bar == min(int(b // 4), 32) for b, bar, _ in pos["notes"])
    for b, bar, x in pos["notes"]:
        box = pos["bars"][bar]
        assert box["x"] <= x <= box["x"] + box["w"] and 0 <= box["y"] < pos["pages"][box["page"]]["h"]
    # a pickup of a beat: beat 0 in it, bar 1 from beat 1
    pick = json.loads((out / "dobro-099-pickup" / "positions.json").read_text())
    assert [(n[0], n[1]) for n in pick["notes"]] == [(0.0, 0), (1.0, 1), (3.0, 1), (4.0, 1), (5.0, 2)]
    # --pages-only again, in place: the same, and a stale page and positions removed when no longer made
    (out / "dobro-099-pickup" / "page-2.svg").write_text("<svg/>")
    r = subprocess.run([sys.executable, str(REPO / "tools" / "push-exercise"), "--pages-only", "dobro-099-pickup",
                        "--to", str(out)], capture_output=True, text=True, timeout=300)
    assert r.returncode == 0 and "dobro-099-pickup: 1 page, positions" in r.stdout, r.stdout + r.stderr
    assert not (out / "dobro-099-pickup" / "page-2.svg").exists()
    assert json.loads((out / "dobro-099-pickup" / "positions.json").read_text()) == pick
