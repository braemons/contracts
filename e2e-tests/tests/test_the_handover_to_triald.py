"""The handover between triald and statemachined, with nothing faked in the middle.

**Stage 2, and it used to live in statemachined.** Its own docstring said why:
the expensive fixture is the firmware built for this machine, and that is over
there. The reason it is here instead is the one this repository exists to
state -- a test that needs *two* daemons has no honest home inside either of
them, and the price of pretending otherwise was paid in statemachined's
lockfile. Installing triald there meant a `[dependency-groups] e2e` naming a
private repository, a `tool.uv.sources` pin to its `main`, and a CI job that
fetched it with credentials. When triald began depending on `statemachined`
itself -- for the client it had stopped hand-copying -- the two pins closed a
cycle uv cannot resolve: statemachined's editable copy of itself and the git URL
triald asks for are the same distribution from two sources, and the lock could
not be refreshed at all. The test could then only run against a triald frozen at
whatever commit the lock had captured, which is how it came to fail on `main`
against a bug fixed weeks earlier.

Nothing about the coupling changed; only where the test is run from. statemachined
still knows nothing about triald -- no client, no base URL, no schema, no
outbound call of any kind -- and this file is the assertion of that, made from
outside both.

**What this covers that `test_a_trial_across_the_rig.py` does not.** That file
runs three daemons and is about the join between a renderer's frame clock and a
trial. This one runs two and never starts a display: it is about the handover
alone -- that a graph triald names is one the executor has, that a trial triald
starts is the trial that runs, and that what the executor *publishes* is
something triald can read and act on. Keeping them apart keeps this one runnable
with no vstimd anywhere, which is also the shape of a statemachined-only bench.

**triald as a library, not as a daemon.** `Session` rather than `POST
`Trial/Next`, which is this suite's idiom and the reason triald ships a
wheel: what crosses the boundary here is the executor's HTTP API, and triald's
own is triald's suite's business. The refusals that were HTTP status codes in
the old file are `SessionError` here, and they are the same refusals.

**What is not covered.** The device's input lines: statemachined sends commands
and never drives its own inputs, so a graph waiting for a lever cannot be
answered from this process. These graphs reach their outcome on a timeout
instead, which exercises everything above the trigger. Driving lines needs
wires -- `test_acceptance_with_hardware.py`.

**On trial numbering.** triald numbers its trials from 1 within a session, and
that number is what the executor is keyed by. Against a statemachined this suite
spawned, whose trace begins empty, that is exactly the rig's arrangement. Against
a long-lived one (`--executor`) those ids may already carry results from somebody
else's session, and `outcome_of` would be answering about the wrong trial -- so
these tests always get a daemon of their own -- see the `executor` fixture.
"""

from __future__ import annotations

import contextlib

import pytest
import scenarios
from statemachined_client import DaemonRefusedTheRequest


@pytest.fixture
def executor(request: pytest.FixtureRequest, tmp_path):
    """A statemachined and a device of this test's own, even beside a shared one.

    **Never the attached daemon** (`--executor`), which is what the container
    and a rig both are, for two reasons that are not this file's to fix:

    * triald numbers its trials from 1 in every session, and the executor keys
      its trace by that number for its whole life. Every test here arms a new
      session, so against one daemon the second test's trial 1 already has a
      result and `outcome_of` refuses it -- one trial ends once.
    * a trial that is armed and never started cannot be cancelled (the daemon
      answers `unknown_trial`, the device stays armed and refuses every later
      upload `busy`), and refusing to start is what one test here is about.

    This used to skip instead, which meant the whole file ran nowhere CI looks.
    A device of its own is the packaged `statemachined device` in the container
    and statemachined's in-process bridge from a checkout; either way it is the
    firmware compiled for this host, which is all a handover test needs. On
    `--hardware` it is still a native device: these tests are about the wire
    format between two daemons, and a board is a thing they must not share.
    """
    from conftest import _spawned_executor

    yield from _spawned_executor(request, tmp_path, native=True)


@pytest.fixture
def session():
    """A real triald, in this process, with its demo experiment armed."""
    from triald import Session
    from triald.cli import demo_experiment

    store, config = demo_experiment()
    session = Session(store, config)
    session.arm()
    return session


@pytest.fixture
def machine(executor):
    """triald's client for the executor, over real HTTP, as it ships."""
    from triald.api.statemachine_executor import StateMachineExecutor

    return StateMachineExecutor(executor.address)


