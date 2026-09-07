"""Detached CLI worker command, environment, and PID liveness."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Mapping

from ui.services.paths import KIND_CASE, KIND_COMPARISON, REPO_ROOT, SRC_DIRECTORY

PopenFactory = Callable[..., Any]
PidCheck = Callable[[int | None], bool]


def worker_command(kind: str, request_path: str | Path) -> list[str]:
    if kind == KIND_CASE:
        verb = "run"
    elif kind == KIND_COMPARISON:
        verb = "compare"
    else:
        raise ValueError("unsupported job kind")
    return [
        sys.executable,
        "-u",
        "-m",
        "stepinbel",
        verb,
        "--request",
        str(Path(request_path)),
        "--quiet",
    ]


def worker_env(*, root: Path | None = None) -> dict[str, str]:
    env = os.environ.copy()
    src = str((Path(root) / "src").resolve() if root is not None else SRC_DIRECTORY.resolve())
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = src if not existing else src + os.pathsep + existing
    env["PYTHONUNBUFFERED"] = "1"
    return env


def windows_creationflags() -> int:
    no_window = int(getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000))
    new_group = int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200))
    return no_window | new_group


def popen_kwargs(
    *,
    kind: str,
    request_path: Path,
    cwd: Path,
    stdout: Any,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "args": worker_command(kind, request_path),
        "stdout": stdout,
        "stderr": subprocess.STDOUT,
        "cwd": str(cwd),
        "env": worker_env(root=cwd),
        "shell": False,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = windows_creationflags()
    else:
        kwargs["start_new_session"] = True
    return kwargs


def start_worker(
    *,
    kind: str,
    request_path: Path,
    console_path: Path,
    cwd: Path,
    popen: PopenFactory,
) -> int:
    console_path.parent.mkdir(parents=True, exist_ok=True)
    handle = console_path.open("ab")
    try:
        process = popen(**popen_kwargs(kind=kind, request_path=request_path, cwd=cwd, stdout=handle))
    finally:
        handle.close()
    pid = getattr(process, "pid", None)
    if type(pid) is not int or pid <= 0:
        raise OSError("worker PID is missing")
    return pid


def pid_is_alive(pid: int | None) -> bool:
    if pid is None:
        return False
    if type(pid) is not int or pid <= 0:
        return False
    if sys.platform == "win32":
        return _windows_pid_is_alive(pid)
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _windows_pid_is_alive(pid: int) -> bool:
    import ctypes

    kernel32 = ctypes.windll.kernel32
    process_query_limited_information = 0x1000
    still_active = 259
    handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
    if not handle:
        return False
    code = ctypes.c_ulong()
    ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
    kernel32.CloseHandle(handle)
    if not ok:
        return False
    return int(code.value) == still_active


def default_cwd() -> Path:
    return REPO_ROOT


def inspect_popen_call(kwargs: Mapping[str, Any]) -> dict[str, Any]:
    """Test helper: summarise a recorded Popen call without storing the handle."""
    return {
        "args": list(kwargs.get("args") or []),
        "cwd": kwargs.get("cwd"),
        "shell": kwargs.get("shell"),
        "env_unbuffered": (kwargs.get("env") or {}).get("PYTHONUNBUFFERED"),
        "pythonpath_prefix": str((kwargs.get("env") or {}).get("PYTHONPATH", "")).split(os.pathsep)[0],
        "stderr": kwargs.get("stderr"),
        "creationflags": kwargs.get("creationflags"),
        "start_new_session": kwargs.get("start_new_session"),
    }