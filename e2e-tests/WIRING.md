# Wiring, and running the tests on a real rig

What the hardware tests assume is physically connected, and how to run the suite
on that rig. The line names are the ones in `tests/line_map.json`; this file and that map
must agree.

## The rig

```
                    ┌──────────────────┐
                    │  statemachined   │   MCU (Uno R4 Minima)
                    │   on --target    │
                    └──┬────────────┬──┘
        ready_lamp  out│0          1│out  stimulus_gate
                       │            │
              ┌────────┘            └──────────────┐
              │  (loopback jumper)                 │  (TTL to the renderer)
              │                                    ▼
        lever  in│4                        ┌──────────────────┐
              └──────────────────────►     │      vstimd      │
                                           │  VTL input (GPIO)│
     reward_valve out│3 ──► solenoid       └──────────────────┘
                                                   │
     start_switch  in│0 ──► push button            ▼
                                            stimulus display
                                          (+ photodiode patch)
```

## Connections

| from | to | what it proves |
|---|---|---|
| `ready_lamp` (out 0) | `lever` (in 4) | the trigger path: pin → wire → edge → transition, with no host in the loop |
| `stimulus_gate` (out 1) | a GPIO input on the vstimd machine, via a level shifter (on a Raspberry Pi: header pin 29 by default) | a TTL crossing between two daemons that know nothing of each other; `gpiochip-daqd` turns it into a vstimd VTL input |
| `reward_valve` (out 3) | the solenoid | that a reported reward is a delivered one |
| `start_switch` (in 0) | a push button | hand-driven trials |

**The loopback jumper.** `ready_lamp` → `lever` is a test wire, not part of an
experiment. It lets a graph trigger itself: entering `Wait` raises the lamp, the
wire raises the lever, and the device takes the `HIT` branch. Without the jumper
the same graph times out into `LATE`.

**The gate to the renderer.** The Uno R4 Minima drives 5 V and a Raspberry Pi
GPIO takes 3.3 V: **use a level shifter** (or a 1 kΩ / 2 kΩ divider), and
**share a ground**. A floating TTL input produces edges nobody sent,
which looks exactly like a subject responding.

## Running on real hardware

Two interactive runners. Both print what they will do and what they change, ask
before starting, check prerequisites, show the wiring with the pin names the
board reports, and ask again before any pin moves.

### A workstation with the board on USB, through Docker

```sh
cd e2e-tests
make accept-docker                    # or: ./run_on_hardware.py docker [--device /dev/ttyACM0]
```

Nothing is installed on the machine. The pinned packages are built into the same
image `make test` uses, the board is passed into the container, and the suite
runs with `--hardware`. Needs Docker, an x86_64 machine, `gh` logged in, and the
statemachined firmware on the board.

vstimd runs headless in the container, so the `manual` tests and the TTL into
vstimd are left out. Without the loopback jumper, the tests that need it are left
out too.

### A Raspberry Pi test system with the board attached

```sh
cd e2e-tests
make accept-pi                        # or: sudo ./run_on_hardware.py pi [--gate-pin 29]
./run_on_hardware.py pi --dry-run     # only print the plan
```

For a 64-bit Raspberry Pi OS with a monitor on HDMI, the board on USB and the
wiring above. The runner:

1. downloads the pinned arm64 packages (`braemons-vstimd`, `braemons-gpiochip-daqd`,
   `braemons-statemachined`) and the triald wheel into `/var/tmp/braemons-e2e`, and
   installs the packages with apt;
2. backs up and edits `/etc/braemons/statemachined-rig-config.toml` (the board and
   the test line map) and `/etc/braemons/gpiochip-daqd-config.toml` (the gate pin as
   a VTL input);
3. optionally stops the desktop, restarts the services and waits for the board;
4. creates a test venv, asks which tests to run (everything, no questions, or only
   wiring and manual) and runs them. `manual` tests ask you what you saw or heard;
5. offers to restore the configuration. The packages stay installed.

### What runs

| tests | on hardware |
|---|---|
| `test_a_trial_across_the_rig.py` | trials through triald, against the real board |
| `test_an_experiment_run_by_one_script.py` | the scripted detection task; one line per trial is printed, and on the Pi the targets appear on the display |
| `test_the_handover_to_triald.py` | starts its own statemachined and native device; never touches the board |
| `test_acceptance_with_hardware.py`, `-m wiring` | line map vs. wiring, loopback trigger, TTL into vstimd inside the trial's frame window |
| `test_acceptance_with_hardware.py`, `-m manual` | was the stimulus visible, did the valve click (Pi only) |

### A rig that is already set up

With the daemons already running and configured, `make accept` runs the suite
against them directly (`DISPLAY_ADDR=`, `EXECUTOR=`, `ARGS=` to adjust).
