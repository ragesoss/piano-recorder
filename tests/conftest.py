import pytest


@pytest.fixture(autouse=True)
def room_inputs(tmp_path, monkeypatch):
    """Each test's own room inputs file (recorder/inputs.py), none to begin with: the configs' own inputs stand in,
    and nothing a test names reaches the desktop's file."""
    path = tmp_path / "room-inputs.toml"
    monkeypatch.setenv("ROOM_INPUTS_FILE", str(path))
    return path
