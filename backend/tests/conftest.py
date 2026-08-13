"""
Make ``app`` importable, explicitly.

Until this file existed the suite imported `app` by accident: `test_grid.py` and
friends do a bare ``from app.scoring.grid import ...`` with nothing on
``sys.path`` that would satisfy it, and it worked only because pytest imports
every test module during collection and two of them — `test_run_telemetry.py` and
`test_run_cancellation.py`, both hand-run scripts — do a
``sys.path.insert(0, backend)`` at module scope. Collection order did the rest.

That is a real trap: delete or rename either of those scripts, or run a single
test file that sorts before them, and a dozen unrelated tests start failing with
``ModuleNotFoundError: No module named 'app'`` for reasons that have nothing to
do with the change being made. Doing it here makes it deliberate, order-independent,
and true for subprocesses too — see ``PYTHONPATH`` below, which is what
``test_grid.py::test_cell_id_is_stable_across_processes`` needs in order to spawn
a child that can import the grid at all.
"""
import os
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# Propagate to child processes. Tests that shell out to prove a property holds
# outside this interpreter (cell ids being a pure function of coordinates; the
# TypeScript/Python projection parity check) cannot import `app` without it.
_existing = os.environ.get("PYTHONPATH", "")
if str(BACKEND_DIR) not in _existing.split(os.pathsep):
    os.environ["PYTHONPATH"] = (
        f"{BACKEND_DIR}{os.pathsep}{_existing}" if _existing else str(BACKEND_DIR)
    )
