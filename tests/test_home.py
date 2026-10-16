import re

from fastapi.testclient import TestClient

from home.server import create_app


def test_home_page_and_status_with_apps_down():
    cfg = {"apps": [{"id": "practice", "name": "Practice", "about": "x", "port": 1, "stop": ["/api/stop"]}]}
    with TestClient(create_app(cfg)) as c:
        assert "Music room" in c.get("/").text
        apps = c.get("/api/status").json()["apps"]
        assert apps[0]["id"] == "practice" and apps[0]["up"] is False
        assert c.post("/api/stop-all").status_code == 200


def test_home_page_fonts_are_served():
    """The page's fonts come from the home server: the exercise app's (exercises/static/fonts/)."""
    with TestClient(create_app({"apps": []})) as c:
        names = re.findall(r"url\(/fonts/([^)]+)\)", c.get("/").text)
        assert len(names) == 2
        for name in names:
            r = c.get(f"/fonts/{name}")
            assert r.status_code == 200 and r.headers["content-type"] == "font/woff2"
        for bad in ("nope.woff2", "OFL-public-sans.txt", "public-sans.ttf"):
            assert c.get(f"/fonts/{bad}").status_code == 404
