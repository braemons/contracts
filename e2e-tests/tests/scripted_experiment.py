#!/usr/bin/env python3
"""A whole experiment in one Python script: no triald anywhere.

    python scripted_experiment.py --trials 12 --seed 7 --out session.jsonl

**What this is.** The other way a rig gets used. triald is the decision
authority for a session somebody runs every day; a bench, a pilot or a one-off
paradigm is just as often a single script that somebody wrote the afternoon
before. That script configures the renderer and the state machine itself, picks
its own trials, runs them, reacts to what comes back, and writes its own record.
Both daemons are supposed to be fully usable that way -- they are participants
that know nobody, so a script is as good a commander as triald is -- and until
this file nothing checked that they are.

**It imports no daemon and no test.** `vstimd-client` for the renderer and
`statemachined-client` for the state machine and its trace stream — the two
client libraries a script's author installs when triald is not in the picture.
So it is also the check that both APIs are usable by somebody who is not one of
these daemons.

**The paradigm.** A detection task with catch trials, on the rig `WIRING.md`
describes:

* the renderer holds a fixation spot and two targets, each a vstimd
  *condition*: `blank` between trials, `left` / `right` show one target, `catch`
  shows only fixation;
* the state machine runs a foreperiod, then a response window, per trial.
  A target trial waits for the lever: pressed is `HIT`, not pressed is `LATE`.
  A catch trial is the opposite: pressed is `WRONG_RESPONSE`, withheld is `HIT`;
* the foreperiod is drawn **by this script** and sent with the trial as a
  distribution patch, so the value the device ran can be checked against the
  value the script asked for.

With no subject every lever window times out -- the graphs never drive
`ready_lamp`, so the loopback jumper does not press the lever either -- so a target trial
ends `LATE` and a catch trial ends `HIT`. That is the assertion that a script's
plan reached the device: the outcome is decided by which graph ran.

**Control.** `Experiment` is the script's loop, broken into the steps a person
would want to reach between trials or during one: `run_trial` takes a
`while_running` hook and a deadline, `cancel` ends the trial in flight, and
`run` takes a `should_stop` rule that sees every record so far.
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import datetime as dt
import json
import random
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

#: The lines this paradigm drives and reads. The script refuses to run on a rig
#: whose line map does not name them, rather than uploading graphs that do.
LINES_NEEDED = ("lever", "reward_valve", "stimulus_gate")

#: vstimd conditions, by index. Index 0 is what the renderer shows by default,
#: so it is the one a script leaves behind.
CONDITIONS = [(0, "blank"), (1, "left"), (2, "right"), (3, "catch")]

#: Which graph each trial condition runs.
GRAPH_FOR_CONDITION = {"left": "detect", "right": "detect", "catch": "catch"}


def detection_graph(name: str, *, pressed: str, withheld: str, window_ms: int) -> dict:
    """Foreperiod, then a response window on the lever.

    The foreperiod's `fixed` value is a placeholder: every trial patches it.
    """
    return {
        "name": name,
        "entry": "Foreperiod",
        "distributions": {
            "foreperiod": {"kind": "fixed", "duration_ms": 200},
            "window": {"kind": "fixed", "duration_ms": window_ms},
        },
        "states": [
            {
                "name": "Foreperiod",
                # No ready_lamp: on WIRING.md's rig it is jumpered to the lever,
                # and driving it would press the lever for every trial.
                "timeout": {"after": "foreperiod", "goto": "Window"},
            },
            {
                "name": "Window",
                "on_entry": [{"line": "stimulus_gate", "kind": "high"}],
                "transitions": [{"when": {"all": ["lever"]}, "goto": "Pressed"}],
                "timeout": {"after": "window", "goto": "Withheld"},
            },
            {
                "name": "Pressed",
                "on_entry": [{"line": "reward_valve", "kind": "pulse", "pulse_ms": 40}]
                if pressed == "HIT"
                else [],
                "outcome": pressed,
            },
            {"name": "Withheld", "outcome": withheld},
        ],
    }


class ExperimentError(RuntimeError):
    """The rig refused something, in the daemon's own words."""


# ── statemachined, over its gRPC API ──────────────────────────────────────────


