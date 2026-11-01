"""Unit tests for DockerExecutionRunner and get_execution_runner (subprocess mocked)."""

import subprocess
from types import SimpleNamespace
from unittest import mock

from backend.app.services import arbitration_engine as ae
from backend.app.services.arbitration_engine import (
    CodeExecutionRunner,
    DockerExecutionRunner,
    get_execution_runner,
)

PASS_OUT = "...\n3 passed in 0.02s\n"


def _cp(rc=0, out="", err=""):
    return subprocess.CompletedProcess(args=[], returncode=rc, stdout=out, stderr=err)


def test_docker_command_has_exact_hardening_flags():
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        seen["kw"] = kw
        # workspace must exist with both files while the container runs
        mount = cmd[cmd.index("-v") + 1]
        host = mount.split(":")[0]
        import os

        seen["files"] = sorted(os.listdir(host))
        return _cp(0, PASS_OUT)

    with mock.patch.object(ae.subprocess, "run", side_effect=fake_run):
        passed, total, out = DockerExecutionRunner().run_tests("x = 1\n", "def test_a(): pass\n")

    assert (passed, total) == (3, 3)
    cmd = seen["cmd"]
    assert cmd[:3] == ["docker", "run", "--rm"]
    flags = " ".join(cmd)
    for expected in (
        "--network none",
        "--read-only",
        "--tmpfs /tmp:rw,noexec,nosuid,size=64m",
        "--memory 256m",
        "--memory-swap 256m",
        "--cpus 0.5",
        "--pids-limit 64",
        "--security-opt no-new-privileges:true",
        "--cap-drop ALL",
    ):
        assert expected in flags
    mount = cmd[cmd.index("-v") + 1]
    assert mount.endswith(":/workspace:ro")
    assert "aegispay-sandbox-runner:latest" in cmd
    assert seen["files"] == ["deliverable.py", "test_deliverable.py"]
    assert seen["kw"]["timeout"] == 5.0
    # temp dir cleaned up
    import os

    assert not os.path.exists(mount.split(":")[0])


def test_docker_failures_parsed_like_subprocess_runner():
    out = "1 failed, 2 passed in 0.1s"
    with mock.patch.object(ae.subprocess, "run", return_value=_cp(1, out)):
        assert DockerExecutionRunner().run_tests("a", "b")[:2] == (2, 3)


def test_docker_timeout_kills_container_by_name():
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        if cmd[1] == "run":
            raise subprocess.TimeoutExpired(cmd, kw["timeout"], output=b"partial")
        return _cp(0)

    with mock.patch.object(ae.subprocess, "run", side_effect=fake_run):
        passed, total, out = DockerExecutionRunner().run_tests("a", "b", timeout=1.5)

    assert (passed, total) == (0, 1)
    assert "timed out" in out and "partial" in out
    run_cmd, kill_cmd = calls
    name = run_cmd[run_cmd.index("--name") + 1]
    assert name.startswith("aegis-sbx-")
    assert kill_cmd == ["docker", "kill", name]


def test_docker_binary_missing_returns_failure():
    with mock.patch.object(ae.subprocess, "run", side_effect=FileNotFoundError("docker")):
        assert DockerExecutionRunner().run_tests("a", "b")[:2] == (0, 1)


def test_factory_explicit_modes():
    assert isinstance(get_execution_runner(SimpleNamespace(SANDBOX_MODE="docker")), DockerExecutionRunner)
    assert isinstance(get_execution_runner(SimpleNamespace(SANDBOX_MODE="subprocess")), CodeExecutionRunner)


def test_factory_auto_uses_docker_when_available():
    with mock.patch.object(ae.subprocess, "run", return_value=_cp(0)) as run:
        r = get_execution_runner(SimpleNamespace(SANDBOX_MODE="auto", SANDBOX_IMAGE="img:1"))
    assert isinstance(r, DockerExecutionRunner) and r.image == "img:1"
    assert [c.args[0][1] for c in run.call_args_list] == ["info", "image"]


def test_factory_auto_falls_back_when_daemon_down(caplog):
    with mock.patch.object(ae.subprocess, "run", return_value=_cp(1)):
        with caplog.at_level("WARNING"):
            r = get_execution_runner(SimpleNamespace(SANDBOX_MODE="auto"))
    assert isinstance(r, CodeExecutionRunner)
    assert "falling back" in caplog.text


def test_factory_auto_falls_back_when_image_missing_or_no_binary():
    results = [_cp(0), _cp(1)]
    with mock.patch.object(ae.subprocess, "run", side_effect=results):
        assert isinstance(get_execution_runner(SimpleNamespace(SANDBOX_MODE="auto")), CodeExecutionRunner)
    with mock.patch.object(ae.subprocess, "run", side_effect=FileNotFoundError()):
        assert isinstance(get_execution_runner(SimpleNamespace()), CodeExecutionRunner)
