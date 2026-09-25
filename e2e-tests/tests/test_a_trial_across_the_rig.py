"""One trial, across all three daemons, with nothing faked in the middle.

**What only this can be wrong about.** Each daemon's own suite proves that
daemon, and `test_the_handover_to_triald.py` beside this one proves the handover
between statemachined and triald. What is left, and lives nowhere else, is
whether *three* fit: whether the frame axis vstimd publishes is the same axis
triald bounds a trial with, and whether a trial that a state machine ran can be
joined to what a renderer saw while it ran.

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

import contextlib
import time

import pytest
import scenarios
from conftest import wait_until
from statemachined_client import DaemonRefusedTheRequest


@pytest.fixture
def armed_executor(executor):
    """A statemachined with one graph uploaded to the device, ready to run.

    The graph comes from `scenarios`, which the acceptance suite also uses. This
    file used to carry a second copy of it -- and a second copy of the trial loop
    -- which is the drift scenarios.py exists to prevent, pointing the wrong way.
    """
    scenarios.upload(executor, scenarios.timed_graph("show"))
    return executor


# ── The rig comes up ──────────────────────────────────────────────────────────


def test_all_three_are_up_and_none_of_them_knows_the_others(display, armed_executor):
    """The shape of the family, asserted rather than assumed.

    A renderer answering commands and a state machine answering commands, with
    no configuration on either naming anything else. If this ever needs a
    setting pointing one daemon at another, the architecture changed.
    """
    from vstimd_client_class import VstimdClient

    with VstimdClient(display["address"], recv_timeout_s=10.0) as renderer:
        # Every response carries the current frame count; `wait_for_frames(0)`
        # is the cheapest way to ask for one without changing anything.
        assert renderer.system.wait_for_frames(0).frame_count > 0, (
            "the renderer's frame clock is not running"
        )

    state = armed_executor.client.read_state()
    assert "triald" not in str(state).lower(), (
        "the state machine daemon named the decision authority in its own state"
    )
    # The **rig** config — this box's hardware. Not a state-machine config,
    # which is one experiment's graphs and wiring (contracts/DAEMON_LAYOUT.md).
    settings = str(armed_executor.client.read_configuration()).lower()
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
    from vstimd_client_class import VstimdClient
    from vstimd.events import EventSubscriber, Topic
    from vstimd.stimuli import RectParams

    with EventSubscriber("127.0.0.1", display["event_port"], topic=Topic.COMMAND_APPLIED) as events:
        time.sleep(0.5)  # PUB discards anything sent before a subscription lands
        with VstimdClient(display["address"], recv_timeout_s=10.0) as renderer:
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
    from vstimd_client_class import VstimdClient
    from vstimd.stimuli import RectParams

    observer = StimulusObserver(connect("127.0.0.1", display["event_port"]))
    observer.start()
    # The real client: a real gRPC channel to a real server, as it ships.
    executor = StateMachineExecutor(armed_executor.address)

    try:
        with VstimdClient(display["address"], recv_timeout_s=10.0) as renderer:
            # The stimulus goes up, and the frame it went up on opens the window.
            stimulus = renderer.stimuli.shapes.create_rect(
                params=RectParams(width_px=60, height_px=60)
            )
            first_frame = renderer.system.wait_for_frames(0).frame_count
            observer.open_window(first_frame=first_frame)

            # Before arming: the subscription is opened after, and a short
            # trial ends before a late subscriber is watching.
            before_arming = armed_executor.mark()
            executor.configure(
                TrialConfiguration(trial_id=1, statemachine_graph="show", cap_milliseconds=5000)
            )
            executor.start(1)

            # Observed, not waited on: the executor published and moved on, and
            # this side is the one holding a deadline. Same shape as stage 2.
            with armed_executor.trace_stream(since=before_arming) as messages:
                finished = next(executor.finished_trials(messages))
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
    # The real client: a real gRPC channel to a real server, as it ships.
    executor = StateMachineExecutor(armed_executor.address)
    try:
        before_arming = armed_executor.mark()
        executor.configure(
            TrialConfiguration(trial_id=7, statemachine_graph="show", cap_milliseconds=5000)
        )
        executor.start(7)
        with armed_executor.trace_stream(since=before_arming) as messages:
            assert next(executor.finished_trials(messages)) == 7
        # No window was ever opened, and nothing anywhere minded.
        assert observer.close_window(last_frame=1) is None
    finally:
        observer.close()


# ── The three things one happy trial could not reach ──────────────────────────
#
# The tests above run one trial, it succeeds, and the display drops nothing.
# That leaves three gaps which nothing else in the family covers either, because
# each of them needs all three daemons to be real at once.


def test_two_trials_keep_their_own_frames_and_their_own_outcomes(display, armed_executor):
    """A session is trials, plural, and the second must not inherit the first.

    One trial cannot show that a window closes cleanly enough for the next one
    to open — the observer's own suite tests that against a fake source, and
    statemachined's tests that trial numbers do not bleed, but neither has a
    renderer's clock in it. What is unproven until here is that two consecutive
    windows come back with *different, advancing* frames from a real display and
    each with its own outcome.
    """
    from triald.api.statemachine_executor import StateMachineExecutor
    from triald.api.stimulus_subscriber import StimulusObserver, connect
    from triald.executor import TrialConfiguration
    from vstimd_client_class import VstimdClient

    observer = StimulusObserver(connect("127.0.0.1", display["event_port"]))
    observer.start()
    executor = StateMachineExecutor(armed_executor.address)

    windows = []
    # Unique, not 1 and 2: the executor may be a daemon that has been up for
    # weeks, and a trial id is its key. See scenarios.unique_trial_id.
    trial_ids = [scenarios.unique_trial_id(), scenarios.unique_trial_id()]
    try:
        with VstimdClient(display["address"], recv_timeout_s=10.0) as renderer:
            for trial_id in trial_ids:
                first_frame = renderer.system.wait_for_frames(0).frame_count
                observer.open_window(first_frame=first_frame)

                before_arming = armed_executor.mark()
                executor.configure(
                    TrialConfiguration(
                        trial_id=trial_id,
                        statemachine_graph="show",
                        cap_milliseconds=5000,
                    )
                )
                executor.start(trial_id)
                with armed_executor.trace_stream(since=before_arming) as messages:
                    assert next(executor.finished_trials(messages)) == trial_id

                outcome = executor.outcome_of(trial_id=trial_id)
                assert outcome is not None, f"trial {trial_id} reported nothing"
                assert outcome.outcome.name == "HIT", outcome

                last_frame = renderer.system.wait_for_frames(0).frame_count
                window = observer.close_window(last_frame=last_frame)
                assert window is not None
                windows.append(window)
    finally:
        observer.close()

    first, second = windows
    # Disjoint and in order. Not merely different: a second window that began
    # before the first ended would mean the loop lost track of a trial, and the
    # observer discards rather than merges precisely so that cannot pass quietly.
    assert second.first_frame >= first.last_frame, (
        f"the second trial's window began at frame {second.first_frame}, which is "
        f"before the first one ended at {first.last_frame} — the windows overlap"
    )
    assert first.last_frame > first.first_frame
    assert second.last_frame > second.first_frame
    assert not first.uncertain and not second.uncertain


def test_a_trial_nobody_reports_the_end_of_is_the_consumers_to_end(display, armed_executor):
    """**Only the side that is waiting can tell "not yet" from "never".**

    The rule the whole family rests on, and the one outcome triald assigns to
    itself. An executor publishes what it saw and assumes nobody read it — it
    cannot know whether a consumer exists — so nothing is responsible for
    delivering an outcome, and a subscription that dies would otherwise be a
    session that stops with no error anywhere.

    Every other test here runs a trial that finishes. This one runs a trial that
    is still going when triald's patience runs out: a graph waiting far longer
    than the session's cap. The device is fine, the executor is fine, nobody has
    failed — and triald still has to write something down, because a gap in the
    trial numbering is a thing somebody has to explain months later.
    """
    import datetime

    from triald import Session, TrialOutcome
    from triald.api.statemachine_executor import StateMachineExecutor
    from triald.cli import demo_experiment
    from triald.executor import TrialConfiguration

    store, config = demo_experiment()
    # Short enough that the test is quick, long enough that a slow container is
    # not what ends the trial.
    config.trial_cap_ms = 1500
    session = Session(store, config)
    session.arm()

    # A graph whose wait outlasts the cap by a wide margin. The device's own
    # watchdog is set well beyond both, so nothing but triald ends this trial.
    patient = scenarios.timed_graph("patient", milliseconds=30_000)
    scenarios.upload(armed_executor, patient)

    executor = StateMachineExecutor(armed_executor.address)
    spec = session.next_trial()
    trial_id = spec.trial_number

    # Whatever the executor already holds under this number, from an earlier
    # session on a long-lived rig. The assertion at the end is that this test
    # added nothing to it.
    already = _finished_results_for(armed_executor, trial_id)

    executor.configure(
        TrialConfiguration(
            trial_id=trial_id, statemachine_graph="patient", cap_milliseconds=60_000
        )
    )
    executor.start(trial_id)

    try:
        deadline = datetime.datetime.now() + datetime.timedelta(seconds=15)
        record = None
        while record is None and datetime.datetime.now() < deadline:
            record = session.expire_overdue_trial()
            if record is None:
                time.sleep(0.1)
    finally:
        # The device is still running the trial: this test ended triald's
        # interest in it, not the trial. Leave the rig idle for the next test.
        # Tolerated, because by now it may have ended on its own -- and "there
        # is nothing to cancel" is a fine outcome for a teardown.
        with contextlib.suppress(DaemonRefusedTheRequest):
            armed_executor.client.cancel_trial(trial_id)

    assert record is not None, (
        f"triald never gave up on trial {trial_id}: a cap of "
        f"{config.trial_cap_ms} ms expired and nothing was recorded"
    )
    assert record.report.outcome is TrialOutcome.NEVER_FINISHED, record
    # Recorded, and never accepted: it consumes nothing from the round and moves
    # no stop rule. A trial nobody watched must not look like a trial that ran.
    assert not record.accepted, "a trial nobody reported the end of was accepted"
    assert record.spec.trial_number == trial_id
    # The note says what happened, because a NEVER_FINISHED somebody reads in a
    # year is otherwise indistinguishable from a rig that was simply switched off.
    assert str(trial_id) in (record.report.note or ""), record.report.note

    # And the executor is not at fault and never was: it published no result for
    # this trial, because there was none to publish.
    #
    # Asserted as a delta rather than an absolute. triald numbers its own trials
    # from 1, so on a rig whose statemachined has been up for weeks this id has
    # very likely been used before by somebody else's session — which is a real
    # property of the rig and not this test's business.
    assert _finished_results_for(armed_executor, trial_id) == already, (
        f"the executor published a result for trial {trial_id} after all"
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "server.started cannot reach the subscriber that needs it. It is published "
        "once, at startup, and a SUB socket that was attached to the previous "
        "process has not finished reconnecting yet — measured: the reconnect works "
        "(a command after the restart is received) but nothing spontaneous crosses. "
        "A subscriber attaching later misses it for the ordinary slow-joiner "
        "reason. So in practice the topic is delivered to nobody, and a window "
        "spanning a restart is reported clean with frame numbers from two "
        "different runs. Fix belongs in vstimd: a run id on every event, since "
        "ZeroMQ PUB has no retained message to re-deliver. Strict, so this turns "
        "red the day it is fixed."
    ),
)
def test_a_restart_reaches_the_observer_through_the_stream_it_really_subscribes_to(
    restartable_display,
):
    """**The production path, carrying a real event for once.**

    `StimulusObserver` subscribes to exactly two topics — `frame.dropped` and
    `server.started` — and in a clean headless run neither ever arrives. So the
    observer's own suite drives it with a fake source, the three-daemon test
    above builds a real one that receives nothing, and the two halves have never
    met: every assertion about a window passes on an event stream that was
    silent, and a decode that did not work would look exactly the same.

    A restart is the one of the two topics a test can cause. It is also the case
    that matters most for correctness, because a restart resets the frame
    counter: a window spanning one is a window whose numbers come from two
    different runs, and calling it clean would put a fabricated frame count on a
    trial's record.

    This owns its renderer — see `RestartableDisplay`. Restarting one somebody
    else is attached to would be doing to another test what this one is testing.
    """
    from triald.api.stimulus_subscriber import StimulusObserver, connect

    observer = StimulusObserver(connect("127.0.0.1", restartable_display.info["event_port"]))
    observer.start()
    try:
        # PUB discards anything sent before a subscription lands, so the socket
        # has to be attached before the restart it is here to hear about.
        time.sleep(1.0)

        observer.open_window(first_frame=1)
        restartable_display.restart()

        def the_observer_noticed() -> bool:
            return observer.last_frame_seen is not None

        assert wait_until(the_observer_noticed, timeout_s=20.0), (
            "the observer received nothing across a restart of the display it is "
            "subscribed to — `server.started` never arrived, or never decoded"
        )

        window = observer.close_window(last_frame=2)
    finally:
        observer.close()

    assert window is not None
    # The whole point: a window spanning a restart is not trustworthy, and the
    # observer says so rather than reporting a clean trial with frame numbers
    # from two different runs of the renderer.
    assert window.uncertain, (
        "a window spanning a restart of the display was reported clean — the "
        "frame counter reset underneath it and nothing said so"
    )


def test_the_graph_the_acceptance_suite_needs_a_wire_for_uploads_and_runs(
    display, executor, on_hardware
):
    """The acceptance suite's lever graph, uploaded and run where there is no lever.

    Those tests only run on a wired rig, so nothing else ever sent this graph to
    a daemon -- and it had rotted: it declared a `MISS` outcome the executor
    does not have, and a transition and a pulse in a schema the executor had
    stopped accepting. The first acceptance run would have failed on the upload
    and blamed the wiring. Here it goes through the same upload and the same
    trial, and with nothing on the input it must take the timeout branch.
    """
    from triald.api.statemachine_executor import StateMachineExecutor
    from triald.api.stimulus_subscriber import StimulusObserver, connect
    from vstimd_client_class import VstimdClient

    scenarios.upload(executor, scenarios.graph_waiting_for_a_lever("lever", timeout_ms=200))
    observer = StimulusObserver(connect("127.0.0.1", display["event_port"]))
    observer.start()
    try:
        with VstimdClient(display["address"], recv_timeout_s=10.0) as renderer:
            ran = scenarios.run_one_trial(
                display_connection=renderer,
                executor_client=executor,
                executor=StateMachineExecutor(executor.address),
                observer=observer,
                graph="lever",
            )
    finally:
        observer.close()
    assert ran.outcome.outcome.name == ("HIT" if on_hardware else "LATE"), ran.outcome


def _finished_results_for(executor_client, trial_id: int) -> int:
    """How many finished results the executor holds for one trial id.

    Counted off the trace rather than through the client's `outcome_of`,
    because that one refuses a trial with no result *and* a trial with two —
    and this test needs to tell those apart rather than catch either.

    A trial the ring has never heard of reads as an empty list rather than a
    refusal: the ring cannot tell "no such trial" from "a trial whose entries
    were overwritten", so it says what it saw.
    """
    from statemachined_client import KIND_TRIAL_RESULT

    entries = executor_client.client.read_trial_trace(trial_id)
    return sum(1 for entry in entries if entry.kind == KIND_TRIAL_RESULT)
