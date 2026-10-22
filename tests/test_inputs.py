"""The room's inputs (recorder/inputs.py): one file for every app, the room's mics and what is on each of the
interface's inputs and whether it is recorded; a mic chosen from the list for an input (taken off the one it was
on), a mic not on the list, named or not, and onto the list; a take keeps the recorded ones, each named in its
sidecar; a mic chosen on one app's page is named on every app's; the transcribe app's instruments find their input
by its name; the tools pick a take's channel by its input's name (recorder/sidecar.py, transcriber/importer.py);
configs, room files and sidecars of before still read."""

import json
import subprocess
import sys
import time
import tomllib
import wave
from dataclasses import replace

import numpy as np
import pytest
from fastapi.testclient import TestClient

from recorder.config import REPO, load_config
from recorder.inputs import KNOWN_MICS, Inputs, InputError, channels_meta, mic_id
from recorder.sidecar import channels as sidecar_channels
from recorder.sidecar import find_channel

ROOM = """
[mics.piezo]
name = "Piezo"
for = "bass"

[mics.nadine]
name = "Nadine"
details = "Ear Trumpet Labs Nadine, on the bass"
for = "bass"

[mics.mxl-990]
name = "MXL 990"
details = "MXL 990, a large-diaphragm condenser"
aka = ["Room mic"]

[mics.bluebird-sl]
name = "Bluebird SL"
details = "Blue Bluebird SL"

[inputs.1]
mic = "piezo"
record = true

[inputs.3]
mic = "nadine"
record = true

[inputs.4]
mic = "mxl-990"
record = true
"""
# The room's file as installed 2026-10-20, before the list (on the control unit until the first change from a page),
# on the fake device's four inputs (the unit's 5 and 7 here 2 and 1)
BEFORE = """
[inputs.1]
name = "Piezo"
details = "Fishman Full Circle, through the Fishman Pro-EQ Platinum DI, into rear line input 7"
for = "bass"
record = true

[inputs.2]
name = "Records, left"
details = "The record player, for the speakers (the 18i8's Mix A): not recorded"
record = false

[inputs.3]
name = "Nadine"
details = "Ear Trumpet Labs Nadine, on the bass"
for = "bass"
record = true

[inputs.4]
name = "Room mic"
details = "MXL 990 (cardioid large-diaphragm condenser, phantom power), gain about halfway (2026-10-17)"
record = true
"""


def dev(name, tmp_path, **kw):
    return replace(load_config(REPO / "config" / name), takes_dir=tmp_path / "takes", **kw)


def wait_idle(client, limit=20):
    end = time.monotonic() + limit
    while time.monotonic() < end and client.get("/api/status").json()["state"] not in ("idle", "transcribing"):
        time.sleep(0.05)


# --- the file


def test_with_no_file_the_config_stands_in_as_before(tmp_path, room_inputs):
    ins = Inputs(dev("exercises-dev.toml", tmp_path))
    assert not room_inputs.exists()
    # keep_channels' order; the config's names read as the first copy's list has them ("Room mic", the MXL 990)
    assert [(i.number, i.name, i.mic, i.record) for i in ins.kept()] == [
        (3, "Nadine", "nadine", True), (4, "MXL 990", "mxl-990", True), (1, "Piezo", "piezo", True)]
    assert [(i.number, i.named, i.record) for i in ins.all()] == [(1, True, True), (2, False, False), (3, True, True),
                                                                  (4, True, True)]
    # the practice recorder's config names nothing: the profile's kept input, unnamed
    p = Inputs(dev("dev.toml", tmp_path))
    assert [(i.number, i.name, i.named) for i in p.kept()] == [(1, "Input 1", False)]


