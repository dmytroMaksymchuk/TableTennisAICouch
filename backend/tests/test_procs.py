from __future__ import annotations

import os
import sys
import threading
import time

import pytest

from ttcoach import procs
from ttcoach.errors import ChildCancelled

# Child (through the venv launcher on Windows) spawns a grandchild sleeper and reports the PIDs.
TREE_SCRIPT = (
    "import os, subprocess, sys, time;"
    "g = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']);"
    "print('PIDS', os.getpid(), os.getppid(), g.pid, flush=True);"
    "time.sleep(60)"
)


def pid_alive(pid: int) -> bool:
    if sys.platform == "win32":
        import ctypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.restype = ctypes.c_void_p
        h = k32.OpenProcess(0x00100000 | 0x1000, False, pid)  # SYNCHRONIZE | QUERY_LIMITED_INFORMATION
        if not h:
            return False
        try:
            return k32.WaitForSingleObject(ctypes.c_void_p(h), 0) == 0x102  # WAIT_TIMEOUT -> running
        finally:
            k32.CloseHandle(ctypes.c_void_p(h))
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:  # reap if it is our zombie
        return os.waitpid(pid, os.WNOHANG) == (0, 0)
    except ChildProcessError:
        return True


def wait_dead(pids: list[int], within_s: float = 5.0) -> None:
    deadline = time.monotonic() + within_s
    while time.monotonic() < deadline:
        if not any(pid_alive(p) for p in pids):
            return
        time.sleep(0.05)
    pytest.fail(f"still alive after {within_s}s: {[p for p in pids if pid_alive(p)]}")


class PidCollector:
    def __init__(self) -> None:
        self.pids: list[int] = []
        self.ready = threading.Event()

    def __call__(self, line: str) -> None:
        if line.startswith("PIDS "):
            self.pids = [int(v) for v in line.split()[1:]]
            self.ready.set()


def test_timeout_kills_whole_tree() -> None:
    col = PidCollector()
    t0 = time.monotonic()
    res = procs.run_child([sys.executable, "-c", TREE_SCRIPT], 3.0, col)
    assert res.timed_out and not res.ok
    assert time.monotonic() - t0 < 3.0 + 5.0
    assert col.ready.is_set()
    wait_dead(col.pids)


def test_stop_path_kills_whole_tree() -> None:
    runner = procs.ChildRunner()
    col = PidCollector()
    outcome: list[BaseException | object] = []

    def work() -> None:
        try:
            outcome.append(runner([sys.executable, "-c", TREE_SCRIPT], 120, col))
        except BaseException as e:  # noqa: BLE001
            outcome.append(e)

    th = threading.Thread(target=work)
    th.start()
    assert col.ready.wait(30)
    runner.stop()
    th.join(10)
    assert not th.is_alive()
    assert isinstance(outcome[0], ChildCancelled)
    wait_dead(col.pids)
    with pytest.raises(ChildCancelled):
        runner([sys.executable, "-c", "pass"])


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Job Object behaviour")
def test_closing_job_handle_kills_launcher_chain() -> None:
    # sys.executable under `uv run` is the venv's Scripts\python.exe launcher -> base interpreter -> grandchild.
    assert "scripts" in sys.executable.lower()
    col = PidCollector()
    child = procs.spawn([sys.executable, "-c", TREE_SCRIPT], col)
    assert col.ready.wait(30)
    interp, parent, grandchild = col.pids
    pids = sorted({child.pid, interp, parent, grandchild})
    assert len(pids) >= 2 and all(pid_alive(p) for p in pids)
    child.close_job()  # no kill(): KILL_ON_JOB_CLOSE must take the whole tree down
    wait_dead(pids)
    child.close()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Job Object behaviour")
def test_assignment_failure_falls_back_to_taskkill(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(procs, "_assign_process", lambda job, handle: False)
    calls: list[list[str]] = []
    real_run = procs.subprocess.run

    def spy(cmd, *a, **kw):
        calls.append(list(cmd))
        return real_run(cmd, *a, **kw)

    monkeypatch.setattr(procs.subprocess, "run", spy)
    col = PidCollector()
    res = procs.run_child([sys.executable, "-c", TREE_SCRIPT], 3.0, col)
    assert res.timed_out
    assert any(c[:1] == ["taskkill"] and "/T" in c and "/F" in c for c in calls)
    wait_dead(col.pids)


def test_on_line_splits_cr_and_lf() -> None:
    lines: list[str] = []
    res = procs.run_child(
        [sys.executable, "-c", r"import sys; sys.stdout.write('a\rb\nc\n'); sys.stdout.flush()"], 30, lines.append
    )
    assert res.ok
    assert lines == ["a", "b", "c"]
    assert res.tail == ["a", "b", "c"]


def test_nonzero_exit_and_utf8() -> None:
    lines: list[str] = []
    res = procs.run_child([sys.executable, "-c", "print('héllo ｜'); raise SystemExit(3)"], 30, lines.append)
    assert res.returncode == 3 and not res.ok
    assert lines == ["héllo ｜"]


def test_no_callbacks_after_close() -> None:
    lines: list[str] = []
    child = procs.spawn([sys.executable, "-c", "print('x')"], lines.append)
    child.proc.wait(30)
    child.close()
    child._emit("late progress 9/10 [")
    assert lines == ["x"]


def test_kill_current_races_with_close() -> None:
    runner = procs.ChildRunner()
    stop = threading.Event()
    errors: list[BaseException] = []

    def hammer() -> None:
        while not stop.is_set():
            try:
                runner.kill_current()
            except BaseException as e:  # noqa: BLE001
                errors.append(e)

    th = threading.Thread(target=hammer)
    th.start()
    try:
        for _ in range(15):
            runner([sys.executable, "-c", "pass"], 30)
    finally:
        stop.set()
        th.join()
    assert not errors


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Job Object behaviour")
def test_spawn_failures_release_resources(monkeypatch: pytest.MonkeyPatch) -> None:
    closed: list[int] = []
    real_close = procs._close_handle
    monkeypatch.setattr(procs, "_close_handle", lambda h: (closed.append(h), real_close(h))[1])

    def bad_popen(*a, **kw):
        raise OSError("cannot start")

    with monkeypatch.context() as m:
        m.setattr(procs.subprocess, "Popen", bad_popen)
        with pytest.raises(OSError):
            procs.spawn([sys.executable, "-c", "pass"])
    assert len(closed) == 1  # job handle not leaked

    started: list = []
    real_popen = procs.subprocess.Popen

    def spy_popen(*a, **kw):
        p = real_popen(*a, **kw)
        started.append(p)
        return p

    def bad_resume(handle):
        raise OSError("resume failed")

    monkeypatch.setattr(procs.subprocess, "Popen", spy_popen)
    monkeypatch.setattr(procs, "_resume_process", bad_resume)
    with pytest.raises(OSError):
        procs.spawn([sys.executable, "-c", "pass"])
    (proc,) = started
    assert proc.returncode is not None  # killed and waited
    assert proc.stdout.closed
    assert len(closed) == 2
