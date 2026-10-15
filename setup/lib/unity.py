#!/usr/bin/env python3
"""Run one Unity editor method on this project for the setup, under a watchdog (was tools/import_watchdog.py).

  python -I setup/lib/unity.py --method <Class.Method> [--marker <log text that proves success>] [--quit] [--timeout <min>]

Unity = config key `unity`. Windowed by default (a window opens and closes by itself); config `unity_batchmode: true` adds
-batchmode -nographics (needs a licence that allows it). The editor scripts get BDSP_SETUP_WORK = the setup's work folder.
Every 20 s: free space on the project's drive and on the system drive (Unity's caches), free RAM; below a floor the editor's whole
process tree is stopped. Success = exit code 0, no "Scripts have compiler errors", and the marker in the log when one is given.
Log: <work>/logs/unity_<method>.log.
"""
import argparse
import ctypes
import os
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paths  # noqa: E402


def free_ram_gb():
    class M(ctypes.Structure):
        _fields_ = [("l", ctypes.c_ulong), ("ml", ctypes.c_ulong)] + [(n, ctypes.c_ulonglong) for n in
                                                                       ("t", "a", "tp", "ap", "tv", "av", "x")]
    m = M()
    m.l = ctypes.sizeof(M)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
    return m.a / 2 ** 30


def free_gb(path):
    return shutil.disk_usage(path).free / 2 ** 30


def run(method, marker=None, quit_after=False, timeout_min=0, min_disk=3.0, min_sys=1.0, min_ram=6.0):
    unity = paths.need(paths.CFG.get("unity"), "unity")
    log = paths.work("logs", "unity_%s.log" % method.replace(".", "_"))
    cmd = [unity, "-projectPath", paths.PROJECT, "-logFile", log, "-executeMethod", method]
    if quit_after:
        cmd.append("-quit")
    if paths.CFG.get("unity_batchmode"):
        cmd += ["-batchmode", "-nographics"]
    env = dict(os.environ, BDSP_SETUP_WORK=paths.WORK)
    proj_drive = os.path.splitdrive(paths.PROJECT)[0] + "\\"
    sys_drive = os.environ.get("SystemDrive", "C:") + "\\"
    t0 = time.time()
    print("unity: %s (log %s)" % (method, log), flush=True)
    time.sleep(3)                                    # never launch right after another editor exited
    p = subprocess.Popen(cmd, env=env)
    last = 0
    reason = None
    while p.poll() is None:
        time.sleep(20)
        d, s, r = free_gb(proj_drive), free_gb(sys_drive), free_ram_gb()
        if d < min_disk:
            reason = "project drive %.1f GB free" % d
        elif s < min_sys:
            reason = "system drive %.1f GB free" % s
        elif r < min_ram:
            reason = "free RAM %.1f GB" % r
        elif timeout_min and time.time() - t0 > timeout_min * 60:
            reason = "timeout %d min" % timeout_min
        if reason:
            print("STOPPING Unity: " + reason, flush=True)
            subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], capture_output=True)
            break
        if time.time() - last >= 300:
            last = time.time()
            print("  %5.1f min | project drive %.1f GB free | RAM %.1f GB free" % ((time.time() - t0) / 60, d, r), flush=True)
    rc = p.wait()
    txt = open(log, encoding="utf-8", errors="replace").read() if os.path.exists(log) else ""
    ok = rc == 0 and not reason and "Scripts have compiler errors" not in txt and (marker is None or marker in txt)
    print("unity: %s exit %s after %.1f min; compiler errors %s; marker %s -> %s" % (
        method, rc, (time.time() - t0) / 60, "Scripts have compiler errors" in txt,
        "-" if marker is None else (marker in txt), "OK" if ok else "FAILED"), flush=True)
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--method", required=True)
    ap.add_argument("--marker")
    ap.add_argument("--quit", action="store_true")
    ap.add_argument("--timeout", type=float, default=0)
    a = ap.parse_args()
    sys.exit(0 if run(a.method, a.marker, a.quit, a.timeout) else 1)


if __name__ == "__main__":
    main()
