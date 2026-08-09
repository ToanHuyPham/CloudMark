from __future__ import annotations

import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence


RUNNER_VERSION = "1.0"


class RunStopped(RuntimeError):
    def __init__(self, message: str):
        super().__init__(message)
        self.partial_result: dict[str, Any] | None = None


class RunCancelled(RunStopped):
    pass


class RunTimedOut(RunStopped):
    pass


class CancellationToken:
    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()


@dataclass(frozen=True)
class ProcessResult:
    args: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str
    elapsed_seconds: float


ProgressCallback = Callable[[dict[str, Any]], None]


class JobContext:
    def __init__(
        self,
        run_id: str,
        *,
        total_steps: int,
        timeout_seconds: float,
        token: CancellationToken | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> None:
        self.run_id = run_id
        self.total_steps = max(1, total_steps)
        self.timeout_seconds = max(1.0, timeout_seconds)
        self.token = token or CancellationToken()
        self.on_progress = on_progress
        self.completed_steps = 0
        self.started = time.monotonic()

    @property
    def elapsed_seconds(self) -> float:
        return time.monotonic() - self.started

    def checkpoint(self) -> None:
        if self.token.cancelled:
            raise RunCancelled("Run cancelled by the operator.")
        if self.elapsed_seconds > self.timeout_seconds:
            raise RunTimedOut(f"Run exceeded the {self.timeout_seconds:.0f}-second timeout.")

    def report(
        self,
        phase: str,
        current_job: str | None = None,
        *,
        partial_result: dict[str, Any] | None = None,
    ) -> None:
        self.checkpoint()
        if self.on_progress:
            self.on_progress(
                {
                    "progress": min(1.0, self.completed_steps / self.total_steps),
                    "phase": phase,
                    "current_job": current_job,
                    "completed_steps": self.completed_steps,
                    "total_steps": self.total_steps,
                    "result": partial_result,
                }
            )

    def complete_step(
        self,
        phase: str,
        current_job: str | None = None,
        *,
        partial_result: dict[str, Any] | None = None,
    ) -> None:
        self.completed_steps = min(self.total_steps, self.completed_steps + 1)
        self.report(phase, current_job, partial_result=partial_result)

    @staticmethod
    def _stop_process(process: subprocess.Popen[str]) -> None:
        if process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)

    def run_process(
        self,
        command: Sequence[str],
        *,
        label: str,
        cwd: Path | None = None,
    ) -> ProcessResult:
        self.checkpoint()
        started = time.monotonic()
        process = subprocess.Popen(
            list(command),
            cwd=str(cwd) if cwd else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            shell=False,
        )
        try:
            while process.poll() is None:
                try:
                    self.checkpoint()
                except RunStopped:
                    self._stop_process(process)
                    raise
                time.sleep(0.1)
            stdout, stderr = process.communicate()
        except BaseException:
            self._stop_process(process)
            process.communicate()
            raise
        return ProcessResult(
            args=tuple(str(item) for item in command),
            returncode=int(process.returncode or 0),
            stdout=stdout,
            stderr=stderr,
            elapsed_seconds=round(time.monotonic() - started, 3),
        )
