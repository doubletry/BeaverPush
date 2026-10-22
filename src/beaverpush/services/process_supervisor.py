"""Thread-safe ownership and shutdown of FFmpeg-family subprocesses."""

from __future__ import annotations

from dataclasses import dataclass
import subprocess
import threading
import time

import psutil

from .log_service import logger


@dataclass(frozen=True)
class _ProcessRecord:
    process: subprocess.Popen
    kind: str
    context: str
    command: str


class ProcessSupervisor:
    """Keep process handles reachable until their exit has been confirmed."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._records: dict[int, _ProcessRecord] = {}
        self._stopping: set[int] = set()

    def register(
        self,
        process: subprocess.Popen,
        *,
        kind: str,
        context: str = "",
        command: str = "",
    ) -> None:
        key = id(process)
        with self._lock:
            self._records[key] = _ProcessRecord(process, kind, context, command)
        logger.info(
            "子进程已登记 kind={} pid={} context={} command={}",
            kind, getattr(process, "pid", "?"), context, command,
        )

    def unregister(self, process: subprocess.Popen) -> None:
        key = id(process)
        with self._lock:
            record = self._records.pop(key, None)
            self._stopping.discard(key)
        if record:
            logger.info(
                "子进程已退出 kind={} pid={} context={}",
                record.kind, getattr(process, "pid", "?"), record.context,
            )

    def request_stop(
        self,
        process: subprocess.Popen | None,
        *,
        reason: str,
        grace_seconds: float = 2.0,
    ) -> None:
        """Request termination without blocking the Qt/UI caller."""
        if process is None:
            return
        key = id(process)
        with self._lock:
            if key in self._stopping:
                return
            self._stopping.add(key)
        self._send_terminate(process, reason)
        threading.Thread(
            target=self._reap,
            args=(process, reason, grace_seconds),
            name=f"process-reaper-{getattr(process, 'pid', 'unknown')}",
            daemon=True,
        ).start()

    def stop_and_wait(
        self,
        process: subprocess.Popen | None,
        *,
        reason: str,
        grace_seconds: float = 2.0,
    ) -> None:
        """Synchronously stop and reap a process from a worker/shutdown path."""
        if process is None:
            return
        self._send_terminate(process, reason)
        self._reap(process, reason, grace_seconds)

    def shutdown(self, timeout_seconds: float = 5.0) -> None:
        """Stop every registered process, escalating before returning."""
        with self._lock:
            records = list(self._records.values())
        for record in records:
            self.request_stop(
                record.process, reason="application-shutdown", grace_seconds=1.0,
            )

        deadline = time.monotonic() + max(0.0, timeout_seconds)
        while time.monotonic() < deadline:
            with self._lock:
                if not self._records:
                    return
            time.sleep(0.05)

        with self._lock:
            survivors = list(self._records.values())
        for record in survivors:
            self._kill_tree(record.process, "application-shutdown-timeout")
            if self._wait_quietly(record.process, 1.0):
                self.unregister(record.process)
            else:
                logger.error(
                    "子进程强制回收后仍未退出 kind={} pid={} context={}",
                    record.kind, getattr(record.process, "pid", "?"), record.context,
                )

    def active_count(self) -> int:
        with self._lock:
            return len(self._records)

    def _reap(
        self, process: subprocess.Popen, reason: str, grace_seconds: float,
    ) -> None:
        if self._wait_quietly(process, grace_seconds):
            self.unregister(process)
            return
        self._kill_tree(process, reason)
        if self._wait_quietly(process, 1.0):
            self.unregister(process)
        else:
            logger.error(
                "子进程强制回收后仍未退出 pid={} reason={}",
                getattr(process, "pid", "?"), reason,
            )

    @staticmethod
    def _send_terminate(process: subprocess.Popen, reason: str) -> None:
        try:
            if process.poll() is None:
                logger.info(
                    "请求终止子进程 pid={} reason={}",
                    getattr(process, "pid", "?"), reason,
                )
                process.terminate()
        except Exception:
            logger.exception(
                "终止子进程失败 pid={} reason={}",
                getattr(process, "pid", "?"), reason,
            )

    @staticmethod
    def _wait_quietly(process: subprocess.Popen, timeout: float) -> bool:
        try:
            process.wait(timeout=max(0.0, timeout))
            return True
        except (subprocess.TimeoutExpired, TimeoutError):
            return False
        except TypeError:
            # Some test doubles and older wrappers do not accept timeout.
            try:
                process.wait()
                return True
            except Exception:
                return False
        except Exception:
            return process.poll() is not None

    @staticmethod
    def _kill_tree(process: subprocess.Popen, reason: str) -> None:
        pid = getattr(process, "pid", None)
        logger.warning("强制回收子进程树 pid={} reason={}", pid or "?", reason)
        if isinstance(pid, int) and pid > 0:
            try:
                parent = psutil.Process(pid)
                descendants = parent.children(recursive=True)
                for child in descendants:
                    try:
                        child.terminate()
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
                _, alive = psutil.wait_procs(descendants, timeout=0.5)
                for child in alive:
                    try:
                        child.kill()
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
                try:
                    parent.kill()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            except (psutil.NoSuchProcess, psutil.AccessDenied, ValueError):
                pass
        try:
            if process.poll() is None:
                process.kill()
        except Exception:
            pass


process_supervisor = ProcessSupervisor()
