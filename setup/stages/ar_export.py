#!/usr/bin/env python3
"""Unattended AssetRipper export of the game dump into a Unity project - the first stage of the automated setup (author, s46:
"I want the players to be able to setup the project with as few steps as possible, so automate everything as much as possible").

Drives AssetRipper's GUI server headless over its local HTTP API (the same calls the browser UI makes): start the server, apply the
export settings (POST /Settings/Update, then read them back and verify), POST /LoadFolder <game root>, POST /Export/UnityProject
<out>, then follow the log until "Finished post-export" and stop the server. Guards every 20 s: free space on the output drive and
the memory Windows can still hand out (RAM + page file; free physical RAM alone is no floor - paging is normal on a 16 GB PC) -
below a floor the server is stopped (memory rules: disk budget before any run, one heavy job at a time).

  python -I setup/stages/ar_export.py [--exe <AssetRipper.GUI.*.exe>] [--game <game root>] [--out <folder>] [--port 29232]
        [--min-disk 2] [--min-commit 3] [--status <file>] [--heap-limit-gb N] [--workstation-gc]
Defaults come from the setup config (setup/lib/paths.py): assetripper, dump, <work>/export. A half-finished export left by an
interrupted run is removed first; a finished one is kept (EXPORT_DONE marker) and the stage succeeds without exporting again.

<game root> holds exefs\\main + romfs\\Data (AssetRipper's Switch detection needs the NSO + metadata; never romfs\\Data alone).
Settings = the ones the s3 export (Premium 1.3.14) used; options the running edition does not offer are reported, not fatal.
"""
import argparse
import ctypes
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))
import assetripper_settings as ars  # noqa: E402
import paths  # noqa: E402

# the s3 export's settings (notes/assetripper_settings_before.html); SaveSettingsToDisk off = nothing written next to the exe
SETTINGS = {
    "BundledAssetsExportMode": "DirectExport", "ScriptContentLevel": "Level3", "AudioExportFormat": "Default",
    "ImageExportFormat": "Png", "LightmapTextureExportFormat": "Yaml", "SpriteExportMode": "Yaml", "ShaderExportMode": "Decompile",
    "TextExportMode": "Parse", "ScriptLanguageVersion": "AutoSafe", "ScriptExportMode": "Decompiled",
    "IgnoreStreamingAssets": False, "EnableStaticMeshSeparation": True, "RemoveNullableAttributes": False,
    "PublicizeAssemblies": False, "EnablePrefabOutlining": False, "EnableAssetDeduplication": False,
    "ScriptTypesFullyQualified": False, "PreferOriginalTextureExtension": True, "ExportUnreadableAssets": False,
    "SaveSettingsToDisk": False, "DefaultVersion": "0.0.0a0", "TargetVersion": "0.0.0a0",
}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


OPENER = urllib.request.build_opener(NoRedirect)


def http(port, path, form=None, timeout=7200):
    url = "http://localhost:%d%s" % (port, path)
    data = urllib.parse.urlencode(form).encode() if form is not None else None
    try:
        with OPENER.open(urllib.request.Request(url, data=data), timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:          # 302 = success for the POST endpoints
        return e.code, ""


def memory_gb():
    """(free physical RAM, free commit = what Windows can still hand out from RAM + page file) in GB."""
    class M(ctypes.Structure):
        _fields_ = [("l", ctypes.c_ulong), ("ml", ctypes.c_ulong)] + [(n, ctypes.c_ulonglong) for n in
                                                                       ("t", "a", "tp", "ap", "tv", "av", "x")]
    m = M()
    m.l = ctypes.sizeof(M)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
    return m.a / 2 ** 30, m.ap / 2 ** 30


def free_ram_gb():
    return memory_gb()[0]


# AssetRipper's .NET heap cap. Measured on BDSP v1.3.0 (2026-10-15, work/setup/memtest): 10 GB -> OutOfMemory while loading,
# 14 GB -> OutOfMemory while exporting, 20 GB -> complete (all 175,208 asset paths, 0 errors, 8.3 min, peak commit 21.2 GB).
# Without a cap the GC grows to what the machine offers (~28 GB on a 96 GB PC), which pushes a 32 GB PC into paging.
HEAP_GB = 20


def proc_mem_gb(pid):
    """(peak private commit, peak working set, current working set) of one process in GB, or None."""
    class PMC(ctypes.Structure):
        _fields_ = [("cb", ctypes.c_ulong), ("faults", ctypes.c_ulong)] + [(n, ctypes.c_size_t) for n in
                                                                            ("pws", "ws", "qppp", "qpp", "qpnp", "qnp", "pf", "ppf")]
    h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)        # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return None
    try:
        c = PMC()
        c.cb = ctypes.sizeof(PMC)
        if not ctypes.windll.kernel32.K32GetProcessMemoryInfo(h, ctypes.byref(c), c.cb):
            return None
        return c.ppf / 2 ** 30, c.pws / 2 ** 30, c.ws / 2 ** 30
    finally:
        ctypes.windll.kernel32.CloseHandle(h)


