from __future__ import annotations

import os

import docker

from cairn.dispatcher.runtime.containers import ContainerManager


SCRIPT = r"""
set +e
allowed_code=$(curl -sS -o /tmp/allowed -w '%{http_code}' --max-time 15 http://10.102.35.135/ncycx/)
allowed_exit=$?
blocked_code=$(curl -sS -o /tmp/blocked -w '%{http_code}' --max-time 10 http://127.0.0.1:8000/)
blocked_exit=$?
spoof_code=$(curl -sS -o /tmp/spoof -w '%{http_code}' --max-time 15 -H 'Host: 127.0.0.1:8000' http://10.102.35.135/)
spoof_exit=$?
if grep -q '<title>Rabbit</title>' /tmp/spoof 2>/dev/null; then spoof_reached_rabbit=yes; else spoof_reached_rabbit=no; fi
curl --noproxy '*' -sS -o /dev/null --max-time 5 http://10.102.35.135/ncycx/
bypass_exit=$?
printf 'allowed_code=%s allowed_exit=%s\n' "$allowed_code" "$allowed_exit"
printf 'blocked_code=%s blocked_exit=%s\n' "$blocked_code" "$blocked_exit"
printf 'spoof_code=%s spoof_exit=%s spoof_reached_rabbit=%s\n' "$spoof_code" "$spoof_exit" "$spoof_reached_rabbit"
printf 'direct_bypass_exit=%s\n' "$bypass_exit"
"""


def main() -> int:
    environment = ContainerManager._proxy_environment("proj_040")
    if environment is None:
        raise SystemExit("egress proxy environment is not configured")
    client = docker.from_env()
    try:
        output = client.containers.run(
            "ghcr.io/oritera/cairn-worker-container:latest",
            ["/bin/sh", "-lc", SCRIPT],
            network_mode="rabbit-pentest-worker-net",
            environment=environment,
            remove=True,
            stdout=True,
            stderr=True,
        )
    finally:
        client.close()
    print(output.decode(errors="replace").strip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
