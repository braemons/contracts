#!/usr/bin/env python3
"""Run the end-to-end suite against real hardware, interactively.

    ./run_on_hardware.py docker            # a workstation with the MCU on USB
    sudo ./run_on_hardware.py pi           # a Raspberry Pi test system with the MCU attached
    ./run_on_hardware.py pi --dry-run      # only say what would happen

Both modes say what they are going to do and ask before doing it, check what
they can before touching anything, and ask again before anything that moves a
pin or changes the system.

**docker** installs nothing on this machine. The pinned release packages go into
the same image `make test` builds; the board is passed through into the
container. vstimd runs headless there, so the tests that need a display or a
GPIO input are left out.

**pi** turns a Raspberry Pi into the rig WIRING.md draws: it installs the pinned
arm64 packages, configures statemachined for the board and gpiochip-daqd for the
TTL from the board into vstimd, starts the services, runs the whole suite
including the questions only a person can answer, and offers to put the
configuration back afterwards.

Standard library only: this has to run before anything is installed.
"""

from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import os
import platform
import shlex
import shutil
import socket
import subprocess
import sys
import time
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
PINS = tomllib.loads((HERE / "rig_versions.toml").read_text())
LINE_MAP = json.loads((HERE / "tests" / "line_map.json").read_text())
IMAGE = "braemons-e2e-tests"

#: Raspberry Pi 40-pin header pin -> BCM GPIO, which is the gpiochip line offset
#: on both the Pi 4 (pinctrl-bcm2711) and the Pi 5 (pinctrl-rp1).
HEADER_TO_BCM = {
    7: 4, 11: 17, 12: 18, 13: 27, 15: 22, 16: 23, 18: 24, 19: 10, 21: 9, 22: 25,
    23: 11, 24: 8, 26: 7, 29: 5, 31: 6, 32: 12, 33: 13, 35: 19, 36: 16, 37: 26,
    38: 20, 40: 21,
}  # fmt: skip

# ── Talking to the person ─────────────────────────────────────────────────────

COLOUR = sys.stdout.isatty()


