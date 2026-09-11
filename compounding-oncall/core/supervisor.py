"""Job supervision. Turns a real crash into an incident.

This is the observability edge of the system: we run the job as a subprocess,
watch the exit code, and capture stderr verbatim. Nothing is simulated, so the
error text the agent fingerprints is the error text the job actually emitted.
"""

import asyncio
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from adapters.contracts import Incident
from core.config import REPO_ROOT, WAREHOUSE_DIR

JOB_SCRIPT = REPO_ROOT / "scripts" / "run_job.py"


class JobResult:
    def __init__(self, exit_code: int, stdout: str, stderr: str):
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr

    @property
    def crashed(self) -> bool:
        return self.exit_code != 0


async def run_job(table: str, partition: str = "today") -> JobResult:
    """Execute the job and capture its output."""
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        str(JOB_SCRIPT),
        "--table",
        table,
        "--partition",
        partition,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(REPO_ROOT),
    )
    stdout, stderr = await process.communicate()
    return JobResult(
        process.returncode or 0,
        stdout.decode(errors="replace").strip(),
        stderr.decode(errors="replace").strip(),
    )


async def watch(table: str) -> Optional[Incident]:
    """Run the job. If it crashes, build an incident from the real stderr."""
    result = await run_job(table, "today")
    if not result.crashed:
        return None

    return {
        "incident_id": uuid.uuid4().hex[:8],
        "job_id": f"job_{table}_daily",
        "table": table,
        "run_ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "error_text": result.stderr,
        "exit_code": result.exit_code,
        "parquet_path": str(WAREHOUSE_DIR / f"{table}_today.parquet"),
    }


async def verify(table: str) -> tuple[bool, int, str]:
    """Re-run the job after the fix.

    The quarantine action redirects the job at the known-good partition, which is
    what a real quarantine does: stop consuming the bad data. Success here is the
    process exiting 0, not us asserting that it would.
    """
    result = await run_job(table, "good")
    detail = (result.stdout or result.stderr).splitlines()
    return result.exit_code == 0, result.exit_code, detail[-1] if detail else ""
