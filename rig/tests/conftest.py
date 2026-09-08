"""Bringing up a rig: a renderer, a state machine and the daemon that decides.

**Two rigs, one set of tests.** By default the daemons are brought up on this
machine — the renderer headless, the state machine as firmware compiled for the
host — and that is what CI runs. With `--hardware` the same fixtures resolve to a
real rig instead: a vstimd already running on the stimulus display, a board on
the end of a cable, and wires between them.

The tests do not know which. That is the whole design of this file, and it is
worth defending: an acceptance suite written separately from the CI suite is an
acceptance suite that drifts, and the drift is invisible precisely because the
two are never run together. Here, `make accept` runs every test CI runs, against
silicon, and then the handful that only wiring can answer.

## Resolving the daemons

Every fixture resolves the same way, in the same order, and the order is the
design:

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

#: The rig half of the contracts repo — `rig/`, not the repo root.
#: The rig half of the contracts repo — `rig/`, not the repo root.
REPO = pathlib.Path(__file__).resolve().parents[1]

#: Where a rig's vstimd answers, when one is already running. On a rig box it is
#: started by systemd and owns the stimulus display; nothing here starts or stops
#: it, because a renderer that a test could restart is a renderer that a test can
#: leave a monitor black.
DEFAULT_DISPLAY = "tcp://localhost:5555"
DEFAULT_EVENT_PORT = 5556

#: Where the board is. Any pyserial URL, the same spelling statemachined's own
#: hardware suite takes.
DEFAULT_TARGET = "/dev/ttyACM0"


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--hardware",
        action="store_true",
        help="run against a real rig: a running vstimd on the stimulus display "
        "and a board on --target. Without it, everything is brought up locally.",
    )
    parser.addoption(
        "--display",
        default=DEFAULT_DISPLAY,
        help=f"ZMQ address of the rig's vstimd (default: {DEFAULT_DISPLAY}). --hardware only.",
    )
    parser.addoption(
        "--event-port",
        type=int,
        default=DEFAULT_EVENT_PORT,
        help=f"its event stream (default: {DEFAULT_EVENT_PORT}). --hardware only.",
    )
    parser.addoption(
        "--target",
        default=DEFAULT_TARGET,
        help=f"device path, host:port, or any pyserial URL (default: "
        f"{DEFAULT_TARGET}). --hardware only.",
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "wiring: needs the rig physically wired — see WIRING.md. Skipped without "
        "--hardware, because there is nothing to be wrong about on a machine with "
        "no wires.",
    )
    config.addinivalue_line(
        "markers",
        "manual: a person has to look at the display and answer. Skipped without "
        "--hardware. These are the ones no amount of CI replaces: whether the "
        "stimulus was actually *visible* is not a thing any daemon can report.",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list) -> None:
    if config.getoption("--hardware"):
        return
    skip = pytest.mark.skip(reason="needs --hardware and a wired rig; see rig/WIRING.md")
    for item in items:
        if "wiring" in item.keywords or "manual" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def on_hardware(request: pytest.FixtureRequest) -> bool:
    """Whether this run is against a real rig.

    A test should need this only to *report* differently, never to assert
    differently. A test that asserts one thing locally and another on hardware
    has stopped being one test.
    """
    return bool(request.config.getoption("--hardware"))


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
def display(request: pytest.FixtureRequest, tmp_path_factory) -> dict:
    """A vstimd to command and listen to.

    On a rig, one that is **already running** and owns the stimulus monitor:
    started by systemd, on for alignment and luminance checks whether or not a
    session exists. Nothing here starts or stops it. A test that could restart
    the renderer is a test that can leave a monitor black in front of an animal,
    and the acceptance run happens with the animal's rig, not a spare.

    Locally, one of its own in null mode. Null is not a lesser path: it runs the
    same per-frame drain as the display backends, so the events it publishes are
    the events a rig publishes — which is what lets the same tests run in both
    places.
    """
    if request.config.getoption("--hardware"):
        yield from _attached_display(
            request.config.getoption("--display"),
            request.config.getoption("--event-port"),
        )
    else:
        yield from _headless_display(request.getfixturevalue("vstimd_binary"), tmp_path_factory)


def _attached_display(address: str, event_port: int):
    from vstimd import Connection

    try:
        with Connection(address, recv_timeout_s=2.0) as probe:
            probe.system.wait_for_frames(0)
    except Exception as error:
        pytest.skip(
            f"no vstimd answering at {address}: {error}. On a rig it is a service "
            f"(`systemctl status vstimd`); point --display elsewhere if yours is "
            f"not there."
        )
    yield {"address": address, "event_port": event_port, "log": None}


def _headless_display(vstimd_binary: pathlib.Path, tmp_path_factory):
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
                    probe.system.wait_for_frames(0)
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


def _daemon_configuration(device_target: str, tmp_path):
    """One rig config, whatever is on the other end of `device_target`.

    Shared between the two executor fixtures on purpose: a board and a
    host-compiled device must be given *the same* daemon, or a difference in
    behaviour between them is a difference between two configurations and proves
    nothing about the hardware.
    """
    import json

    from statemachined.rig_configuration import RigConfiguration

    configs = tmp_path / "configs"
    configs.mkdir(parents=True, exist_ok=True)
    (configs / "bench.config.json").write_text(
        json.dumps({"name": "bench", "line_map": LINE_MAP, "graphs": []}, indent=2) + "\n"
    )
    return RigConfiguration(
        device_target=device_target,
        device_timeout_seconds=5.0,
        graph_store_directory=tmp_path / "graphs",
        state_machine_config_directory=configs,
        trace_directory=tmp_path / "trace",
        recording_directory=tmp_path / "recordings",
        heartbeat_seconds=0.5,
        startup_state_machine_config="bench",
    )


#: The rig these tests assume, as a state-machine config.
#:
#: **This is the wiring, written down.** `WIRING.md` says which physical pin each
#: of these names is, and the acceptance tests are only meaningful if the two
#: agree — a `lever` on line 4 here and a button soldered to line 5 there is a
#: test that fails for a reason having nothing to do with any daemon.
LINE_MAP = {
    "input_lines": [
        {"name": "start_switch", "line_index": 0},
        {"name": "lever", "line_index": 4},
    ],
    "output_lines": [
        {"name": "ready_lamp", "line_index": 0},
        {"name": "reward_valve", "line_index": 3, "safe_level_is_high": True},
        {"name": "stimulus_gate", "line_index": 1},
    ],
}


@pytest.fixture
def executor(request: pytest.FixtureRequest, tmp_path):
    """A statemachined in front of a device.

    On a rig, the board on `--target`. Locally, the same firmware compiled for
    this host, on a socket. The daemon is identical either way — see
    `_daemon_configuration` — so a test that passes locally and fails on the
    bench has told you something about the device, which is the only reason to
    own a bench.
    """
    from fastapi.testclient import TestClient
    from statemachined.api.application import create_application

    if request.config.getoption("--hardware"):
        target = request.config.getoption("--target")
        configuration = _daemon_configuration(target, tmp_path)
        with TestClient(create_application(configuration)) as client:
            if client.get("/api/device").json().get("state") in (None, "absent"):
                pytest.skip(f"no board answering on {target}")
            yield client
            _leave_the_device_idle(client, target)
        return

    device = request.getfixturevalue("statemachined_bench").NativeDeviceOnASocket(
        store_path=str(tmp_path / "store.bin")
    )
    device.start()
    configuration = _daemon_configuration(device.target_url, tmp_path)
    with TestClient(create_application(configuration)) as client:
        yield client
    device.stop()


def _leave_the_device_idle(client, target: str) -> None:
    """A board is one object shared by every test, unlike a fresh native device.

    It refuses a graph upload while a trial is running, so one test that walks
    away mid-trial fails the next several with an error about something else
    entirely. Local runs get a new device per test and need none of this.
    """
    try:
        client.post("/api/trial/cancel", json={"reason": "test teardown"})
    except Exception as error:  # the run must not end because teardown was untidy
        print(f"\ncould not return {target} to idle: {error}")


# ── The operator ──────────────────────────────────────────────────────────────


@pytest.fixture
def confirm(request: pytest.FixtureRequest):
    """Ask the person at the rig, and fail if the answer is no.

    **The only assertion in this repository a machine cannot make.** Whether a
    stimulus was *visible*, whether the valve audibly clicked, whether the lamp
    lit — no daemon reports these, and a rig that reports a stimulus it did not
    draw is exactly the failure an acceptance test exists to catch.

    Capture is suspended around the prompt: pytest swallows stdout by default,
    and an acceptance run that appeared to hang while silently waiting for an
    answer would be worse than no prompt at all.
    """
    if not request.config.getoption("--hardware"):
        pytest.skip("--hardware only: there is nobody to ask")

    capture = request.config.pluginmanager.getplugin("capturemanager")

    def ask(question: str) -> None:
        with capture.global_and_fixture_disabled():
            print(f"\n  {question}")
            answer = input("  [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            pytest.fail(f"the operator said no: {question}")

    return ask
