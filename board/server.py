"""The music room's board: the project's cards as a kanban, on port 8007.

    python -m board.server    (config from $ROOM_BOARD_CONFIG or /etc/room-board.toml)

The cards are dobro-transcription's `board/cards.toml`, pushed here as JSON by tools/push-board, with the
project's large-scale goals (each at a stage from 1, an idea, to 10, released, with its history; a card
serves any of them). Each card has its number ("card 42"), and the player marks a card Claude can take up
"ready to go". The player's edits are kept as a log (board/store.py) that tools/pull-board brings to
the desktop; until then the board shows them applied. Live cards (board/live.py) come from the review app
and the exercise app. The board holds no audio device, takes no turns with the recorders, and is in no
peers list.

GET   /api/board                   the cards and the goals (the base with the pending edits), the live cards
GET   /api/status                  for the home page: "Ready" (its tile names no counts), the counts, each goal's stage
POST  /api/cards                   {title, area, who, work}: a new card (into Backlog)
PATCH /api/cards/{id}              {status, importance, who, work, effort_player, effort_claude,
                                    waiting_on, area, goals, ready}: any of them (ready true only on a card
                                    Claude can take up now: its "claude_ready")
POST  /api/cards/{id}/claude-next  "Claude: do this next": next, Claude among who, importance 1
POST  /api/cards/{id}/notes        {note}
POST  /api/goals/{id}/stage        {stage, note}: the goal moved to that stage (a history entry)
POST  /api/goals/{id}/notes        {note}
GET   /api/changes                 the edits not yet in the cards (the log, less the base's "applied")
POST  /api/client-error            {msg, src, line, col, stack, ua, w, h}: a script error on a device's page, written to
                                   the service's log (journalctl -u room-board), so one met on the iPad can be read
"""

from __future__ import annotations

import re
from pathlib import Path

import uvicorn
from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse

from . import live as live_mod
from .store import STAGE_NAMES, WORK, Invalid, NotFound, Store, load_config

STATIC = Path(__file__).parent / "static"
FONTS = Path(__file__).parent.parent / "exercises" / "static" / "fonts"  # the exercise app's (SIL OFL 1.1)
OPEN = ("next", "doing")


def counts(cards: list[dict], live: list[dict], goals: list[dict] = (), stages: list[str] | None = None) -> dict:
    """The board's state for the home page, "Ready" (2026-10-22, the player: "just get rid of the index stats i
    think. too hard to intrepret which of the 193 cards are meant in the index stats"), and its counts for whoever
    asks: the cards in Next or Doing for each kind of work (the live ones too), in the contract's order; the
    player's, Claude's, and the columns' counts; and each goal's stage."""
    now = [c for c in cards if c["status"] in OPEN] + live
    by_work = {w: sum(c["work"] == w for c in now) for w in WORK}
    claude = [c for c in cards if "claude" in c["who"] and c["status"] in OPEN]
    out = {"by_work": by_work,
           "for_player": sum("player" in c["who"] for c in now), "for_claude": len(claude),
           "doing": sum(c["status"] == "doing" for c in cards),
           "next": sum(c["status"] == "next" for c in cards),
           "waiting": sum(c["status"] == "waiting" for c in cards),
           "live": len(live), "cards": len(cards),
           "pending": sum(c["pending"] for c in cards) + sum(g["pending"] for g in goals),
           "goals": [{"id": g["id"], "name": g["name"], "stage": g["stage"],
                      "stage_name": stages[g["stage"] - 1] if stages else STAGE_NAMES[g["stage"]]} for g in goals]}
    out["state"] = "Ready"
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

    def guard(fn, *a, what="card"):
        try:
            got, change = fn(*a)
        except NotFound:
            raise HTTPException(404, f"no such {what}")
        except Invalid as e:
            raise HTTPException(422, str(e))
        return {what: got, "change": change}

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
                "areas": b.get("areas", []), "cards": b["cards"], "goals": b["goals"], "stages": b["stages"],
                "live": cards, "live_up": up,
                "pending": b["pending"]}

    @app.get("/api/status")
    def status():
        b, cards, _ = current(False)
        out = counts(b["cards"], cards, b["goals"], b["stages"])
        if not b["has_base"]:
            out["state"] = "No cards yet"
        return out

    @app.get("/api/changes")
    def changes():
        return {"changes": store.pending()}

    @app.post("/api/client-error", status_code=204)
    def client_error(body: dict = Body(...)):
        keep = {k: str(body.get(k, ""))[:2000] for k in ("msg", "src", "line", "col", "stack", "ua", "w", "h")}
        print(f"client error: {keep}", flush=True)

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

    @app.post("/api/goals/{goal_id}/stage")
    def move_goal(goal_id: str, body: dict = Body(...)):
        return guard(store.move_goal, goal_id, body.get("stage"), body.get("note", ""), what="goal")

    @app.post("/api/goals/{goal_id}/notes")
    def goal_note(goal_id: str, body: dict = Body(...)):
        return guard(store.goal_note, goal_id, body.get("note", ""), what="goal")

    return app


def main() -> None:
    cfg = load_config()
    uvicorn.run(create_app(cfg), host=cfg.get("host", "0.0.0.0"), port=int(cfg["port"]), log_level="warning")


if __name__ == "__main__":
    main()
