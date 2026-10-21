"""Where an exercise's bars and notes are on its pages, for the app's cursor: a line that moves across the score in
time with the click while a take is recorded or the chart plays, and the bar it is in lit (the transcribe page's
look). Made on the desktop at push time (tools/push-exercise), from the positions MuseScore exports beside the pages
it renders with the same style (`mscore -o x.mpos`, `-o x.spos`: each measure's and each segment's box on its page,
and the time each starts, repeats played out), as `positions.json` in the exercise's folder:

    {"version": 1,
     "pages": [{"w": 13516.3, "h": 8909.67}, ...],   each page's SVG viewBox
     "bars": [{"page": 0, "x": ..., "y": ..., "w": ..., "h": ...}, ...],
                                                       each measure's box (its system's height), in its page's units
     "notes": [[beat, bar, x], ...]}                   each onset (a chord, a note or a rest) in play order: its beat
                                                       from the score's first (the pickup's), the bar it is in (an
                                                       index into bars) and its x on that bar's page

MuseScore's positions are in its page's units at 14,400 to the inch, twelve to each of the SVG's 1,200, and on the
whole page; the pages are trimmed (`-T 30`), so each page's offset is found from its drawing: a page's first measure
starts where its first system's staff lines start, at their top line. Every other system on the page is checked the
same way; a page that does not line up raises (the push then goes without positions, and the app without a cursor).
The times are in milliseconds at the score's tempo, which the click follows too (exercise.json's tempo).
"""

import re
import xml.etree.ElementTree as ET
from pathlib import Path

UNITS = 12.0  # MuseScore's position units to an SVG unit
TOLERANCE = 3.0  # SVG units (1/400 inch): how far a system's staff lines may be from where its measure says
VIEWBOX_RE = re.compile(r'viewBox="([\d.\s-]+)"')
STAFF_RE = re.compile(r'<polyline class="StaffLines"[^>]*points="([\d.-]+),([\d.-]+) ')


class LayoutError(ValueError):
    pass


def read_positions(path: Path) -> tuple[dict[int, dict], list[tuple[float, int]]]:
    """A MuseScore .mpos or .spos file: its elements by id ({"x", "y", "w", "h", "page"}, in its units) and its
    events in play order ((ms, element id))."""
    root = ET.parse(path).getroot()
    elements = {}
    for e in root.iter("element"):
        elements[int(e.get("id"))] = {"x": float(e.get("x")), "y": float(e.get("y")), "w": float(e.get("sx")),
                                      "h": float(e.get("sy")), "page": int(e.get("page", 0))}
    events = [(float(e.get("position")), int(e.get("elid"))) for e in root.iter("event")]
    if not elements or not events:
        raise LayoutError(f"{path}: no positions")
    return elements, sorted(events, key=lambda ev: ev[0])


def read_page(svg: str) -> tuple[tuple[float, float], list[tuple[float, float]]]:
    """A page's SVG: its viewBox size and where each staff line starts (x, y)."""
    m = VIEWBOX_RE.search(svg)
    if not m:
        raise LayoutError("a page with no viewBox")
    box = [float(v) for v in m.group(1).split()]
    return (box[2], box[3]), [(float(x), float(y)) for x, y in STAFF_RE.findall(svg)]


def page_offset(measures: list[dict], lines: list[tuple[float, float]]) -> tuple[float, float]:
    """The trim's offset on one page: its first measure against its first system's top staff line; every system
    on the page checked."""
    if not lines:
        raise LayoutError("a page with no staff lines")
    first = min(measures, key=lambda m: (m["y"], m["x"]))
    top = min(lines, key=lambda p: (p[1], p[0]))
    ox, oy = first["x"] / UNITS - top[0], first["y"] / UNITS - top[1]
    for y in sorted({m["y"] for m in measures}):
        m = min((m for m in measures if m["y"] == y), key=lambda m: m["x"])
        x0, y0 = m["x"] / UNITS - ox, m["y"] / UNITS - oy
        if not any(abs(x - x0) <= TOLERANCE and abs(ly - y0) <= TOLERANCE for x, ly in lines):
            raise LayoutError(f"a system at {y0:.0f} does not line up with the page's staff lines")
    return ox, oy


def layout(pages: list[Path], mpos: Path, spos: Path, bpm: float) -> dict:
    """positions.json for an exercise: its pages (rendered with -T), and MuseScore's measure and segment positions
    from the same render; `bpm` the score's tempo, which MuseScore's times are at."""
    if bpm <= 0:
        raise LayoutError("no tempo")
    measures, bar_events = read_positions(mpos)
    segments, note_events = read_positions(spos)
    sizes, offsets = [], []
    for p, path in enumerate(pages):
        size, lines = read_page(Path(path).read_text(encoding="utf-8"))
        sizes.append({"w": round(size[0], 2), "h": round(size[1], 2)})
        on = [m for m in measures.values() if m["page"] == p]
        offsets.append(page_offset(on, lines) if on else (0.0, 0.0))
    if any(m["page"] >= len(pages) for m in measures.values()):
        raise LayoutError(f"positions on a page past the {len(pages)} rendered")
    ids = sorted(measures)
    index = {i: n for n, i in enumerate(ids)}

    def box(m: dict) -> dict:
        ox, oy = offsets[m["page"]]
        return {"page": m["page"], "x": round(m["x"] / UNITS - ox, 1), "y": round(m["y"] / UNITS - oy, 1),
                "w": round(m["w"] / UNITS, 1), "h": round(m["h"] / UNITS, 1)}

    bars = [box(measures[i]) for i in ids]
    beat = lambda ms: round(ms / 60000.0 * bpm, 3)  # noqa: E731  (MuseScore's whole milliseconds: 3.9996 is 4)
    notes, b = [], 0
    for ms, elid in note_events:
        while b + 1 < len(bar_events) and bar_events[b + 1][0] <= ms + 0.5:
            b += 1
        bar = index[bar_events[b][1]]
        s = segments[elid]
        if s["page"] != bars[bar]["page"]:
            raise LayoutError(f"a note at {ms:g} ms is not on its bar's page")
        notes.append([beat(ms), bar, round(s["x"] / UNITS - offsets[s["page"]][0], 1)])
    return {"version": 1, "pages": sizes, "bars": bars, "notes": notes}
