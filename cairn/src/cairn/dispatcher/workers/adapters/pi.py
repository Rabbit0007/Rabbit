from __future__ import annotations

import json
from pathlib import PurePosixPath
from typing import Any

from cairn.dispatcher.config import WorkerConfig
from cairn.dispatcher.workers.base import DriverResult, WorkerDriver


class PiDriver(WorkerDriver):
    type_name = "pi"

    def build_healthcheck(self, worker: WorkerConfig) -> list[str]:
        env = worker.env
        return self._wrap_with_models(
            worker,
            [
                "--provider",
                "cairn",
                "--model",
                env["PI_MODEL"],
                "--mode",
                "json",
                "--session-dir",
                self._session_dir(worker),
                "--no-session",
                "--no-tools",
                "-p",
                "Reply with exactly pong.",
            ],
            enable_tools=False,
        )

    def build_execute(self, worker: WorkerConfig, prompt: str, session: str | None) -> DriverResult:
        env = worker.env
        argv = [
            "--provider",
            "cairn",
            "--model",
            env["PI_MODEL"],
            "--mode",
            "json",
            "--session-dir",
            self._session_dir(worker),
        ]
        if session:
            argv.extend(["--session", session])
        argv.extend(["-p", prompt])
        return DriverResult(argv=self._wrap_with_models(worker, argv), session=session)

    def build_conclude(self, worker: WorkerConfig, prompt: str, session: str) -> list[str]:
        env = worker.env
        argv = [
            "--provider",
            "cairn",
            "--model",
            env["PI_MODEL"],
            "--mode",
            "json",
            "--session-dir",
            self._session_dir(worker),
            "--session",
            session,
            "-p",
            prompt,
        ]
        return self._wrap_with_models(worker, argv)

    def extract_session(self, session: str | None, stdout: str, stderr: str) -> str | None:
        if session:
            return session
        for event in self._iter_events(stdout):
            if event.get("type") != "session":
                continue
            session_id = event.get("id")
            if isinstance(session_id, str) and session_id:
                return session_id
        return None

    def extract_response_text(self, stdout: str, stderr: str) -> str:
        events = self._iter_events(stdout)
        error = self._event_error(events)
        if error is not None:
            raise ValueError(error)
        assistant_message = self._assistant_message(events)
        if assistant_message is None:
            raise ValueError("pi output did not contain an assistant response")
        text = self._message_text(assistant_message)
        if not text:
            raise ValueError("pi assistant response was empty")
        return text

    def healthcheck_error(self, returncode: int, stdout: str, stderr: str) -> str | None:
        base_error = super().healthcheck_error(returncode, stdout, stderr)
        if base_error is not None:
            return base_error
        events = self._iter_events(stdout)
        event_error = self._event_error(events)
        if event_error is not None:
            return event_error
        assistant_message = self._assistant_message(events)
        if assistant_message is None:
            return "pi healthcheck did not contain an assistant response"
        response = self._message_text(assistant_message).strip().lower().rstrip(".!")
        if response != "pong":
            return f"pi healthcheck expected pong, got: {response or '<empty>'}"
        return None

    def _wrap_with_models(self, worker: WorkerConfig, pi_argv: list[str], *, enable_tools: bool = True) -> list[str]:
        script = (
            'agent_dir="$1"\n'
            'models_json="$2"\n'
            "shift 2\n"
            'mkdir -p "$agent_dir"\n'
            'mkdir -p "$agent_dir/sessions"\n'
            'printf "%s" "$models_json" > "$agent_dir/models.json"\n'
            'exec env PI_CODING_AGENT_DIR="$agent_dir" pi "$@"\n'
        )
        argv = [
            "--no-extensions",
            "--no-skills",
            "--no-prompt-templates",
            "--no-themes",
            "--no-context-files",
        ]
        if enable_tools:
            argv.extend(["--tools", "read,write,edit,bash,grep,find,ls"])
        return [
            "/bin/sh",
            "-lc",
            script,
            "--",
            self._agent_dir(worker),
            self._models_json(worker),
            *argv,
            *pi_argv,
        ]

    @staticmethod
    def _agent_dir(worker: WorkerConfig) -> str:
        return str(PurePosixPath("/tmp/cairn-pi") / worker.name)

    @staticmethod
    def _session_dir(worker: WorkerConfig) -> str:
        return str(PurePosixPath(PiDriver._agent_dir(worker)) / "sessions")

    @staticmethod
    def _iter_events(stdout: str) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for line in stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                events.append(payload)
        return events

    @staticmethod
    def _assistant_message(events: list[dict[str, Any]]) -> dict[str, Any] | None:
        assistant_message: dict[str, Any] | None = None
        for event in events:
            event_type = event.get("type")
            if event_type == "turn_end":
                message = event.get("message")
                if isinstance(message, dict) and message.get("role") == "assistant":
                    assistant_message = message
            elif event_type == "agent_end":
                messages = event.get("messages")
                if isinstance(messages, list):
                    for message in reversed(messages):
                        if isinstance(message, dict) and message.get("role") == "assistant":
                            assistant_message = message
                            break
        return assistant_message

    @staticmethod
    def _event_error(events: list[dict[str, Any]]) -> str | None:
        for event in reversed(events):
            if event.get("type") != "agent_end":
                continue
            messages = event.get("messages")
            if not isinstance(messages, list):
                continue
            for message in reversed(messages):
                if not isinstance(message, dict) or message.get("role") != "assistant":
                    continue
                if message.get("stopReason") != "error":
                    continue
                error_message = message.get("errorMessage")
                if isinstance(error_message, str) and error_message.strip():
                    return error_message.strip()
                return "pi assistant stopped with an error"
        return None

    @staticmethod
    def _message_text(message: dict[str, Any]) -> str:
        content = message.get("content")
        if not isinstance(content, list):
            return ""
        parts: list[str] = []
        for item in content:
            if not isinstance(item, dict) or item.get("type") != "text":
                continue
            text = item.get("text")
            if isinstance(text, str) and text:
                parts.append(text)
        return "\n".join(parts).strip()

    @staticmethod
    def _models_json(worker: WorkerConfig) -> str:
        env = worker.env
        model: dict[str, Any] = {
            "id": env["PI_MODEL"],
            "name": env["PI_MODEL"],
        }
        context_window = env.get("PI_MODEL_CONTEXT_WINDOW")
        if context_window:
            model["contextWindow"] = int(context_window)

        provider: dict[str, Any] = {
            "baseUrl": env["PI_BASE_URL"],
            "api": env["PI_PROVIDER_API"],
            "apiKey": env["PI_API_KEY"],
            "models": [model],
        }
        payload = {"providers": {"cairn": provider}}
        return json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