def flattened(entry) -> dict:
    """One published trace entry as the flat dict this script reads.

    statemachined's ring holds flat records -- `kind`, `trial_id`, `outcome`,
    `state_name` in one dict -- and its wire type names four of those and
    carries the rest in `payload`, because the set differs per kind and
    protobuf has no type for "and some other things". Putting them back
    together is the whole of this function.

    The named fields win over the payload: a payload carrying a `trial_id` of
    its own would otherwise decide which trial an entry belonged to.
    """
    return {
        **entry.payload,
        "entry_number": entry.entry_number,
        "kind": entry.kind,
        "trial_id": entry.trial_id,
    }


class StateMachine:
    """The five calls a script needs, and the trace stream.

    A thin adapter over `statemachined-client` rather than a client of its own.
    It exists for the script's *vocabulary* -- `store_graph`, `trace_for` -- and
    for the two places a script must wait rather than fail; everything else is
    the published client doing the work.

    **It was httpx and a WebSocket.** statemachined's interface is
    `proto/statemachined/v1/` now and the routes are gone.
    """

    def __init__(self, address: str, timeout_s: float = 10.0) -> None:
        from statemachined_client import StatemachinedClient

        self.address = address
        self.client = StatemachinedClient(address)
        self.client.wait_until_ready(timeout_s=timeout_s)

    def close(self) -> None:
        self.client.close()

    def line_names(self) -> set[str]:
        view = self.client.read_lines()
        return {line.name for line in list(view.input_lines) + list(view.output_lines)}

    def store_graph(self, graph: dict) -> None:
        # As text: the daemon is the only thing that parses a graph, and a
        # script carrying its own copy of those models would carry a copy that
        # is right until it is not.
        self.client.write_graph(graph["name"], json.dumps(graph))

    def upload_graph_set(self, names: list[str], timeout_s: float = 30.0):
        """Put the set on the device, waiting out a trial that is still ending.

        A cancelled trial is over for the daemon a moment before it is over for
        the device, which answers `busy` in between -- the **device's** own
        word, passed up rather than paraphrased, which is what makes it
        possible to tell from a graph that will not compile.
        """
        from statemachined_client import DaemonRefusedTheRequest

        deadline = time.monotonic() + timeout_s
        while True:
            try:
                return self.client.upload_graph_set(names)
            except DaemonRefusedTheRequest as refused:
                if refused.error != "busy" or time.monotonic() > deadline:
                    raise ExperimentError(f"uploading {names}: {refused}") from refused
            time.sleep(0.25)

    def configure(self, trial_id: int, graph: str, cap_ms: int, patches: list[dict]):
        from statemachined_client import DistributionPatch

        return self.client.configure_trial(
            trial_id,
            graph=graph,
            cap_milliseconds=cap_ms,
            distribution_patches=[DistributionPatch(**patch) for patch in patches],
        )

    def start(self, trial_id: int):
        return self.client.start_trial(trial_id)

    def cancel(self, trial_id: int):
        """End a trial, or say why it could not be ended.

        **"There is nothing to cancel" is a refusal now**, where the route
        answered a body nobody read: the rpc says `unknown_trial` when no trial
        by that id is armed or running, which is the honest answer to a script
        whose deadline expired a moment after the trial ended on its own.

        It arrives as this script's own exception, like every other refusal
        here, so a caller that does not care can suppress one kind rather than
        learn another library's.
        """
        from statemachined_client import DaemonRefusedTheRequest

        try:
            return self.client.cancel_trial(trial_id)
        except DaemonRefusedTheRequest as refused:
            raise ExperimentError(f"cancelling trial {trial_id}: {refused}") from refused

    def trace_for(self, trial_id: int) -> list[dict]:
        return [flattened(entry) for entry in self.client.read_trial_trace(trial_id)]

    def mark(self) -> int:
        """The entry number to start a subscription after, to see only what
        happens from now on."""
        return self.client.read_state().newest_trace_entry_number + 1

    @contextlib.contextmanager
    def trace_stream(self, since: int, deadline_s: float) -> Iterator[Any]:
        """The trace from `since` onwards, named so the rig can see who watches.

        **`deadline_s` is the script's patience, and it is the stream's
        deadline.** A gRPC iterator has no per-read timeout, so a subscription
        with a longer deadline than the caller's patience blocks past it — and
        a script that meant to give up after 1.5 s instead waits for the
        board's own trial cap and records the wrong reason for the ending.
        Giving the call the deadline is what makes the two the same number.
        """
        subscription = self.client.watch_trace(
            since, observer="script", timeout_s=deadline_s
        )
        with subscription:
            yield subscription


