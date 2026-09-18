"""Child helper for verify-coexistence.py — holds the mutating mutex, no input.

Also demonstrates the kill switch: run with DSH_CUA_ARBITER=0 and mode=killswitch.
Usage:
    python coex_child.py hold <logfile>
    python coex_child.py killswitch
"""
import json
import sys
import time


from dsh_cua import arbiter

mode = sys.argv[1]

if mode == "hold":
    log = sys.argv[2]
    hold_s = float(sys.argv[3]) if len(sys.argv) > 3 else 1.2
    gate = arbiter.admit_mutating(hard=False)
    assert gate.get("ok"), gate
    with open(log, "a", encoding="utf-8") as f:
        f.write(json.dumps({"pid": "child", "event": "acquired",
                            "hold_s": hold_s, "t": time.time()}) + "\n")
    time.sleep(hold_s)
    with open(log, "a", encoding="utf-8") as f:
        f.write(json.dumps({"pid": "child", "event": "released", "t": time.time()}) + "\n")
    gate["release"]()
elif mode == "killswitch":
    gate = arbiter.admit_mutating(hard=True)
    print(json.dumps({k: v for k, v in gate.items() if k != "release"}))
