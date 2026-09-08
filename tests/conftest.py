"""Bringing up a rig: a renderer, a state machine and the daemon that decides.

Every fixture here resolves the same way, in the same order, and the order is
the design:

1. **The pinned release artifact**, from `rig_versions.toml`. What an operator
   installs, which is the only thing this repo is really asking about.
2. **A local build named by an environment variable**, for developing against a
   change that is not released yet.
3. **Skip, naming exactly what was missing.**

Never a silent pass. A three-daemon suite that quietly ran two daemons would be
worse than no suite, because it would be green.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import socket
import subprocess
import time
import tomllib

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def pins() -> dict:
    return tomllib.loads((REPO / "rig_versions.toml").read_text())


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def distinct_ports(count: int) -> list[int]:
    """`count` ports that are not each other.

    Two ephemeral ports can come back the same number, and the collision is
    silent: the second bind fails and that daemon is simply never there.
    """
    ports: list[int] = []
    while len(ports) < count:
        port = free_port()
        if port not in ports:
            ports.append(port)
    return ports


def wait_until(predicate, timeout_s: float = 10.0, interval_s: float = 0.25) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval_s)
    return False


# ── Resolving the daemons ─────────────────────────────────────────────────────


def _released_artifact(name: str, pins: dict) -> pathlib.Path | None:
    """The pinned release, if it has been fetched into `artifacts/`.

    Fetching is CI's job (see .github/workflows/ci.yml) rather than this
    fixture's: a test that downloads from the network is a test that fails when
    GitHub is slow, and it would do it once per session per developer.
    """
    entry = pins.get(name, {})
    asset = entry.get("asset")
    if not asset:
        return None
    path = REPO / "artifacts" / asset.format(version=entry["version"])
    return path if path.exists() else None


@pytest.fixture(scope="session")
def vstimd_binary(pins: dict) -> pathlib.Path:
    """The renderer, as a compiled server.

    The Python client is a wheel and comes from the `rig` group; this is the
    other half, and it is a binary because the thing under test is a process
    with a frame clock in it. A client with no server would let every test here
    pass while proving nothing.
    """
    override = os.environ.get("VSTIMD_BINARY")
    if override:
        path = pathlib.Path(override)
        if not path.exists():
            pytest.fail(f"VSTIMD_BINARY is set to {path}, which does not exist")
        return path
    released = _released_artifact("vstimd", pins)
    if released is not None:
        return released
    found = shutil.which("vstimd")
    if found:
        return pathlib.Path(found)
    pytest.skip(
        "no vstimd server: the pinned release asset is not in artifacts/, "
        "nothing named vstimd is on PATH, and VSTIMD_BINARY is unset. "
        "vstimd 0.2 is not released yet — see README.md, 'The bootstrap gap'. "
        "For now: VSTIMD_BINARY=/path/to/vstimd/target/release/vstimd"
    )


@pytest.fixture(scope="session")
def statemachined_device(pins: dict) -> pathlib.Path:
    """The firmware's own session and engine, built for this host.

    Not a mock and not a simulator: it is the same C++ the MCU runs, compiled
    for x86, which is what makes a test of the daemon a test of the device's
    parser, validator and scan loop rather than of a stand-in for them.
    """
    override = os.environ.get("STATEMACHINED_DEVICE")
    if override:
        path = pathlib.Path(override)
        if not path.exists():
            pytest.fail(f"STATEMACHINED_DEVICE is set to {path}, which does not exist")
        return path
    released = _released_artifact("statemachined", pins)
    if released is not None:
        return released
    pytest.skip(
        "no statemachined native device: it is not published as a release asset "
        "yet — see README.md, 'The bootstrap gap'. For now, build it in the "
        "statemachined repo and set STATEMACHINED_DEVICE"
    )


# ── Running them ──────────────────────────────────────────────────────────────


@pytest.fixture(scope="session")
def display(vstimd_binary: pathlib.Path, tmp_path_factory) -> dict:
    """A running vstimd in null mode: no display, a real frame clock.

    Null mode is not a lesser path. It runs the same per-frame drain as the
    display backends, so the events it publishes are the events a rig publishes
    — which is the whole reason this test can run without a monitor attached.
    """
    from vstimd import Connection

    command_port, event_port = distinct_ports(2)
    log = tmp_path_factory.mktemp("vstimd") / "vstimd.log"

    with log.open("w") as sink:
        proc = subprocess.Popen(
            [
                str(vstimd_binary),
                "--null",
                "--zmq-port",
                str(command_port),
                "--event-port",
                str(event_port),
                "--no-web",
            ],
            stdout=sink,
            stderr=subprocess.STDOUT,
            env={**os.environ, "RUST_LOG": "info"},
        )
        address = f"tcp://localhost:{command_port}"

        def answering() -> bool:
            if proc.poll() is not None:
                pytest.fail(f"vstimd exited at once:\n{log.read_text()}")
            try:
                with Connection(address, recv_timeout_s=1.0) as probe:
                    probe.system.query_server_info()
                return True
            except Exception:
                return False

        if not wait_until(answering, timeout_s=20.0):
            proc.terminate()
            pytest.fail(f"vstimd never answered:\n{log.read_text()}")

        yield {"address": address, "event_port": event_port, "log": log}

        proc.terminate()
        proc.wait(timeout=5)


@pytest.fixture(scope="session")
def statemachined_bench(statemachined_device: pathlib.Path):
    """statemachined's own socket bridge for the native device.

    **Imported, never reimplemented.** statemachined's conftest says why: "two
    bridges that drift are two different devices". So this reaches for that one
    rather than writing a second, which means the bridge has to be findable —
    today by `STATEMACHINED_BENCH`, and properly by statemachined shipping
    `bench/` in its distribution, which it should: the bridge is already
    described there as "how anybody runs this daemon with no board on the desk",
    which is a runtime concern, not a test one.
    """
    import sys

    bench = os.environ.get("STATEMACHINED_BENCH")
    candidates = [pathlib.Path(bench)] if bench else []
    # Next to the device binary is where a release asset would unpack it.
    candidates.append(statemachined_device.parent / "bench")
    for directory in candidates:
        if (directory / "native_device_on_a_socket.py").exists():
            sys.path.insert(0, str(directory))
            import native_device_on_a_socket

            return native_device_on_a_socket
    pytest.skip(
        "statemachined's bench bridge was not found: set STATEMACHINED_BENCH to "
        "the statemachined repo's daemon/bench directory. It is not packaged "
        "yet — see README.md, 'The bootstrap gap'"
    )


@pytest.fixture
def executor(statemachined_bench, statemachined_device: pathlib.Path, tmp_path):
    """A statemachined daemon in front of a freshly booted device.

    One device per test, so state cannot leak: a device remembers its wiring,
    its graph set and whether it should be running trials on its own, and a
    shared store would make one test's settings the next test's boot.
    """
    import json

    from fastapi.testclient import TestClient
    from statemachined.api.application import create_application
    from statemachined.rig_configuration import RigConfiguration

    os.environ.setdefault("STATEMACHINED_NATIVE_DEVICE", str(statemachined_device))
    device = statemachined_bench.NativeDeviceOnASocket(
        store_path=str(tmp_path / "store.bin")
    )
    device.start()

    configs = tmp_path / "configs"
    configs.mkdir(parents=True, exist_ok=True)
    (configs / "bench.config.json").write_text(
        json.dumps(
            {
                "name": "bench",
                "line_map": {
                    "input_lines": [
                        {"name": "start_switch", "line_index": 0},
                        {"name": "lever", "line_index": 4},
                    ],
                    "output_lines": [
                        {"name": "ready_lamp", "line_index": 0},
                        {"name": "reward_valve", "line_index": 3, "safe_level_is_high": True},
                    ],
                },
                "graphs": [],
            },
            indent=2,
        )
        + "\n"
    )

    configuration = RigConfiguration(
        device_target=device.target_url,
        device_timeout_seconds=5.0,
        graph_store_directory=tmp_path / "graphs",
        state_machine_config_directory=configs,
        trace_directory=tmp_path / "trace",
        recording_directory=tmp_path / "recordings",
        heartbeat_seconds=0.5,
        startup_state_machine_config="bench",
    )
    with TestClient(create_application(configuration)) as client:
        yield client
    device.stop()
