#!/usr/bin/env python3
"""BDSP PC rebuild - automated project setup: turns the user's own game dump into this Unity project's assets.

  python setup/setup.py --dump <folder with exefs\\ and romfs\\> [--unity <Unity.exe 2019.4.27f1>] [--wwise <project with the
                        Launcher's Wwise integration>] [--assetripper <AssetRipper.GUI.Free.exe>] [--work <folder>]
                        [--from STAGE] [--only STAGE] [--until STAGE] [--keep-backups] [--list]

Each stage runs as its own process with a log in <work>/logs/<stage>.log; <work>/state.json records the finished stages, so
running the command again resumes after the last one that finished (--from / --only re-run on purpose). Settings given once are
kept in <work>/config.json.
"""
import argparse
import datetime
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))
import paths  # noqa: E402

PY = [sys.executable, "-I"]
STAGES_DIR = os.path.join(paths.SETUP, "stages")


def stage_cmd(script, *args):
    return PY + [os.path.join(STAGES_DIR, script)] + list(args)


# (name, what it does, command or None while not ported yet)
STAGES = [
    ("check", "preflight: dump, Unity, Wwise, tools, disk, RAM", stage_cmd("check.py")),
    ("export", "AssetRipper exports the dump into <work>/export", stage_cmd("ar_export.py")),
    ("normalise", "the export's random asset GUIDs -> this project's GUIDs; package/Wwise/DOTween script references",
     stage_cmd("normalise_guids.py")),
    ("merge", "move the exported asset folders into Assets/ (sound banks Switch -> Windows)", stage_cmd("merge.py")),
    ("wwise", "copy the Wwise integration files in; AkInitializer.LateUpdate / AkGameObj.SetPosition public",
     stage_cmd("wwise.py")),
    ("startup", "StartupSettings: load assets from the project in the editor", None),
    ("shaders", "install the recovered shaders over the exported placeholders", None),
    ("containers", "scan the shipped bundles' container tables; map them to project files", None),
    ("atlas_spec", "SpriteAtlas rebuild spec from the shipped atlases", None),
    ("sprite_bake", "bake the atlas binding into packed sprites", None),
    ("atlases", "first Unity import + build the sprite atlases (Unity editor)", None),
    ("placeholders", "point the exported SpriteAtlas placeholders at the rebuilt atlases; write bundle_map.tsv", None),
    ("sprite_restore", "restore the shipped sprite geometry", None),
    ("d7", "every texture holds the shipped texels (incl. the editor template + verify steps)", None),
    ("untag", "empty the runtime atlas tags of packed sprites (needed for Windows bundles)", None),
    ("verify", "reimport, audits, play smoke (Unity editor)", None),
]
NAMES = [s[0] for s in STAGES]


def log(msg):
    line = "%s %s" % (datetime.datetime.now().strftime("%H:%M:%S"), msg)
    print(line, flush=True)
    with open(paths.work("logs", "setup.log"), "a", encoding="utf-8") as f:
        f.write(line + "\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    for k in ("dump", "unity", "wwise", "assetripper", "work"):
        ap.add_argument("--" + k)
    ap.add_argument("--from", dest="start", choices=NAMES)
    ap.add_argument("--only", choices=NAMES)
    ap.add_argument("--until", choices=NAMES)
    ap.add_argument("--keep-backups", action="store_true")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()
    if a.list:
        for n, what, cmd in STAGES:
            print("%-15s %s%s" % (n, what, "" if cmd else "   [not ported yet]"))
        return
    cfg = dict(paths.CFG)
    for k in ("dump", "unity", "wwise", "assetripper", "work"):
        v = getattr(a, k)
        if v:
            cfg[k] = os.path.abspath(v)
    if a.keep_backups:
        cfg["keep_backups"] = True
    os.makedirs(os.path.dirname(paths.CONFIG_PATH), exist_ok=True)
    with open(paths.CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=1)
    os.environ["BDSP_SETUP_CONFIG"] = paths.CONFIG_PATH
    state_path = paths.work("state.json")
    state = json.load(open(state_path, encoding="utf-8")) if os.path.exists(state_path) else {}
    names = NAMES
    if a.only:
        names = [a.only]
    else:
        if a.start:
            names = names[names.index(a.start):]
        if a.until:
            names = names[:names.index(a.until) + 1]
    for n, what, cmd in STAGES:
        if n not in names:
            continue
        if state.get(n, {}).get("done") and not (a.only or a.start):
            log("skip %-14s (done %s)" % (n, state[n]["done"]))
            continue
        if cmd is None:
            sys.exit("stage '%s' is not ported yet - stopping before it" % n)
        log("STAGE %-13s %s" % (n, what))
        with open(paths.work("logs", n + ".log"), "a", encoding="utf-8") as lf:
            lf.write("\n=== %s %s\n" % (datetime.datetime.now().isoformat(timespec="seconds"), " ".join(cmd)))
            lf.flush()
            p = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 text=True, encoding="utf-8", errors="replace")
            for line in p.stdout:
                lf.write(line)
                print("    " + line.rstrip(), flush=True)
            rc = p.wait()
        if rc != 0:
            log("FAILED %s (exit %d) - see %s; fix and run again to resume here" % (n, rc, paths.work("logs", n + ".log")))
            sys.exit(rc)
        state[n] = {"done": datetime.datetime.now().isoformat(timespec="seconds")}
        with open(state_path, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=1)
    log("finished: %s" % ", ".join(names))


if __name__ == "__main__":
    main()
