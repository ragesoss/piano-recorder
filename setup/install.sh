#!/bin/sh
# Make this machine the control unit (slot 5 in docs/components.md), or
# update it. Runs on the control unit, from the deployed tree
# (tools/deploy puts it at ~/practice-recorder). Safe to run again.
set -eu
app=$(cd "$(dirname "$0")/.." && pwd)

sudo apt-get install -y -q alsa-utils flac ffmpeg python3-venv >/dev/null

[ -d "$app/.venv" ] || python3 -m venv "$app/.venv"
"$app/.venv/bin/pip" install -q --upgrade pip
"$app/.venv/bin/pip" install -q -r "$app/requirements.txt"

# The config is installed once; later edits on the control unit are kept.
if [ ! -f /etc/practice-recorder.toml ]; then
    sudo install -m 644 "$app/config/practice.toml" /etc/practice-recorder.toml
    echo "installed /etc/practice-recorder.toml"
fi

takes=$(PYTHONPATH="$app" "$app/.venv/bin/python" -c 'from recorder.config import load_config; print(load_config().takes_dir)')
mkdir -p "$takes"

# The exercise app (exercises/): its own config, takes and exercises.
if [ ! -f /etc/exercise-recorder.toml ]; then
    sudo install -m 644 "$app/config/exercises.toml" /etc/exercise-recorder.toml
    echo "installed /etc/exercise-recorder.toml"
fi
for d in takes_dir exercises_dir; do
    mkdir -p "$(PYTHONPATH="$app" "$app/.venv/bin/python" -c "from recorder.config import load_config; print(load_config('/etc/exercise-recorder.toml').$d)")"
done

# The chord app (chords/): its own config, and the checks it keeps.
if [ ! -f /etc/chord-listener.toml ]; then
    sudo install -m 644 "$app/config/chords.toml" /etc/chord-listener.toml
    echo "installed /etc/chord-listener.toml"
fi
mkdir -p "$(PYTHONPATH="$app" "$app/.venv/bin/python" -c "from recorder.config import load_config; print(load_config('/etc/chord-listener.toml').takes_dir)")"

# The transcribe app (transcriber/): its own config, and its take folders. It runs the transcription
# project's pipeline and review generator from ~/dobro-transcription (its [pipeline] and [review]).
if [ ! -f /etc/take-transcriber.toml ]; then
    sudo install -m 644 "$app/config/transcribe.toml" /etc/take-transcriber.toml
    echo "installed /etc/take-transcriber.toml"
fi
mkdir -p "$(PYTHONPATH="$app" "$app/.venv/bin/python" -c "from recorder.config import load_config; print(load_config('/etc/take-transcriber.toml').takes_dir)")"

# The services: (re)installed and restarted on every run. room-home is the
# home page on port 80, linking this and the other apps (home/server.py).
if [ ! -f /etc/room-home.toml ]; then
    sudo install -m 644 "$app/config/home.toml" /etc/room-home.toml
    echo "installed /etc/room-home.toml"
fi
# The units run as this user, from this tree: @USER@ and @APP@ in
# setup/*.service are filled in here.
user=$(id -un)
unit_tmp=$(mktemp)
trap 'rm -f "$unit_tmp"' EXIT
for unit in practice-recorder exercise-recorder chord-listener take-transcriber room-home; do
    sed -e "s|@USER@|$user|g" -e "s|@APP@|$app|g" "$app/setup/$unit.service" >"$unit_tmp"
    sudo install -m 644 "$unit_tmp" /etc/systemd/system/$unit.service
done
sudo systemctl daemon-reload
for unit in practice-recorder exercise-recorder chord-listener take-transcriber room-home; do
    sudo systemctl enable -q $unit
    sudo systemctl restart $unit
done

# Running means answering over HTTP, not just started.
port=$(PYTHONPATH="$app" "$app/.venv/bin/python" -c 'from recorder.config import load_config; print(load_config().web_port)')
state="NOT answering on port $port (journalctl -u practice-recorder)"
for i in $(seq 1 20); do
    if python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:$port/api/status', timeout=2)" 2>/dev/null; then
        state="answering on port $port"
        break
    fi
    sleep 1
done
echo "control unit ready: $app, takes in $takes, service $state"

# Configs installed before are kept as they were: say what they lack.
grep -q '127.0.0.1:8002' /etc/practice-recorder.toml || echo "note: /etc/practice-recorder.toml has no exercise app (127.0.0.1:8002) among its peers"
grep -q 'port = 8002' /etc/room-home.toml || echo "note: /etc/room-home.toml does not list the exercise app (port 8002)"
for f in /etc/practice-recorder.toml /etc/exercise-recorder.toml; do
    grep -q '127.0.0.1:8006' "$f" || echo "note: $f has no transcribe app (127.0.0.1:8006) among its peers"
done
grep -q 'port = 8006' /etc/room-home.toml || echo "note: /etc/room-home.toml does not list the transcribe app (port 8006)"
