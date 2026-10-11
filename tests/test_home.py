from fastapi.testclient import TestClient

from home.server import create_app


def test_home_page_and_status_with_apps_down():
    cfg = {"apps": [{"id": "practice", "name": "Practice", "about": "x", "port": 1, "stop": ["/api/stop"]}]}
    with TestClient(create_app(cfg)) as c:
        assert "Music room" in c.get("/").text
        apps = c.get("/api/status").json()["apps"]
        assert apps[0]["id"] == "practice" and apps[0]["up"] is False
        assert c.post("/api/stop-all").status_code == 200
