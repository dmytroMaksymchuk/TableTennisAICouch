"""Child-process launcher with whole-tree kill (DESIGN §2.3).

Windows: each child runs in a Job Object with JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE (created suspended,
assigned, then resumed), so descendants are contained and die with the job handle — also when this
process dies. POSIX: own session/process group, killed with killpg.
"""

from __future__ import annotations

import codecs
import collections
import logging
import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from ttcoach.errors import ChildCancelled

log = logging.getLogger(__name__)

TAIL_LINES = 50
IS_WINDOWS = sys.platform == "win32"


@dataclass
class ChildResult:
    returncode: int | None
    tail: list[str] = field(default_factory=list)
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out

    def tail_text(self) -> str:
        return "\n".join(self.tail)


class RunChild(Protocol):
    def __call__(
        self,
        cmd: Sequence[str],
        timeout_s: float | None = None,
        on_line: Callable[[str], None] | None = None,
        *,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
    ) -> ChildResult: ...


# --------------------------------------------------------------------------- Windows Job Objects
if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _ntdll = ctypes.WinDLL("ntdll")

    class _IO_COUNTERS(ctypes.Structure):
        _fields_ = [(n, ctypes.c_ulonglong) for n in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
        )]  # fmt: skip

    class _BASIC_LIMIT(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _EXTENDED_LIMIT(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _BASIC_LIMIT),
            ("IoInfo", _IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    _JobObjectExtendedLimitInformation = 9
    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
    _CREATE_SUSPENDED = 0x00000004
    _CREATE_NEW_PROCESS_GROUP = 0x00000200

    _kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    _kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    _kernel32.SetInformationJobObject.restype = wintypes.BOOL
    _kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    _kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    _kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    _kernel32.TerminateJobObject.restype = wintypes.BOOL
    _kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    _kernel32.CloseHandle.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _ntdll.NtResumeProcess.restype = ctypes.c_long
    _ntdll.NtResumeProcess.argtypes = [wintypes.HANDLE]

    def _create_job() -> int:
        job = _kernel32.CreateJobObjectW(None, None)
        if not job:
            raise ctypes.WinError(ctypes.get_last_error())
        info = _EXTENDED_LIMIT()
        info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not _kernel32.SetInformationJobObject(
            job, _JobObjectExtendedLimitInformation, ctypes.byref(info), ctypes.sizeof(info)
        ):
            err = ctypes.get_last_error()
            _kernel32.CloseHandle(job)
            raise ctypes.WinError(err)
        return job

    def _assign_process(job: int, process_handle: int) -> bool:
        return bool(_kernel32.AssignProcessToJobObject(job, process_handle))

    def _resume_process(process_handle: int) -> None:
        status = _ntdll.NtResumeProcess(process_handle)
        if status != 0:
            raise OSError(f"NtResumeProcess failed: NTSTATUS 0x{status & 0xFFFFFFFF:08x}")

    def _terminate_job(job: int) -> None:
        _kernel32.TerminateJobObject(job, 1)

    def _close_handle(handle: int) -> None:
        _kernel32.CloseHandle(handle)


# --------------------------------------------------------------------------- Child
class Child:
    """A started child process tree. Use `spawn()`; always `close()` when done."""

    def __init__(self, proc: subprocess.Popen[bytes], job: int | None, on_line: Callable[[str], None] | None):
        self.proc = proc
        self.job = job
        self.tail: collections.deque[str] = collections.deque(maxlen=TAIL_LINES)
        self._on_line = on_line
        # Guards job-handle use (kill vs close) and callback delivery (no callbacks after close()).
        self._lock = threading.Lock()
        self._cb_lock = threading.Lock()
        self._closed = False
        self._reader = threading.Thread(target=self._read, name=f"child-reader-{proc.pid}", daemon=True)
        self._reader.start()

    @property
    def pid(self) -> int:
        return self.proc.pid

    def _emit(self, fragment: str) -> None:
        if not fragment:
            return
        with self._cb_lock:
            if self._closed:
                return  # late output after close(): never reaches the caller (e.g. a stale progress write)
            self.tail.append(fragment)
            if self._on_line is not None:
                try:
                    self._on_line(fragment)
                except Exception:  # a broken callback must not kill the reader
                    log.exception("on_line callback failed")

    def _read(self) -> None:
        assert self.proc.stdout is not None
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        buf = ""
        stream = self.proc.stdout
        while True:
            chunk = stream.read1(65536) if hasattr(stream, "read1") else stream.read(65536)
            if not chunk:
                break
            buf += decoder.decode(chunk)
            buf = buf.replace("\r\n", "\n")
            *parts, buf = buf.replace("\r", "\n").split("\n")
            for part in parts:
                self._emit(part)
        buf += decoder.decode(b"", final=True)
        for part in buf.replace("\r", "\n").split("\n"):
            self._emit(part)

    def kill(self) -> None:
        """Kill the whole process tree."""
        if IS_WINDOWS:
            with self._lock:
                if self.job is not None:
                    _terminate_job(self.job)
                    return
            # No job object (assignment failed): fall back to taskkill of the tree.
            subprocess.run(
                ["taskkill", "/PID", str(self.proc.pid), "/T", "/F"],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                check=False,
            )  # fmt: skip
            if self.proc.poll() is None:
                self.proc.kill()
        else:
            import signal

            try:
                os.killpg(self.proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    def close_job(self) -> None:
        """Close the Job Object handle (KILL_ON_JOB_CLOSE kills any survivors)."""
        if IS_WINDOWS:
            with self._lock:
                if self.job is not None:
                    _close_handle(self.job)
                    self.job = None

    def close(self) -> None:
        if self.proc.poll() is None:
            self.kill()
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            log.warning("child %s did not exit after kill", self.proc.pid)
        if not IS_WINDOWS:
            self.kill()  # leftovers of the process group after a normal exit
        self.close_job()
        self._reader.join(timeout=5)
        with self._cb_lock:
            self._closed = True


def spawn(
    cmd: Sequence[str],
    on_line: Callable[[str], None] | None = None,
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> Child:
    full_env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    if env:
        full_env.update(env)
    common = dict(
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        cwd=str(cwd) if cwd else None, env=full_env,
    )  # fmt: skip
    if not IS_WINDOWS:
        proc = subprocess.Popen(list(cmd), start_new_session=True, **common)  # type: ignore[call-overload]
        return Child(proc, None, on_line)

    job = _create_job()
    try:
        proc = subprocess.Popen(  # type: ignore[call-overload]
            list(cmd), creationflags=_CREATE_SUSPENDED | _CREATE_NEW_PROCESS_GROUP, **common
        )
    except BaseException:
        _close_handle(job)
        raise
    handle = int(proc._handle)  # noqa: SLF001
    if not _assign_process(job, handle):
        log.warning(
            "AssignProcessToJobObject failed for pid %s (error %s); falling back to taskkill /T",
            proc.pid, ctypes.get_last_error(),
        )  # fmt: skip
        _close_handle(job)
        job = None
    try:
        _resume_process(handle)
    except OSError:
        proc.kill()
        try:
            proc.wait(timeout=10)
        finally:
            if proc.stdout is not None:
                proc.stdout.close()
            if job is not None:
                _close_handle(job)
        raise
    return Child(proc, job, on_line)


class ChildRunner:
    """Runs one child at a time; `stop()` kills the current tree and refuses new children."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._current: Child | None = None
        self._stopped = threading.Event()

    @property
    def stopped(self) -> bool:
        return self._stopped.is_set()

    def __call__(
        self,
        cmd: Sequence[str],
        timeout_s: float | None = None,
        on_line: Callable[[str], None] | None = None,
        *,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
    ) -> ChildResult:
        with self._lock:
            if self._stopped.is_set():
                raise ChildCancelled("worker is stopping")
            child = spawn(cmd, on_line, cwd=cwd, env=env)
            self._current = child
        deadline = None if timeout_s is None else time.monotonic() + timeout_s
        timed_out = False
        try:
            while True:
                try:
                    child.proc.wait(timeout=0.1)
                except subprocess.TimeoutExpired:
                    pass
                else:
                    if self._stopped.is_set():  # killed by stop() while we were waiting
                        raise ChildCancelled("worker is stopping")
                    break
                if self._stopped.is_set():
                    child.kill()
                    raise ChildCancelled("worker is stopping")
                if deadline is not None and time.monotonic() > deadline:
                    child.kill()
                    timed_out = True
                    break
        finally:
            with self._lock:  # kill_current() can no longer reach this child once it is being closed
                self._current = None
            child.close()
        return ChildResult(returncode=child.proc.returncode, tail=list(child.tail), timed_out=timed_out)

    def kill_current(self) -> None:
        with self._lock:
            if self._current is not None:
                self._current.kill()

    def stop(self) -> None:
        self._stopped.set()
        self.kill_current()


def run_child(
    cmd: Sequence[str],
    timeout_s: float | None = None,
    on_line: Callable[[str], None] | None = None,
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> ChildResult:
    """One-off run with tree-kill on timeout (no worker binding)."""
    return ChildRunner()(cmd, timeout_s, on_line, cwd=cwd, env=env)