def mem_text(pid):
    m = proc_mem_gb(pid)
    return "AssetRipper peak commit %.1f GB, peak working set %.1f GB, now %.1f GB" % m if m else "AssetRipper memory n/a"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", default=paths.CFG.get("assetripper"))
    ap.add_argument("--game", default=paths.DUMP)
    ap.add_argument("--out", default=paths.EXPORT)
    ap.add_argument("--port", type=int, default=29232)
    ap.add_argument("--min-disk", type=float, default=2.0)
    ap.add_argument("--min-commit", type=float, default=3.0, help="GB of RAM + page file that must stay available")
    ap.add_argument("--status", default=None)
    ap.add_argument("--heap-limit-gb", type=float, default=float(paths.CFG.get("assetripper_heap_gb", HEAP_GB)),
                    help="cap AssetRipper's .NET heap (DOTNET_GCHeapHardLimit); 0 = no cap. Default %d (config "
                         "assetripper_heap_gb)" % HEAP_GB)
    ap.add_argument("--workstation-gc", action="store_true", help="DOTNET_gcServer=0 (one GC heap instead of one per CPU)")
    a = ap.parse_args()
    out = os.path.abspath(a.out)
    work = os.path.dirname(out)
    os.makedirs(work, exist_ok=True)
    log = os.path.join(work, os.path.basename(out) + "_assetripper.log")
    status = open(a.status or os.path.join(work, os.path.basename(out) + "_status.txt"), "a", encoding="utf-8")

    def say(msg):
        line = time.strftime("%H:%M:%S ") + msg
        print(line, flush=True)
        status.write(line + "\n")
        status.flush()

    if not a.exe or not a.game:
        sys.exit("AssetRipper exe or game dump not configured (run setup.py with --assetripper / --dump)")
    done_marker = os.path.join(out, "EXPORT_DONE")
    if os.path.exists(done_marker):
        say("export already complete (%s) - kept" % done_marker)
        return
    if os.path.exists(out) and os.listdir(out):
        say("removing the half-finished export of an interrupted run: " + out)
        shutil.rmtree(out)
    os.makedirs(work, exist_ok=True)
    drive = os.path.splitdrive(out)[0] + "\\"
    say("START disk %s %.1f GB free, RAM %.1f GB free, RAM + page file %.1f GB free; floors disk %.1f, RAM + page file %.1f" % (
        drive, shutil.disk_usage(drive).free / 2 ** 30, *memory_gb(), a.min_disk, a.min_commit))
    env = dict(os.environ, DOTNET_Thread_DefaultStackSize="1000000", COMPlus_Thread_DefaultStackSize="1000000")
    if a.heap_limit_gb:
        env["DOTNET_GCHeapHardLimit"] = "%X" % int(a.heap_limit_gb * 2 ** 30)
    if a.workstation_gc:
        env["DOTNET_gcServer"] = "0"
    say("GC environment: %s" % (", ".join("%s=%s" % (k, env[k]) for k in ("DOTNET_GCHeapHardLimit", "DOTNET_gcServer") if k in env)
                                or "AssetRipper's defaults"))
    srv = subprocess.Popen([a.exe, "--headless", "--port", str(a.port), "--log-path", log], cwd=os.path.dirname(a.exe), env=env,
                           stdout=open(os.path.join(work, os.path.basename(out) + "_stdout.log"), "w"), stderr=subprocess.STDOUT)

    def stop(reason):
        say("STOP server (%s)" % reason)
        subprocess.run(["taskkill", "/PID", str(srv.pid), "/T", "/F"], capture_output=True)

    try:
        for _ in range(90):
            time.sleep(2)
            try:
                http(a.port, "/", timeout=5)
                break
            except OSError:
                continue
        else:
            stop("server did not come up")
            sys.exit(1)
        page = os.path.join(work, os.path.basename(out) + "_settings_default.html")
        open(page, "w", encoding="utf-8").write(http(a.port, "/Settings/Edit")[1])
        offered = ars.parse(page)
        form, missing = {}, []
        for k, v in SETTINGS.items():
            if k not in offered or offered[k].endswith("LOCKED"):
                missing.append(k + ("" if k not in offered else " (locked: %s)" % offered[k].split()[0]))
            elif isinstance(v, bool):
                if v:
                    form[k] = ""             # a checked box posts its value="" as the browser does; unchecked = absent
            else:
                form[k] = v
        say("settings not offered by this edition: %s" % (", ".join(missing) or "none"))
        code, _ = http(a.port, "/Settings/Update", form)
        page2 = os.path.join(work, os.path.basename(out) + "_settings_used.html")
        open(page2, "w", encoding="utf-8").write(http(a.port, "/Settings/Edit")[1])
        now = ars.parse(page2)
        bad = [k for k, v in SETTINGS.items() if k in now and not now[k].endswith("LOCKED") and
               now[k].split()[0] != (v if not isinstance(v, bool) else ("checked" if v else "off"))]
        say("Settings/Update http %s; settings not as wanted after update: %s" % (code, ", ".join(bad) or "none"))
        if bad:
            stop("settings did not apply")
            sys.exit(1)
        t0 = time.time()
        say("LoadFolder %s" % a.game)
        code, _ = http(a.port, "/LoadFolder", {"Path": a.game})
        say("LoadFolder http %s after %.0f s" % (code, time.time() - t0))
        if code not in (200, 302):
            oom = os.path.exists(log) and "OutOfMemoryException" in open(log, encoding="utf-8", errors="replace").read()
            stop("loading the dump failed" + (" - AssetRipper ran out of memory" if oom else ""))
            sys.exit("AssetRipper could not load the dump (http %s)%s - see %s" % (
                code, "; it ran out of memory: close other programs and make sure Windows' page file can grow" if oom else "", log))
        os.makedirs(out, exist_ok=True)
        say("Export/UnityProject -> %s" % out)
        exp = subprocess.Popen([sys.executable, "-I", "-c",
                                "import urllib.request,urllib.parse,sys\n"
                                "try: urllib.request.urlopen(urllib.request.Request(sys.argv[1], data=urllib.parse.urlencode("
                                "{'Path': sys.argv[2]}).encode()), timeout=36000)\n"
                                "except Exception as e: print(e)\n",
                                "http://localhost:%d/Export/UnityProject" % a.port, out], stdin=subprocess.DEVNULL)
        last = 0
        read_to = 0
        while True:
            time.sleep(20)
            disk = shutil.disk_usage(drive).free / 2 ** 30
            ram, commit = memory_gb()
            tail = ""
            if os.path.exists(log):
                with open(log, "rb") as f:
                    f.seek(read_to)
                    new = f.read()
                    read_to += len(new)
                if b"OutOfMemoryException" in new:
                    # AssetRipper carries on after it (e.g. "Unable to read MonoBehaviour Structure ... (OutOfMemoryException)")
                    # and would write an incomplete export
                    stop("AssetRipper ran out of memory")
                    sys.exit("AssetRipper ran out of memory during the export - close other programs and make sure Windows' page "
                             "file can grow (it is on the system drive), then run the setup again; log: %s" % log)
                with open(log, "rb") as f:
                    f.seek(max(0, os.path.getsize(log) - 4096))
                    lines = f.read().decode("utf-8", "replace").strip().splitlines()
                tail = lines[-1][:120] if lines else ""
                if any("Finished post-export" in l for l in lines):
                    say("DONE after %.1f min; disk %.1f GB free, RAM %.1f GB free; %s" % ((time.time() - t0) / 60, disk, ram,
                                                                                         mem_text(srv.pid)))
                    break
            if srv.poll() is not None:
                say("server exited by itself rc=%s; log tail: %s" % (srv.returncode, tail))
                sys.exit(1)
            if disk < a.min_disk:
                stop("disk %.1f GB < %.1f" % (disk, a.min_disk))
                sys.exit(1)
            if commit < a.min_commit:
                stop("Windows is about to run out of memory: RAM + page file %.1f GB free < %.1f" % (commit, a.min_commit))
                sys.exit(1)
            if time.time() - last >= 120:
                last = time.time()
                say("%5.1f min | disk %.1f GB free | RAM %.1f GB free | RAM + page file %.1f GB free | %s | %s" % (
                    (time.time() - t0) / 60, disk, ram, commit, mem_text(srv.pid), tail))
        exp.wait(timeout=60)
        with open(done_marker, "w", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S") + "\n")
    finally:
        if srv.poll() is None:
            stop("finished")


if __name__ == "__main__":
    main()
