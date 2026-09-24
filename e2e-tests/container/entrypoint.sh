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
# The device the daemon talks to. Defaults to the one the package itself ships:
# `statemachined device` puts the firmware compiled for this host on a port, so
# a container -- like a rig box on the day it arrives -- has something real to
# point at without a board. Override it to reach a board on a cable.
STATEMACHINED_DEVICE_TARGET="${STATEMACHINED_DEVICE_TARGET:-socket://127.0.0.1:5300}"

# `expected_board` is the third edit and the least obvious, so it is worth
# saying why it is here rather than treating it as boilerplate.
#
# The package ships `expected_board = "uno_r4_minima"`, and the device this
# container runs identifies itself as `native`. statemachined refuses the
# mismatch on purpose -- "its pin names may look right and mean different
# holes, so nothing is pushed to it" -- and it is right to: a line map written
# for one board silently addressing another board's pins is how a rig rewards
# the wrong animal. So an operator running the packaged device has to say so,
# and this is that step, not a workaround for it.
EXPECTED_BOARD="${EXPECTED_BOARD:-native}"
if [ "$STATEMACHINED_DEVICE_TARGET" != "socket://127.0.0.1:5300" ]; then
  EXPECTED_BOARD="${EXPECTED_BOARD_OVERRIDE:-uno_r4_minima}"
fi

python3 - "$STATEMACHINED_DEVICE_TARGET" "$EXPECTED_BOARD" <<'PY'
import pathlib, sys

target, expected_board = sys.argv[1], sys.argv[2]
config = pathlib.Path("/etc/braemons/statemachined-rig-config.toml")
lines = [
    line
    for line in config.read_text().splitlines()
    if not line.strip().startswith(
        ("device_target", "startup_state_machine_config", "expected_board")
    )
]
lines += [
    f'device_target = "{target}"',
    f'expected_board = "{expected_board}"',
    'startup_state_machine_config = "bench"',
]
config.write_text("\n".join(lines) + "\n")
print(f"device_target  = {target}")
print(f"expected_board = {expected_board}")
PY

# The state-machine config holds the line map — the wiring, written down. On a
# rig this is edited through the web UI or dropped in by hand; either way it is a
# file in the store, and the daemon loads it by name at startup.
install -d /var/lib/braemons/statemachined/configs
python3 - <<'PY'
import json, pathlib
# tests/line_map.json is the one copy: conftest, this file and run_on_hardware.py read it.
line_map = json.loads(pathlib.Path("/opt/e2e-tests/tests/line_map.json").read_text())
config = {"name": "bench", "line_map": line_map, "graphs": []}
pathlib.Path("/var/lib/braemons/statemachined/configs/bench.config.json").write_text(
    json.dumps(config, indent=2) + "\n"
)
PY

# ── Run ───────────────────────────────────────────────────────────────────────

log "starting the device"
# Only when nothing else was named: an operator pointing this at a real board
# does not also want a simulated one answering on 5300.
DEVICE_PID=
if [ "$STATEMACHINED_DEVICE_TARGET" = "socket://127.0.0.1:5300" ]; then
  /opt/braemons/statemachined/bin/statemachined device --port 5300 \
    >/var/log/statemachined-device.log 2>&1 &
  DEVICE_PID=$!
  echo "the firmware, compiled for this host, on $STATEMACHINED_DEVICE_TARGET"
else
  echo "using $STATEMACHINED_DEVICE_TARGET; not starting a device"
fi

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
  kill $VSTIMD_PID $STATEMACHINED_PID $DEVICE_PID 2>/dev/null || true
  wait 2>/dev/null || true
}
trap cleanup EXIT

# Asked over gRPC, on 8082 -- one above the port the panels are served on,
# which is what `--port 8081` sets. A Python daemon binds twice; see
# contracts/DAEMON_LAYOUT.md.
for _ in $(seq 30); do
  if /opt/e2e-tests/bin/python -c "
import sys
from statemachined_client import StatemachinedClient
try:
    with StatemachinedClient('127.0.0.1:8082') as rig:
        rig.wait_until_ready(timeout_s=1)
        sys.exit(0 if rig.read_health().ok else 1)
except Exception:
    sys.exit(1)
" 2>/dev/null; then break; fi
  sleep 1
done

log "the daemons, as installed"
dpkg-query -W -f='${Package} ${Version}\n' \
  braemons-vstimd braemons-statemachined braemons-triald statemachined 2>/dev/null || true
/opt/e2e-tests/bin/pip list 2>/dev/null | grep -iE "vstimd|triald|statemachined" || true

log "tests"
cd /opt/e2e-tests
exec ./bin/python -m pytest tests -v \
  --display tcp://127.0.0.1:5555 \
  --event-port 5556 \
  --executor 127.0.0.1:8081 \
  "$@"