def _paint(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if COLOUR else text


def heading(text: str) -> None:
    print(f"\n{_paint('1;36', '══ ' + text + ' ')}{_paint('36', '═' * max(0, 70 - len(text)))}")


def say(text: str = "") -> None:
    print(text)


def ok(text: str) -> None:
    print(f"  {_paint('32', '✓')} {text}")


def bad(text: str) -> None:
    print(f"  {_paint('31', '✗')} {text}")


def warn(text: str) -> None:
    print(f"  {_paint('33', '!')} {text}")


def ask(question: str, default: bool = False) -> bool:
    hint = "[Y/n]" if default else "[y/N]"
    while True:
        try:
            answer = input(f"\n{_paint('1', question)} {hint} ").strip().lower()
        except EOFError:
            return default
        if not answer:
            return default
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False


def choose(question: str, options: list[tuple[str, str]], default: str) -> str:
    say(f"\n{_paint('1', question)}")
    for key, text in options:
        marker = "*" if key == default else " "
        say(f"  {marker} [{key}] {text}")
    keys = {key for key, _ in options}
    while True:
        try:
            answer = input(f"choice [{default}]: ").strip().lower() or default
        except EOFError:
            return default
        if answer in keys:
            return answer


def prompt(question: str, default: str) -> str:
    try:
        return input(f"{_paint('1', question)} [{default}]: ").strip() or default
    except EOFError:
        return default


class Abort(Exception):
    """Stop here, and say why."""


def run(command: list[str], *, check: bool = True, capture: bool = False, **kwargs):
    say(_paint("2", "  $ " + shlex.join(command)))
    result = subprocess.run(command, text=True, capture_output=capture, **kwargs)
    if check and result.returncode:
        detail = (result.stderr or result.stdout or "").strip() if capture else ""
        raise Abort(f"`{shlex.join(command)}` failed ({result.returncode}). {detail}")
    return result


def print_plan(title: str, steps: list[str], touches: list[str], not_covered: list[str]) -> None:
    heading(title)
    say("This is what will happen, in order:\n")
    for number, step in enumerate(steps, 1):
        say(f"  {number:>2}. {step}")
    say("\nWhat it changes:")
    for item in touches:
        say(f"   • {item}")
    if not_covered:
        say("\nWhat it cannot test in this mode:")
        for item in not_covered:
            say(f"   • {item}")


def safety_notice() -> None:
    heading("Before any pin moves")
    warn("The tests pulse `reward_valve`. Disconnect the water supply, or anything")
    warn("else the valve drives, and make sure no animal is in the rig.")
    warn("Outputs are raised and lowered for real; nothing wired to them should be")
    warn("able to hurt anybody or anything when that happens.")


def wiring_table(pin_labels: dict[str, str] | None, gate_input: str) -> None:
    heading("Wiring")
    say("  line            board line      board pin   connect to")
    say("  ──────────────  ──────────────  ──────────  ────────────────────────────────")
    targets = {
        "ready_lamp": "lever (the loopback jumper)",
        "stimulus_gate": gate_input,
        "reward_valve": "the valve driver (water disconnected)",
        "start_switch": "a push button (not used by the automatic tests)",
        "lever": "ready_lamp (the loopback jumper)",
    }
    for direction in ("output_lines", "input_lines"):
        for line in LINE_MAP[direction]:
            name = line["name"]
            label = (pin_labels or {}).get(name) or "?"
            kind = "out" if direction == "output_lines" else "in"
            say(f"  {name:<14}  {kind} {line['line_index']:<11}  {label:<10}  {targets[name]}")
    say("\n  Share a ground between everything above.")


# ── Pinned releases ───────────────────────────────────────────────────────────


def asset_names(arch: str) -> dict[str, str]:
    """The pinned package file names for one architecture, by role."""

    def deb(entry: dict, key: str = "asset") -> str:
        return entry[key].format(version=entry["version"]).replace("_amd64.deb", f"_{arch}.deb")

    return {
        "vstimd": deb(PINS["vstimd"]),
        "gpiochip-daqd": deb(PINS["vstimd"], "gpiochip_daqd_asset"),
        "statemachined": deb(PINS["statemachined"]),
        "triald-wheel": PINS["triald"]["wheel"].format(
            wheel_version=PINS["triald"]["wheel_version"]
        ),
    }


def release_url(repo_key: str, asset: str) -> str:
    entry = PINS[repo_key]
    return f"https://github.com/{entry['repo']}/releases/download/{entry['tag']}/{asset}"


def download(url: str, into: Path) -> Path:
    into.mkdir(parents=True, exist_ok=True)
    destination = into / url.rsplit("/", 1)[-1]
    if destination.exists() and destination.stat().st_size > 0:
        ok(f"already downloaded: {destination.name}")
        return destination
    say(_paint("2", f"  ↓ {url}"))
    try:
        with urllib.request.urlopen(url, timeout=60) as response, destination.open("wb") as out:
            shutil.copyfileobj(response, out)
    except urllib.error.URLError as error:
        destination.unlink(missing_ok=True)
        raise Abort(f"could not download {url}: {error}") from error
    ok(f"{destination.name} ({destination.stat().st_size // 1024} KiB)")
    return destination


# ── Checks shared by both modes ───────────────────────────────────────────────


def serial_candidates() -> list[str]:
    by_id = sorted(glob.glob("/dev/serial/by-id/*"))
    return (
        (["/dev/braemons/statemachined0"] if os.path.exists("/dev/braemons/statemachined0") else [])
        + by_id
        + sorted(glob.glob("/dev/ttyACM*"))
    )


def pick_device(given: str | None) -> str:
    if given:
        if not os.path.exists(given):
            raise Abort(f"{given} does not exist. Is the board plugged in?")
        ok(f"board: {given} -> {os.path.realpath(given)}")
        return given
    candidates = serial_candidates()
    if not candidates:
        raise Abort("no serial device found (/dev/serial/by-id, /dev/ttyACM*). Plug the board in.")
    say("  Serial devices found:")
    for candidate in candidates:
        say(f"    {candidate} -> {os.path.realpath(candidate)}")
    device = prompt("  Which one is the statemachined board?", candidates[0])
    if not os.path.exists(device):
        raise Abort(f"{device} does not exist")
    return device


def pytest_selection(
    *, manual: bool, jumper: bool, gpio_to_renderer: bool, only_hardware: bool = False
) -> list[str]:
    """pytest -m/-k for what this rig can do and what the person asked for."""
    markers = ["(wiring or manual)"] if only_hardware else []
    names = []
    if not manual:
        markers.append("not manual")
    if not jumper:
        markers.append("not wiring")
        names.append("not needs_a_wire")
    if not gpio_to_renderer:
        names.append("not ttl_reaches_the_renderer")
    selection = []
    if markers:
        selection += ["-m", " and ".join(markers)]
    if names:
        selection += ["-k", " and ".join(names)]
    return selection


# ── Firmware ──────────────────────────────────────────────────────────────────

#: Where the statemachined package puts the firmware image it ships, and its manifest.
FIRMWARE_DIR = "/usr/share/braemons/statemachined/firmware"

#: What an unstamped firmware reports. statemachined stamps `fw` from the git tag
#: after 0.2.0-alpha1 and reports 0.0.0 when a build was not stamped; before
#: that the MCU build hard-coded 0.1.0 and the native device 0.0.0.
UNSTAMPED_FIRMWARE = {"0.1.0", "0.0.0", ""}


def parse_manifest(text: str) -> dict[str, str]:
    """`version`, `commit` and the image's `sha256` from the package's MANIFEST.txt."""
    found: dict[str, str] = {}
    in_hashes = False
    for line in text.splitlines():
        key, _, value = line.partition(":")
        if key.strip() in ("version", "commit") and value.strip():
            found[key.strip()] = value.strip()
        if line.strip() == "sha256:":
            in_hashes = True
            continue
        if in_hashes and line.strip().endswith(".bin"):
            found["sha256"], found["image"] = line.split()[0], line.split()[-1]
    return found


def parse_device(output: str) -> dict:
    """board, firmware and protocol from `statemachinectl device`, if it has a board.

    The daemon answers whether or not it reached one; `connected` is what says
    the board replied to the greeting.
    """
    try:
        device = json.loads(output)
    except ValueError:
        return {}
    if not device.get("connected"):
        return {}
    return {key: device.get(key) for key in ("board", "firmware_version", "protocol_version")}


def pin_names(output: str) -> str:
    """The board's own pin labels, from `statemachinectl lines`, one line per direction."""
    try:
        lines = json.loads(output)
    except ValueError:
        return output
    return "\n".join(
        f"  {direction:<7} " + "  ".join(
            f"{index}:{label}" for index, label in enumerate(lines.get(key) or [])
        )
        for direction, key in (("inputs", "board_input_pins"), ("outputs", "board_output_pins"))
    )


#: Run inside the image: the daemon in front of the board, asked what it found
#: through the client's command line -- the way a rig asks it -- then stopped.
PROBE = """
cat > /tmp/probe.toml <<TOML
device_target = "{device}"
connect_on_startup = true
expected_board = ""
graph_store_directory = "/tmp/probe/graphs"
state_machine_config_directory = "/tmp/probe/configs"
trace_directory = "/tmp/probe/trace"
recording_directory = "/tmp/probe/recordings"
TOML
/usr/bin/statemachined serve --rig-config /tmp/probe.toml --port 8081 --no-mdns \
  >/tmp/probe.log 2>&1 &
daemon=$!
ctl="/opt/e2e-tests/bin/statemachinectl --rig 127.0.0.1:8081"
for _ in $(seq 40); do
  $ctl device 2>/dev/null | grep -q '"connected": true' && break
  sleep 0.25
done
$ctl device; echo '=== pins'; $ctl lines; echo '=== manifest'
cat {manifest} 2>/dev/null
kill $daemon
"""


def firmware_check(board: dict, manifest: dict[str, str]) -> bool:
    """Say what the board runs against what the package ships; True to carry on.

    The protocol version is already enforced: the firmware refuses a hello in
    any other version (`bad_proto`), so a connected board speaks the daemon's
    protocol. What is not enforced is *which build* it is, and features added
    without a protocol bump fail only when a graph uses them.
    """
    heading("Firmware")
    running = str(board.get("firmware_version") or "")
    ok(
        f"board {board.get('board')!r}, firmware {running or '?'}, protocol "
        f"{board.get('protocol_version')} (the daemon's: another would be refused as bad_proto)"
    )
    shipped = manifest.get("version")
    if manifest:
        say(
            f"  The package ships {manifest.get('image', 'a firmware image')}"
            f"{' version ' + shipped if shipped else ''}, commit {manifest.get('commit', '?')}"
        )
        if manifest.get("sha256"):
            say(f"  sha256 {manifest['sha256']}")
    else:
        warn(f"the package ships no firmware manifest ({FIRMWARE_DIR}/MANIFEST.txt)")

    if shipped and running not in UNSTAMPED_FIRMWARE:
        if running == shipped:
            ok("the board runs the firmware this release ships")
            return True
        warn(f"the board runs {running}; this release ships {shipped}")
        return ask("Continue with a firmware that does not match the release?")

    warn("The versions cannot be compared: the board reports an unstamped version, or the")
    warn("package's manifest carries none (releases up to 0.2.0-alpha1), so nothing can tell")
    warn("which build is on the board. A graph using a feature the board lacks fails only")
    warn("when it is uploaded.")
    image = manifest.get("image", "statemachined-uno_r4_minima.bin")
    say(f"  To be sure, flash {image} (a release asset of statemachined, and in {FIRMWARE_DIR}")
    say(f"  where the package is installed):  bossac -i -e -w -R {image}")
    return ask("Was the board flashed with the firmware from this release?", default=True)


# ── docker ────────────────────────────────────────────────────────────────────


def docker_mode(args: argparse.Namespace) -> int:
    print_plan(
        "End-to-end tests on real hardware, through Docker",
        [
            "Check that Docker works, that this is an x86_64 machine, and find the board.",
            "Fetch the pinned release packages into e2e-tests/artifacts/ (needs `gh`).",
            f"Build the `{IMAGE}` image: the packaged daemons and the test venv.",
            "Ask the board for its firmware and pin names, compare the firmware with the "
            "release, and show the wiring.",
            "Ask you to confirm the wiring and that the valve is safe to pulse.",
            "Start a container with the board passed through: statemachined drives the "
            "board, vstimd runs headless, and pytest runs with --hardware.",
        ],
        [
            "nothing installed on this machine: a Docker image, and files in e2e-tests/artifacts/",
            "the board: graphs are uploaded to it and its outputs move while the tests run",
        ],
        [
            "the `manual` tests (vstimd has no display in the container)",
            "the TTL from the board into vstimd (the container has no GPIO input)",
        ],
    )
    if args.dry_run:
        return 0
    if not ask("Continue?"):
        return 1

    heading("Checks")
    if platform.machine() not in ("x86_64", "AMD64"):
        raise Abort(
            f"the pinned packages in the image are amd64; this machine is {platform.machine()}"
        )
    ok("x86_64")
    if not shutil.which("docker"):
        raise Abort("docker is not installed")
    if run(["docker", "info"], check=False, capture=True).returncode:
        raise Abort("docker is installed but not usable by this user (try the docker group)")
    ok("docker works")
    device = pick_device(args.device)
    real_device = os.path.realpath(device)
    in_container = "/dev/statemachined0"

    heading("Packages and image")
    have_artifacts = (HERE / "artifacts" / "requirements.txt").exists()
    if not have_artifacts or ask("Fetch the pinned artifacts again?", default=not have_artifacts):
        if not shutil.which("gh"):
            raise Abort("fetching needs the GitHub CLI `gh`, logged in")
        run([sys.executable, str(HERE / "fetch_artifacts.py")], cwd=HERE)
    run(["docker", "build", "-q", "-f", "container/Dockerfile", "-t", IMAGE, "."], cwd=HERE)
    ok(f"image {IMAGE}")

    passthrough = ["--device", f"{real_device}:{in_container}"]
    heading("The board")
    probe = run(
        [
            "docker",
            "run",
            "--rm",
            *passthrough,
            "--entrypoint",
            "sh",
            IMAGE,
            "-c",
            PROBE.format(device=in_container, manifest=f"{FIRMWARE_DIR}/MANIFEST.txt"),
        ],
        check=False,
        capture=True,
    )
    device, _, rest = probe.stdout.partition("=== pins")
    pins, _, manifest_text = rest.partition("=== manifest")
    board = parse_device(device)
    if not board:
        warn("the daemon could not greet the board:")
        say((device + probe.stderr).strip())
        say(
            "  Is the statemachined firmware flashed, and is it the right port? A firmware "
            "speaking another protocol refuses the hello with bad_proto."
        )
        if not ask("Continue anyway? (the tests will very likely fail)"):
            return 1
    else:
        if not firmware_check(board, parse_manifest(manifest_text)):
            return 1
        heading("The board's own pin names")
        say(pin_names(pins))
        say("\n  Line numbers below are the ones in that list.")
    wiring_table(None, "nothing (no GPIO in this mode)")

    safety_notice()
    jumper = ask("Is the loopback jumper from ready_lamp to lever fitted?", default=True)
    if not jumper:
        warn("without it, the wiring tests and the lever graph test are left out")
    if not ask("Wiring checked, valve safe. Start the tests?"):
        return 1

    heading("Tests")
    environment = ["-e", f"STATEMACHINED_DEVICE_TARGET={in_container}"]
    if args.expected_board:
        environment += ["-e", f"EXPECTED_BOARD_OVERRIDE={args.expected_board}"]
    interactive = ["-it"] if sys.stdin.isatty() else []
    selection = pytest_selection(manual=False, jumper=jumper, gpio_to_renderer=False)
    result = run(
        [
            "docker",
            "run",
            "--rm",
            *interactive,
            *passthrough,
            *environment,
            IMAGE,
            "--hardware",
            *selection,
            *args.pytest_args,
        ],
        check=False,
    )
    heading("Done")
    (ok if result.returncode == 0 else bad)(f"pytest exited with {result.returncode}")
    return result.returncode


# ── pi ────────────────────────────────────────────────────────────────────────

WORK = Path("/var/tmp/braemons-e2e")
STATEMACHINED_CONFIG = Path("/etc/braemons/statemachined-rig-config.toml")
STATEMACHINED_CONFIGS = Path("/var/lib/braemons/statemachined/configs")
LINE_MAP_CONFIG_NAME = "e2e-bench"
DAQD_CONFIG = Path("/etc/braemons/gpiochip-daqd-config.toml")


def pi_model() -> str:
    try:
        return Path("/proc/device-tree/model").read_text().strip("\x00\n")
    except OSError:
        return ""


def gpio_chips() -> list[tuple[str, str]]:
    """(device, label) for every gpiochip, from `gpiodetect` when it is there."""
    if shutil.which("gpiodetect"):
        output = subprocess.run(["gpiodetect"], capture_output=True, text=True).stdout
        chips = []
        for line in output.splitlines():
            name, _, rest = line.partition(" ")
            label = rest.split("]")[0].lstrip("[")
            chips.append((f"/dev/{name}", label))
        return chips
    return [(path, "?") for path in sorted(glob.glob("/dev/gpiochip*"))]


def header_chip(chips: list[tuple[str, str]]) -> str | None:
    for device, label in chips:
        if label in ("pinctrl-rp1", "pinctrl-bcm2711", "pinctrl-bcm2835"):
            return device
    return None


def service_active(unit: str) -> bool:
    return subprocess.run(["systemctl", "is-active", "--quiet", unit]).returncode == 0


def wait_for(what: str, predicate, timeout_s: float) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            ok(what)
            return True
        time.sleep(0.5)
    bad(f"{what}: not after {timeout_s:.0f} s")
    return False


def http_json(url: str):
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            return json.load(response)
    except (OSError, ValueError):
        return None


def port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


class Backup:
    """Every file this run edits, copied first, and put back on request."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.saved: list[tuple[Path, Path | None]] = []

    def keep(self, path: Path) -> None:
        if any(original == path for original, _ in self.saved):
            return
        if path.exists():
            self.directory.mkdir(parents=True, exist_ok=True)
            copy = self.directory / str(path).strip("/").replace("/", "__")
            shutil.copy2(path, copy)
            self.saved.append((path, copy))
            ok(f"backed up {path} -> {copy}")
        else:
            self.saved.append((path, None))

    def restore(self) -> None:
        for original, copy in self.saved:
            if copy is None:
                original.unlink(missing_ok=True)
                ok(f"removed {original} (it did not exist before)")
            else:
                shutil.copy2(copy, original)
                ok(f"restored {original}")


def edit_toml_keys(path: Path, values: dict[str, str]) -> None:
    """Set top-level string keys, keeping every other line as it was."""
    lines = path.read_text().splitlines() if path.exists() else []
    kept = [line for line in lines if line.split("=")[0].strip() not in values]
    # Top-level keys have to come before the first [table].
    first_table = next(
        (i for i, line in enumerate(kept) if line.lstrip().startswith("[")), len(kept)
    )
    added = [f'{key} = "{value}"' for key, value in values.items()]
    path.write_text("\n".join(kept[:first_table] + added + kept[first_table:]) + "\n")


def pi_mode(args: argparse.Namespace) -> int:
    arch_names = asset_names("arm64")
    gate_pin = args.gate_pin
    print_plan(
        "End-to-end tests on a Raspberry Pi test system",
        [
            "Check: root, a 64-bit Raspberry Pi, Python 3.11+, the board on USB, a monitor on "
            "HDMI, a GPIO chip for the header.",
            f"Download the pinned arm64 packages and the triald wheel into {WORK}.",
            "Install them with apt: "
            + ", ".join(v for k, v in arch_names.items() if k != "triald-wheel")
            + " (replacing any installed version; python3-venv and gpiod if missing).",
            f"Back up, then edit {STATEMACHINED_CONFIG} (the board's port and type, and a "
            f"startup line map) and {DAQD_CONFIG} (header pin {gate_pin} as a VTL input).",
            "Write the test line map to "
            f"{STATEMACHINED_CONFIGS}/{LINE_MAP_CONFIG_NAME}.config.json.",
            "Optionally stop the desktop (display-manager): vstimd needs the display to itself.",
            "Restart vstimd, gpiochip-daqd and statemachined, and wait for them to answer.",
            "Compare the board's firmware with the one the package ships, show the wiring with "
            "the pin names the board reports, and ask you to confirm both.",
            f"Create a test venv in {WORK}/venv (vstimd-client, the triald wheel, pytest).",
            "Ask which tests to run, then run them. `manual` tests ask you questions.",
            "Offer to restore the backed-up configuration and restart the services.",
        ],
        [
            "installs or upgrades the braemons-vstimd, braemons-gpiochip-daqd and "
            "braemons-statemachined packages (they stay installed)",
            "edits two files in /etc/braemons (backed up; restored on request) and adds one "
            "state-machine config",
            "restarts the vstimd, gpiochip-daqd and statemachined services; the stimulus "
            "display goes to vstimd",
            "the board: graphs are uploaded to it and its outputs move while the tests run",
        ],
        [],
    )
    heading("5 V and 3.3 V")
    warn("The Uno R4 Minima drives 5 V. Raspberry Pi GPIO pins take 3.3 V and are")
    warn(f"damaged by 5 V. stimulus_gate -> header pin {gate_pin} NEEDS a level shifter")
    warn("(or a divider, e.g. 1 kΩ / 2 kΩ). Share a ground between the board and the Pi.")
    if args.dry_run:
        return 0
    if not ask("Continue?"):
        return 1

    heading("Checks")
    if os.geteuid() != 0:
        raise Abort("run this with sudo: it installs packages and restarts services")
    ok("root")
    if platform.machine() != "aarch64":
        raise Abort(f"the arm64 packages need a 64-bit OS; this is {platform.machine()}")
    ok("aarch64")
    model = pi_model()
    if "Raspberry Pi" in model:
        ok(model)
    elif not ask(f"This does not look like a Raspberry Pi ({model or 'unknown'}). Continue?"):
        return 1
    if gate_pin not in HEADER_TO_BCM:
        raise Abort(
            f"header pin {gate_pin} is not a free GPIO pin. Use one of {sorted(HEADER_TO_BCM)}"
        )
    connected = [
        p
        for p in glob.glob("/sys/class/drm/card*-*/status")
        if Path(p).read_text().strip() == "connected"
    ]
    if connected:
        ok("a display is connected: " + ", ".join(Path(p).parent.name for p in connected))
    else:
        warn("no connected display found; vstimd will not have a stimulus screen")
        if not ask("Continue without a display? (manual tests will fail)"):
            return 1
    device = pick_device(args.device)

    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = Backup(WORK / f"backup-{stamp}")
    stopped_desktop = False
    try:
        heading("Packages")
        downloads = WORK / "artifacts"
        files = {
            "vstimd": download(release_url("vstimd", arch_names["vstimd"]), downloads),
            "gpiochip-daqd": download(
                release_url("vstimd", arch_names["gpiochip-daqd"]), downloads
            ),
            "statemachined": download(
                release_url("statemachined", arch_names["statemachined"]), downloads
            ),
            "triald-wheel": download(release_url("triald", arch_names["triald-wheel"]), downloads),
        }
        installed = run(
            [
                "dpkg-query",
                "-W",
                "-f=${Package} ${Version}\n",
                "braemons-vstimd",
                "braemons-gpiochip-daqd",
                "braemons-statemachined",
            ],
            check=False,
            capture=True,
        ).stdout.strip()
        say(
            "  Installed now:\n"
            + ("\n".join("    " + line for line in installed.splitlines()) or "    none")
        )
        extra = []
        if subprocess.run(
            [sys.executable, "-c", "import ensurepip"], capture_output=True
        ).returncode:
            extra.append("python3-venv")
        if not shutil.which("gpiodetect"):
            extra.append("gpiod")
        debs = [str(files[k]) for k in ("vstimd", "gpiochip-daqd", "statemachined")]
        if not ask(
            "Install " + ", ".join([Path(d).name for d in debs] + extra) + "?",
            default=True,
        ):
            return 1
        run(["apt-get", "update"])
        run(["apt-get", "install", "-y", "--no-install-recommends", *debs, *extra])

        heading("Configuration")
        chip = header_chip(gpio_chips())
        if chip is None:
            say("  GPIO chips: " + ", ".join(f"{d} ({label})" for d, label in gpio_chips()))
            chip = prompt("  Which chip carries the 40-pin header?", "/dev/gpiochip0")
        ok(f"header GPIO chip: {chip}")

        board_device = (
            "/dev/braemons/statemachined0"
            if os.path.exists("/dev/braemons/statemachined0")
            else device
        )
        backup.keep(STATEMACHINED_CONFIG)
        edit_toml_keys(
            STATEMACHINED_CONFIG,
            {
                "device_target": board_device,
                "expected_board": args.expected_board or "uno_r4_minima",
                "startup_state_machine_config": LINE_MAP_CONFIG_NAME,
            },
        )
        ok(f"{STATEMACHINED_CONFIG}: device_target = {board_device}")

        line_map_file = STATEMACHINED_CONFIGS / f"{LINE_MAP_CONFIG_NAME}.config.json"
        backup.keep(line_map_file)
        STATEMACHINED_CONFIGS.mkdir(parents=True, exist_ok=True)
        line_map_file.write_text(
            json.dumps({"name": LINE_MAP_CONFIG_NAME, "line_map": LINE_MAP, "graphs": []}, indent=2)
            + "\n"
        )
        shutil.chown(line_map_file, "statemachined", "statemachined")
        ok(f"{line_map_file}")

        backup.keep(DAQD_CONFIG)
        DAQD_CONFIG.write_text(
            "# Written by contracts/e2e-tests/run_on_hardware.py: the state machine's\n"
            "# stimulus_gate, into a VTL input vstimd publishes as vtl.edge.\n"
            '[vtl]\nshm_name = "/vstimd_vtl"\n\n'
            f'[gpio]\nchip = "{chip}"\n\n'
            "[[inputs]]\n"
            'name      = "stimulus_gate"\n'
            "vtl_bank  = 0\n"
            f"vtl_bit   = {gate_pin}\n"
            f"gpio_line = {HEADER_TO_BCM[gate_pin]}\n"
            'edge      = "both"\n'
        )
        ok(
            f"{DAQD_CONFIG}: header pin {gate_pin} (GPIO{HEADER_TO_BCM[gate_pin]})"
            f" -> VTL input 0:{gate_pin}"
        )

        heading("Services")
        if service_active("display-manager") and ask(
            "A desktop is running and holds the display. "
            "Stop it for the tests (restarted at the end)?",
            default=True,
        ):
            run(["systemctl", "stop", "display-manager"])
            stopped_desktop = True
        run(["systemctl", "restart", "vstimd.service"])
        run(["systemctl", "restart", "statemachined.service"])
        wait_for("vstimd answers on 5555", lambda: port_open(5555), 30)
        # Restart, not start: a running daemon would keep its old inputs.
        run(["systemctl", "restart", "gpiochip-daqd.service"], check=False)
        wait_for("gpiochip-daqd is running", lambda: service_active("gpiochip-daqd"), 15)
        if not wait_for(
            "statemachined is healthy", lambda: http_json("http://127.0.0.1:8081/api/health"), 30
        ):
            run(["journalctl", "-u", "statemachined", "-n", "30", "--no-pager"], check=False)
            raise Abort("statemachined did not come up")
        if not wait_for(
            "statemachined is connected to the board",
            lambda: (http_json("http://127.0.0.1:8081/api/device") or {}).get("connected"),
            30,
        ):
            run(["journalctl", "-u", "statemachined", "-n", "30", "--no-pager"], check=False)
            link = (http_json("http://127.0.0.1:8081/api/device") or {}).get("link") or {}
            if link.get("last_error"):
                bad(f"the daemon says: {link['last_error']}")
            raise Abort(
                "no board on the link. Is the statemachined firmware flashed "
                "(statemachined-uno_r4_minima.bin from the release)? Is it the right port? "
                "A firmware speaking another protocol refuses the hello with bad_proto."
            )
        described = http_json("http://127.0.0.1:8081/api/device") or {}
        manifest = Path(FIRMWARE_DIR) / "MANIFEST.txt"
        if not firmware_check(
            described, parse_manifest(manifest.read_text()) if manifest.exists() else {}
        ):
            return 1

        lines = http_json("http://127.0.0.1:8081/api/device/lines") or {}
        labels = {
            line["name"]: line.get("pin_label") or ""
            for kind in ("input_lines", "output_lines")
            for line in lines.get(kind, [])
        }
        wiring_table(
            labels, f"Pi header pin {gate_pin} (GPIO{HEADER_TO_BCM[gate_pin]}) via a level shifter"
        )
        safety_notice()
        jumper = ask("Is the loopback jumper from ready_lamp to lever fitted?", default=True)
        gate = ask(
            f"Is stimulus_gate wired (level-shifted) to header pin {gate_pin}?", default=True
        )
        if not ask("Wiring checked, valve safe. Continue to the tests?"):
            return 1

        heading("Test environment")
        venv = WORK / "venv"
        if not (venv / "bin" / "python").exists():
            run([sys.executable, "-m", "venv", str(venv)])
        vstimd_client = f"{PINS['vstimd']['pypi']}=={PINS['vstimd']['pypi_version']}"
        run(
            [
                str(venv / "bin" / "pip"),
                "install",
                "--quiet",
                vstimd_client,
                str(files["triald-wheel"]),
                "pytest>=8.3",
                "httpx>=0.28",
                "websockets>=13",
            ]
        )

        which = choose(
            "Which tests?",
            [
                ("a", "everything, including the questions for a person (manual)"),
                ("n", "everything a machine can check (no questions)"),
                ("w", "only the wiring and manual tests"),
            ],
            default="a",
        )
        selection = pytest_selection(
            manual=which != "n", jumper=jumper, gpio_to_renderer=gate, only_hardware=which == "w"
        )
        report = WORK / f"report-{stamp}.xml"

        heading("Tests")
        result = run(
            [
                str(venv / "bin" / "python"),
                "-m",
                "pytest",
                "tests",
                "-v",
                "-p",
                "no:cacheprovider",
                "--hardware",
                "--display",
                "tcp://127.0.0.1:5555",
                "--event-port",
                "5556",
                "--executor",
                "http://127.0.0.1:8081",
                f"--junitxml={report}",
                *selection,
                *args.pytest_args,
            ],
            check=False,
            cwd=HERE,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        heading("Done")
        (ok if result.returncode == 0 else bad)(f"pytest exited with {result.returncode}")
        ok(f"report: {report}")
        return result.returncode
    finally:
        if backup.saved:
            heading("Cleaning up")
            if ask("Restore the configuration this run changed?", default=True):
                backup.restore()
                run(["systemctl", "restart", "statemachined.service"], check=False)
                run(["systemctl", "restart", "vstimd.service"], check=False)
                run(["systemctl", "restart", "gpiochip-daqd.service"], check=False)
            else:
                say(f"  Left as configured. The backups are in {backup.directory}.")
        if stopped_desktop:
            run(["systemctl", "start", "display-manager"], check=False)
        say(
            "\n  The packages stay installed. To remove them:\n"
            "    sudo apt-get remove braemons-vstimd braemons-gpiochip-daqd braemons-statemachined"
        )


# ── main ──────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="mode", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--device", help="the board's serial device (asked for when omitted)")
        p.add_argument(
            "--expected-board",
            help="the board type statemachined should insist on (default: uno_r4_minima)",
        )
        p.add_argument("--dry-run", action="store_true", help="print the plan and stop")
        p.add_argument("pytest_args", nargs="*", help="passed to pytest after `--`")

    common(sub.add_parser("docker", help="a workstation with the board on USB; installs nothing"))
    pi = sub.add_parser("pi", help="a Raspberry Pi test system; run with sudo")
    common(pi)
    pi.add_argument(
        "--gate-pin",
        type=int,
        default=29,
        help="Pi header pin stimulus_gate is wired to (default: 29, GPIO5)",
    )
    args = parser.parse_args(argv)

    try:
        return docker_mode(args) if args.mode == "docker" else pi_mode(args)
    except Abort as reason:
        bad(str(reason))
        return 2
    except KeyboardInterrupt:
        say("\ninterrupted")
        return 130


if __name__ == "__main__":
    sys.exit(main())
