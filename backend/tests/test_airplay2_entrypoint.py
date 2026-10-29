"""Discovery failures must not take down the Web UI or AirPlay 1 fallback."""

import os
import shlex
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize("failed_command", [None, "dbus-daemon", "avahi-daemon"])
@pytest.mark.parametrize("uid", ["0", "1001"])
def test_discovery_preserves_standard_privilege_drop(tmp_path, failed_command, uid):
    project = Path(__file__).resolve().parents[2]
    scripts = {
        "mkdir": "exit 0",
        "rm": "exit 0",
        "dbus-send": "exit 1",
        "dbus-uuidgen": "exit 0",
        "dbus-daemon": "exit 0",
        "avahi-daemon": '[ "$1" = "--check" ] && exit 1; exit 0',
        "id": f'printf "%s\\n" "{uid}"',
        "chown": "exit 0",
        "gosu": 'printf "run-as:%s\\n" "$1"; shift; exec "$@"',
        "entrypoint.sh": f'exec /bin/sh {shlex.quote(str(project / "entrypoint.sh"))} "$@"',
        "python": 'printf "%s\\n" "backend:$*"',
    }
    if failed_command:
        scripts[failed_command] = "exit 1"
    for name, body in scripts.items():
        command = tmp_path / name
        command.write_text(f"#!/bin/sh\n{body}\n")
        command.chmod(0o755)
    entrypoint = Path(__file__).resolve().parents[1] / "scripts/entrypoint.sh"
    result = subprocess.run(
        ["/bin/sh", str(entrypoint), "python", "run.py"],
        env={**os.environ, "PATH": str(tmp_path)},
        capture_output=True, text=True, timeout=5,
    )
    assert result.returncode == 0
    expected = "run-as:miair\nbackend:run.py" if uid == "0" else "backend:run.py"
    assert result.stdout.strip() == expected
    assert ("AirPlay 2 discovery unavailable" in result.stderr) is bool(failed_command)
