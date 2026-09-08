#!/usr/bin/env bash
# Configure and run, the way an operator does.
#
# There is one honest difference from a rig, and it is worth naming rather than
# hiding: **there is no systemd here**, so the daemons are started with the exact
# command line their unit files run rather than with `systemctl start`. What that
# does not exercise is supervision — restart-on-failure, ordering, the sysusers
# and tmpfiles the units rely on. Everything else, including the packaged
# interpreter, the installed entry point and the config in /etc/braemons, is the
# real thing.
set -euo pipefail

log() { printf '\n=== %s ===\n' "$1"; }

# ── Configure ─────────────────────────────────────────────────────────────────
#
# Editing /etc/braemons is the operator's first act after installing, and the
# packages ship these files precisely so there is something to edit. Overwriting
# them here is not a shortcut around the package: it is the step the package
# expects, and its postinstall says so.

log "configuring /etc/braemons"
: "${STATEMACHINED_DEVICE_TARGET:?set STATEMACHINED_DEVICE_TARGET (e.g. socket://127.0.0.1:5300)}"

python3 - "$STATEMACHINED_DEVICE_TARGET" <<'PY'
import pathlib, sys

target = sys.argv[1]
config = pathlib.Path("/etc/braemons/statemachined-rig-config.toml")
lines = [
    line
    for line in config.read_text().splitlines()
    if not line.strip().startswith(("device_target", "startup_state_machine_config"))
]
lines += [f'device_target = "{target}"', 'startup_state_machine_config = "bench"']
config.write_text("\n".join(lines) + "\n")
print(f"device_target = {target}")
PY

# The state-machine config holds the line map — the wiring, written down. On a
# rig this is edited through the web UI or dropped in by hand; either way it is a
# file in the store, and the daemon loads it by name at startup.
install -d /var/lib/braemons/statemachined/configs
cat > /var/lib/braemons/statemachined/configs/bench.config.json <<'JSON'
{
  "name": "bench",
  "line_map": {
    "input_lines": [
      {"name": "start_switch", "line_index": 0},
      {"name": "lever", "line_index": 4}
    ],
    "output_lines": [
      {"name": "ready_lamp", "line_index": 0},
      {"name": "reward_valve", "line_index": 3, "safe_level_is_high": true},
      {"name": "stimulus_gate", "line_index": 1}
    ]
  },
  "graphs": []
}
JSON

# ── Run ───────────────────────────────────────────────────────────────────────

log "starting vstimd"
# --null: there is no display in a container. The frame loop is the same one the
# display backends run, which is what makes the event stream here the event
# stream a rig publishes.
/usr/bin/vstimd --null --no-web >/var/log/vstimd.log 2>&1 &
VSTIMD_PID=$!

log "starting statemachined"
/opt/braemons/statemachined/bin/statemachined serve \
  --host 127.0.0.1 --port 8081 --no-mdns \
  >/var/log/statemachined.log 2>&1 &
STATEMACHINED_PID=$!

cleanup() {
  kill "$VSTIMD_PID" "$STATEMACHINED_PID" 2>/dev/null || true
  wait 2>/dev/null || true
}
trap cleanup EXIT

for _ in $(seq 30); do
  if /opt/rig-tests/bin/python -c "
import sys, httpx
try:
    sys.exit(0 if httpx.get('http://127.0.0.1:8081/api/health', timeout=1).status_code == 200 else 1)
except Exception:
    sys.exit(1)
" 2>/dev/null; then break; fi
  sleep 1
done

log "the daemons, as installed"
dpkg-query -W -f='${Package} ${Version}\n' braemons-vstimd statemachined 2>/dev/null || true
/opt/rig-tests/bin/pip list 2>/dev/null | grep -iE "vstimd|triald" || true

log "tests"
cd /opt/rig-tests
exec ./bin/python -m pytest tests -v \
  --display tcp://127.0.0.1:5555 \
  --event-port 5556 \
  --executor http://127.0.0.1:8081 \
  "$@"
