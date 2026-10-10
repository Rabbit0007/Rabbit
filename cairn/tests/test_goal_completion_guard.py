"""FGS role-boundary tests for goal completion.

Semantic completion is Decide's responsibility.  Execute may only return a Fact
(and optionally one Finding); it cannot smuggle Goal mutations into its result.
The Server separately enforces referential integrity for Decide's completion.
"""

from __future__ import annotations

import pytest

from cairn.dispatcher.contracts import validate_execute_payload


def test_execute_payload_rejects_goal_completion_mutation() -> None:
    with pytest.raises(ValueError, match="unexpected execute data keys"):
        validate_execute_payload(
            {
                "accepted": True,
                "data": {
                    "description": "confirmed incremental fact",
                    "complete": {
                        "goal_id": "g001",
                        "from": ["f001"],
                        "description": "Execute must not complete Goals",
                    },
                },
            }
        )
