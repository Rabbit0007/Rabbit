from __future__ import annotations

import json
import os
from typing import Any

from cairn.dispatcher.config import WorkerConfig
from cairn.dispatcher.workers.base import DriverResult, WorkerDriver
from cairn.dispatcher.workers.health import HealthResult, http_ping, proxies_from_env


class PiDriver(WorkerDriver):
    type_name = "pi"

    def __init__(self, local: bool = False):
        self.local = local

    def local_binary(self, worker: WorkerConfig | None = None) -> str | None:
        if worker is not None:
            configured = worker.env.get("PI_CLI")
            if configured:
                return configured
        return "pi"

    def check_health(self, worker: WorkerConfig, *, timeout: float) -> HealthResult:
        env = worker.env
        base = env["PI_BASE_URL"].rstrip("/")
        model = env["PI_MODEL"]
        api = env["PI_PROVIDER_API"]
        proxies = proxies_from_env(env)
        headers = {"Authorization": f"Bearer {env['PI_API_KEY']}", "content-type": "application/json"}
        if "anthropic" in api:
            return http_ping(
                f"{base}/v1/messages",
                headers={**headers, "anthropic-version": "2023-06-01"},
                json_body={"model": model, "max_tokens": 10, "messages": [{"role": "user", "content": "ping"}]},
                timeout=timeout,
                proxies=proxies,
            )
        if "responses" in api:
            return http_ping(
                f"{base}/responses",
                headers=headers,
                json_body={"model": model, "input": [{"role": "user", "content": "ping"}], "stream": False},
                timeout=timeout,
                proxies=proxies,
            )
        return http_ping(
            f"{base}/chat/completions",
            headers=headers,
            json_body={"model": model, "max_tokens": 10, "messages": [{"role": "user", "content": "ping"}]},
            timeout=timeout,
            proxies=proxies,
        )

    def describe_health(self, worker: WorkerConfig) -> str:
        env = worker.env
        return f"POST {env['PI_BASE_URL']} (api={env['PI_PROVIDER_API']}, model={env['PI_MODEL']})"

    def _has_custom_provider(self, worker: WorkerConfig) -> bool:
        env = worker.env
        return all(k in env for k in ("PI_MODEL", "PI_BASE_URL", "PI_API_KEY", "PI_PROVIDER_API"))

    def build_execute(self, worker: WorkerConfig, prompt: str, session: str | None) -> DriverResult:
        if self.local and self._has_custom_provider(worker):
            return DriverResult(argv=self._custom_local_argv(worker, prompt, session, tools="read,write,edit,bash,grep,find,ls"), session=session)
        if self.local:
            return DriverResult(argv=self._local_argv(worker, prompt, session, tools="read,write,edit,bash,grep,find,ls"), session=session)
        return DriverResult(argv=self._container_argv(worker, prompt, session, tools="read,write,edit,bash,grep,find,ls"), session=session)

    def build_decide(self, worker: WorkerConfig, prompt: str, session: str | None) -> DriverResult:
        if self.local and self._has_custom_provider(worker):
            return DriverResult(argv=self._custom_local_argv(worker, prompt, session, tools="read"), session=session)
        if self.local:
            return DriverResult(argv=self._local_argv(worker, prompt, session, tools="read"), session=session)
        return DriverResult(argv=self._container_argv(worker, prompt, session, tools="read"), session=session)

    def build_conclude(self, worker: WorkerConfig, prompt: str, session: str) -> list[str]:
        if self.local and self._has_custom_provider(worker):
            return self._custom_local_argv(worker, prompt, session, tools="read,write,edit,bash,grep,find,ls")
        if self.local:
            return self._local_argv(worker, prompt, session, tools="read,write,edit,bash,grep,find,ls")
        return self._container_argv(worker, prompt, session, tools="read,write,edit,bash,grep,find,ls")

    def _custom_local_argv(self, worker: WorkerConfig, prompt: str, session: str | None, *, tools: str) -> list[str]:
        """Local mode with custom provider: write models.json, run pi with --provider cairn."""
        env = worker.env
        agent_dir = self._agent_dir(worker)
        session_dir = self._session_dir(worker)
        self._ensure_private_directory(agent_dir)
        self._ensure_private_directory(session_dir)

        with open(os.path.join(agent_dir, "models.json"), "w") as f:
            f.write(self._models_json(worker))
        os.chmod(os.path.join(agent_dir, "models.json"), 0o600)

        argv = [
            self.local_binary(worker) or "pi",
            "--provider", "cairn",
            "--model", env["PI_MODEL"],
            "--mode", "json",
            "--session-dir", session_dir,
            "--no-extensions", "--no-skills", "--no-prompt-templates",
            "--no-themes", "--no-context-files",
            "--tools", tools,
        ]
        if session:
            argv.extend(["--session", session])
        argv.extend(["-p", prompt])

        # Credentials are already passed in the subprocess environment by the
        # runtime. Keeping them out of argv prevents exposure through process listings.
        return ["/usr/bin/env", f"PI_CODING_AGENT_DIR={agent_dir}", *argv]

    def _local_argv(self, worker: WorkerConfig, prompt: str, session: str | None, *, tools: str) -> list[str]:
        """Local mode without custom provider: use pi's own host configuration."""
        session_dir = self._session_dir(worker)
        self._ensure_private_directory(session_dir)
        pi_argv = [
            "--mode", "json",
            "--session-dir", session_dir,
            "--no-extensions", "--no-skills", "--no-prompt-templates",
            "--no-themes", "--no-context-files",
            "--tools", tools,
        ]
        if session:
            pi_argv.extend(["--session", session])
        pi_argv.extend(["-p", prompt])
        return [self.local_binary(worker) or "pi", *pi_argv]

    def _container_argv(self, worker: WorkerConfig, prompt: str, session: str | None, *, tools: str) -> list[str]:
        """Container mode: inject models.json via shell wrapper."""
        env = worker.env
        argv = [
            "--provider", "cairn",
            "--model", env["PI_MODEL"],
            "--mode", "json",
            "--session-dir", self._session_dir(worker),
        ]
        if session:
            argv.extend(["--session", session])
        argv.extend(["-p", prompt])
        return self._wrap_with_models(worker, argv, tools=tools)

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
        assistant_message: dict[str, Any] | None = None
        for event in self._iter_events(stdout):
            event_type = event.get("type")
            if event_type in {"message", "message_end", "turn_end"}:
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
        if assistant_message is None:
            detail = stderr.strip()
            suffix = f": {detail[:500]}" if detail else ""
            raise ValueError(f"Pi returned no assistant message{suffix}")

        stop_reason = assistant_message.get("stopReason")
        error_message = assistant_message.get("errorMessage")
        if stop_reason == "error" or error_message:
            detail = str(error_message or "unknown provider error").strip()
            raise RuntimeError(f"Pi assistant error: {detail}")

        content = assistant_message.get("content")
        if not isinstance(content, list):
            raise ValueError(f"Pi returned invalid assistant content (stopReason={stop_reason or 'unknown'})")
        parts: list[str] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") != "text":
                continue
            text = item.get("text")
            if isinstance(text, str) and text:
                parts.append(text)
        response = "\n".join(parts).strip()
        if not response:
            raise ValueError(f"Pi returned no assistant text (stopReason={stop_reason or 'unknown'})")
        return response

    def _wrap_with_models(self, worker: WorkerConfig, pi_argv: list[str], *, tools: str) -> list[str]:
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
            "--no-extensions", "--no-skills", "--no-prompt-templates",
            "--no-themes", "--no-context-files",
        ]
        argv.extend(["--tools", tools])
        return [
            "/bin/sh", "-lc", script, "--",
            self._agent_dir(worker),
            self._models_json(worker),
            *argv, *pi_argv,
        ]

    @staticmethod
    def _agent_dir(worker: WorkerConfig) -> str:
        return os.path.join("/tmp/cairn-pi", worker.name)

    @staticmethod
    def _session_dir(worker: WorkerConfig) -> str:
        return os.path.join(PiDriver._agent_dir(worker), "sessions")

    @staticmethod
    def _ensure_private_directory(path: str) -> None:
        os.makedirs(path, mode=0o700, exist_ok=True)
        os.chmod(path, 0o700)

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
            # Pi 0.84 treats an environment-variable name as a literal API key.
            # Write the configured value so custom providers work consistently.
            "apiKey": env["PI_API_KEY"],
            "models": [model],
        }
        payload = {"providers": {"cairn": provider}}
        return json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
