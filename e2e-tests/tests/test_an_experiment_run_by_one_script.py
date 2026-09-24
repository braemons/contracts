"""An experiment configured, run and controlled by one Python script, not triald.

**Why this is its own file.** Every other test here has triald in the middle,
because triald is the only daemon allowed to know the others. But that rule is
about *daemons*. A script is not a daemon, and the claim the family makes is
that a participant is usable by anything that commands it -- a bench script as
much as a decision authority. Nothing tested that claim. triald's own client
code carried every trial in this suite, so a statemachined API that only
triald's client could drive, or a renderer whose conditions only worked
alongside a triald observer, would have passed.

`scripted_experiment.py` is that script, written the way a person writes one:
it imports `vstimd` and talks to statemachined over plain HTTP and a websocket,
and it knows nothing about this test suite. These tests run it as a program
first -- the operator's path -- and then reach into its loop for the control a
person exercises by hand: cancelling mid-trial, giving up on a trial, changing
the next trial on what the last one did, stopping on a rule.

**Runs in CI and on a rig, unchanged.** In the container every lever window
times out, so target trials end `LATE` and catch trials `HIT`. On a wired rig
with nobody pressing the lever the same is true, and the targets are on the
stimulus display for the length of each trial, one line printed per trial as it
ends -- so a person watching can see the session go by.
"""

from __future__ import annotations

import itertools
import json
import pathlib
import subprocess
import sys
import time

import pytest
import scenarios
import scripted_experiment as script
from scripted_experiment import Experiment, PlannedTrial

SCRIPT = pathlib.Path(script.__file__)

#: How much of a trial's device-timed duration its condition must at least be on
#: screen for, in frames at the renderer's own rate. Loose, because the two
#: commands that bound it are host round trips; a stuck clock is zero.
FRAME_TOLERANCE = 0.5


def trial_ids(count: int) -> int:
    """The first of `count` consecutive ids nothing else in this run will use."""
    first = scenarios.unique_trial_id()
    for _ in range(count - 1):
        scenarios.unique_trial_id()
    return first


@pytest.fixture
def say(request: pytest.FixtureRequest):
    """Print past pytest's capture, so a rig run shows the session as it goes."""
    capture = request.config.pluginmanager.getplugin("capturemanager")

    def emit(line: str) -> None:
        with capture.global_and_fixture_disabled():
            print(f"  {line}", flush=True)

    return emit


@pytest.fixture
def experiment(display, executor, say):
    with Experiment(
        renderer_address=display["address"],
        event_port=display["event_port"],
        executor_address=executor.address,
        seed=20260914,
    ) as running:
        yield running


def narrate(say, record) -> None:
    say(
        f"trial {record.trial_id} {record.condition:<5} -> {record.outcome}"
        f" (foreperiod {record.drawn_foreperiod_ms} ms, frames "
        f"{record.stimulus_on_frame}..{record.stimulus_off_frame})"
    )


# ── The operator's path: run the script as a program ──────────────────────────


def test_a_session_run_as_a_program_leaves_a_record_of_every_trial(
    display, executor, tmp_path, say
):
    """`python scripted_experiment.py ...`, and the file it writes.

    What an operator has at the end of an afternoon is the output file, so that
    is what is checked: one record per planned trial, in plan order, each one
    showing that the condition the script chose reached *both* daemons -- the
    renderer showed it, and the state machine ran its graph with the foreperiod
    the script drew.
    """
    trials = 8
    first = trial_ids(trials)
    out = tmp_path / "session.jsonl"
    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--renderer",
            display["address"],
            "--event-port",
            str(display["event_port"]),
            "--executor",
            executor.address,
            "--trials",
            str(trials),
            "--first-trial-id",
            str(first),
            "--seed",
            "11",
            "--out",
            str(out),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    for line in completed.stdout.splitlines():
        say(line)
    assert completed.returncode == 0, completed.stderr

    header, *lines = out.read_text().splitlines()
    plan = json.loads(header)["session"]["plan"]
    records = [json.loads(line) for line in lines]
    assert len(records) == trials

    for planned, record in zip(plan, records, strict=True):
        assert record["trial_id"] == planned["trial_id"]
        assert record["condition"] == planned["condition"]

        # The renderer: the condition was the active one while the trial ran.
        assert record["condition_seen_during_trial"] == planned["condition"]

        # The state machine: the graph the condition maps to, with the
        # foreperiod the script drew rather than the graph's placeholder.
        assert record["drawn_foreperiod_ms"] == planned["foreperiod_ms"], record
        expected = "HIT" if planned["condition"] == "catch" else "LATE"
        assert record["outcome"] == expected, record
        assert record["states"] == ["Foreperiod", "Window", "Withheld"], record
        assert record["cancel_reason"] is None
        assert record["frames_dropped"] == 0

    # Both kinds of trial were in the session, or the outcome check proved half.
    assert {r["condition"] for r in records} >= {"catch", "left", "right"}

    # Stimulus windows are on one renderer clock: in order, never overlapping,
    # and as long as the trial the device timed.
    for earlier, later in itertools.pairwise(records):
        assert later["stimulus_on_frame"] >= earlier["stimulus_off_frame"]
    from vstimd import Connection

    with Connection(display["address"], recv_timeout_s=10.0) as renderer:
        hz = renderer.system.query_server_info().frame_rate_hz
    for record in records:
        shown = record["stimulus_off_frame"] - record["stimulus_on_frame"]
        timed = record["duration_us"] / 1e6 * hz
        assert shown >= timed * FRAME_TOLERANCE, (
            f"trial {record['trial_id']} ran {record['duration_us']} us on the device but "
            f"its condition was on screen for {shown} frames"
        )


