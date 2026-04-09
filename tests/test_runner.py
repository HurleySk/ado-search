import asyncio
import sys

from ado_search.runner import run_command, run_commands_parallel


def test_run_command_success():
    result = asyncio.run(run_command([sys.executable, "-c", "print('hello')"]))
    assert result.returncode == 0
    assert "hello" in result.stdout


def test_run_command_failure():
    result = asyncio.run(run_command([sys.executable, "-c", "raise SystemExit(1)"]))
    assert result.returncode == 1


def test_run_command_retries_on_failure():
    result = asyncio.run(run_command(
        [sys.executable, "-c", "import sys; sys.exit(1)"],
        retries=2,
    ))
    assert result.returncode == 1


def test_run_commands_parallel():
    cmds = [
        [sys.executable, "-c", f"print({i})"]
        for i in range(5)
    ]
    results = asyncio.run(run_commands_parallel(cmds, max_concurrent=3))
    assert len(results) == 5
    for r in results:
        assert r.returncode == 0
