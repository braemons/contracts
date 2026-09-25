"""The acceptance run: a real rig, real wires, and a person watching.

    make accept                 # everything, on the rig
    make accept ARGS="-m wiring"

**Every test CI runs also runs here**, against silicon — that is what
`--hardware` does, and it is why there is no copy of the trial loop in this file.
What is *only* here is the handful of things no amount of CI can reach, and they
divide cleanly into two:

* **`wiring`** — a claim about electricity. Something has to leave one daemon's
  pin, travel down a wire, and arrive at another's. Nothing on a single machine
  can be wrong about this, because there is no wire to be wrong.
* **`manual`** — a claim only a person can settle. Whether the stimulus was
  *visible*; whether the valve audibly clicked. A rig that reports a stimulus it
  never drew is precisely the failure an acceptance test exists to catch, and it
  reports success at every layer a machine can inspect.

`WIRING.md` says which pin is which. These tests and that document have to agree
or they are testing a rig nobody owns.

**The gap this closes.** The device sends commands and never drives its own
inputs, so off a bench every graph waiting for a lever reaches its outcome on a
timeout. The whole trigger path — pin, wire, edge detection, transition — is
exercised nowhere else in the family. Neither is the hourglass's waist: a TTL
leaving the state machine and arriving at the renderer's virtual trigger line
without a host round trip, which is the single assumption the architecture rests
on.
"""

from __future__ import annotations

import time

import pytest
import scenarios
from scenarios import graph_waiting_for_a_lever, line_levels, timed_graph, upload

pytestmark = pytest.mark.usefixtures("on_hardware")


@pytest.fixture
def observer(display):
    from triald.api.stimulus_subscriber import StimulusObserver, connect

    watcher = StimulusObserver(connect("127.0.0.1", display["event_port"]))
    watcher.start()
    yield watcher
    watcher.close()


@pytest.fixture
def renderer(display):
    from vstimd_client import VstimdClient

    with VstimdClient(display["address"], recv_timeout_s=10.0) as connection:
        yield connection


@pytest.fixture
def trial_executor(executor):
    from triald.api.statemachine_executor import StateMachineExecutor

    return StateMachineExecutor(executor.address)


# ── Electricity ───────────────────────────────────────────────────────────────


@pytest.mark.wiring
def test_the_line_map_matches_the_wire(executor):
    """Before anything else: does the map name the pins somebody wired?

    First because every other failure in this file is unreadable if it does not.
    A `lever` on line 4 in the config and a button soldered to line 5 produces a
    test that fails while blaming the trigger path.
    """
    upload(executor, timed_graph("idle", milliseconds=40))
    levels = line_levels(executor)
    for name in ("start_switch", "lever", "ready_lamp", "reward_valve", "stimulus_gate"):
        assert name in levels, (
            f"the device does not know a line called {name!r}; the config and "
            f"WIRING.md disagree about what this rig is"
        )


@pytest.mark.wiring
def test_an_output_reaches_an_input_through_a_wire(executor, trial_executor, renderer, observer):
    """**The trigger path, end to end, through actual copper.**

    Needs `ready_lamp` jumpered to `lever` (WIRING.md, "loopback"). Entering
    `Wait` raises the lamp; if the wire is there the device sees a rising edge on
    the lever and takes the HIT branch. Without the wire the same graph times out
    into LATE — which is what it does on every machine in CI, and why this cannot
    be tested there.

    The graph triggers itself, which is the point: nothing on the host is in the
    loop. That is the property the rig is built around.
    """
    upload(executor, graph_waiting_for_a_lever("loopback", timeout_ms=2000))
    ran = scenarios.run_one_trial(
        display_connection=renderer,
        executor_client=executor,
        executor=trial_executor,
        observer=observer,
        graph="loopback",
        trial_id=101,
    )
    assert ran.outcome.outcome.name == "HIT", (
        f"the lever never went high, so the graph timed out into "
        f"{ran.outcome.outcome.name}. Either ready_lamp is not jumpered to lever, "
        f"or the edge is not reaching the device — see WIRING.md, 'loopback'"
    )


