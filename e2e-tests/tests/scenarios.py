"""The trials themselves: graphs, and the loop that runs one.

**This is the sharing.** The CI suite and the acceptance suite call the same
functions here; what differs between them is only which fixtures brought the
daemons up. An acceptance suite that reimplemented the loop would drift from the
one CI runs, and the drift would be invisible exactly because the two are never
run together.

Nothing here is a fixture and nothing here knows what a board is. Every function
takes the pieces it needs and returns what happened, which is also what makes
them readable as a description of the rig's loop.
"""

from __future__ import annotations

import dataclasses
import itertools
import json
import time
from typing import Any

#: Trial ids nothing else in this run will reuse.
#:
#: A trial id is the *executor's* key, and an executor on a rig is a daemon that
#: has been up for weeks: `State/ReadTrialTrace` searches a trace holding every
#: trial anybody has run. So two tests that both call their trial "1" do not get
#: a fresh one each -- they get one trial with two results, and the executor
#: refuses that ("one trial ends once") rather than picking. Found by running
#: the suite against an attached daemon, where it is true; invisible against a
#: spawned one, where every test gets a new trace.
#:
#: Seeded from the clock so that two *runs* against one long-lived rig do not
#: collide either, which is the same problem one loop further out.
_trial_ids = itertools.count(int(time.time()) % 100_000 * 10)


def unique_trial_id() -> int:
    return next(_trial_ids)


def timed_graph(name: str, milliseconds: int = 60, outcome: str = "HIT") -> dict:
    """A graph that waits, then declares `outcome`. Nothing to press.

    The graph a rig can run with no subject and no wires: it reaches its outcome
    on a timeout, which exercises the compiler, the framing, the device's scan
    loop and the trace — everything above the trigger.
    """
    return {
        "name": name,
        "entry": "Wait",
        "distributions": {"dwell": {"kind": "fixed", "duration_ms": milliseconds}},
        "states": [
            {
                "name": "Wait",
                "on_entry": [{"line": "ready_lamp", "kind": "high"}],
                "timeout": {"after": "dwell", "goto": "End"},
            },
            {"name": "End", "outcome": outcome},
        ],
    }


def graph_waiting_for_a_lever(name: str, timeout_ms: int = 3000) -> dict:
    """HIT if the lever goes high in time, LATE if it never does.

    **The graph that needs a wire**, and the one no local run can answer: the
    device sends commands and never drives its own inputs, so off a bench this
    always takes the LATE branch. With a lever — or a jumper — on the input, the
    HIT branch is reached through the real trigger path, which is the thing the
    whole hourglass rests on and which nothing else in the family tests.

    It raises `stimulus_gate` on entry: that is the TTL a rig runs to vstimd's
    virtual trigger line, so a stimulus can be armed by the state machine
    without a host round trip.
    """
    return {
        "name": name,
        "entry": "Wait",
        "distributions": {"limit": {"kind": "fixed", "duration_ms": timeout_ms}},
        "states": [
            {
                "name": "Wait",
                "on_entry": [
                    {"line": "ready_lamp", "kind": "high"},
                    {"line": "stimulus_gate", "kind": "high"},
                ],
                "transitions": [{"when": {"all": ["lever"]}, "goto": "Hit"}],
                "timeout": {"after": "limit", "goto": "Missed"},
            },
            {
                "name": "Hit",
                "on_entry": [{"line": "reward_valve", "kind": "pulse", "pulse_ms": 40}],
                "outcome": "HIT",
            },
            {"name": "Missed", "outcome": "LATE"},
        ],
    }


def upload(executor, graph: dict) -> None:
    """Store a graph and send the set to the device.

    Two steps, and they are different things: the store keeps it, the session
    upload compiles the named set and puts it on the device. A trial can only
    name a graph the device is holding.
    """
    # **The graph crosses as text.** statemachined is the only thing that
    # parses one; a client carrying its own copy of those models would carry a
    # copy that is right until it is not.
    executor.client.write_graph(graph["name"], json.dumps(graph))
    executor.client.upload_graph_set([graph["name"]])


@dataclasses.dataclass(frozen=True)
class Ran:
    """One trial, and the frames it occupied. What every scenario returns."""

    trial_id: int
    outcome: Any
    window: Any
    first_frame: int
    last_frame: int

    @property
    def frames(self) -> int:
        return self.last_frame - self.first_frame


def run_one_trial(
    *,
    display_connection,
    executor_client,
    executor,
    observer,
    graph: str,
    trial_id: int | None = None,
    cap_milliseconds: int = 10_000,
    while_running=None,
) -> Ran:
    """The rig's loop, in the order a rig runs it.

    triald opens a window on the renderer's clock, arms the executor for this
    trial and no other, starts it, watches the executor's trace go by, pulls that
    trial's events by id and turns them into an outcome — then closes the window.

    `while_running` is called once the trial has started, for a test that has to
    *do* something to it: press a lever, watch a display. It is the only place
    the two suites diverge, and it diverges by adding, never by replacing.
    """
    from triald.executor import TrialConfiguration

    # Never a default of 1: see unique_trial_id above. A caller that wants a
    # particular number says so; a caller that just wants "a trial" gets one
    # nothing else in this run will claim.
    if trial_id is None:
        trial_id = unique_trial_id()

    first_frame = display_connection.system.wait_for_frames(0).frame_count
    observer.open_window(first_frame=first_frame)

    # **Taken before the trial is armed**, because the subscription is opened
    # after it: a 40 ms trial ends before a late subscriber is watching, and a
    # stream that started "from now" would then wait for a result that has
    # already gone past. The ring is what makes reading from a mark possible.
    before_arming = executor_client.mark()

    executor.configure(
        TrialConfiguration(
            trial_id=trial_id,
            statemachine_graph=graph,
            cap_milliseconds=cap_milliseconds,
        )
    )
    executor.start(trial_id)

    # Observed, not waited on: the executor published and moved on, and this side
    # is the one holding a deadline. Subscribing is opening the stream; nothing
    # on the far end is holding the trial for anybody.
    with executor_client.trace_stream(since=before_arming) as messages:
        if while_running is not None:
            while_running()
        finished = next(executor.finished_trials(messages))
    assert finished == trial_id

    outcome = executor.outcome_of(trial_id=trial_id)
    last_frame = display_connection.system.wait_for_frames(0).frame_count
    window = observer.close_window(last_frame=last_frame)

    return Ran(
        trial_id=trial_id,
        outcome=outcome,
        window=window,
        first_frame=first_frame,
        last_frame=last_frame,
    )


def line_levels(executor_client) -> dict[str, bool]:
    """Every named line and whether it is high now, as the device reports it.

    `Device/ReadLines` is the only read-back there is: nothing can sense a pin
    directly, so this is the device's own `io` word resolved against the line
    map. It is what lets a test check that a graph's line numbers reach the pins
    somebody actually wired.

    `is_high_now` is **absent** rather than false when nothing is attached —
    there is no read-back path from a pin — so `None` reads as low here and the
    caller that cares about the difference asks the device directly.
    """
    view = executor_client.read_lines()
    lines = list(view.input_lines) + list(view.output_lines)
    return {line.name: bool(line.is_high_now) for line in lines}
