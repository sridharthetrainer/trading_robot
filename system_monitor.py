"""
system_monitor.py — System resource monitor for the trading bot.
Provides CPU, memory, disk and process health checks.
"""
from __future__ import annotations
import logging
import os
import subprocess
import time

logger = logging.getLogger(__name__)


def get_bot_pid() -> int:
    """Get PID of main_autonomous.py process."""
    try:
        r = subprocess.run(["pgrep", "-f", "main_autonomous.py"],
                           capture_output=True, text=True, timeout=5)
        pids = [int(p) for p in r.stdout.split() if p.isdigit()]
        return pids[0] if pids else 0
    except Exception:
        return 0


def get_memory_mb(pid: int) -> float:
    """RSS memory in MB for a PID."""
    try:
        with open(f"/proc/{pid}/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024
    except Exception:
        pass
    return 0.0


def get_cpu_ticks(pid: int) -> int:
    """Total CPU ticks (utime+stime) for a PID."""
    try:
        with open(f"/proc/{pid}/stat") as f:
            parts = f.read().split()
        return int(parts[13]) + int(parts[14])
    except Exception:
        return -1


def is_cpu_active(pid: int, wait_sec: float = 3.0) -> bool:
    """True if process consumed any CPU in the last wait_sec seconds."""
    t1 = get_cpu_ticks(pid)
    if t1 < 0:
        return False
    time.sleep(wait_sec)
    t2 = get_cpu_ticks(pid)
    return t2 > t1


def get_disk_free_gb(path: str = ".") -> float:
    """Free disk space in GB at path."""
    try:
        st = os.statvfs(path)
        return st.f_bavail * st.f_frsize / (1024 ** 3)
    except Exception:
        return 0.0


def get_open_files(pid: int) -> int:
    """Count of open file descriptors for a PID."""
    try:
        return len(os.listdir(f"/proc/{pid}/fd"))
    except Exception:
        return 0


def system_health() -> dict:
    """Return a full system health snapshot."""
    pid = get_bot_pid()
    return {
        "bot_pid":       pid,
        "bot_running":   pid > 0,
        "memory_mb":     round(get_memory_mb(pid), 1) if pid else 0.0,
        "cpu_active":    is_cpu_active(pid, 1.0) if pid else False,
        "disk_free_gb":  round(get_disk_free_gb(), 1),
        "open_files":    get_open_files(pid) if pid else 0,
    }


def log_health(interval_sec: int = 300) -> None:
    """Log system health every interval_sec seconds (background task)."""
    while True:
        try:
            h = system_health()
            logger.info(
                "SystemHealth pid=%d mem=%.0fMB disk=%.1fGB cpu_active=%s",
                h["bot_pid"], h["memory_mb"], h["disk_free_gb"], h["cpu_active"],
            )
        except Exception as e:
            logger.debug("Health log error: %s", e)
        time.sleep(interval_sec)