def test_a_change_writes_the_file_from_what_the_app_had(tmp_path, room_inputs):
    ins = Inputs(dev("exercises-dev.toml", tmp_path))
    ins.update(2, mic="bluebird-sl")
    raw = tomllib.loads(room_inputs.read_text())
    known = tomllib.loads(KNOWN_MICS.read_text())["mics"]
    assert raw["mics"] == known  # the first copy's list, written in
    assert sorted(raw["inputs"]) == ["1", "2", "3", "4"]  # the config's three, and the new one
    assert raw["inputs"]["2"]["mic"] == "bluebird-sl" and raw["inputs"]["2"]["record"] is True and raw["inputs"]["2"]["since"]
    assert raw["inputs"]["3"] == {"mic": "nadine", "record": True}
    # from now on the file: the recorded inputs by number
    assert [(i.number, i.name) for i in ins.kept()] == [(1, "Piezo"), (2, "Bluebird SL"), (3, "Nadine"), (4, "MXL 990")]
    with pytest.raises(InputError, match="no mic"):
        ins.update(2, mic="banjo-mic")
    with pytest.raises(InputError):
        ins.update(2, name="")
    with pytest.raises(InputError):
        ins.update(9, name="Nine")
    with pytest.raises(InputError, match="from the list"):
        ins.update(2, for_kind="bass")  # the Bluebird's kind is the list's
    with pytest.raises(InputError):
        ins.update(2, name="Shure SM57", for_kind="Bass Guitar!")
    with pytest.raises(InputError):
        ins.update(2, name="x" * 41)


def test_a_mic_from_the_list_is_in_one_place(tmp_path, room_inputs):
    room_inputs.write_text(ROOM)
    ins = Inputs(dev("exercises-dev.toml", tmp_path))
    assert [(m.id, m.name) for m in ins.mics()][:2] == [("piezo", "Piezo"), ("nadine", "Nadine")]
    ins.update(2, mic="mxl-990")  # the MXL moved from input 4 to input 2
    assert (ins.get(2).name, ins.get(2).record) == ("MXL 990", True)
    assert (ins.get(4).name, ins.get(4).named, ins.get(4).record) == ("Input 4", False, False)
    assert "4" not in tomllib.loads(room_inputs.read_text())["inputs"]
    ins.update(3, mic="bluebird-sl")  # the Bluebird in the Nadine's place: the Nadine on no input
    assert [(i.number, i.name) for i in ins.kept()] == [(1, "Piezo"), (2, "MXL 990"), (3, "Bluebird SL")]
    ins.update(3, record=False)
    ins.update(3, mic="nadine")  # another mic on an input not recorded: still not recorded
    assert (ins.get(3).name, ins.get(3).for_kind, ins.get(3).record) == ("Nadine", "bass", False)
    ins.update(3, clear=True)  # nothing plugged in
    assert not ins.get(3).named and not ins.get(3).record
    on = {m["id"]: m["input"] for m in ins.mics_json()}
    assert on == {"piezo": 1, "nadine": None, "mxl-990": 2, "bluebird-sl": None}


def test_a_mic_not_on_the_list_and_onto_it(tmp_path, room_inputs):
    room_inputs.write_text(ROOM)
    ins = Inputs(dev("exercises-dev.toml", tmp_path))
    ins.update(2, name=" Shure  SM57 ")
    two = ins.get(2)
    assert (two.name, two.mic, two.record, two.details) == ("Shure SM57", "", True, "")
    assert tomllib.loads(room_inputs.read_text())["inputs"]["2"]["name"] == "Shure SM57"
    ins.update(2, details="a dynamic mic, on the dobro")  # its own details
    assert ins.get(2).name == "Shure SM57" and ins.get(2).details == "a dynamic mic, on the dobro"
    ins.update(2, add_to_list=True)
    assert ins.get(2).mic == "shure-sm57" and [m.name for m in ins.mics()][-1] == "Shure SM57"
    raw = tomllib.loads(room_inputs.read_text())
    assert raw["mics"]["shure-sm57"] == {"name": "Shure SM57", "details": "a dynamic mic, on the dobro"}
    assert raw["inputs"]["2"]["mic"] == "shure-sm57"
    with pytest.raises(InputError, match="on the list"):
        ins.update(2, add_to_list=True)
    ins.update(4, name="nadine")  # a name on the list is that mic: moved from input 3
    assert ins.get(4).mic == "nadine" and not ins.get(3).named
    ins.update(4, name="Another mic")  # a mic not on the list, in the Nadine's place: none of its details
    assert (ins.get(4).name, ins.get(4).details, ins.get(4).for_kind) == ("Another mic", "", "")
    assert mic_id("Shure SM-57", {"shure-sm57"}) == "shure-sm-57" and mic_id("Shure SM57!", {"shure-sm57"}) == "shure-sm57-2"
    assert mic_id("***", {}) == "mic"


