"""Optional local telemetry, independent of the inference backend."""
import ctypes
import os
from pathlib import Path
import subprocess
import threading
import time


def command(args):
    result = subprocess.run(args, capture_output=True, text=True, timeout=5,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.returncode:
        raise OSError(result.stderr.strip()[:400] or "command failed")
    return result.stdout.strip()


def gpu_info():
    try:
        text = command(["nvidia-smi", "--query-gpu=index,name,power.limit", "--format=csv,noheader,nounits"])
        rows = []
        for line in text.splitlines():
            index, name, power = [part.strip() for part in line.split(",", 2)]
            try:
                power = float(power)
            except ValueError:
                power = None
            rows.append({"index": int(index), "name": name, "power_limit_w": power})
        return {"available": True, "devices": rows}
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        return {"available": False, "reason": str(exc)}


def available_gib():
    if os.name == "nt":
        class Memory(ctypes.Structure):
            _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [
                (name, ctypes.c_ulonglong) for name in
                ("total", "available", "total_page", "available_page", "total_virtual", "available_virtual", "extended")]
        memory = Memory()
        memory.length = ctypes.sizeof(memory)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(memory)):
            return memory.available / 2**30
    else:
        try:
            for line in Path("/proc/meminfo").read_text().splitlines():
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) * 1024 / 2**30
        except OSError:
            pass
    return None


class Monitor:
    def __init__(self, samples, enabled):
        self.samples, self.enabled = samples, enabled
        self.stop = threading.Event()
        self.thread = None

    def start(self):
        if self.enabled:
            self.thread = threading.Thread(target=self._run, daemon=True)
            self.thread.start()

    def _run(self):
        while not self.stop.is_set():
            row = {"time": time.time(), "available_gib": available_gib()}
            try:
                row["gpu"] = command(["nvidia-smi", "--query-gpu=index,utilization.gpu,memory.used,power.draw,clocks.sm",
                                      "--format=csv,noheader,nounits"])
            except (OSError, subprocess.SubprocessError) as exc:
                row["error"] = str(exc)
                self.samples.append(row)
                return
            self.samples.append(row)
            self.stop.wait(1)

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=6)
