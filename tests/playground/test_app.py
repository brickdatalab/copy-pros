from fastapi.testclient import TestClient

from trader.playground.app import app


def test_playground_home_and_state_routes() -> None:
    client = TestClient(app)
    home = client.get("/")
    assert home.status_code == 200
    assert "Copy Pros Local Playground" in home.text

    state = client.get("/api/state")
    assert state.status_code == 200
    body = state.json()
    assert "running" in body
    assert "markets" in body
