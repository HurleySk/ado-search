from __future__ import annotations

import asyncio
import json
import os
import shutil
from dataclasses import dataclass


@dataclass
class CommandResult:
    command: list[str]
    returncode: int
    stdout: str
    stderr: str

    def parse_json(self):
        """Parse stdout as JSON, unwrapping double-serialization from ConvertTo-Json."""
        data = json.loads(self.stdout)
        if isinstance(data, str):
            data = json.loads(data)
        return data


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
            stdout=stdout_bytes.decode("utf-8", errors="replace").strip(),
            stderr=stderr_bytes.decode("utf-8", errors="replace").strip(),
        )
        if result.returncode == 0:
            return result

        last_result = result
        if attempt < retries - 1:
            await asyncio.sleep(2 ** attempt)

    return last_result  # type: ignore[return-value]


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
