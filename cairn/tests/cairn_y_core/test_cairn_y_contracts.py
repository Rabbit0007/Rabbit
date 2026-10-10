from __future__ import annotations

import pytest

from cairn.dispatcher.contracts import validate_decide_payload, validate_execute_payload


def test_decide_accepts_combined_graph_mutations() -> None:
    kind, data = validate_decide_payload(
        {
            "accepted": True,
            "data": {
                "steps": [{"from": ["origin"], "description": "inspect", "goal_id": "g001", "priority": 2}],
                "step_updates": [{"id": "s002", "action": "abandon"}],
                "sub_goals": [{"action": "create", "description": "establish state", "parent_goal_id": "g001"}],
            },
        },
        open_steps_empty=False,
        max_steps=3,
    )
    assert kind == "mutations"
    assert set(data) == {"steps", "step_updates", "sub_goals"}


@pytest.mark.parametrize(
    "payload",
    [
        {"accepted": True, "data": {"complete": {"goal_id": "g001", "from": "f001", "description": "done"}}},
        {"accepted": True, "data": {"complete": {"goal_id": "g001", "from": ["f001"], "description": "done"}, "steps": []}},
        {"accepted": True, "data": {"steps": [{"from": [], "description": "work"}]}},
        {"accepted": True, "data": {"step_updates": [{"id": "s001", "action": "unknown"}]}},
        {"accepted": True, "data": {"sub_goals": [{"action": "complete", "id": "g002"}]}},
    ],
)
def test_decide_rejects_ambiguous_or_malformed_payloads(payload: dict) -> None:
    with pytest.raises(ValueError):
        validate_decide_payload(payload, open_steps_empty=False, max_steps=3)


def test_decide_requires_fact_producing_work_when_no_step_is_open() -> None:
    with pytest.raises(ValueError, match="step is required"):
        validate_decide_payload(
            {"accepted": True, "data": {"sub_goals": [{"action": "create", "description": "phase"}]}},
            open_steps_empty=True,
            max_steps=3,
        )


def test_execute_validates_structured_finding() -> None:
    kind, data = validate_execute_payload(
        {
            "accepted": True,
            "data": {
                "description": "confirmed result",
                "finding": {"title": "issue", "description": "evidence", "severity": "high"},
            },
        }
    )
    assert kind == "fact"
    assert data["finding"]["severity"] == "high"

    with pytest.raises(ValueError):
        validate_execute_payload(
            {"accepted": True, "data": {"description": "result", "finding": {"title": "issue", "severity": "urgent"}}}
        )