def test_a_room_file_of_before_the_list_reads_its_mics_from_the_first_copy(tmp_path, room_inputs):
    room_inputs.write_text(BEFORE)
    ins = Inputs(dev("transcribe-dev.toml", tmp_path))
    assert [(i.number, i.name, i.mic) for i in ins.all() if i.named] == [
        (1, "Piezo", "piezo"), (2, "Records, left", "records-left"), (3, "Nadine", "nadine"), (4, "MXL 990", "mxl-990")]
    assert ins.problems == []
    assert ins.pick("Room mic", "dobro") == (ins.get(4), "name")  # a preset of before: the name the MXL went by
    ins.update(3, mic="bluebird-sl")
    raw = tomllib.loads(room_inputs.read_text())
    assert "bluebird-sl" in raw["mics"] and raw["inputs"]["4"] == {"mic": "mxl-990", "record": True}
    assert [(i.number, i.name) for i in ins.kept()] == [(1, "Piezo"), (3, "Bluebird SL"), (4, "MXL 990")]


def test_every_app_reads_the_file_again_when_another_writes_it(tmp_path, room_inputs):
    room_inputs.write_text(ROOM)
    a, b = Inputs(dev("exercises-dev.toml", tmp_path)), Inputs(dev("transcribe-dev.toml", tmp_path))
    v = b.version
    a.update(3, mic="bluebird-sl")
    assert b.version != v and b.get(3).name == "Bluebird SL" and b.get(3).for_kind == ""
    a.update(4, record=False)
    assert [i.number for i in b.kept()] == [1, 3]
    assert b.get(4).name == "MXL 990" and not b.get(4).record  # named, not recorded


def test_a_bad_table_is_left_out_and_said(tmp_path, room_inputs):
    room_inputs.write_text(ROOM + '\n[inputs.12]\nname = "Too far"\n\n[inputs.2]\nname = ""\nfor = "Bad Kind!"\n'
                           '\n[mics."Bad Key"]\nname = "Bad"\n\n[mics.twin]\nname = "nadine"\n')
    ins = Inputs(dev("exercises-dev.toml", tmp_path))
    assert [i.number for i in ins.kept()] == [1, 3, 4]
    assert len(ins.problems) == 4 and [m.id for m in ins.mics()] == ["piezo", "nadine", "mxl-990", "bluebird-sl"]
    room_inputs.write_text(ROOM.replace('mic = "nadine"', 'mic = "banjo-mic"'))
    assert ins.get(3).name == "banjo-mic" and ins.get(3).mic == "" and "banjo-mic" in ins.problems[0]
    room_inputs.write_text("not = [toml")
    assert [i.number for i in ins.kept()] == [1, 3, 4]  # the last good reading stands
    assert ins.problems and "room-inputs.toml" in ins.problems[0]


def test_an_instrument_picks_its_input_by_name(tmp_path, room_inputs):
    room_inputs.write_text(ROOM)
    ins = Inputs(dev("transcribe-dev.toml", tmp_path))
    assert (ins.pick("MXL 990", "dobro")[0].number, ins.pick("room MIC", "dobro")[1]) == (4, "name")  # or as it was
    assert ins.pick("Nadine", "bass")[0].number == 3
    ins.update(3, mic="bluebird-sl")  # the Nadine's input has another mic now
    hit, how = ins.pick("Nadine", "bass")
    assert (hit.number, how) == (1, "kind")  # the piezo, for the bass
    hit, how = ins.pick("Nadine", "bass", number=3)  # a config of before: "input = 3", named Nadine there
    assert (hit.number, how) == (1, "kind")  # never the Bluebird by its place
    ins.update(1, record=False)
    hit, how = ins.pick("Nadine", "bass", number=3)
    assert (hit.number, how) == (3, "room")  # the first room mic (the Bluebird is one), not input 3 by its number
    assert ins.pick("", "dobro", number=3)[1] == "number"  # no name asked for: the number
    assert ins.pick("Banjo mic", "banjo") == (ins.get(3), "room")


# --- a take