@pytest.mark.wiring
def test_the_state_machines_ttl_reaches_the_renderer(
    display, executor, trial_executor, renderer, observer
):
    """**The waist of the hourglass**, and the one claim nothing else tests.

    `stimulus_gate` runs from the state machine's output pin to a virtual trigger
    line input on the renderer (WIRING.md). When a graph raises it, vstimd must
    see an input edge — without a host round trip, without either daemon knowing
    the other exists, and on a frame it can name.

    That last part is why this is checkable at all: the edge arrives as a
    `vtl.edge` event stamped with the frame it was drained at, so it can be
    placed inside the trial's own frame window rather than merely "seen at some
    point".
    """
    from vstimd_client.events import EventSubscriber, Topic

    upload(executor, graph_waiting_for_a_lever("gate", timeout_ms=1500))

    # A *second* subscriber, alongside the observer's, watching a topic the
    # observer does not take: triald subscribes only to frame loss and restarts,
    # deliberately, and this test wants the edges.
    with EventSubscriber("127.0.0.1", display["event_port"], topic=Topic.VTL_EDGE) as edges:
        time.sleep(0.5)  # PUB drops whatever it sends before a subscription lands
        ran = scenarios.run_one_trial(
            display_connection=renderer,
            executor_client=executor,
            executor=trial_executor,
            observer=observer,
            graph="gate",
            trial_id=102,
        )
        seen = []
        while (event := edges.receive(timeout_ms=1000)) is not None:
            seen.append(event)

    inputs = [e for e in seen if e.payload.kind == 1]  # VirtualTriggerLineKind INPUT
    assert inputs, (
        "the renderer saw no virtual trigger line input at all while the state "
        "machine raised stimulus_gate. The wire, the pin, or the VTL bank is "
        "wrong — see WIRING.md"
    )
    inside = [e for e in inputs if ran.first_frame <= e.frame <= ran.last_frame]
    assert inside, (
        f"the renderer saw input edges at frames {[e.frame for e in inputs]}, none "
        f"inside this trial's window {ran.first_frame}..{ran.last_frame}. The "
        f"electrical path works and the two clocks do not agree"
    )


# ── A person ──────────────────────────────────────────────────────────────────


@pytest.mark.manual
def test_the_stimulus_was_actually_visible(executor, trial_executor, renderer, observer, confirm):
    """Everything a machine can check can be green while the screen is black.

    vstimd reports a frame presented; the GPU reports no drops; the trial record
    is perfect. None of that is a photon. This is the only test in the repository
    that asks about one.
    """
    from vstimd_client.stimuli import RectParams

    upload(executor, timed_graph("visible", milliseconds=1500))
    handle = renderer.stimuli.shapes.create_rect(params=RectParams(width_px=400, height_px=400))
    try:
        scenarios.run_one_trial(
            display_connection=renderer,
            executor_client=executor,
            executor=trial_executor,
            observer=observer,
            graph="visible",
            trial_id=103,
        )
        confirm("Was a large light rectangle visible on the stimulus display?")
    finally:
        renderer.stimuli.delete(handle)


@pytest.mark.manual
def test_the_reward_valve_operated(executor, trial_executor, renderer, observer, confirm):
    """A valve that does not open is a subject that is never rewarded.

    The daemon can say it pulsed the line and be entirely right while nothing
    moves: a blown driver, an unplugged solenoid, a valve wired to the wrong
    output. Needs the loopback jumper, so the HIT branch — and its pulse — is
    reached at all.
    """
    upload(executor, graph_waiting_for_a_lever("reward", timeout_ms=2000))
    ran = scenarios.run_one_trial(
        display_connection=renderer,
        executor_client=executor,
        executor=trial_executor,
        observer=observer,
        graph="reward",
        trial_id=104,
    )
    if ran.outcome.outcome.name != "HIT":
        pytest.skip("the trial did not reach HIT, so no reward was due")
    confirm("Did the reward valve audibly click?")
