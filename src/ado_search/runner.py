from __future__ import annotations

import asyncio
import json
import locale
import os
import shutil
from contextlib import nullcontext
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypedDict


class SyncResult(TypedDict, total=False):
    """Standardized return type for all sync operations."""
    fetched: int
    errors: int
    fetched_ids: set[int]  # only for work-item syncs that track IDs
    dry_run: bool
    would_fetch: int


@dataclass
class CommandResult:
    command: list[str]
    returncode: int
    stdout: str
    stderr: str
    headers: dict[str, str] = field(default_factory=dict)

    def parse_json(self):
        """Parse stdout as JSON, unwrapping double-serialization from ConvertTo-Json."""
        data = json.loads(self.stdout)
        if isinstance(data, str):
            data = json.loads(data)
        return data


def _decode_output(data: bytes) -> str:
    """Decode subprocess output: UTF-8, else the locale encoding.

    az on Windows writes piped output in the ANSI code page (cp1252), where a
    curly quote is one byte that is not valid UTF-8.
    """
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode(locale.getpreferredencoding(False) or "utf-8", errors="replace")


async def run_command(
    cmd: list[str], *, timeout: float = 120, retries: int = 3,
) -> CommandResult:
    # Resolve the executable via PATH (handles Windows .cmd/.bat extensions)
    resolved = shutil.which(cmd[0])
    exec_cmd = [resolved, *cmd[1:]] if resolved else cmd

    last_result: CommandResult | None = None
    for attempt in range(retries):
        # Prevent MSYS/Git Bash from mangling paths like "/" → "C:/Program Files/Git/"
        env = {**os.environ, "MSYS_NO_PATHCONV": "1"}
        proc = await asyncio.create_subprocess_exec(
            *exec_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            last_result = CommandResult(
                command=cmd, returncode=-1,
                stdout="", stderr="Command timed out",
            )
            if attempt < retries - 1:
                await asyncio.sleep(2 ** attempt)
            continue

        result = CommandResult(
            command=cmd,
            returncode=proc.returncode or 0,
            stdout=_decode_output(stdout_bytes).strip(),
            stderr=_decode_output(stderr_bytes).strip(),
        )
        if result.returncode == 0:
            return result

        last_result = result
        if attempt < retries - 1:
            await asyncio.sleep(2 ** attempt)

    return last_result  # type: ignore[return-value]


async def run_pat_request(operation: str, *, org: str, project: str, pat: str, **kwargs) -> CommandResult:
    """Execute an ADO API call using PAT auth. Returns CommandResult for compatibility."""
    from ado_search.auth import pat_request
    try:
        data = await asyncio.to_thread(
            pat_request, operation, org=org, project=project, pat=pat, **kwargs
        )
        return CommandResult(
            command=["pat_request", operation],
            returncode=0,
            stdout=json.dumps(data),
            stderr="",
        )
    except Exception as e:
        return CommandResult(
            command=["pat_request", operation],
            returncode=1,
            stdout="",
            stderr=str(e),
        )


async def run_operation(
    auth_method: str,
    operation: str,
    *,
    org: str,
    project: str,
    pat: str = "",
    retries: int = 3,
    **kwargs,
) -> CommandResult:
    """Route to PAT direct HTTP or shell command based on auth method."""
    if auth_method == "pat":
        return await run_pat_request(operation, org=org, project=project, pat=pat, **kwargs)
    from ado_search.auth import build_command
    cmd = build_command(operation, auth_method, org=org, project=project, **kwargs)
    return await run_command(cmd, retries=retries)


def _full_result(operation: str, status: int, headers: dict, text: str) -> CommandResult:
    if status == 203:
        # ADO answers an unauthenticated call with 203 and a sign-in page.
        return CommandResult(["request_full", operation], 1, "",
                             f"HTTP 203 for {operation}: not signed in (sign-in page returned)", headers)
    if 200 <= status < 300:
        return CommandResult(["request_full", operation], 0, text, "", headers)
    return CommandResult(["request_full", operation], 1, "", f"HTTP {status} for {operation}: {text[:2000]}", headers)


async def run_operation_full(
    auth_method: str,
    operation: str,
    *,
    org: str,
    project: str,
    pat: str = "",
    body: str | bytes | None = None,
    content_type: str | None = None,
    extra_headers: dict[str, str] | None = None,
    **kwargs,
) -> CommandResult:
    """Run an operation and keep the response headers (``CommandResult.headers``, lower-cased).

    Needed for continuation tokens and ETags. PAT goes over HTTP directly; az-cli
    and az-powershell go through pwsh Invoke-WebRequest. Writes are not retried.
    """
    from ado_search.auth import OPERATIONS, build_full_request_command, pat_request_full

    if auth_method == "pat":
        try:
            resp = await asyncio.to_thread(
                pat_request_full, operation, org=org, project=project, pat=pat, body=body,
                content_type=content_type, extra_headers=extra_headers, **kwargs,
            )
        except Exception as e:
            return CommandResult(["request_full", operation], 1, "", str(e))
        return _full_result(operation, resp.status, resp.headers, resp.text)

    import tempfile
    body_file = None
    try:
        if body is not None:
            fd, name = tempfile.mkstemp(prefix="ado-search-", suffix=".body")
            with os.fdopen(fd, "wb") as f:
                f.write(body if isinstance(body, bytes) else body.encode("utf-8"))
            body_file = Path(name)
        cmd = build_full_request_command(
            operation, auth_method, org=org, project=project, body_file=body_file,
            content_type=content_type, extra_headers=extra_headers, **kwargs,
        )
        retries = 3 if OPERATIONS[operation].method == "GET" else 1
        result = await run_command(cmd, retries=retries)
    finally:
        if body_file is not None:
            body_file.unlink(missing_ok=True)
    if result.returncode != 0:
        return result
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return CommandResult(cmd, 1, "", f"Unreadable response for {operation}: {result.stdout[:300]}")
    return _full_result(operation, int(data.get("status") or 0), data.get("headers") or {},
                        data.get("content") or "")


async def fetch_and_parse(
    auth_method: str,
    operation: str,
    label: str,
    *,
    org: str,
    project: str,
    pat: str = "",
    semaphore: asyncio.Semaphore | None = None,
    **op_kwargs,
) -> dict | list | str:
    """Optionally acquire semaphore, run an operation, and parse JSON.

    Returns parsed JSON on success, or an error message string on failure.
    """
    async with semaphore if semaphore is not None else nullcontext():
        result = await run_operation(
            auth_method, operation, org=org, project=project, pat=pat, **op_kwargs,
        )
        if result.returncode != 0:
            return f"Failed to fetch {label}: {result.stderr}"
        try:
            return result.parse_json()
        except (json.JSONDecodeError, ValueError):
            return f"Invalid JSON for {label}"


async def download_binary(
    auth_method: str,
    *,
    url: str,
    dest_path: "Path",
    org: str,
    pat: str = "",
    semaphore: asyncio.Semaphore | None = None,
) -> str | None:
    """Download a binary file. Returns None on success, error string on failure."""
    dest = Path(dest_path) if not isinstance(dest_path, Path) else dest_path
    dest.parent.mkdir(parents=True, exist_ok=True)

    async with semaphore if semaphore is not None else nullcontext():
        if auth_method == "pat":
            from ado_search.auth import pat_download_binary
            try:
                await asyncio.to_thread(pat_download_binary, url=url, pat=pat, dest_path=dest)
                return None
            except Exception as e:
                return f"Download failed for {url}: {e}"
        else:
            from ado_search.auth import build_download_command
            cmd = build_download_command(url, dest, auth_method, org)
            result = await run_command(cmd, timeout=300)
            if result.returncode != 0:
                return f"Download failed for {url}: {result.stderr}"
            return None


async def run_commands_parallel(
    cmds: list[list[str]],
    *,
    max_concurrent: int = 5,
    timeout: float = 120,
) -> list[CommandResult]:
    semaphore = asyncio.Semaphore(max_concurrent)

    async def _run(cmd: list[str]) -> CommandResult:
        async with semaphore:
            return await run_command(cmd, timeout=timeout)

    return await asyncio.gather(*[_run(cmd) for cmd in cmds])
