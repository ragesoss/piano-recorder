"""The chart: an exercise's notes read as played, and made into MIDI and audio (exercises/chart.py)."""

import runpy
import shutil
import wave

import numpy as np
import pytest

from exercises import chart
from recorder.config import REPO
from recorder.playalong import click_track, with_click

STEP = {0: ("C", 0), 2: ("D", 0), 4: ("E", 0), 5: ("F", 0), 6: ("F", 1), 7: ("G", 0), 9: ("A", 0), 11: ("B", 0)}


def pitch(midi):
    step, alter = STEP[midi % 12]
    return f"<pitch><step>{step}</step>{f'<alter>{alter}</alter>' if alter else ''}<octave>{midi // 12 - 1}</octave></pitch>"


def note(midi, dur, string=None, staff=1, chord=False, extra="", notations="", tie=""):
    """A note as transcribe.notation writes them: on the notation staff (1), or the tab (2) with its string."""
    tech = f"<technical><string>{string}</string><fret>0</fret></technical>" if staff == 2 and midi is not None else ""
    body = "<rest/>" if midi is None else pitch(midi)
    return (f"<note>{'<chord/>' if chord else ''}{body}<duration>{dur}</duration>{tie}<voice>{1 if staff == 1 else 5}</voice>"
            f"<staff>{staff}</staff>{extra}<notations>{tech}{notations}</notations></note>")


def both(*notes):
    """The same notes on the notation staff and on the tab (as the exercises are written), one measure's worth."""
    total = sum(n[1] for n in notes if not n[3].get("chord"))
    one = "".join(note(m, d, None, 1, **kw) for m, d, s, kw in notes)
    two = "".join(note(m, d, s, 2, **kw) for m, d, s, kw in notes)
    return f"{one}<backup><duration>{total}</duration></backup>{two}"


def n(midi, dur, string, **kw):
    return (midi, dur, string, kw)


def score(measures, pickup=None, bpm=90):
    attrs = ("<attributes><divisions>4</divisions><time><beats>4</beats><beat-type>4</beat-type></time><staves>2</staves>"
             "<clef number=\"1\"><sign>G</sign><line>2</line></clef><clef number=\"2\"><sign>TAB</sign><line>5</line></clef></attributes>"
             f"<direction><direction-type><metronome><per-minute>{bpm}</per-minute></metronome></direction-type><sound tempo=\"{bpm}\"/></direction>")
    ms = []
    if pickup:
        ms.append(f'<measure number="0" implicit="yes">{attrs}{pickup}</measure>')
    for i, m in enumerate(measures):
        ms.append(f'<measure number="{i + 1}">{"" if ms else attrs}{m}</measure>')
    return ('<?xml version="1.0"?><score-partwise version="4.0"><part-list><score-part id="P1"><part-name>Dobro</part-name>'
            f'</score-part></part-list><part id="P1">{"".join(ms)}</part></score-partwise>')


SLIDE = '<slide type="{0}" number="1"/>'
SLUR = '<slur type="{0}" number="1"/>'
VIB = '<direction><direction-type><words>{0}</words></direction-type><staff>1</staff></direction>'


def test_the_tab_staff_is_read_once_with_chords_rests_and_ties(tmp_path):
    p = tmp_path / "s.musicxml"
    p.write_text(score([
        both(n(67, 8, 1), n(62, 8, 3, chord=True), n(None, 4, None), n(71, 4, 2, tie='<tie type="start"/>')),
        both(n(71, 8, 2, tie='<tie type="stop"/>'), n(None, 8, None)),
    ], pickup=both(n(62, 4, 3))))
    notes, info = chart.read_notes(p)
    assert info == {"bpm": 90.0, "beats": 9.0, "pedal": []}  # a beat of pickup, two bars
    got = [(x.start, x.length, x.midi, x.string, x.picked) for x in notes]
    # the pickup's D4 is beat 0; the chord's two notes together; the rest; B3 tied over the bar
    assert got == [(0.0, 1.0, 62, 3, True), (1.0, 2.0, 67, 1, True), (1.0, 2.0, 62, 3, True), (4.0, 3.0, 71, 2, True)]


