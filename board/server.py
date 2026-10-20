"""The music room's board: the project's cards as a kanban, on port 8007.

    python -m board.server    (config from $ROOM_BOARD_CONFIG or /etc/room-board.toml)

The cards are dobro-transcription's `board/cards.toml`, pushed here as JSON by tools/push-board. The
player's edits are kept as a log (board/store.py) that tools/pull-board brings to the desktop; until
then the board shows them applied. Live cards (board/live.py) come from the review app and the exercise
app. The board holds no audio device, takes no turns with the recorders, and is in no peers list.

GET   /api/board                   the cards (the base with the pending edits) and the live cards
GET   /api/status                  for the home page: "3 to listen · 2 to play", and counts
POST  /api/cards                   {title, area, who, work}: a new card (into Backlog)
PATCH /api/cards/{id}              {status, importance, who, work, effort_player, effort_claude,
                                    waiting_on, area}: any of them
POST  /api/cards/{id}/claude-next  "Claude: do this next": next, Claude among who, importance 1
POST  /api/cards/{id}/notes        {note}
GET   /api/changes                 the edits not yet in the cards (the log, less the base's "applied")
"""

from __future__ import annotations

import re
from pathlib import Path

import uvicorn
from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse

from . import live as live_mod
from .store import WORK, Invalid, NotFound, Store, load_config

STATIC = Path(__file__).parent / "static"
FONTS = Path(__file__).parent.parent / "exercises" / "static" / "fonts"  # the exercise app's (SIL OFL 1.1)
OPEN = ("next", "doing")
# the home tile's words for each kind of work: "3 to listen · 2 to play · 4 for the agent"
WORK_WORDS = {"listen": "to listen", "play": "to play", "agent": "for the agent", "people": "with people",
              "hardware": "hardware"}
TILE_TERMS = 3  # at most this many kinds named on the tile (the rest are in by_work)


def counts(cards: list[dict], live: list[dict]) -> dict:
    """What the home tile says: the cards in Next or Doing for each kind of work (the live ones too), in the
    contract's order; and the player's, Claude's, and the columns' counts."""
    now = [c for c in cards if c["status"] in OPEN] + live
    by_work = {w: sum(c["work"] == w for c in now) for w in WORK}
    claude = [c for c in cards if "claude" in c["who"] and c["status"] in OPEN]
    out = {"by_work": by_work,
           "for_player": sum("player" in c["who"] for c in now), "for_claude": len(claude),
           "doing": sum(c["status"] == "doing" for c in cards),
           "next": sum(c["status"] == "next" for c in cards),
           "waiting": sum(c["status"] == "waiting" for c in cards),
           "live": len(live), "cards": len(cards),
           "pending": sum(c["pending"] for c in cards)}
    words = [f"{n} {WORK_WORDS[w]}" for w, n in by_work.items() if n][:TILE_TERMS]
    out["state"] = " · ".join(words) if words else "Nothing in Next or Doing"
    return out


def create_app(cfg: dict) -> FastAPI:
    store = Store(cfg["data_dir"])
    live = live_mod.Live(cfg.get("live", {}))
    app = FastAPI(title="Board", docs_url=None, redoc_url=None)
    app.state.store, app.state.live = store, live

    def href(l: dict, host: str) -> dict:
        # a live link is to another app on this unit: by the name the page reached it by
        return {"label": l["label"], "href": f"http://{host}:{l['port']}{l['path']}", "repo": ""}

    def current(fresh: bool, host: str = "practice.local"):
        """The cards (the base and the pending edits), the live cards not folded into one of them, which apps
        answered."""
        b = store.board()
        lc, up = live.cards(fresh=fresh)
        lc = live_mod.fold_reviews(b["cards"], lc)
        for c in b["cards"]:
            if "live_link" in c:
                c["live_link"] = href(c["live_link"], host)
        return b, [c | {"links": [href(l, host) for l in c["links"]]} for c in lc], up

    def guard(fn, *a):
        try:
            card, change = fn(*a)
        except NotFound:
            raise HTTPException(404, "no such card")
        except Invalid as e:
            raise HTTPException(422, str(e))
        return {"card": card, "change": change}

    def editable(card_id: str) -> str:
        if card_id.startswith("live:"):
            raise HTTPException(409, "a live card changes in its own app")
        return card_id

    @app.get("/")
    def page():
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})

    @app.get("/manifest.webmanifest")
    def manifest():
        return FileResponse(STATIC / "manifest.webmanifest", media_type="application/manifest+json")

    @app.get("/icon.svg")
    def icon():
        return FileResponse(STATIC / "icon.svg", media_type="image/svg+xml")

    @app.get("/fonts/{name}")
    def font(name: str):
        path = FONTS / name
        if not re.match(r"^[A-Za-z0-9-]+\.woff2$", name) or not path.is_file():
            raise HTTPException(404, "no such font")
        return FileResponse(path, media_type="font/woff2", headers={"Cache-Control": "max-age=86400"})

    @app.get("/api/board")
    def board(request: Request):
        b, cards, up = current(True, request.url.hostname or "practice.local")
        return {"version": b.get("version", 1), "generated": b.get("generated"), "has_base": b["has_base"],
                "areas": b.get("areas", []), "cards": b["cards"], "live": cards, "live_up": up,
                "pending": sum(c["pending"] for c in b["cards"])}

    @app.get("/api/status")
    def status():
        b, cards, _ = current(False)
        out = counts(b["cards"], cards)
        if not b["has_base"]:
            out["state"] = "No cards yet"
        return out

    @app.get("/api/changes")
    def changes():
        return {"changes": store.pending()}

    @app.post("/api/cards", status_code=201)
    def new_card(body: dict = Body(...)):
        return guard(store.new, body)

    @app.patch("/api/cards/{card_id}")
    def set_card(card_id: str, body: dict = Body(...)):
        return guard(store.set, editable(card_id), body)

    @app.post("/api/cards/{card_id}/claude-next")
    def claude_next(card_id: str):
        return guard(store.claude_next, editable(card_id))

    @app.post("/api/cards/{card_id}/notes")
    def add_note(card_id: str, body: dict = Body(...)):
        return guard(store.note, editable(card_id), body.get("note", ""))

    return app


def main() -> None:
    cfg = load_config()
    uvicorn.run(create_app(cfg), host=cfg.get("host", "0.0.0.0"), port=int(cfg["port"]), log_level="warning")


if __name__ == "__main__":
    main()
