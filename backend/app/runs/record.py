"""
Immutable per-run JSON records.

Every analysis writes exactly one file to ``data/runs/``. The record is the
audit trail and the input to the benchmark harness — deliberately files on disk
so it works under ``DEV_MODE=true``, where nothing else is persisted.

The provenance block is the part that makes the rest worth keeping. When a score
moves between two runs there are four candidate causes — a code change, a
knowledge-file change, a prompt change, or plain LLM nondeterminism — and
without recording the first three, every diff is uninterpretable.

Cell geometry is **not** stored: ``cell_id`` regenerates it exactly (see
``scoring/grid.py``), which keeps records small enough to retain thousands.
"""
import hashlib
import json
import logging
import os
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from app.config import RUNS_DIR, settings

logger = logging.getLogger(__name__)

#: Bump by hand whenever an agent's prompt construction changes in a way that
#: could move scores. Recorded in provenance and mixed into the cache key, so a
#: bump both shows up in benchmark diffs and invalidates stale cached scores.
PROMPT_VERSION = "2026-08-01"

#: Keys that must never appear anywhere in a run record.
_SECRET_KEYS = {"anthropic_api_key", "api_key", "apikey", "secret_key", "authorization"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_text(text: Optional[str]) -> str:
    return "sha256:" + hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def canonical_json(obj: Any) -> str:
    """Stable JSON for hashing: sorted keys, no incidental whitespace."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def _git(*args: str) -> Optional[str]:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=RUNS_DIR.parent.parent,
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        ).stdout.strip()
    except Exception:
        return None


_GIT_CACHE: Dict[str, Any] = {}


def git_state() -> Dict[str, Any]:
    """Current commit and whether the tree is dirty.

    ``git_dirty: true`` is a warning that the run is not reproducible at all —
    the commit alone will not recreate the code that produced it.
    """
    if not _GIT_CACHE:
        commit = _git("rev-parse", "--short", "HEAD")
        status = _git("status", "--porcelain")
        _GIT_CACHE.update(
            {
                "git_commit": commit,
                "git_dirty": bool(status) if status is not None else None,
            }
        )
    # Dirtiness can change while the server is running; the commit cannot
    # meaningfully change under it, so only re-check the cheap part.
    status = _git("status", "--porcelain")
    return {
        "git_commit": _GIT_CACHE["git_commit"],
        "git_dirty": bool(status) if status is not None else None,
    }


def provenance_block(
    knowledge_files: Dict[str, Optional[str]],
    agents_without_knowledge: List[str],
    spatial_context_available: bool,
    model: str,
) -> Dict[str, Any]:
    """Everything needed to interpret a score change between two runs.

    ``knowledge_files`` maps ``"<domain>/<file>.md"`` → the file's text (or None
    when the agent ran ungrounded); the text is hashed, not stored.
    """
    return {
        **git_state(),
        "model": model,
        "prompt_version": PROMPT_VERSION,
        "grid_version": _grid_version(),
        "knowledge_files": {
            name: sha256_text(text)
            for name, text in sorted(knowledge_files.items())
            if text is not None
        },
        "agents_without_knowledge": sorted(agents_without_knowledge),
        "spatial_context_available": spatial_context_available,
        "dev_mode": settings.dev_mode,
    }


def _grid_version() -> str:
    """Identifies the cell-id scheme, so a grid change is never silent."""
    from app.scoring import grid

    return (
        f"{grid.GRID_TAG}:{grid.EPSG_ANALYSIS}:"
        f"{grid.GRID_ORIGIN_X},{grid.GRID_ORIGIN_Y}"
    )


def assert_no_secrets(payload: Any, path: str = "") -> None:
    """Fail loudly if a secret-looking key made it into a record.

    In dev mode the Anthropic key arrives in the request body, one careless
    ``config`` passthrough away from being written to disk in plaintext. This is
    cheap insurance against that.
    """
    if isinstance(payload, dict):
        for k, v in payload.items():
            if str(k).lower() in _SECRET_KEYS:
                raise ValueError(
                    f"Refusing to write run record: secret key {path}{k!r} present"
                )
            assert_no_secrets(v, f"{path}{k}.")
    elif isinstance(payload, list):
        for i, v in enumerate(payload):
            assert_no_secrets(v, f"{path}{i}.")


def _thin_cell(cell: Any) -> Dict[str, Any]:
    """A composite cell without its geometry — cell_id regenerates that."""
    d = cell if isinstance(cell, dict) else cell.model_dump()
    d.pop("geometry", None)
    return d


class RunRecorder:
    """Accumulates a run's record and writes it once, atomically.

    Constructed by the orchestrator at the start of a run and written in the
    ``finally`` path so a **failed run still leaves a record** — those are
    diagnostically the most valuable ones.
    """

    def __init__(
        self,
        run_id: Optional[str] = None,
        runs_dir: Optional[Path] = None,
        enabled: Optional[bool] = None,
    ):
        self.run_id = run_id or str(uuid.uuid4())
        self.runs_dir = Path(runs_dir) if runs_dir else RUNS_DIR
        self.enabled = settings.save_run_records if enabled is None else enabled
        self.created_at = _utc_now()
        self._doc: Dict[str, Any] = {
            "run_id": self.run_id,
            "created_at": self.created_at,
            "status": "running",
            "provenance": {},
            "inputs": {},
            "composite_cells": [],
            "agent_results": {},
            "raw_llm": {},
            "timings": {},
            "cache": {"hits": 0, "misses": 0},
        }

    # --- accumulation ----------------------------------------------------

    def set_inputs(self, **kwargs: Any) -> None:
        self._doc["inputs"].update(kwargs)

    def set_provenance(self, block: Dict[str, Any]) -> None:
        self._doc["provenance"] = block

    def set_timings(self, **kwargs: Any) -> None:
        self._doc["timings"].update(kwargs)

    def set_cache_stats(self, hits: int, misses: int) -> None:
        self._doc["cache"] = {"hits": hits, "misses": misses}

    def set_composite_cells(self, cells: Iterable[Any]) -> None:
        self._doc["composite_cells"] = [_thin_cell(c) for c in cells]

    def add_agent_result(self, result: Any) -> None:
        d = result.model_dump() if hasattr(result, "model_dump") else dict(result)
        raw = d.pop("raw_batches", None) or []
        agent_id = d.get("agent_id", "unknown")
        d["cells"] = [_thin_cell(c) for c in d.pop("scored_cells", [])]
        self._doc["agent_results"][agent_id] = d
        if settings.save_raw_llm and raw:
            self._doc["raw_llm"][agent_id] = raw

    def set_status(self, status: str, error: Optional[str] = None) -> None:
        self._doc["status"] = status
        if error:
            self._doc["error"] = error

    # --- write -----------------------------------------------------------

    @property
    def path(self) -> Path:
        stamp = self.created_at.replace(":", "").replace("-", "")
        return self.runs_dir / f"{stamp}_{self.run_id}.json"

    def write(self) -> Optional[Path]:
        """Write the record. Never raises — a bookkeeping failure must not
        take down a run that otherwise succeeded."""
        if not self.enabled:
            return None
        try:
            assert_no_secrets(self._doc)
            self.runs_dir.mkdir(parents=True, exist_ok=True)
            target = self.path
            # Write-then-rename: a crash mid-write leaves the temp file, never
            # a half-written record that the benchmark would silently misread.
            tmp = target.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(self._doc, indent=2, default=str), encoding="utf-8")
            os.replace(tmp, target)
            logger.info(
                "[%s] Run record written: %s (%d cells)",
                self.run_id,
                target,
                len(self._doc["composite_cells"]),
            )
            return target
        except Exception as exc:
            logger.warning("[%s] Could not write run record: %s", self.run_id, exc)
            return None


# --- reading ---------------------------------------------------------------


def list_runs(runs_dir: Optional[Path] = None) -> List[Path]:
    """Run record paths, newest first."""
    d = Path(runs_dir) if runs_dir else RUNS_DIR
    if not d.exists():
        return []
    return sorted(d.glob("*.json"), reverse=True)


def load_run(path: Path) -> Dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))
