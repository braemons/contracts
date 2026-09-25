"""Interaction D: triald names a zone set, and mousewheeld arms it.

The trial type carries `mousewheel_zone_set`; the session latches it onto the
trial; `MousewheelZoneArming` hands it to a real mousewheeld, which compiles it
against its calibration and uploads it to its (simulated) board. What is
checked is what mousewheeld then says is armed, read back through its own
client, not what triald believes it sent.

**What is not here is E**, the path back into the record: mousewheeld has no
marks or path ring yet (its `dev/PLAN.md` M2).
"""

from __future__ import annotations

import pytest
from triald.api.mousewheel_zone_arming import MousewheelZoneArming
from triald.executor import ExecutorError
from triald.session import Session, SessionConfig
from triald.trialtypes import TrialType, TrialTypeSet, TrialTypeStore


def a_session_whose_trials_arm(zone_set: str) -> Session:
    store = TrialTypeStore(
        [
            TrialTypeSet(
                name="corridor",
                trial_types=[
                    TrialType(name="run", trials_per_round=1, mousewheel_zone_set=zone_set)
                ],
            )
        ]
    )
    session = Session(store, SessionConfig(initial_set="corridor", rounds=1, seed=0))
    session.arm()
    return session


def test_the_trial_types_zone_set_is_what_mousewheeld_arms(wheel):
    spec = a_session_whose_trials_arm("fixed").next_trial()
    assert spec.mousewheel_zone_set == "fixed"

    arming = MousewheelZoneArming(wheel["address"])
    arming.arm_for_trial(spec.trial_number, spec.mousewheel_zone_set)

    armed = wheel["client"].armed()
    assert armed.zone_set == "fixed"
    assert armed.label == f"trial {spec.trial_number}"
    assert [zone.name for zone in armed.zones] == ["goal"]


def test_a_trial_with_no_zone_set_leaves_nothing_armed(wheel):
    arming = MousewheelZoneArming(wheel["address"])
    arming.arm_for_trial(1, "fixed")
    arming.arm_for_trial(2, "")
    assert wheel["client"].armed().zone_set is None


def test_a_set_mousewheeld_cannot_arm_stops_the_trial_with_a_reason(wheel):
    arming = MousewheelZoneArming(wheel["address"])
    with pytest.raises(ExecutorError, match="'nowhere' for trial 3"):
        arming.arm_for_trial(3, "nowhere")
    # A `$goal_cm` nobody supplied is refused, never armed as zero.
    with pytest.raises(ExecutorError, match="goal_cm"):
        arming.arm_for_trial(4, "parameterised")
    assert wheel["client"].armed().zone_set is None
