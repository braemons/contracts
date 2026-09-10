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

import contextlib
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

#: The port a vstimd publishes events on, when one is already running.
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
        default=None,
        help="ZMQ address of a vstimd that is already running, e.g. "
        "tcp://127.0.0.1:5555. Given, this suite attaches to it; omitted, it "
        "starts one of its own. Deliberately no default: an earlier version "
        "compared the option against a default string, so a container passing "
        "that same string silently got a second renderer of its own.",
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
        f"{DEFAULT_TARGET}). Only used when this suite starts the daemon.",
    )
    parser.addoption(
        "--executor",
        default=None,
        help="base URL of a statemachined that is already running, e.g. "
        "http://127.0.0.1:8081. Given, this suite attaches to it; omitted, it "
        "starts one of its own.",
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
    """Whether this run is against a real rig — wires, and a person.

    Not the same as "the daemons are already running": a container attaches to
    running daemons too. This is only about what is physically there.

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
    attached = request.config.getoption("--display")
    if attached:
        yield from _attached_display(attached, request.config.getoption("--event-port"))
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


def _start_display(vstimd_binary: pathlib.Path, scratch: pathlib.Path, ports=None):
    """One vstimd, on ports of its own, touching nothing that outlives it.

    Two things here are isolation rather than configuration, and both are the
    difference between a suite you can run on your own workstation and one you
    cannot.

    **The storage directory.** Without `--storage-dir`, vstimd resolves
    `/var/lib/braemons/vstimd`, then `~/.local/braemons/vstimd` -- so a test
    server seeds demo scene-configs into the real one, writes a `_last_session`
    slot over whatever was there, and a developer's saved work is quietly a test
    run's leftovers. A temporary directory per server ends that.

    **The shared-memory name.** The virtual trigger lines live at a POSIX shm
    segment whose default name is the fixed `/vstimd_vtl`, so two vstimd
    processes on one host share it -- a test run and a rig, or two test runs at
    once, writing each other's trigger lines. It is a rig-config setting, so a
    per-server name is a two-line file rather than a change to vstimd.

    `ports` reuses an earlier server's, which is what a *restart* is: the same
    address coming back, so a subscriber that was attached to it reconnects
    rather than being handed a different rig.
    """
    from vstimd import Connection

    command_port, event_port = ports or distinct_ports(2)
    log = scratch / "vstimd.log"
    storage = scratch / "storage"
    storage.mkdir(exist_ok=True)

    # Unique per server, and short: Linux caps a POSIX shm name at NAME_MAX.
    rig_config = scratch / "rig-config.toml"
    rig_config.write_text(f'[vtl]\nshm_name = "/vstimd_test_{os.getpid()}_{command_port}"\n')

    sink = log.open("a")
    proc = subprocess.Popen(
        [
            str(vstimd_binary),
            "--null",
            "--zmq-port",
            str(command_port),
            "--event-port",
            str(event_port),
            "--no-web",
            "--storage-dir",
            str(storage),
            "--rig-config",
            str(rig_config),
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
        sink.close()
        pytest.fail(f"vstimd never answered:\n{log.read_text()}")

    info = {
        "address": address,
        "event_port": event_port,
        "log": log,
        "ports": (command_port, event_port),
    }
    return proc, sink, info


def _headless_display(vstimd_binary: pathlib.Path, tmp_path_factory):
    """A running vstimd in null mode: no display, a real frame clock.

    Null mode is not a lesser path. It runs the same per-frame drain as the
    display backends, so the events it publishes are the events a rig publishes
    -- which is the whole reason this test can run without a monitor attached.
    """
    scratch = tmp_path_factory.mktemp("vstimd")
    proc, sink, info = _start_display(vstimd_binary, scratch)
    try:
        yield info
    finally:
        _stop(proc)
        sink.close()


class RestartableDisplay:
    """A renderer this test owns, and may stop and bring back.

    **Its own, never the rig's.** A restart resets the frame counter, and a
    subscriber holding a frame index across one is holding a number from a
    different run -- which is exactly what `server.started` exists to tell it,
    and exactly why nothing may do this to a display somebody else is attached
    to. So this always spawns, even when `--display` named one, and the one it
    spawns is on ports of its own.
    """

    def __init__(self, vstimd_binary: pathlib.Path, scratch: pathlib.Path) -> None:
        self._binary = vstimd_binary
        self._scratch = scratch
        self._proc, self._sink, self.info = _start_display(vstimd_binary, scratch)

    def restart(self) -> None:
        """Stop it and bring it back on the same ports, as a power cut would."""
        _stop(self._proc)
        self._sink.close()
        self._proc, self._sink, self.info = _start_display(
            self._binary, self._scratch, ports=self.info["ports"]
        )

    def close(self) -> None:
        _stop(self._proc)
        self._sink.close()


@pytest.fixture
def restartable_display(vstimd_binary: pathlib.Path, tmp_path_factory):
    """A renderer of this test's own, which it may restart. See above."""
    display = RestartableDisplay(vstimd_binary, tmp_path_factory.mktemp("vstimd-restart"))
    try:
        yield display
    finally:
        display.close()


@pytest.fixture(scope="session")
def statemachined_bench(statemachined_device: pathlib.Path):
    """statemachined's own socket bridge for the native device.

    **Imported, never reimplemented.** statemachined's conftest says why: "two
    bridges that drift are two different devices". So this reaches for that one
    rather than writing a second.

    It used to have to reach into a checkout by path, because the bridge lived
    in `daemon/bench/` and was not in any distribution -- the bootstrap gap the
    README described. statemachined ships it now, as
    `statemachined.device.native_device_on_a_socket`, so the first thing tried
    is an ordinary import: an installed daemon has it, and so does anything with
    the wheel. The path fallbacks stay for a checkout that is not installed.
    """
    import sys

    try:
        from statemachined.device import native_device_on_a_socket

        return native_device_on_a_socket
    except ImportError:
        pass

    # A checkout, not installed. STATEMACHINED_SRC first because somebody who
    # said where it is has already answered the question.
    source = os.environ.get("STATEMACHINED_SRC")
    candidates = [pathlib.Path(source) / "daemon" / "src"] if source else []
    # Next to the device binary is where a release asset would unpack it.
    candidates.append(statemachined_device.parent / "src")
    for directory in candidates:
        if (directory / "statemachined" / "device" / "native_device_on_a_socket.py").exists():
            sys.path.insert(0, str(directory))
            from statemachined.device import native_device_on_a_socket

            return native_device_on_a_socket
    pytest.skip(
        "statemachined's socket bridge was not found. Install the daemon "
        "(`apt install braemons-statemachined`, or pip-install its wheel), or "
        "set STATEMACHINED_SRC to a checkout"
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


class Executor:
    """A statemachined on a port, talked to the way anything else would.

    **Not an imported application.** An earlier version of this file built the
    daemon in-process with Starlette's `TestClient`, which is how statemachined's
    own suite tests statemachined — correctly, because there the daemon is the
    subject. Here it is not: what is under test is a rig, and on a rig this
    daemon is a service on port 8081 that nothing imports. A test that reached
    into it as a library would be exercising a path no operator has.

    So: a real process, real HTTP, and a real WebSocket for the trace. Which also
    means `StateMachineExecutor` is used the way it ships, over httpx, rather
    than with a test client injected into it.
    """

    def __init__(self, base_url: str, client) -> None:
        self.base_url = base_url
        self._client = client

    def get(self, path: str, **kw):
        return self._client.get(path, **kw)

    def post(self, path: str, **kw):
        return self._client.post(path, **kw)

    def put(self, path: str, **kw):
        return self._client.put(path, **kw)

    @contextlib.contextmanager
    def trace_stream(self, observer: str = "triald"):
        """The executor's published trace, as an iterator of messages.

        Opening this is the whole of subscribing and closing it is the whole of
        leaving: nothing on the far end holds a trial for a subscriber, and
        `?observer=` is a label on its diagnostics page that grants nothing.
        """
        from websockets.sync.client import connect

        scheme = "wss" if self.base_url.startswith("https") else "ws"
        rest = self.base_url.split("://", 1)[-1]
        with connect(f"{scheme}://{rest}/api/trace/stream?observer={observer}") as socket:
            yield iter(lambda: socket.recv(), None)


def _rig_config(device_target: str, tmp_path: pathlib.Path) -> pathlib.Path:
    """The TOML an operator edits, written for this run.

    The same file `/etc/braemons/statemachined-rig-config.toml` is, pointed at a
    temporary state directory so a test run leaves nothing behind and cannot pick
    up a previous one's graphs.
    """
    import json

    configs = tmp_path / "configs"
    configs.mkdir(parents=True, exist_ok=True)
    (configs / "bench.config.json").write_text(
        json.dumps({"name": "bench", "line_map": LINE_MAP, "graphs": []}, indent=2) + "\n"
    )
    toml = tmp_path / "statemachined-rig-config.toml"
    toml.write_text(
        "\n".join(
            [
                f'device_target = "{device_target}"',
                "device_timeout_seconds = 5.0",
                f'graph_store_directory = "{tmp_path / "graphs"}"',
                f'state_machine_config_directory = "{configs}"',
                f'trace_directory = "{tmp_path / "trace"}"',
                f'recording_directory = "{tmp_path / "recordings"}"',
                "heartbeat_seconds = 0.5",
                'startup_state_machine_config = "bench"',
                "",
            ]
        )
    )
    return toml


@pytest.fixture
def executor(request: pytest.FixtureRequest, tmp_path):
    """A statemachined serving, in front of a device.

    **Attached, if one is already running** (`--executor`). That is what a
    container and a rig both look like: the daemon is a service, started from
    its unit with the config in `/etc/braemons`, and a test neither starts nor
    stops it. Nothing in the fixture differs between the two — a rig is a
    container with wires.

    Otherwise this suite starts one, the way the unit does, in front of the same
    firmware compiled for this host. That path is for developing; it is not what
    `make test` runs.
    """
    import httpx

    attached = request.config.getoption("--executor")
    if attached:
        client = httpx.Client(base_url=attached, timeout=10.0)
        try:
            if client.get("/api/health").status_code != 200:
                pytest.skip(f"statemachined at {attached} is not healthy")
        except Exception as error:
            pytest.skip(f"no statemachined at {attached}: {error}")
        yield Executor(attached, client)
        _leave_the_device_idle(client, attached)
        client.close()
        return

    yield from _spawned_executor(request, tmp_path)


def _spawned_executor(request: pytest.FixtureRequest, tmp_path):
    import httpx

    on_hardware = request.config.getoption("--hardware")
    if on_hardware:
        device_target = request.config.getoption("--target")
        device = None
    else:
        device = request.getfixturevalue("statemachined_bench").NativeDeviceOnASocket(
            store_path=str(tmp_path / "store.bin")
        )
        device.start()
        device_target = device.target_url

    port = distinct_ports(1)[0]
    base_url = f"http://127.0.0.1:{port}"
    log = tmp_path / "statemachined.log"
    with log.open("w") as sink:
        proc = subprocess.Popen(
            [
                _statemachined_command(),
                "serve",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--config",
                str(_rig_config(device_target, tmp_path)),
                # A test run must not advertise itself to the lab as a rig.
                "--no-mdns",
            ],
            stdout=sink,
            stderr=subprocess.STDOUT,
        )
        client = httpx.Client(base_url=base_url, timeout=10.0)

        def answering() -> bool:
            if proc.poll() is not None:
                pytest.fail(f"statemachined exited at once:\n{log.read_text()}")
            try:
                return client.get("/api/health").status_code == 200
            except Exception:
                return False

        if not wait_until(answering, timeout_s=30.0):
            _stop(proc)
            pytest.fail(f"statemachined never answered:\n{log.read_text()}")

        if on_hardware and client.get("/api/device").json().get("state") in (None, "absent"):
            _stop(proc)
            pytest.skip(f"no board answering on {device_target}")

        yield Executor(base_url, client)

        if on_hardware:
            _leave_the_device_idle(client, device_target)
        client.close()
        _stop(proc)
    if device is not None:
        device.stop()


def _stop(proc: subprocess.Popen) -> None:
    """SIGTERM, then insist.

    A daemon holding a serial link does not always unwind on the first signal,
    and a test run that hung in teardown would be indistinguishable from one that
    hung in a test. The suite's job is to leave nothing behind, not to be polite
    about it.
    """
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def _statemachined_command() -> str:
    """The daemon's entry point, as installed.

    `shutil.which` rather than `sys.executable -m`: the package declares a
    console script and that is what the service unit runs, so it is what should
    be exercised. A `.deb` puts it at /opt/braemons/statemachined/bin.
    """
    for candidate in ("statemachined", "/opt/braemons/statemachined/bin/statemachined"):
        found = shutil.which(candidate) or (candidate if pathlib.Path(candidate).exists() else None)
        if found:
            return found
    pytest.skip(
        "no statemachined on PATH: install it (`pip install statemachined`, or the "
        ".deb) — see rig/README.md, 'The bootstrap gap'"
    )


def _leave_the_device_idle(client, target: str) -> None:
    """A board is one object shared by every test, unlike a fresh native device.

    It refuses a graph upload while a trial is armed or running, so one test that
    walks away mid-trial fails the next several with an error about something
    else entirely. Local runs get a new device per test and need none of this.

    **A cancel names the trial it is cancelling.** `POST /api/trial/cancel` takes
    a `trial_id` and forbids anything else in the body, so the `{"reason": ...}`
    this used to send was a 422 the daemon never acted on -- a teardown that had
    never once returned a device to idle, invisible because nothing had yet run
    after the one test that leaves a trial in flight. The id comes from the
    daemon rather than from the caller: whatever is actually armed is what has
    to be cancelled, and the test that armed it may be the one that failed.
    """
    try:
        state = client.get("/api/state")
        if state.status_code != 200:
            return
        frame = state.json()
        # The daemon's own definition of busy — `running`, or a link state of 2,
        # which is armed but not yet started.
        client.post("/api/trial/cancel", json={"trial_id": frame.get("trial_id") or 0})
        # Cancelling is a round trip to the device; the next test's upload is
        # refused if it arrives first.
        wait_until(lambda: not _is_busy(client), timeout_s=5.0)
    except Exception as error:  # the run must not end because teardown was untidy
        print(f"\ncould not return {target} to idle: {error}")


def _is_busy(client) -> bool:
    frame = client.get("/api/state")
    if frame.status_code != 200:
        return False
    body = frame.json()
    return bool(body.get("running") or body.get("link_state") == 2)


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
