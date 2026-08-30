"""relay SessionEnd hook — drops one marker and does nothing else.

Earlier versions started the recorder here. In terminal setups where the
window closes together with claude the hook can be killed mid-flight at any
moment (observed: sometimes before it can log a single line), so recording
moved to SessionStart entirely. What remains is the one fact SessionStart
cannot learn on its own: that this session is over rather than merely quiet.

Without the marker, detection has to wait out the idle threshold, and a
session closed and reopened a minute later would hand nothing to its
successor. Creating an empty file is the cheapest thing this hook could
possibly do, and if even that is cut short the idle rule still catches up —
so the marker is an optimisation, never a dependency.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from relay_common import ended_dir, in_scope, is_disabled, read_hook_input


def main():
    if is_disabled():
        return
    data = read_hook_input()
    cwd = data.get("cwd") or os.getcwd()
    session_id = data.get("session_id")
    if not session_id or not in_scope(cwd):
        return
    d = ended_dir(cwd)
    d.mkdir(parents=True, exist_ok=True)
    open(d / str(session_id), "w").close()


if __name__ == "__main__":
    try:
        main()
    except Exception:  # never break session shutdown, and never spend time
        pass           # explaining why: the idle fallback covers this
    sys.exit(0)