def test_the_script_leaves_the_rig_as_it_found_it(display, executor, tmp_path):
    """A script is a guest on a rig that is always on.

    vstimd is up all day for alignment and luminance checks, and statemachined
    for whatever runs next. A script that exits leaving its stimuli drawn, its
    condition active or a trial armed would break the next person's session
    with an error about something else.
    """
    from vstimd import Connection

    with Connection(display["address"], recv_timeout_s=10.0) as renderer:
        before = {s.name for s in renderer.system.list_stimuli()}

    subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--renderer",
            display["address"],
            "--event-port",
            str(display["event_port"]),
            "--executor",
            executor.address,
            "--trials",
            "2",
            "--first-trial-id",
            str(trial_ids(2)),
            "--out",
            str(tmp_path / "s.jsonl"),
        ],
        check=True,
        capture_output=True,
        timeout=60,
    )

    with Connection(display["address"], recv_timeout_s=10.0) as renderer:
        assert {s.name for s in renderer.system.list_stimuli()} == before
        assert renderer.conditions.active == 0
    assert not executor.client.read_state().running
    # And the device takes a new graph set, which it refuses while armed.
    scenarios.upload(executor, scenarios.timed_graph("after-the-script"))


# ── Control from inside the script's loop ─────────────────────────────────────


def test_the_script_cancels_a_trial_mid_window_and_the_session_carries_on(experiment, say):
    """The experimenter's stop button, pressed during the response window.

    The trial must end on the device as `CANCELLED` by the host -- not as a
    miss, which would put a response the subject never had the chance to make
    in the record -- with the window it was cancelled in as its last state. And
    the next trial must run normally, because a cancel that leaves the device
    busy stops a session as surely as a crash.
    """
    first = trial_ids(2)
    long_window = PlannedTrial(first, "left", foreperiod_ms=100, window_ms=20_000)

    def cancel_once_the_window_is_open(running: Experiment, trial: PlannedTrial) -> None:
        deadline = time.monotonic() + 5
        while running.machine.client.read_state().state_name != "Window":
            assert time.monotonic() < deadline, "the trial never reached its window"
            time.sleep(0.02)
        running.cancel()

    started = time.monotonic()
    cancelled = experiment.run_trial(long_window, while_running=cancel_once_the_window_is_open)
    narrate(say, cancelled)

    assert time.monotonic() - started < 10, "the cancel did not end a 20 s window"
    assert cancelled.outcome == "CANCELLED"
    assert cancelled.cancel_reason == "HOST"
    assert cancelled.states == ["Foreperiod", "Window"]

    after = experiment.run_trial(PlannedTrial(first + 1, "right", foreperiod_ms=120))
    narrate(say, after)
    assert after.outcome == "LATE"
    assert after.states == ["Foreperiod", "Window", "Withheld"]


def test_a_trial_past_the_scripts_deadline_is_ended_and_recorded(experiment, say):
    """Only the side waiting can tell "not yet" from "never" -- here, the script.

    The executor publishes and assumes nobody read it, so without a deadline of
    its own a script whose trial never ends simply hangs. This one gives up,
    ends the trial on the device so nothing is left running, says in the record
    that it was the script's patience and not the subject, and goes on.
    """
    first = trial_ids(2)
    record = experiment.run_trial(
        PlannedTrial(first, "catch", foreperiod_ms=100, window_ms=30_000), deadline_s=1.5
    )
    narrate(say, record)
    assert record.outcome == "SCRIPT_TIMEOUT"
    # The device's own account of it: ended by the host, not by the graph.
    assert record.cancel_reason == "HOST"

    after = experiment.run_trial(PlannedTrial(first + 1, "catch", foreperiod_ms=100))
    narrate(say, after)
    assert after.outcome == "HIT"


def test_the_script_changes_each_trial_on_what_the_last_one_did(experiment, say):
    """Adaptive control without triald: the next trial is decided between trials.

    A shrinking foreperiod after every miss, the shape of any staircase. What
    crosses the wire each time is a distribution patch on `configure`, and the
    device's own record of what it drew is the proof it arrived -- a patch
    silently dropped would run every trial at the graph's placeholder value.
    """
    first = trial_ids(4)
    foreperiod = 380
    asked, drawn = [], []
    for number in range(4):
        record = experiment.run_trial(PlannedTrial(first + number, "left", foreperiod))
        narrate(say, record)
        asked.append(foreperiod)
        drawn.append(record.drawn_foreperiod_ms)
        if record.outcome == "LATE":
            foreperiod -= 80

    assert asked == [380, 300, 220, 140]
    assert drawn == asked


def test_a_stop_rule_ends_the_session_on_the_outcomes_that_came_back(experiment, say):
    """Three misses in a row and the script stops, however long the plan was."""
    plan = experiment.plan(10, trial_ids(10), catch_fraction=0.0)

    def three_misses(records) -> bool:
        return len(records) >= 3 and all(r.outcome == "LATE" for r in records[-3:])

    records = experiment.run(plan, should_stop=three_misses, on_trial=lambda r: narrate(say, r))
    assert [r.trial_id for r in records] == [t.trial_id for t in plan[:3]]


def test_the_same_seed_plans_the_same_session(display, executor):
    """A script's session is reproducible from its record, or it is an anecdote."""

    def planned(seed: int):
        running = Experiment(
            renderer_address=display["address"],
            event_port=display["event_port"],
            executor_address=executor.address,
            seed=seed,
        )
        try:
            return running.plan(12, first_trial_id=1)
        finally:
            running.events.close()
            running.renderer.close()
            running.machine.close()

    assert planned(3) == planned(3)
    assert planned(3) != planned(4)
