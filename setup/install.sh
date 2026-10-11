#!/bin/sh
# Make this machine the control unit (slot 5 in docs/components.md), or
# update it. Runs on the control unit, from the deployed tree
# (tools/deploy puts it at ~/practice-recorder). Safe to run again.
set -eu
app=$(cd "$(dirname "$0")/.." && pwd)

sudo apt-get install -y -q alsa-utils flac python3-venv >/dev/null

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
for unit in practice-recorder room-home; do
    sed -e "s|@USER@|$user|g" -e "s|@APP@|$app|g" "$app/setup/$unit.service" >"$unit_tmp"
    sudo install -m 644 "$unit_tmp" /etc/systemd/system/$unit.service
done
sudo systemctl daemon-reload
for unit in practice-recorder room-home; do
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
