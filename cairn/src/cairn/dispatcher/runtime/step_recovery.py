"""Private, atomic Step checkpoints for a single local dispatcher.

This journal is deliberately separate from the FGS graph: a failed serialization
is not a confirmed Fact. Never store provider credentials here. A missing session
following a started action requires inspection, not an automatic action replay.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
import math
import uuid


class StepRecoveryStore:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        root.chmod(0o700)

    def _path(self, project_id: str, step_id: str) -> Path:
        digest = hashlib.sha256(json.dumps([project_id, step_id]).encode()).hexdigest()
        return self.root / f"{digest}.json"

    def load(self, project_id: str, step_id: str) -> dict | None:
        path = self._path(project_id, step_id)
        if not path.exists():
            return None
        try:
            value = json.loads(path.read_text())
            if (not isinstance(value, dict) or value.get('project_id') != project_id
                    or value.get('step_id') != step_id or value.get('version') != 1
                    or value.get('status') not in {'running', 'summary_pending', 'commit_pending', 'needs_attention', 'done'}):
                raise ValueError('invalid checkpoint')
            if any(not isinstance(value.get(k, 0), int) or value.get(k, 0) < 0
                   for k in ('summary_attempts', 'commit_attempts')):
                raise ValueError('invalid attempts')
            delay = value.get('retry_after', 0)
            if not isinstance(delay, (int, float)) or not math.isfinite(delay):
                raise ValueError('invalid retry timestamp')
            return value
        except (ValueError, OSError):
            # Fail closed; never turn a corrupt journal into permission to rerun.
            return {'status': 'needs_attention', 'reason': 'checkpoint_unreadable',
                    'project_id': project_id, 'step_id': step_id}

    def for_step(self, project, step) -> dict | None:
        record = self.load(project.project.id, step.id)
        instance = [project.project.created_at, step.created_at]
        if record and record.get("instance") is not None and record["instance"] != instance:
            # IDs may be reused after deleting a project. Preserve old evidence,
            # but never replay it into a newly created project with the same ID.
            os.replace(self._path(project.project.id, step.id),
                       self.root / ("archived-" + uuid.uuid4().hex + ".json"))
            return None
        return record

    def save(self, value: dict) -> None:
        path = self._path(value['project_id'], value['step_id'])
        fd, tmp = tempfile.mkstemp(prefix='.checkpoint-', dir=self.root)
        try:
            with os.fdopen(fd, 'w') as f:
                json.dump({**value, 'version': 1}, f, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
            directory = os.open(self.root, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    @staticmethod
    def ready(record: dict | None) -> bool:
        if record is None:
            return True
        return (record.get('status') in {'summary_pending', 'commit_pending'}
                and bool(record.get('session') or record.get('payload'))
                and record.get('retry_after', 0) <= time.time())


def worker_identity(worker) -> str:
    # Do not include credentials, but detect incompatible session/model changes.
    identity = [worker.name, worker.type, {k: worker.env.get(k) for k in
                ('PI_MODEL', 'PI_BASE_URL', 'PI_PROVIDER_API', 'PI_CLI')}]
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