# ── The record ────────────────────────────────────────────────────────────────


@dataclasses.dataclass
class PlannedTrial:
    trial_id: int
    condition: str
    foreperiod_ms: int
    window_ms: int | None = None
    """Overrides the response window for this trial only; None keeps the graph's."""

    @property
    def graph(self) -> str:
        return GRAPH_FOR_CONDITION[self.condition]


@dataclasses.dataclass
class TrialRecord:
    trial_id: int
    condition: str
    graph: str
    foreperiod_ms: int
    outcome: str
    """The executor's outcome name, or `SCRIPT_TIMEOUT` if the script gave up."""
    cancel_reason: str | None
    drawn_foreperiod_ms: int | None
    states: list[str]
    duration_us: int | None
    stimulus_on_frame: int
    stimulus_off_frame: int
    condition_seen_during_trial: str | None
    frames_dropped: int
    started_at: str

    def as_json(self) -> str:
        return json.dumps(dataclasses.asdict(self))


# ── The experiment ────────────────────────────────────────────────────────────


class Experiment:
    """One session, owned by one script."""

    def __init__(
        self,
        *,
        renderer_address: str,
        event_port: int,
        executor_address: str,
        window_ms: int = 300,
        seed: int | None = None,
    ) -> None:
        from vstimd_client_class import VstimdClient
        from vstimd.events import EventSubscriber, Topic

        self.seed = seed if seed is not None else random.randrange(2**31)
        self.rng = random.Random(self.seed)
        self.window_ms = window_ms
        self.machine = StateMachine(executor_address)
        self.renderer = VstimdClient(renderer_address, recv_timeout_s=10.0)
        host = renderer_address.split("://", 1)[-1].rsplit(":", 1)[0]
        self.events = EventSubscriber(
            host, event_port, topic=[Topic.COMMAND_APPLIED, Topic.FRAME_DROPPED]
        )
        self.stimuli: list[Any] = []
        self.records: list[TrialRecord] = []
        self._in_flight: int | None = None

    # -- setting up ------------------------------------------------------------

    def set_up(self) -> None:
        """Everything done once: check the wiring, graphs, stimuli, conditions."""
        missing = [name for name in LINES_NEEDED if name not in self.machine.line_names()]
        if missing:
            raise ExperimentError(f"the rig's line map has no {', '.join(missing)}")

        self.machine.store_graph(
            detection_graph("detect", pressed="HIT", withheld="LATE", window_ms=self.window_ms)
        )
        self.machine.store_graph(
            detection_graph(
                "catch", pressed="WRONG_RESPONSE", withheld="HIT", window_ms=self.window_ms
            )
        )
        self.machine.upload_graph_set(["detect", "catch"])

        from vstimd.stimuli import RectParams
        from vstimd.stimuli.stimuli_models import Vec2

        shapes = self.renderer.stimuli.shapes
        fixation = shapes.create_rect(name="fixation", params=RectParams(width_px=12, height_px=12))
        left = shapes.create_rect(
            name="target-left",
            position_px=Vec2(-300, 0),
            params=RectParams(width_px=80, height_px=80),
        )
        right = shapes.create_rect(
            name="target-right",
            position_px=Vec2(300, 0),
            params=RectParams(width_px=80, height_px=80),
        )
        self.stimuli = [fixation, left, right]

        conditions = self.renderer.conditions
        conditions.declare(CONDITIONS)
        index = dict((name, i) for i, name in CONDITIONS)
        conditions.set_stimulus_conditions(
            fixation, [index["left"], index["right"], index["catch"]]
        )
        conditions.set_stimulus_conditions(left, [index["left"]])
        conditions.set_stimulus_conditions(right, [index["right"]])
        conditions.set("blank")

        # PUB drops what it sends before a subscription has landed.
        time.sleep(0.5)
        self._drain_events()

    def tear_down(self) -> None:
        """Leave the renderer as the script found it, and the device idle."""
        with contextlib.suppress(Exception):
            if self._in_flight is not None:
                self.machine.cancel(self._in_flight)
        with contextlib.suppress(Exception):
            self.renderer.conditions.set("blank")
            self.renderer.conditions.declare([])
        for handle in self.stimuli:
            with contextlib.suppress(Exception):
                self.renderer.stimuli.delete(handle)
        self.stimuli = []
        self.events.close()
        self.renderer.close()
        self.machine.close()

    def __enter__(self) -> Experiment:
        self.set_up()
        return self

    def __exit__(self, *_: object) -> None:
        self.tear_down()

    # -- planning --------------------------------------------------------------

    def plan(
        self, trials: int, first_trial_id: int, catch_fraction: float = 0.25
    ) -> list[PlannedTrial]:
        """Balanced targets, a fixed share of catch trials, shuffled with the seed."""
        catches = round(trials * catch_fraction)
        targets = trials - catches
        conditions = (
            ["catch"] * catches + ["left", "right"] * (targets // 2) + ["left"] * (targets % 2)
        )
        self.rng.shuffle(conditions)
        return [
            PlannedTrial(
                trial_id=first_trial_id + number,
                condition=condition,
                foreperiod_ms=self.rng.randrange(100, 400, 10),
            )
            for number, condition in enumerate(conditions)
        ]

    # -- one trial -------------------------------------------------------------

    def run_trial(
        self,
        trial: PlannedTrial,
        *,
        deadline_s: float = 10.0,
        while_running: Callable[[Experiment, PlannedTrial], None] | None = None,
    ) -> TrialRecord:
        """Show the condition, run the graph, hide it, and write down what happened.

        The deadline is the script's own: an executor publishes and assumes
        nobody read it, so only the side waiting can tell "not yet" from
        "never". A trial past it is cancelled and recorded as `SCRIPT_TIMEOUT`,
        and the session carries on.
        """
        started_at = dt.datetime.now(dt.UTC).isoformat()
        patches = [{"name": "foreperiod", "duration_ms": trial.foreperiod_ms}]
        if trial.window_ms is not None:
            patches.append({"name": "window", "duration_ms": trial.window_ms})
        # Before arming, so the subscription opened below misses nothing even
        # though it is opened after.
        before_arming = self.machine.mark()
        self.machine.configure(
            trial.trial_id, trial.graph, cap_ms=int(deadline_s * 2000), patches=patches
        )

        with self.machine.trace_stream(before_arming, deadline_s) as stream:
            on = self.renderer.conditions.set(trial.condition).frame_count
            self.machine.start(trial.trial_id)
            self._in_flight = trial.trial_id
            seen_condition = self.renderer.conditions.list_conditions().active_name

            if while_running is not None:
                while_running(self, trial)

            finished = self._wait_for_end(stream, trial.trial_id, deadline_s)

        if not finished:
            # Past our patience: end it on the device, and keep the fact that it
            # was the script and not the subject that ended it.
            with contextlib.suppress(ExperimentError):
                self.machine.cancel(trial.trial_id)
        off = self.renderer.conditions.set("blank").frame_count
        self._in_flight = None

        entries = self._trace_after_end(trial.trial_id)
        result = next((e for e in entries if e["kind"] == "trial_result"), None)
        visits = [e for e in entries if e["kind"] == "visit"]
        foreperiod = next((v for v in visits if v["state_name"] == "Foreperiod"), None)

        record = TrialRecord(
            trial_id=trial.trial_id,
            condition=trial.condition,
            graph=trial.graph,
            foreperiod_ms=trial.foreperiod_ms,
            outcome=result["outcome"] if finished and result else "SCRIPT_TIMEOUT",
            cancel_reason=None
            if result is None or result.get("cancel_reason") in (None, "NONE")
            else result["cancel_reason"],
            drawn_foreperiod_ms=None if foreperiod is None else foreperiod["drawn_duration_ms"],
            states=[v["state_name"] for v in visits],
            duration_us=None if result is None else result.get("total_duration_microseconds"),
            stimulus_on_frame=on,
            stimulus_off_frame=off,
            condition_seen_during_trial=seen_condition,
            frames_dropped=self._drain_events(),
            started_at=started_at,
        )
        self.records.append(record)
        return record

    def cancel(self) -> None:
        """End the trial in flight, from anywhere -- a hook, a thread, a key.

        A trial that ended between the decision to stop it and the call is not
        an error: a stop button pressed a moment too late has still stopped the
        session, which is what the person pressing it meant.
        """
        if self._in_flight is not None:
            with contextlib.suppress(ExperimentError):
                self.machine.cancel(self._in_flight)

    def _wait_for_end(self, stream, trial_id: int, deadline_s: float) -> bool:
        """Read the subscription until this trial ends, or until we give up.

        **The deadline is the script's**, which is the rule the whole API is
        built on: nothing on the rig waits for a subscriber or holds a trial
        for one, and only the side that knows a trial is in flight can tell
        "not yet" from "never".

        The stream's own gRPC deadline is far longer, so what ends the wait
        here is this clock rather than the transport's.
        """
        from statemachined_client import DaemonRefusedTheRequest

        try:
            for entry in stream:
                if entry.kind == "trial_result" and entry.trial_id == trial_id:
                    return True
        except DaemonRefusedTheRequest as refused:
            # The stream's deadline is this script's patience, so running out
            # of it *is* the answer: the trial did not end in time.
            if refused.status != "deadline_exceeded":
                raise
        return False

    def _trace_after_end(self, trial_id: int, timeout_s: float = 5.0) -> list[dict]:
        """The trial's entries, once its result is in the ring."""
        deadline = time.monotonic() + timeout_s
        while True:
            try:
                entries = self.machine.trace_for(trial_id)
            except ExperimentError:
                entries = []
            if any(e["kind"] == "trial_result" for e in entries) or time.monotonic() > deadline:
                return entries
            time.sleep(0.1)

    def _drain_events(self) -> int:
        """Read what the renderer published since last time; count dropped frames."""
        dropped = 0
        while (event := self.events.receive(timeout_ms=50)) is not None:
            if event.topic == "frame.dropped":
                dropped += max(1, getattr(event.payload, "count", 1))
        return dropped

    # -- a session -------------------------------------------------------------

    def run(
        self,
        plan: list[PlannedTrial],
        *,
        should_stop: Callable[[list[TrialRecord]], bool] | None = None,
        on_trial: Callable[[TrialRecord], None] | None = None,
        deadline_s: float = 10.0,
    ) -> list[TrialRecord]:
        for trial in plan:
            record = self.run_trial(trial, deadline_s=deadline_s)
            if on_trial is not None:
                on_trial(record)
            if should_stop is not None and should_stop(self.records):
                break
        return self.records


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--renderer", default="tcp://127.0.0.1:5555")
    parser.add_argument("--event-port", type=int, default=5556)
    # statemachined's port: the panels, gRPC and gRPC-Web are all on it.
    parser.add_argument("--executor", default="127.0.0.1:8081")
    parser.add_argument("--trials", type=int, default=12)
    parser.add_argument("--first-trial-id", type=int, default=1)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--window-ms", type=int, default=300)
    parser.add_argument(
        "--stop-after-misses",
        type=int,
        default=None,
        help="end the session after this many LATE outcomes in a row",
    )
    parser.add_argument("--out", type=Path, required=True, help="JSON lines, one per trial")
    args = parser.parse_args(argv)

    def too_many_misses(records: list[TrialRecord]) -> bool:
        n = args.stop_after_misses
        return (
            n is not None and len(records) >= n and all(r.outcome == "LATE" for r in records[-n:])
        )

    with (
        Experiment(
            renderer_address=args.renderer,
            event_port=args.event_port,
            executor_address=args.executor,
            window_ms=args.window_ms,
            seed=args.seed,
        ) as experiment,
        args.out.open("w") as out,
    ):
        plan = experiment.plan(args.trials, args.first_trial_id)
        header = {"seed": experiment.seed, "plan": [dataclasses.asdict(t) for t in plan]}
        out.write(json.dumps({"session": header}) + "\n")

        def write(record: TrialRecord) -> None:
            out.write(record.as_json() + "\n")
            out.flush()
            print(f"trial {record.trial_id:>6} {record.condition:<6} {record.outcome}")

        records = experiment.run(plan, should_stop=too_many_misses, on_trial=write)
    print(f"{len(records)} trials, seed {experiment.seed}, written to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
