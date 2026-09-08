# Wiring

What the acceptance tests assume is physically connected. **These tests and this
document have to agree**, or they are testing a rig nobody owns — a `lever` on
line 4 in the config and a button soldered to line 5 gives a test that fails
while blaming the trigger path.

The line names below are the ones in `tests/conftest.py`'s `LINE_MAP`. That map
is the wiring, written down; this file is the wiring, drawn.

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
                                           │  VTL input 0:2   │
     reward_valve out│3 ──► solenoid       └──────────────────┘
                                                   │
     start_switch  in│0 ──► push button            ▼
                                            stimulus display
                                          (+ photodiode patch)
```

## Connections

| from | to | what it proves |
|---|---|---|
| `ready_lamp` (out 0) | `lever` (in 4) | the trigger path: pin → wire → edge → transition, with nothing on the host in the loop |
| `stimulus_gate` (out 1) | vstimd VTL input bank 0 bit 2 | the waist of the hourglass: a TTL crossing between two daemons that know nothing of each other |
| `reward_valve` (out 3) | the solenoid | that a reported reward is a delivered one |
| `start_switch` (in 0) | a push button | hand-driven trials, for the tests a person runs |

### The loopback jumper

`ready_lamp` → `lever` is a jumper wire, not part of a real experiment. It exists
so a graph can trigger *itself* through the device's own input path: entering
`Wait` raises the lamp, the wire carries it to the lever pin, the device sees a
rising edge and takes the HIT branch.

That is the only way to exercise the trigger path without a subject, and it is
the coverage gap the whole family has: **the device sends commands and never
drives its own inputs**, so off a bench every graph waiting for a lever reaches
its outcome on a timeout.

Pull the jumper and the same tests fail into MISS, which is exactly what they do
in CI. That is not a defect in the tests; it is what "needs hardware" means.

### The gate to the renderer

`stimulus_gate` is the one that matters most, and the one nothing else in the
family tests at all.

vstimd's virtual trigger lines exist so a stimulus can be armed by an edge
without a host round trip — the renderer's animations chain inside the server,
the microcontroller names the outcome, and the two agree through TTL edges with
no daemon in between. Every part of that is tested in isolation. The wire is not.

Level-shift if the two boards do not share a logic level, and **share a ground**.
A TTL that arrives as a floating input produces edges nobody sent, which looks
exactly like a subject responding.

## Running

```sh
make accept                          # everything: the CI suite too, on this rig
make accept ARGS="-m wiring"         # only the electrical claims
make accept ARGS="-m 'not manual'"   # nobody has to watch
make accept ARGS="--target /dev/ttyUSB0 --display tcp://rig-box:5555"
```

`vstimd` is expected to be **already running** and owning the stimulus display —
on a rig it is a service. Nothing in this suite starts or stops it: a test that
could restart the renderer is a test that can leave a monitor black in front of
an animal, and acceptance happens on the animal's rig, not a spare.

The `manual` tests print a question and wait for `y`. They are the only
assertions here a machine cannot make: whether the stimulus was *visible*,
whether the valve audibly clicked. Everything a machine can check can be green
while the screen is black.