def upload_when_the_device_will_take_it(executor, graph: dict, timeout_s: float = 45.0) -> None:
    """`scenarios.upload`, waiting out a device the daemon thinks is already idle.

    **A disagreement between the two, and worth naming rather than hiding.**
    After a cancel the daemon reports `running: false` and answers a second one
    with "no such trial is running", while the device goes on refusing a
    graph-set upload with `busy`, "a trial is armed or running". So a cancel
    ends the daemon's trial and not the device's, and the next test to touch a
    shared rig pays for it with an error about a graph, which is the one thing
    that is not wrong.

    Measured against the container's installed packages: the trial ends on its
    own when its graph does, and everything is fine again afterwards -- so this
    waits rather than failing, and the wait is bounded by the longest graph any
    test here leaves behind. Against the spawned devices `make test-local` uses,
    each test gets its own and this returns on the first attempt.

    Delete it when a cancel reaches the device; the one-attempt path is the
    normal one, so this costs nothing until it is needed.
    """
    import json
    import time

    from statemachined_client import DaemonRefusedTheRequest

    deadline = time.monotonic() + timeout_s
    while True:
        executor.client.write_graph(graph["name"], json.dumps(graph))
        try:
            executor.client.upload_graph_set([graph["name"]])
            return
        except DaemonRefusedTheRequest as refused:
            # `busy` is the **device's own word**, passed up rather than
            # paraphrased — which is the whole reason this can be told apart
            # from a graph that will not compile.
            if refused.error != "busy" or time.monotonic() > deadline:
                raise
        time.sleep(0.5)


def run_one_trial(executor, session, machine, *, outcome="HIT", graph="handover"):
    """The rig's loop, in the order a rig runs it.

    triald decides which trial this is and hands out its number; the executor is
    armed for that number and nothing else; the executor publishes; triald reads
    what it published and turns it into an outcome it records against itself.
    """
    from triald.executor import TrialConfiguration

    upload_when_the_device_will_take_it(
        executor, scenarios.timed_graph(graph, milliseconds=40, outcome=outcome)
    )

    spec = session.next_trial()
    trial_id = spec.trial_number

    # **The mark is taken before the trial is armed**, and the subscription
    # opened afterwards starts from it. A 40 ms trial ends before a late
    # subscriber is watching, and a subscription that instead replayed the ring
    # from the beginning would hand back the *previous* trial's result -- which
    # is exactly what happened the first time this ran over the new transport.
    before_arming = executor.mark()

    machine.configure(
        TrialConfiguration(
            trial_id=trial_id,
            statemachine_graph=spec.statemachine_graph or graph,
            cap_milliseconds=5000,
        )
    )
    machine.start(trial_id)

    # Observed, not waited on by the executor: the daemon published and moved on,
    # and this side is the one holding a deadline.
    with executor.trace_stream(since=before_arming) as messages:
        finished = next(machine.finished_trials(messages))
    assert finished == trial_id

    report = machine.outcome_of(trial_id)
    return session.report_outcome(report, trial_id=trial_id)


# ── The whole loop ────────────────────────────────────────────────────────────


def test_a_trial_triald_started_comes_back_in_trialds_record(executor, session, machine):
    record = run_one_trial(executor, session, machine, outcome="HIT")

    assert record.report.outcome.name == "HIT"
    assert record.report.outcome.value == 1
    assert record.accepted is True
    assert record.refusal_reason is None

    totals = session.state().totals
    assert totals.total == 1
    assert totals.hits == 1
    assert totals.accepted == 1


def test_the_outcome_the_device_named_is_the_one_triald_counts(executor, session, machine):
    # The device treats the code as opaque and triald owns what it means. This
    # is the assertion that the eleven .tdr outcomes are spelled the same on
    # both sides -- the name is what crosses, not the number.
    record = run_one_trial(executor, session, machine, outcome="UNEXPECTED_START_SIGNAL")
    assert record.report.outcome.name == "UNEXPECTED_START_SIGNAL"
    assert record.report.outcome.value == 8


def test_the_record_says_the_outcome_was_not_simulated(executor, session, machine):
    # `triald sim` produces outcomes with simulated: true, and a rig whose
    # records could not be told apart from a simulator's is a rig whose data
    # cannot be trusted.
    assert run_one_trial(executor, session, machine).report.simulated is False


def test_the_veto_fields_nobody_here_can_observe_are_left_alone(executor, session, machine):
    # precise_fixation comes from an eye monitor, frame_loss from vstimd, and
    # either can veto acceptance on its own. Neither daemon in this test has
    # heard of either -- and this file, unlike its neighbour, does not even
    # start a renderer.
    record = run_one_trial(executor, session, machine)
    assert record.report.precise_fixation is True
    assert record.report.frame_loss is None


def test_two_trials_keep_their_own_numbers(executor, session, machine):
    first = run_one_trial(executor, session, machine, graph="handover-one")
    second = run_one_trial(executor, session, machine, graph="handover-two")

    assert second.spec.trial_number == first.spec.trial_number + 1
    assert session.state().totals.total == 2


