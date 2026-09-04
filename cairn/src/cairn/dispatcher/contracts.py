from __future__ import annotations

from typing import Any

from cairn.dispatcher.output_parser import extract_json_object


def parse_json_output(stdout: str) -> dict[str, Any]:
    return extract_json_object(stdout)


def _unwrap_wrapped_payload(payload: dict[str, Any]) -> tuple[bool | None, dict[str, Any] | None]:
    accepted = payload.get("accepted")
    if accepted is False:
        return False, None
    if accepted is True:
        data = payload.get("data")
        if not isinstance(data, dict):
            raise ValueError("data must be an object")
        return True, data
    return None, None


# ── Decide payload validation ──────────────────────────────────────────

def _non_empty_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _integer(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be an integer")
    return value


def _fact_ids(value: Any, field: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{field} must be a non-empty array")
    result = [_non_empty_string(item, f"{field}[]") for item in value]
    if len(result) != len(set(result)):
        raise ValueError(f"{field} must not contain duplicate fact ids")
    return result


def _validate_complete(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"goal_id", "from", "description"}:
        raise ValueError("complete must contain only goal_id, from, and description")
    return {
        "goal_id": _non_empty_string(value["goal_id"], "complete.goal_id"),
        "from": _fact_ids(value["from"], "complete.from"),
        "description": _non_empty_string(value["description"], "complete.description"),
    }


def _validate_steps(value: Any, max_steps: int) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ValueError("steps must be an array")
    if len(value) > max_steps:
        raise ValueError(f"steps must contain at most {max_steps} entries")
    result: list[dict[str, Any]] = []
    for index, step in enumerate(value):
        if not isinstance(step, dict):
            raise ValueError(f"steps[{index}] must be an object")
        extra = set(step) - {"from", "description", "goal_id", "priority"}
        if extra or "from" not in step or "description" not in step:
            raise ValueError(f"invalid step at index {index}")
        item: dict[str, Any] = {
            "from": _fact_ids(step["from"], f"steps[{index}].from"),
            "description": _non_empty_string(step["description"], f"steps[{index}].description"),
        }
        goal_id = step.get("goal_id")
        if goal_id is not None:
            item["goal_id"] = _non_empty_string(goal_id, f"steps[{index}].goal_id")
        item["priority"] = _integer(step.get("priority", 0), f"steps[{index}].priority")
        result.append(item)
    return result


def _validate_step_updates(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ValueError("step_updates must be an array")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, update in enumerate(value):
        if not isinstance(update, dict):
            raise ValueError(f"step_updates[{index}] must be an object")
        step_id = _non_empty_string(update.get("id"), f"step_updates[{index}].id")
        if step_id in seen:
            raise ValueError("step_updates must not update the same step twice")
        seen.add(step_id)
        if update.get("action") == "abandon":
            if set(update) != {"id", "action"}:
                raise ValueError(f"invalid abandon update at index {index}")
            result.append({"id": step_id, "action": "abandon"})
            continue
        if set(update) != {"id", "priority"}:
            raise ValueError(f"invalid priority update at index {index}")
        result.append({"id": step_id, "priority": _integer(update["priority"], f"step_updates[{index}].priority")})
    return result


def _validate_sub_goals(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ValueError("sub_goals must be an array")
    result: list[dict[str, Any]] = []
    for index, sub_goal in enumerate(value):
        if not isinstance(sub_goal, dict):
            raise ValueError(f"sub_goals[{index}] must be an object")
        action = sub_goal.get("action")
        if action == "create":
            extra = set(sub_goal) - {"action", "description", "parent_goal_id", "priority"}
            if extra or "description" not in sub_goal:
                raise ValueError(f"invalid sub-goal create at index {index}")
            item: dict[str, Any] = {
                "action": "create",
                "description": _non_empty_string(sub_goal["description"], f"sub_goals[{index}].description"),
                "priority": _integer(sub_goal.get("priority", 0), f"sub_goals[{index}].priority"),
            }
            parent = sub_goal.get("parent_goal_id")
            if parent is not None:
                item["parent_goal_id"] = _non_empty_string(parent, f"sub_goals[{index}].parent_goal_id")
            result.append(item)
            continue
        if action == "delete" and set(sub_goal) == {"action", "id"}:
            result.append({"action": "delete", "id": _non_empty_string(sub_goal["id"], f"sub_goals[{index}].id")})
            continue
        raise ValueError(f"invalid sub-goal action at index {index}")
    return result

def validate_decide_payload(
    payload: dict[str, Any], open_steps_empty: bool, max_steps: int,
) -> tuple[str, dict[str, Any] | None]:
    accepted, data = _unwrap_wrapped_payload(payload)
    if accepted is False:
        return "rejected", None
    if accepted is None:
        if not isinstance(payload, dict):
            raise ValueError("accepted must be true or false")
        data = payload

    if not isinstance(data, dict):
        raise ValueError("accepted must be true or false")

    allowed = {"complete", "steps", "step_updates", "sub_goals"}
    extra = set(data) - allowed
    if extra:
        raise ValueError(f"unexpected decide data keys: {', '.join(sorted(extra))}")

    complete = data.get("complete")
    if complete is not None:
        if set(data) != {"complete"}:
            raise ValueError("complete cannot coexist with graph mutations")
        return "complete", _validate_complete(complete)

    mutations: dict[str, Any] = {}
    if "steps" in data:
        mutations["steps"] = _validate_steps(data["steps"], max_steps)
    if "step_updates" in data:
        mutations["step_updates"] = _validate_step_updates(data["step_updates"])
    if "sub_goals" in data:
        mutations["sub_goals"] = _validate_sub_goals(data["sub_goals"])

    if open_steps_empty and not mutations.get("steps"):
        raise ValueError("at least one step is required when open_steps is empty")
    if any(mutations.values()):
        populated = [key for key, value in mutations.items() if value]
        if len(populated) == 1:
            return populated[0], {populated[0]: mutations[populated[0]]}
        return "mutations", mutations

    return "noop", None


# ── Execute payload validation ─────────────────────────────────────────

def validate_execute_payload(payload: dict[str, Any]) -> tuple[str, dict[str, Any] | None]:
    accepted, data = _unwrap_wrapped_payload(payload)
    if accepted is False:
        return "rejected", None
    if accepted is None:
        if not isinstance(payload, dict):
            raise ValueError("accepted must be true or false")
        data = payload

    if not isinstance(data, dict):
        raise ValueError("accepted must be true or false")

    extra = set(data) - {"description", "finding"}
    if extra:
        raise ValueError(
            f"unexpected execute data keys: {', '.join(sorted(extra))}"
        )

    description = data.get("description")
    if not isinstance(description, str) or not description.strip():
        raise ValueError("description is required")

    result = {"description": description.strip()}

    finding = data.get("finding")
    if finding is not None:
        if not isinstance(finding, dict):
            raise ValueError("finding must be an object")
        extra = set(finding) - {"title", "description", "severity", "kind", "data"}
        if extra or "title" not in finding or "description" not in finding:
            raise ValueError("finding must contain title and description")
        severity = finding.get("severity", "info")
        if severity not in {"critical", "high", "medium", "low", "info"}:
            raise ValueError("finding.severity is invalid")
        structured_data = finding.get("data", {})
        if not isinstance(structured_data, dict):
            raise ValueError("finding.data must be an object")
        result["finding"] = {
            "title": _non_empty_string(finding["title"], "finding.title"),
            "description": _non_empty_string(finding["description"], "finding.description"),
            "severity": severity,
            "kind": _non_empty_string(finding.get("kind", "finding"), "finding.kind"),
            "data": structured_data,
        }

    return "fact", result
