"""Sweep manifests — the resume state and the index over a sweep's tiles.

One JSON per sweep at ``data/sweeps/<sweep_id>.json``. Mirrors ``runs/record.py``
deliberately, down to the atomic write and the secret assertion: the dev path
takes the Anthropic key in the request body, so anything that persists a config
dict is one careless spread away from writing a key to disk.

WHY A TILE-SUCCESS CLASSIFIER EXISTS
------------------------------------
"Steps for Raghav 3.0" §40.2 says "resume = skip tiles marked complete, retry
failed", which assumes a tile that returned cleanly did some work. It did not
necessarily. ``orchestrator.run_analysis`` calls ``recorder.set_status("completed")``
unconditionally, at the end of the try block, whether or not any agent produced
anything — and there is a real record on disk proving the case:
``data/runs/20260813T024442Z_35f1f885-*.json`` has all six agents ``failed``,
``llm_calls: 0``, and top-level status ``completed``.

Resuming on that signal would mark a whole corridor complete having scored
nothing, and the map would show the region uniformly at the zero-confidence fill
value — which looks like barren ground rather than like a failure. So a tile's
outcome is classified from what it actually produced (``classify_tile``), never
from the fact that the call returned.
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from app.config import SWEEPS_DIR
from app.runs.record import _utc_now, assert_no_secrets

logger = logging.getLogger(__name__)

#: Per-tile lifecycle. `pending` is also where an interrupted tile is put back,
#: so an abandoned sweep resumes by re-running at most one tile rather than
#: treating a half-finished one as failed and giving up on it.
TILE_PENDING = "pending"
TILE_RUNNING = "running"
TILE_COMPLETE = "complete"
TILE_FAILED = "failed"

SWEEP_PENDING = "pending"
SWEEP_RUNNING = "running"
SWEEP_COMPLETE = "complete"
SWEEP_PARTIAL = "partial"
SWEEP_CANCELLED = "cancelled"
SWEEP_FAILED = "failed"


@dataclass
class TileOutcome:
    """What a tile run actually produced, independent of whether it returned."""

    status: str
    cells_scored: int
    agents_completed: int
    agents_failed: int
    llm_calls: int
    reason: Optional[str] = None


def classify_tile(
    scored_cells: Optional[Iterable[Any]],
    agent_results: Optional[Dict[str, Any]],
    usage: Optional[Dict[str, Any]] = None,
    cache_hits: int = 0,
) -> TileOutcome:
    """Decide whether a tile really completed. Never trusts a clean return.

    A tile counts as complete when at least one agent completed AND at least one
    cell carries a real (confidence > 0) score. Both halves are needed:

    * every agent failing still yields a full grid of zero-confidence
      placeholders, because the engine fills the grid so coverage is never lost;
    * and an agent can report "completed" having parsed nothing.

    ``llm_calls == 0`` is not by itself a failure — a fully cached re-sweep makes
    no calls and is the workflow the cache exists for — so it is recorded but
    only damning when nothing was scored and nothing was served from cache.
    """
    cells = list(scored_cells or [])
    results = agent_results or {}

    def _get(obj: Any, key: str, default: Any = None) -> Any:
        if isinstance(obj, dict):
            return obj.get(key, default)
        return getattr(obj, key, default)

    completed = sum(1 for r in results.values() if _get(r, "status") == "completed")
    failed = sum(1 for r in results.values() if _get(r, "status") == "failed")
    llm_calls = int((usage or {}).get("llm_calls") or 0)

    scored = 0
    for c in cells:
        conf = _get(c, "confidence", 0.0) or 0.0
        if conf > 0:
            scored += 1

    if not results:
        return TileOutcome(TILE_FAILED, scored, 0, 0, llm_calls, "no agents ran")
    if completed == 0:
        return TileOutcome(
            TILE_FAILED, scored, completed, failed, llm_calls,
            f"every agent failed ({failed}/{len(results)})",
        )
    if scored == 0:
        return TileOutcome(
            TILE_FAILED, scored, completed, failed, llm_calls,
            "no cell received a confident score — the grid is placeholders only",
        )
    if llm_calls == 0 and cache_hits == 0:
        return TileOutcome(
            TILE_FAILED, scored, completed, failed, llm_calls,
            "no LLM calls and no cache hits, yet cells appeared — refusing to trust it",
        )
    return TileOutcome(TILE_COMPLETE, scored, completed, failed, llm_calls)


class SweepManifest:
    """Read/write wrapper over one sweep's JSON.

    Every mutation rewrites the whole file atomically. That is wasteful and
    correct: a sweep runs for hours, and the failure mode being defended against
    is a laptop lid closing between tiles. A manifest that is one tile stale
    costs one tile of rework; a half-written manifest costs the whole sweep.
    """

    def __init__(
        self,
        sweep_id: Optional[str] = None,
        sweeps_dir: Optional[Path] = None,
        doc: Optional[Dict[str, Any]] = None,
    ):
        self.sweeps_dir = Path(sweeps_dir) if sweeps_dir else SWEEPS_DIR
        if doc is not None:
            self._doc = doc
            self.sweep_id = doc["sweep_id"]
        else:
            self.sweep_id = sweep_id or uuid.uuid4().hex[:12]
            self._doc = {
                "sweep_id": self.sweep_id,
                "status": SWEEP_PENDING,
                "created_at": _utc_now(),
                "updated_at": _utc_now(),
                "inputs": {},
                "provenance": {},
                "estimate": {},
                "tiles": [],
                "totals": {},
                "error": None,
            }

    # --- construction ------------------------------------------------------

    def set_inputs(self, **kwargs: Any) -> None:
        self._doc["inputs"].update(kwargs)

    def set_provenance(self, block: Dict[str, Any]) -> None:
        self._doc["provenance"] = block

    def set_estimate(self, block: Dict[str, Any]) -> None:
        self._doc["estimate"] = block

    def set_tiles(self, tiles: Iterable[Any]) -> None:
        """Seed the tile list from ``sweeps.tiles.Tile`` objects."""
        self._doc["tiles"] = [
            {
                "tile_id": t.tile_id,
                "status": TILE_PENDING,
                "cell_count": t.cell_count,
                "prompt_cell_count": t.prompt_cell_count,
                "core_cell_ids": list(t.core_cell_ids),
                "halo_cell_ids": list(t.halo_cell_ids),
                "resolution_m": t.resolution_m,
                "block": t.block,
                "tile_col": t.tile_col,
                "tile_row": t.tile_row,
                "run_id": None,
                "started_at": None,
                "completed_at": None,
                "cells_scored": 0,
                "error": None,
                "usage": {},
            }
            for t in tiles
        ]
        # Populate totals immediately. Without this the history list reports
        # `tiles: None` for a sweep that has been created but not started, which
        # reads as a broken sweep rather than a pending one.
        self._recount()

    # --- lifecycle ---------------------------------------------------------

    @property
    def doc(self) -> Dict[str, Any]:
        return self._doc

    @property
    def status(self) -> str:
        return self._doc["status"]

    @property
    def tiles(self) -> List[Dict[str, Any]]:
        return self._doc["tiles"]

    def tile(self, tile_id: str) -> Optional[Dict[str, Any]]:
        return next((t for t in self.tiles if t["tile_id"] == tile_id), None)

    def pending_tiles(self) -> List[Dict[str, Any]]:
        """Tiles still to do, in order. Resume is exactly this list.

        `running` is included: a tile marked running in a manifest that is being
        read back was interrupted, because nothing else could have left it that
        way. Re-running it is safe (cells are idempotent and cached) and is
        cheaper than reasoning about how far it got.
        """
        return [t for t in self.tiles if t["status"] in (TILE_PENDING, TILE_RUNNING)]

    def mark_tile_running(self, tile_id: str) -> None:
        t = self.tile(tile_id)
        if t is None:
            return
        t["status"] = TILE_RUNNING
        t["started_at"] = _utc_now()
        t["error"] = None
        self._doc["status"] = SWEEP_RUNNING
        self.write()

    def mark_tile_outcome(
        self,
        tile_id: str,
        outcome: TileOutcome,
        run_id: Optional[str] = None,
        usage: Optional[Dict[str, Any]] = None,
    ) -> None:
        t = self.tile(tile_id)
        if t is None:
            return
        t["status"] = outcome.status
        t["completed_at"] = _utc_now()
        t["cells_scored"] = outcome.cells_scored
        t["run_id"] = run_id
        t["error"] = outcome.reason
        t["usage"] = usage or {}
        self._recount()
        self.write()

    def release_tile(self, tile_id: str) -> None:
        """Put an in-flight tile back to `pending` on interruption.

        Not `failed`: nothing is known to be wrong with it. Marking it failed
        would make Resume skip it on the retry-failed path or, worse, make a
        cancelled sweep look broken rather than resumable.
        """
        t = self.tile(tile_id)
        if t is None or t["status"] != TILE_RUNNING:
            return
        t["status"] = TILE_PENDING
        t["started_at"] = None
        self.write()

    def finish(self, cancelled: bool = False, error: Optional[str] = None) -> None:
        self._recount()
        counts = self._doc["totals"]
        if error:
            self._doc["status"] = SWEEP_FAILED
            self._doc["error"] = error
        elif cancelled:
            self._doc["status"] = SWEEP_CANCELLED
        elif counts.get("failed"):
            self._doc["status"] = SWEEP_PARTIAL
        elif counts.get("pending"):
            self._doc["status"] = SWEEP_PARTIAL
        else:
            self._doc["status"] = SWEEP_COMPLETE
        self.write()

    def _recount(self) -> None:
        c = {TILE_PENDING: 0, TILE_RUNNING: 0, TILE_COMPLETE: 0, TILE_FAILED: 0}
        cells = 0
        tok_in = tok_out = calls = 0
        cost = 0.0
        for t in self.tiles:
            c[t["status"]] = c.get(t["status"], 0) + 1
            cells += t.get("cells_scored") or 0
            u = t.get("usage") or {}
            tok_in += u.get("input_tokens") or 0
            tok_out += u.get("output_tokens") or 0
            calls += u.get("llm_calls") or 0
            cost += u.get("est_cost_usd") or 0.0
        self._doc["totals"] = {
            "tiles": len(self.tiles),
            "pending": c[TILE_PENDING] + c[TILE_RUNNING],
            "complete": c[TILE_COMPLETE],
            "failed": c[TILE_FAILED],
            "cells_scored": cells,
            "input_tokens": tok_in,
            "output_tokens": tok_out,
            "llm_calls": calls,
            "est_cost_usd": round(cost, 6),
        }

    @property
    def is_complete(self) -> bool:
        """Every tile finished successfully.

        Region-wide normalization is gated on this (§40.2): normalizing a
        partial sweep would rank the swept part against itself and then quietly
        re-rank it when the rest arrived.
        """
        return bool(self.tiles) and all(
            t["status"] == TILE_COMPLETE for t in self.tiles
        )

    # --- io ----------------------------------------------------------------

    @property
    def path(self) -> Path:
        return self.sweeps_dir / f"{self.sweep_id}.json"

    def write(self) -> Optional[Path]:
        try:
            self._doc["updated_at"] = _utc_now()
            assert_no_secrets(self._doc)
            self.sweeps_dir.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps(self._doc, indent=2, default=str), encoding="utf-8"
            )
            os.replace(tmp, self.path)
            return self.path
        except Exception as exc:
            logger.warning("[%s] Could not write sweep manifest: %s", self.sweep_id, exc)
            return None

    @classmethod
    def load(cls, sweep_id: str, sweeps_dir: Optional[Path] = None) -> "SweepManifest":
        d = Path(sweeps_dir) if sweeps_dir else SWEEPS_DIR
        doc = json.loads((d / f"{sweep_id}.json").read_text(encoding="utf-8"))
        return cls(doc=doc, sweeps_dir=d)


def list_sweeps(sweeps_dir: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Summaries of every sweep on disk, newest first."""
    d = Path(sweeps_dir) if sweeps_dir else SWEEPS_DIR
    if not d.exists():
        return []
    out: List[Dict[str, Any]] = []
    for p in sorted(d.glob("*.json"), reverse=True):
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue  # a .tmp left by a crash, or a hand-edited file
        out.append(
            {
                "sweep_id": doc.get("sweep_id"),
                "status": doc.get("status"),
                "created_at": doc.get("created_at"),
                "updated_at": doc.get("updated_at"),
                "target_mineral": (doc.get("inputs") or {}).get("target_mineral"),
                "resolution_m": (doc.get("inputs") or {}).get("resolution_m"),
                "totals": doc.get("totals") or {},
                "resumable": bool(
                    [
                        t
                        for t in (doc.get("tiles") or [])
                        if t.get("status") in (TILE_PENDING, TILE_RUNNING, TILE_FAILED)
                    ]
                ),
            }
        )
    out.sort(key=lambda s: s.get("created_at") or "", reverse=True)
    return out


def delete_sweep(sweep_id: str, sweeps_dir: Optional[Path] = None) -> bool:
    d = Path(sweeps_dir) if sweeps_dir else SWEEPS_DIR
    p = d / f"{sweep_id}.json"
    if not p.exists():
        return False
    p.unlink()
    return True