def test_a_piano_score_reads_both_staves_with_dynamics_and_the_pedal(tmp_path):
    # a grand staff, no tab (dobro-transcription's write_piano_score): the right hand's C4 then a staccato,
    # accented C4 E4 G4; the left hand's C3 held the bar; p then f; the pedal down on beat 1, up on beat 3
    attrs = ("<attributes><divisions>4</divisions><time><beats>4</beats><beat-type>4</beat-type></time><staves>2</staves>"
             '<clef number="1"><sign>G</sign><line>2</line></clef><clef number="2"><sign>F</sign><line>4</line></clef>'
             '</attributes><direction><direction-type><metronome><per-minute>60</per-minute></metronome></direction-type>'
             '<sound tempo="60"/></direction>')
    dyn = '<direction><direction-type><dynamics><{0}/></dynamics></direction-type><staff>1</staff></direction>'
    ped = '<direction><direction-type><pedal type="{0}" line="yes"/></direction-type><staff>2</staff></direction>'
    right = (dyn.format("p") + ped.format("start") + note(60, 8) + ped.format("stop") + dyn.format("f")
             + note(60, 8, notations="<articulations><staccato/><accent/></articulations>")
             + note(64, 8, chord=True) + note(67, 8, chord=True))
    left = note(48, 16, staff=2)
    xml = ('<?xml version="1.0"?><score-partwise version="4.0"><part-list><score-part id="P1"><part-name>Piano</part-name>'
           f'</score-part></part-list><part id="P1"><measure number="1">{attrs}{right}<backup><duration>16</duration></backup>'
           f'{left}</measure></part></score-partwise>')
    f = tmp_path / "piano.musicxml"
    f.write_text(xml)
    notes, info = chart.read_notes(f)
    assert [(x.start, x.length, x.midi, x.velocity) for x in notes] == [
        (0.0, 2.0, 60, chart.DYNAMICS["p"]), (0.0, 4.0, 48, chart.DYNAMICS["p"]),
        (2.0, 1.0, 67, chart.DYNAMICS["f"] + chart.ACCENT), (2.0, 1.0, 64, chart.DYNAMICS["f"] + chart.ACCENT),
        (2.0, 1.0, 60, chart.DYNAMICS["f"] + chart.ACCENT)]
    assert len({x.string for x in notes}) == 4  # each key its own: C4 struck again, nothing else stopped
    assert info["pedal"] == [(0.0, True), (2.0, False)]
    mid = chart.midi_file(notes, 60, chart.SOUNDS["piano"], pedal=info["pedal"])
    assert bytes([64, 127]) in mid and bytes([64, 0]) in mid


def test_slides_hammers_scoops_and_vibrato(tmp_path):
    p = tmp_path / "s.musicxml"
    p.write_text(score([
        # a legato slide (slide and slur), then a shift slide (the slide alone: picked again)
        both(n(65, 4, 1, notations=SLIDE.format("start") + SLUR.format("start")), n(67, 4, 1, notations=SLIDE.format("stop") + SLUR.format("stop")),
             n(67, 4, 1, notations=SLIDE.format("start")), n(69, 4, 1, notations=SLIDE.format("stop"))),
        # a scoop; a hammer-on (slurred, no slide); vibrato words over the last note
        both(n(69, 4, 1, notations="<articulations><scoop/></articulations>"), n(64, 4, 2, notations=SLUR.format("start")),
             n(66, 4, 2, notations=SLUR.format("stop"))).replace("<note><pitch><step>F</step><alter>1", VIB.format("slow, wide vib.") + "<note><pitch><step>F</step><alter>1", 1)
        + "<forward><duration>4</duration></forward>",
    ]))
    notes, _ = chart.read_notes(p)
    by = {(x.start, x.midi): x for x in notes}
    assert by[(1.0, 67)].slide and not by[(1.0, 67)].picked  # legato: no pick
    assert by[(3.0, 69)].slide and by[(3.0, 69)].picked  # a shift slide: picked again
    assert by[(4.0, 69)].scoop == chart.SCOOP_ST
    assert not by[(6.0, 66)].picked and not by[(6.0, 66)].slide  # the hammer-on
    assert by[(6.0, 66)].vibrato == (6.0, 4.0, 0.45) and by[(4.0, 69)].vibrato is None
    vs = chart.voices(notes, 60.0)
    first = vs[0]  # F4, slid to G4 without a pick: one voice, bent 2 semitones by G4's onset
    assert (first.key, first.on, first.off) == (65, 0.0, 2.0) and first.bend_at(0.5) == 0 and first.bend_at(1.0) == 2
    assert first.bend_at(1.0 - 0.05) < 2
    shift = [v for v in vs if v.on == 2.0][0]  # G4 picked, bent toward A4, then A4 picked on the other channel
    a4 = [v for v in vs if v.on == 3.0][0]
    assert shift.bend_at(3.0) == 2 and shift.off == 3.0 and a4.key == 69 and a4.channel != shift.channel
    scoop = [v for v in vs if v.on == 4.0][0]
    assert scoop.bend_at(4.0) == -chart.SCOOP_ST and scoop.bend_at(4.0 + chart.SCOOP_S) == 0
    hammer = [v for v in vs if v.on == 6.0][0]
    assert hammer.velocity < chart.VELOCITY and hammer.vibratos
    mid = chart.midi_file(notes, 60.0, chart.SOUNDS["dobro"])
    assert mid[:4] == b"MThd" and mid[14:18] == b"MTrk" and int.from_bytes(mid[18:22], "big") == len(mid) - 22