# ── The refusals ──────────────────────────────────────────────────────────────


def test_a_graph_the_executor_does_not_have_is_refused_by_name(executor, session, machine):
    """Why triald validates nothing about a graph name.

    triald holds no graphs and has no opinion about where one sits in the
    executor's store. It can afford that precisely because the executor refuses
    a name it does not have, loudly and by name, instead of running whatever it
    had loaded.
    """
    from triald.executor import ExecutorError, TrialConfiguration

    upload_when_the_device_will_take_it(executor, scenarios.timed_graph("present"))
    trial_id = session.next_trial().trial_number

    with pytest.raises(ExecutorError) as refused:
        machine.configure(
            TrialConfiguration(trial_id=trial_id, statemachine_graph="not-in-the-set")
        )
    assert "not-in-the-set" in str(refused.value)
    assert "graph_not_in_set" in str(refused.value)


def test_starting_a_trial_the_executor_was_not_armed_for_is_refused(executor, machine):
    # The local form of StartPermittable(): no trial may start that the executor
    # was not confirmed configured for, or a session runs the previous trial's
    # parameters without anybody noticing.
    from triald.executor import ExecutorError, TrialConfiguration

    upload_when_the_device_will_take_it(executor, scenarios.timed_graph("armed"))
    armed_for = scenarios.unique_trial_id()
    machine.configure(TrialConfiguration(trial_id=armed_for, statemachine_graph="armed"))

    with pytest.raises(ExecutorError):
        machine.start(armed_for + 1)

    # The executor is still armed for the trial nobody started. Leave it idle.
    # Tolerated, because "there is nothing to cancel" is a fine outcome for a
    # teardown: the rpc refuses `unknown_trial` where the route answered a body
    # nobody read, which is the better behaviour and a caller's to expect.
    with contextlib.suppress(DaemonRefusedTheRequest):
        executor.client.cancel_trial(armed_for)


def test_an_outcome_for_the_wrong_trial_is_refused_by_triald(executor, session, machine):
    """The failure the whole handshake exists to prevent.

    A report attributed to the trial *after* the one it belongs to is how a rig
    quietly mislabels a dataset. triald refuses rather than guessing -- it
    cannot tell which of the two is the truth, so it takes neither.
    """
    from triald.outcomes import OutcomeReport, TrialOutcome
    from triald.session import SessionError

    run_one_trial(executor, session, machine)
    in_flight = session.next_trial().trial_number

    with pytest.raises(SessionError) as refused:
        session.report_outcome(
            OutcomeReport(outcome=TrialOutcome.HIT), trial_id=in_flight + 7
        )
    assert str(in_flight) in str(refused.value)

    state = session.state()
    assert state.current.trial_number == in_flight
    assert state.totals.total == 1


def test_asking_for_a_trial_the_executor_never_ran_is_refused_by_number(machine):
    """A trial nobody ran has no outcome, and saying so is not an error path.

    triald asks by id precisely so that a dropped notification is recoverable;
    the answer to "there is nothing under this id" therefore has to be a refusal
    a session can log and carry on from, naming the trial it was about.
    """
    from triald.executor import ExecutorError

    never_ran = scenarios.unique_trial_id()
    with pytest.raises(ExecutorError) as refused:
        machine.outcome_of(never_ran)

    assert str(never_ran) in str(refused.value)


def test_asking_about_a_trial_nobody_ran_says_so_in_triald_s_own_words(machine):
    """Which words a refusal arrives in, and whose fact it is.

    triald once held a copy of the executor's refusal shape; it uses
    `statemachined-client` now, so an executor's refusal reaches the session log
    in the code statemachined documents rather than in triald's paraphrase of
    it. That is the property this test was written for.

    **What it asserts changed with the interface, and the change is the
    interesting part.** Over HTTP the executor answered 404 `no_trace_for_trial`
    for a trial it had never heard of, and this test looked for that word. It
    is an empty list now: the trace ring cannot tell "no such trial" from "a
    trial whose entries have been overwritten" -- both are the same observation
    -- so the refusal was claiming to know which. With nothing refused there is
    nothing to pass up, and the error is triald's own, about triald's rule:
    **a trial with no result is not an outcome.**

    So the executor's words are still tested, by
    `test_a_graph_the_executor_does_not_have_is_refused_by_name`, where there
    really is a definite negative answer to give.
    """
    from triald.executor import ExecutorError

    with pytest.raises(ExecutorError) as refused:
        machine.outcome_of(scenarios.unique_trial_id())

    assert "published no result" in str(refused.value)
