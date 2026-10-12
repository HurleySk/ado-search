import asyncio
import sys
from unittest.mock import patch

from ado_search.runner import _decode_output, run_command, run_commands_parallel


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


def test_decode_output_keeps_utf8():
    assert _decode_output("What’s wrong".encode("utf-8")) == "What’s wrong"


def test_decode_output_falls_back_to_locale_encoding():
    with patch("ado_search.runner.locale.getpreferredencoding", return_value="cp1252"):
        assert _decode_output(b"What\x92s wrong") == "What’s wrong"


def test_run_command_decodes_cp1252_output():
    script = r"import sys; sys.stdout.buffer.write(b'What\x92s wrong')"
    with patch("ado_search.runner.locale.getpreferredencoding", return_value="cp1252"):
        result = asyncio.run(run_command([sys.executable, "-c", script]))
    assert result.returncode == 0
    assert result.stdout == "What’s wrong"
