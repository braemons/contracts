"""One trial, across all three daemons, with nothing faked in the middle.

**What only this can be wrong about.** Each daemon's own suite proves that
daemon. statemachined's stage-2 test proves the handover between it and triald.
What is left, and lives nowhere else, is whether *three* fit: whether the frame
axis vstimd publishes is the same axis triald bounds a trial with, and whether a
trial that a state machine ran can be joined to what a renderer saw while it ran.

**The direction is the point, and it is visible here.** vstimd is commanded and
publishes; statemachined is commanded and publishes; neither has a client, a
base URL, a schema or an outbound call naming anybody. triald knows both. Delete
triald from this test and the other two carry on rendering and running graphs,
which is exactly what they do on a rig with no session in progress.

**What this cannot cover, and why.** Frame *loss*. The renderer runs headless
here, and a null renderer misses no vblanks — there is no GPU to be late. Loss
is what `triald.stimulus` exists to judge, and judging it needs a display, so it
belongs to a suite with a monitor attached. What is covered is the join that the
judgement rests on: that the window has real frame numbers in it, from a real
renderer, spanning the trial that really ran.
"""

from __future__ import annotations

import json
import time

import pytest


def timed_graph(name: str, milliseconds: int = 60) -> dict:
    """A graph that waits and then declares HIT, with nothing to press.

    Nothing here drives an input line — that needs a wire — so the graph reaches
    its outcome on a timeout. Everything above the trigger still runs: the
    compiler, the framing, the device's scan loop, the trace.
    """
    return {
        "name": name,
        "entry": "Wait",
        "distributions": {"dwell": {"kind": "fixed", "duration_ms": milliseconds}},
        "states": [
            {
                "name": "Wait",
                "on_entry": [{"line": "ready_lamp", "kind": "high"}],
                "timeout": {"after": "dwell", "goto": "Hit"},
            },
            {"name": "Hit", "outcome": "HIT"},
        ],
    }


@pytest.fixture
def armed_executor(executor):
    """A statemachined with one graph uploaded to the device, ready to run.

    Two steps, and they are different things: `PUT /api/graphs/<name>` stores a
    graph, `POST /api/session/graphs` compiles the named set and sends it to the
    device. A trial can only name a graph the device is holding.
    """
    assert executor.put("/api/graphs/show", json=timed_graph("show")).status_code in (
        200,
        201,
    )
    response = executor.post("/api/session/graphs", json={"graph_names": ["show"]})
    assert response.status_code == 200, response.text
    return executor


# ── The rig comes up ──────────────────────────────────────────────────────────


def test_all_three_are_up_and_none_of_them_knows_the_others(display, armed_executor):
    """The shape of the family, asserted rather than assumed.

    A renderer answering commands and a state machine answering commands, with
    no configuration on either naming anything else. If this ever needs a
    setting pointing one daemon at another, the architecture changed.
    """
    from vstimd import Connection

    with Connection(display["address"], recv_timeout_s=10.0) as renderer:
        # Every response carries the current frame count; `wait_for_frames(0)`
        # is the cheapest way to ask for one without changing anything.
        assert renderer.system.wait_for_frames(0).frame_count > 0, (
            "the renderer's frame clock is not running"
        )

    state = armed_executor.get("/api/state")
    assert state.status_code == 200
    assert "triald" not in json.dumps(state.json()).lower(), (
        "the state machine daemon named the decision authority in its own state"
    )
    config = armed_executor.get("/api/config")
    assert config.status_code == 200
    settings = json.dumps(config.json()).lower()
    for stranger in ("triald", "vstimd", "5556"):
        assert stranger not in settings, (
            f"the state machine daemon's configuration names {stranger!r}; it is "
            f"supposed to know about nobody"
        )


# ── The frame axis is shared ──────────────────────────────────────────────────