def test_a_take_records_the_rooms_inputs_each_named(tmp_path, room_inputs):
    from recorder.server import create_app
    room_inputs.write_text(ROOM)
    cfg = dev("dev.toml", tmp_path)
    with TestClient(create_app(cfg)) as c:
        listed = c.get("/api/inputs").json()
        assert [i["input"] for i in listed["inputs"] if i["record"]] == [1, 3, 4] and listed["problems"] == []
        take = c.post("/api/record/start").json()["take"]
        time.sleep(0.5)
        s = c.get("/api/status").json()
        # every device input's level while recording, recorded or not; the take's own meters per channel
        assert len(s["input_levels"]) == 4 and len(s["recording"]["meters_dbfs"]) == 3
        assert s["recording"]["inputs"] == [1, 3, 4]
        assert s["input_levels"][1] < s["input_levels"][0]  # the fake device: each input 6 dB under the one before
        # a mic chosen while the take is recorded: the take has its name
        assert c.put("/api/inputs/3", json={"mic": "bluebird-sl"}).status_code == 200
        time.sleep(0.2)
        c.post("/api/record/stop")
        wait_idle(c)
        side = json.loads((cfg.takes_dir / f"{take}.json").read_text())
        assert [(ch["channel"], ch["input"], ch["input_name"]) for ch in side["channels"]] == [
            (0, 1, "Piezo"), (1, 3, "Bluebird SL"), (2, 4, "MXL 990")]
        assert side["channels"][0]["input_for"] == "bass" and "input_for" not in side["channels"][1]
        with wave.open(str(cfg.takes_dir / f"{take}.wav")) as w:
            assert w.getnchannels() == 3
        # the next take: what is recorded as the room has it now
        c.put("/api/inputs/2", json={"name": "Test mic"})
        c.put("/api/inputs/1", json={"record": False})
        take = c.post("/api/record/start").json()["take"]
        time.sleep(0.3)
        c.post("/api/record/stop")
        wait_idle(c)
        side = json.loads((cfg.takes_dir / f"{take}.json").read_text())
        assert [(ch["input"], ch["input_name"]) for ch in side["channels"]] == [(2, "Test mic"), (3, "Bluebird SL"),
                                                                                (4, "MXL 990")]


def test_with_nothing_recorded_a_take_is_refused(tmp_path, room_inputs):
    from recorder.server import create_app
    room_inputs.write_text('[inputs.4]\nname = "Room mic"\nrecord = false\n')
    with TestClient(create_app(dev("dev.toml", tmp_path))) as c:
        r = c.post("/api/record/start")
        assert r.status_code == 409 and "no input" in r.json()["detail"]
        assert c.get("/api/status").json()["recording"] is None


def test_the_monitor_hears_every_input(tmp_path, room_inputs):
    from recorder.server import create_app
    room_inputs.write_text(ROOM)
    with TestClient(create_app(dev("dev.toml", tmp_path))) as c:
        assert c.get("/api/status").json()["input_levels"] is None
        c.post("/api/monitor")
        time.sleep(0.5)
        s = c.get("/api/status").json()
        assert len(s["input_levels"]) == 4 and s["input_levels"][1] > -70  # input 2, not recorded, still heard
        assert s["inputs_version"] == c.get("/api/inputs").json()["version"]


def test_a_name_from_one_app_is_in_the_others(tmp_path, room_inputs):
    from chords.server import create_app as chords_app
    from exercises.server import create_app as exercise_app
    from recorder.server import create_app as practice_app
    room_inputs.write_text(ROOM)
    ex_cfg = dev("exercises-dev.toml", tmp_path / "e", exercises_dir=tmp_path / "exercises")
    ex_cfg.exercises_dir.mkdir()
    with TestClient(practice_app(dev("dev.toml", tmp_path / "p"))) as p, TestClient(exercise_app(ex_cfg)) as e, \
            TestClient(chords_app(replace(dev("chords-dev.toml", tmp_path / "c")))) as ch:
        v = e.get("/api/status").json()["inputs_version"]
        r = p.put("/api/inputs/3", json={"mic": "bluebird-sl"})
        assert r.status_code == 200 and r.json()["inputs"][2]["name"] == "Bluebird SL"
        assert {m["id"]: m["input"] for m in r.json()["mics"]} == {"piezo": 1, "nadine": None, "mxl-990": 4,
                                                                   "bluebird-sl": 3}
        assert e.get("/api/status").json()["inputs_version"] != v  # the page reads /api/inputs again
        three = e.get("/api/inputs").json()["inputs"][2]
        assert (three["name"], three["mic"], three["details"], three["for"], three["record"]) == (
            "Bluebird SL", "bluebird-sl", "Blue Bluebird SL", "", True)
        assert ch.get("/api/inputs").json()["inputs"][2]["name"] == "Bluebird SL"
        assert p.put("/api/inputs/3", json={"for": "bass"}).status_code == 400  # the list's mic: its own kind
        assert p.put("/api/inputs/3", json={"mic": "banjo-mic"}).status_code == 400
        assert p.put("/api/inputs/7", json={"name": "Seven"}).status_code == 400  # the fake device has four
        for app in (p, e, ch):
            r = app.get("/inputs.js")
            assert r.status_code == 200 and "RoomInputs" in r.text
        # an exercise take: what each channel carried, by name
        e.put("/api/inputs/2", json={"name": "Test mic"})
        take = e.post("/api/record/start", json={"click": False}).json()["take"]
        time.sleep(0.3)
        e.post("/api/record/stop")
        wait_idle(e)
        t = next(x for x in e.get("/api/takes").json() if x["id"] == take)
        assert [(c["input"], c["input_name"], c["carries"]) for c in t["channels"]] == [
            (1, "Piezo", "none"), (2, "Test mic", "room"), (3, "Bluebird SL", "room"), (4, "MXL 990", "room")]


