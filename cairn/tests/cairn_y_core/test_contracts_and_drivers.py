from __future__ import annotations

import json

import pytest

from cairn.dispatcher.contracts import (
    parse_json_output,
    validate_decide_payload,
    validate_execute_payload,
)
from cairn.dispatcher.runtime.process import ManagedProcess
from cairn.dispatcher.workers.adapters.pi import PiDriver


def test_parse_json_output_extracts_object_from_markdown_noise() -> None:
    assert parse_json_output('result:\n```json\n{"accepted": true, "data": {}}\n```') == {
        "accepted": True,
        "data": {},
    }


def test_parse_json_output_prefers_final_fenced_payload_over_prose_object() -> None:
    output = '''The current policy is {} and the goal remains active.
```json
{"accepted": true, "data": {"steps": [{"from": ["f001"], "description": "continue"}]}}
```'''
    assert parse_json_output(output) == {
        "accepted": True,
        "data": {"steps": [{"from": ["f001"], "description": "continue"}]},
    }


def test_decide_payload_rejects_more_than_configured_steps() -> None:
    with pytest.raises(ValueError, match="at most 1"):
        validate_decide_payload(
            {
                "accepted": True,
                "data": {
                    "steps": [
                        {"from": ["f001"], "description": "one"},
                        {"from": ["f001"], "description": "two"},
                    ]
                },
            },
            open_steps_empty=True,
            max_steps=1,
        )


def test_decide_payload_requires_step_when_none_are_open() -> None:
    with pytest.raises(ValueError, match="at least one step"):
        validate_decide_payload(
            {"accepted": True, "data": {}},
            open_steps_empty=True,
            max_steps=3,
        )


def test_execute_payload_rejects_planning_text() -> None:
    with pytest.raises(ValueError):
        validate_execute_payload(parse_json_output("Need inspect files and keep working."))


def test_pi_driver_extracts_session_and_last_assistant_text() -> None:
    driver = PiDriver()
    stdout = "\n".join(
        [
            json.dumps({"type": "session", "id": "session-123"}),
            json.dumps(
                {
                    "type": "turn_end",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": '{"accepted":true,"data":{}}'}],
                    },
                }
            ),
        ]
    )

    assert driver.extract_session(None, stdout, "") == "session-123"
    assert driver.extract_response_text(stdout, "") == '{"accepted":true,"data":{}}'


def test_pi_driver_extracts_assistant_text_from_message_end() -> None:
    driver = PiDriver()
    stdout = "\n".join(
        [
            json.dumps(
                {
                    "type": "message_end",
                    "message": {
                        "role": "assistant",
                        "content": [
                            {"type": "thinking", "thinking": "done"},
                            {"type": "text", "text": '{"accepted":true,"data":{"description":"ok"}}'},
                        ],
                    },
                }
            ),
            json.dumps({"type": "agent_end", "messages": []}),
        ]
    )

    assert driver.extract_response_text(stdout, "") == (
        '{"accepted":true,"data":{"description":"ok"}}'
    )


def test_pi_driver_surfaces_assistant_provider_error_instead_of_session_header() -> None:
    driver = PiDriver()
    stdout = "\n".join(
        [
            json.dumps({"type": "session", "id": "session-123", "cwd": "/tmp/project"}),
            json.dumps(
                {
                    "type": "message_end",
                    "message": {
                        "role": "assistant",
                        "content": [],
                        "stopReason": "error",
                        "errorMessage": "400 Budget has been exceeded",
                    },
                }
            ),
        ]
    )

    with pytest.raises(RuntimeError, match="Budget has been exceeded"):
        driver.extract_response_text(stdout, "")


def test_pi_driver_rejects_json_stream_without_assistant_message() -> None:
    driver = PiDriver()
    stdout = json.dumps({"type": "session", "id": "session-123", "cwd": "/tmp/project"})

    with pytest.raises(ValueError, match="no assistant message"):
        driver.extract_response_text(stdout, "")


def test_close_stream_closes_response_even_when_stream_close_fails() -> None:
    class Response:
        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    class Stream:
        def __init__(self) -> None:
            self._response = Response()

        def close(self) -> None:
            raise ValueError("already closed")

    stream = Stream()
    ManagedProcess._close_stream(stream)

    assert stream._response.closed