def test_repeats_are_played_out_as_the_click_counts_them(tmp_path):
    p = tmp_path / "s.musicxml"
    fwd = '<barline location="left"><repeat direction="forward"/></barline>'
    back = '<barline location="right"><repeat direction="backward"/></barline>'
    p.write_text(score([both(n(55, 16, 3)), fwd + both(n(57, 16, 3)), both(n(59, 16, 2)) + back, both(n(60, 16, 2))]))
    notes, info = chart.read_notes(p)
    assert [(x.start, x.midi) for x in notes] == [(0, 55), (4, 57), (8, 59), (12, 57), (16, 59), (20, 60)]
    push = runpy.run_path(str(REPO / "tools" / "push-exercise"), run_name="push_exercise")
    counted = push["read_score"](p)
    assert counted["beats"] == info["beats"] == 24 and counted["bars"] == 6


def test_the_chart_lands_on_the_clicks_beat_0():
    rate, tempo = 48000, {"bpm": 120.0, "pickup_beats": 1.0, "beats_per_bar": 4}
    notes = np.zeros(rate, dtype=np.float32)
    notes[int(0.5 * rate)] = 1.0  # beat 0 half a second in, as exercises/chart.py writes it
    mix, beat0 = with_click(notes, rate, 0.5, tempo, beats=5.0)
    clicks, click_beat0 = click_track(tempo, rate, 5.0)
    assert abs(beat0 - click_beat0) < 1 / rate and np.argmax(mix) == int(round(beat0 * rate))
    assert np.allclose(mix[: len(clicks)] - np.where(np.arange(len(clicks)) == int(round(beat0 * rate)), 1.0, 0.0)[: len(clicks)], clicks)
    quiet, b0 = with_click(notes, rate, 0.5, tempo, beats=5.0, count_in_bars=0, through=False)
    assert np.argmax(quiet) == int(round(b0 * rate))  # no clicks: the notes, where beat 0 is said to be


def test_a_chart_is_rendered_with_beat_0_where_it_says(tmp_path):
    if not shutil.which("fluidsynth") or chart.find_font(chart.SOUNDS["dobro"].font) is None:
        pytest.skip("FluidSynth or the dobro SoundFont is not here")
    p = tmp_path / "s.musicxml"
    p.write_text(score([both(n(67, 4, 1), n(None, 12, None))]))
    info = chart.render(p, tmp_path / "chart.wav", "dobro")
    with wave.open(str(tmp_path / "chart.wav")) as w:
        assert (w.getnchannels(), w.getframerate(), w.getsampwidth()) == (1, chart.RATE, 2)
        x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(float)
    assert info["beat0_s"] == chart.LEAD_S and info["notes"] == 1
    first = np.flatnonzero(np.abs(x) > 0.1 * np.abs(x).max())[0] / chart.RATE
    assert chart.LEAD_S <= first < chart.LEAD_S + 0.05  # the pick speaks on beat 0