def test_the_chord_app_hears_the_recorded_inputs(tmp_path, room_inputs):
    from chords.listen import Listener
    room_inputs.write_text(ROOM)
    cfg = dev("chords-dev.toml", tmp_path)
    ins = Inputs(cfg)
    lis = Listener(cfg, cfg.takes_dir, inputs=ins)
    assert [i.number for i in lis.inputs] == [1, 3, 4] and [lis.inputs[c].number for c in lis.mix] == [4]  # the room's
    ins.update(2, mic="bluebird-sl")
    lis._follow_inputs()  # each listen starts so
    assert [i.number for i in lis.inputs] == [1, 2, 3, 4] and [lis.inputs[c].number for c in lis.mix] == [2, 4]
    lis.listen(inputs=[3])
    assert [lis.inputs[c].number for c in lis.mix] == [3]
    assert lis.status()["inputs"] == [1, 2, 3, 4]


# --- the transcribe app


def test_the_transcribe_apps_instruments_by_name_and_by_number_before(tmp_path):
    from transcriber.settings import load_settings
    s = load_settings(load_config(REPO / "config" / "transcribe.toml"))
    assert {i.kind: (i.input, i.input_number) for i in s.instruments} == {
        "dobro": ("Bluebird SL", None), "bass": ("Nadine", None), "piano": ("MXL 990", None),
        "strings": ("MXL 990", None)}
    old = tmp_path / "old.toml"  # /etc/take-transcriber.toml as installed before 2026-10-20
    old.write_text('capture = "fake"\nplayback = "fake"\nkeep_channels = [2, 3, 0]\ntakes_dir = "t"\n'
                   '[inputs.3]\nname = "Nadine"\n[inputs.4]\nname = "Room mic"\n[inputs.1]\nname = "Piezo"\n'
                   '[instruments.dobro]\ninput = 4\n[instruments.bass]\ninput = 3\n'
                   '[pipeline]\nsrc = "x"\n[review]\nroot = "y"\n')
    s = load_settings(load_config(old))
    assert {i.kind: (i.input, i.input_number) for i in s.instruments} == {"dobro": ("Room mic", 4), "bass": ("Nadine", 3)}


def test_the_transcribe_apps_bass_follows_the_nadine_by_name(tmp_path, room_inputs, monkeypatch):
    from transcriber.server import create_app
    from transcriber.settings import load_settings
    room_inputs.write_text(ROOM)
    cfg = dev("transcribe-dev.toml", tmp_path)
    monkeypatch.setattr("transcriber.server.load_settings", lambda c: load_settings(c))
    with TestClient(create_app(cfg)) as c:
        bass = next(i for i in c.get("/api/setup").json()["instruments"] if i["kind"] == "bass")
        assert (bass["input"], bass["found"]) == (3, "name")
        c.put("/api/inputs/3", json={"mic": "bluebird-sl"})
        c.put("/api/inputs/2", json={"mic": "nadine"})  # the Nadine, plugged into input 2
        setup = c.get("/api/setup").json()
        bass = next(i for i in setup["instruments"] if i["kind"] == "bass")
        assert (bass["input"], bass["found"]) == (2, "name")
        assert [i["input"] for i in setup["inputs"] if i["record"]] == [1, 2, 3, 4]