def test_the_frame_a_command_lands_on_is_the_frame_the_event_reports(display):
    """**The join key, pinned.**

    Two counters exist and a consumer will inevitably use both: the frame on
    `Response` (the renderer's answer to the command that opened a window) and
    the frame on the event stream (what everything inside the window is stamped
    with). If they ever drift apart, every trial window is wrong by that drift
    and nothing says so — the numbers stay small and plausible.

    So this asserts the relationship rather than trusting it, and will fail the
    day it changes.
    """
    from vstimd import Connection
    from vstimd.events import EventSubscriber, Topic
    from vstimd.stimuli import RectParams

    with EventSubscriber("127.0.0.1", display["event_port"], topic=Topic.COMMAND_APPLIED) as events:
        time.sleep(0.5)  # PUB discards anything sent before a subscription lands
        with Connection(display["address"], recv_timeout_s=10.0) as renderer:
            handle = renderer.stimuli.shapes.create_rect(
                params=RectParams(width_px=40, height_px=40)
            )
            reported = renderer.system.wait_for_frames(0).frame_count

        event = events.receive(timeout_ms=3000)
        assert event is not None, "the command produced no record"
        assert event.payload.response_handle == handle

    # The event's frame is the first frame the command can appear on; the
    # response's count is frames completed. They advance together, and a query
    # sent after the command cannot report a smaller number than the frame the
    # command landed on, minus the one-frame offset between the two.
    assert reported + 1 >= event.frame, (
        f"the two frame counters have drifted: a command landed on frame "
        f"{event.frame} but the renderer reports {reported} frames done"
    )
    assert event.frame > 0


# ── A trial, joined to what the renderer saw ──────────────────────────────────


def test_a_trial_runs_on_one_daemon_and_is_bounded_by_frames_from_another(display, armed_executor):
    """The whole loop: triald opens a window on the renderer's clock, runs a
    trial on the state machine, and closes the window when the trial ends.

    The trial's outcome comes from one process. The frames bounding it come from
    another. Neither knows the other exists, and the join is made here — by the
    only side that knows what a trial is.
    """
    from triald.api.statemachine_executor import StateMachineExecutor
    from triald.api.stimulus_subscriber import StimulusObserver, connect
    from triald.executor import TrialConfiguration
    from vstimd import Connection
    from vstimd.stimuli import RectParams

    observer = StimulusObserver(connect("127.0.0.1", display["event_port"]))
    observer.start()
    executor = StateMachineExecutor(base_url="http://rig.test", client=armed_executor)

    try:
        with Connection(display["address"], recv_timeout_s=10.0) as renderer:
            # The stimulus goes up, and the frame it went up on opens the window.
            stimulus = renderer.stimuli.shapes.create_rect(
                params=RectParams(width_px=60, height_px=60)
            )
            first_frame = renderer.system.wait_for_frames(0).frame_count
            observer.open_window(first_frame=first_frame)

            executor.configure(
                TrialConfiguration(trial_id=1, statemachine_graph="show", cap_milliseconds=5000)
            )
            executor.start(1)

            # Observed, not waited on: the executor published and moved on, and
            # this side is the one holding a deadline. Same shape as stage 2.
            with armed_executor.websocket_connect("/api/trace/stream?observer=triald") as stream:
                finished = next(executor.finished_trials(iter(lambda: stream.receive_text(), None)))
            assert finished == 1
            outcome = executor.outcome_of(trial_id=1)

            renderer.stimuli.delete(stimulus)
            last_frame = renderer.system.wait_for_frames(0).frame_count

        window = observer.close_window(last_frame=last_frame)
    finally:
        observer.close()

    assert outcome is not None
    assert outcome.outcome.name == "HIT", outcome

    assert window is not None
    assert window.first_frame == first_frame
    assert window.last_frame == last_frame
    assert window.last_frame > window.first_frame, (
        "the trial occupied no frames — either the renderer's clock stopped or "
        "the trial did not actually run"
    )
    # Headless: no GPU, so no missed vblanks, so a clean window. That this is
    # clean is not evidence the judgement works — see the module docstring.
    assert not window.uncertain, (
        "the subscription lost events during the trial, so the window is not "
        "trustworthy even though nothing was reported lost"
    )
    assert window.as_report() is None


def test_the_observer_survives_a_trial_it_was_not_watching(display, armed_executor):
    """A rig spends most of its time with no session running.

    vstimd is always on — for alignment, display checks, luminance — and triald
    must be able to attach to one that has been publishing for hours, and to run
    a trial with no window open without either side minding.
    """
    from triald.api.statemachine_executor import StateMachineExecutor
    from triald.api.stimulus_subscriber import StimulusObserver, connect
    from triald.executor import TrialConfiguration

    observer = StimulusObserver(connect("127.0.0.1", display["event_port"]))
    observer.start()
    executor = StateMachineExecutor(base_url="http://rig.test", client=armed_executor)
    try:
        executor.configure(
            TrialConfiguration(trial_id=7, statemachine_graph="show", cap_milliseconds=5000)
        )
        executor.start(7)
        with armed_executor.websocket_connect("/api/trace/stream?observer=triald") as stream:
            assert next(executor.finished_trials(iter(lambda: stream.receive_text(), None))) == 7
        # No window was ever opened, and nothing anywhere minded.
        assert observer.close_window(last_frame=1) is None
    finally:
        observer.close()