# --- reading a take's channels by name


def test_a_channel_is_found_by_its_inputs_name_or_number():
    new = {"channels": [{"channel": 0, "input": 1, "input_name": "Piezo"}, {"channel": 1, "input": 3, "input_name": "Bluebird SL"},
                        {"channel": 2, "input": 4, "input_name": "Room mic", "carries": "room"}]}
    assert find_channel(new, "bluebird sl") == 1 and find_channel(new, "Room  mic") == 2
    assert find_channel(new, 3) == 1 and find_channel(new, "In 4") == 2 and find_channel(new, "input 1") == 0
    assert find_channel(new, carries="room") == 2
    with pytest.raises(LookupError, match="Bluebird SL"):
        find_channel(new, "Nadine")
    # an exercise take of 2026-10-16 (no input_details), a chord check (its "name"), a practice take (no channels)
    old_ex = {"channels": [{"channel": 0, "input": 3, "input_name": "Nadine", "carries": "cleveland"},
                           {"channel": 1, "input": 4, "input_name": "Room mic", "carries": "room"},
                           {"channel": 2, "input": 7, "input_name": "Piezo", "carries": "cleveland"}]}
    assert find_channel(old_ex, "Piezo") == 2 and find_channel(old_ex, carries="cleveland") == 0
    assert find_channel({"channels": [{"channel": 0, "input": 3, "name": "Nadine"}]}, "nadine") == 0
    with pytest.raises(LookupError, match="before 2026-10-20"):
        find_channel({"note": "a piano take"}, "Room mic")
    assert sidecar_channels({}) == []


def test_the_sidecar_tool_writes_one_input_alone(tmp_path):
    wav = tmp_path / "2026-10-20_170656.wav"
    x = (np.arange(4800 * 3).reshape(-1, 3) % 1000).astype("<i4")
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(3)
        w.setsampwidth(4)
        w.setframerate(48000)
        w.writeframes(x.tobytes())
    wav.with_suffix(".json").write_text(json.dumps({"channels": [
        {"channel": 0, "input": 3, "input_name": "Nadine"}, {"channel": 1, "input": 4, "input_name": "Room mic"},
        {"channel": 2, "input": 7, "input_name": "Piezo"}]}))
    run = lambda *a: subprocess.run([sys.executable, "-m", "recorder.sidecar", str(wav), *a], cwd=REPO,  # noqa: E731
                                    capture_output=True, text=True)
    r = run()
    assert r.returncode == 0 and "channel 1: input 4, Room mic" in r.stdout
    out = tmp_path / "room.wav"
    assert run("--input", "room mic", "--out", str(out)).returncode == 0
    with wave.open(str(out)) as w:
        assert w.getnchannels() == 1 and np.array_equal(np.frombuffer(w.readframes(4800), "<i4"), x[:, 1])
    r = run("--input", "Bluebird")
    assert r.returncode != 0 and "Nadine" in r.stderr


def test_pull_take_says_how_to_ask_by_name():
    text = (REPO / "tools" / "pull-take").read_text()
    assert "--input NAME" in text and "recorder.sidecar" in text
    r = subprocess.run(["sh", str(REPO / "tools" / "pull-take"), "--input"], capture_output=True, text=True)
    assert r.returncode == 2 and "--input needs a name" in r.stderr
    r = subprocess.run(["sh", str(REPO / "tools" / "pull-take"), "--bogus", "x", "y"], capture_output=True, text=True)
    assert r.returncode == 2 and "unknown option" in r.stderr


def test_channels_meta_names_each_input_as_the_room_has_it(tmp_path, room_inputs):
    room_inputs.write_text(ROOM)
    ins = Inputs(dev("dev.toml", tmp_path))
    kept = ins.kept()
    ins.update(3, mic="bluebird-sl")
    assert [c["input_name"] for c in channels_meta(kept)] == ["Piezo", "Nadine", "MXL 990"]  # as at its start
    now = channels_meta(kept, ins)
    assert [c["input_name"] for c in now] == ["Piezo", "Bluebird SL", "MXL 990"]
    assert now[1] == {"channel": 1, "input": 3, "input_name": "Bluebird SL", "input_details": "Blue Bluebird SL"}
