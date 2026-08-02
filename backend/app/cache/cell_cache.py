"""
Cell-level score cache.

One SQLite file at ``data/cache/cells.sqlite``. SQLite because it is the only
option that fits the path everyone actually runs: thousands of tiny JSON files
query badly, and Postgres is unreachable under ``DEV_MODE=true``.

Two rules make this safe rather than merely fast:

**Only absolute values are cached.** ``score``, ``confidence``, ``evidence`` and
``data_sources_used`` are properties of the ground. ``relative_score``,
``percentile`` and ``tier`` are properties of the *comparison* — the same cell
is "high" in a barren polygon and "low" in a rich one — so they are recomputed
by ``engine.normalize_relative()`` on every run and never stored here.

**The key hashes everything that could change the answer**, including the
knowledge file's contents and the spatial context the cell was scored with. The
moment ``structure/gold.md`` is written, every cached structure score becomes
unreachable and gets recomputed; when the PostGIS query starts returning data,
cells that gained features invalidate while cells that genuinely have none keep
their entries.
"""
import hashlib
import json
import logging
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from app.config import CACHE_DIR, settings

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_cell_scores (
  cache_key      TEXT PRIMARY KEY,
  cell_id        TEXT NOT NULL,
  resolution_m   INTEGER NOT NULL,
  agent_id       TEXT NOT NULL,
  mineral        TEXT NOT NULL,
  score          REAL NOT NULL,
  confidence     REAL NOT NULL,
  evidence       TEXT NOT NULL,
  data_sources   TEXT NOT NULL,
  created_at     TEXT NOT NULL,
  run_id         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_lookup
  ON agent_cell_scores (cell_id, agent_id, mineral, resolution_m);
"""


def _canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def cache_key_for(
    cell_id: str,
    agent_id: str,
    mineral: str,
    model: str,
    prompt_version: str,
    knowledge_hash: str,
    spatial_context_hash: str,
) -> str:
    """Hash of every input that could change this cell's score.

    Anything omitted here is something the cache will happily ignore when it
    changes — so if you add a new input to an agent's prompt, add it here too.
    """
    return _sha(
        _canonical(
            {
                "cell_id": cell_id,
                "agent_id": agent_id,
                "mineral": mineral,
                "model": model,
                "prompt_version": prompt_version,
                "knowledge_hash": knowledge_hash,
                "spatial_context_hash": spatial_context_hash,
            }
        )
    )


class CellCache:
    """Thread-safe SQLite wrapper. Every method degrades to a miss on error."""

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else CACHE_DIR / "cells.sqlite"
        self._lock = threading.Lock()
        self._conn: Optional[sqlite3.Connection] = None

    def _connect(self) -> Optional[sqlite3.Connection]:
        if self._conn is not None:
            return self._conn
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self.path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            # WAL so a read during a write does not block the event loop.
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(SCHEMA)
            conn.commit()
            self._conn = conn
            return conn
        except Exception as exc:
            logger.warning("Cell cache unavailable (%s) — running uncached", exc)
            return None

    def get_many(self, keys: Iterable[str]) -> Dict[str, Dict[str, Any]]:
        """Look up many cache keys at once. Missing keys are simply absent."""
        keys = list(keys)
        if not keys:
            return {}
        try:
            with self._lock:
                conn = self._connect()
                if conn is None:
                    return {}
                out: Dict[str, Dict[str, Any]] = {}
                # SQLite caps variables per statement (999 on older builds).
                for i in range(0, len(keys), 500):
                    chunk = keys[i : i + 500]
                    placeholders = ",".join("?" * len(chunk))
                    rows = conn.execute(
                        f"SELECT * FROM agent_cell_scores "
                        f"WHERE cache_key IN ({placeholders})",
                        chunk,
                    ).fetchall()
                    for r in rows:
                        out[r["cache_key"]] = {
                            "cell_id": r["cell_id"],
                            "score": r["score"],
                            "confidence": r["confidence"],
                            "evidence": json.loads(r["evidence"]),
                            "data_sources_used": json.loads(r["data_sources"]),
                            "created_at": r["created_at"],
                            "run_id": r["run_id"],
                        }
                return out
        except Exception as exc:
            logger.warning("Cache read failed (%s) — treating as miss", exc)
            return {}

    def put_many(self, rows: List[Tuple[str, Dict[str, Any]]], run_id: str) -> int:
        """Insert or replace cached scores. Returns the number written."""
        if not rows:
            return 0
        try:
            with self._lock:
                conn = self._connect()
                if conn is None:
                    return 0
                now = datetime.now(timezone.utc).isoformat(timespec="seconds")
                conn.executemany(
                    "INSERT OR REPLACE INTO agent_cell_scores "
                    "(cache_key, cell_id, resolution_m, agent_id, mineral, score, "
                    " confidence, evidence, data_sources, created_at, run_id) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    [
                        (
                            key,
                            v["cell_id"],
                            int(v["resolution_m"]),
                            v["agent_id"],
                            v["mineral"],
                            float(v["score"]),
                            float(v["confidence"]),
                            json.dumps(v.get("evidence", [])),
                            json.dumps(v.get("data_sources_used", [])),
                            now,
                            run_id,
                        )
                        for key, v in rows
                    ],
                )
                conn.commit()
                return len(rows)
        except Exception as exc:
            logger.warning("Cache write failed (%s) — continuing", exc)
            return 0

    def composite_cells_in_bbox(
        self, bbox: Tuple[float, float, float, float], mineral: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Every cached cell whose square intersects ``bbox``, averaged across agents.

        Backs the persistent-coverage map layer. Absolute scores only —
        AOI-relative shading has no common denominator across AOIs.
        """
        from app.scoring.grid import cell_id_to_bbox

        try:
            with self._lock:
                conn = self._connect()
                if conn is None:
                    return []
                sql = (
                    "SELECT cell_id, resolution_m, agent_id, mineral, score, "
                    "confidence, created_at FROM agent_cell_scores"
                )
                params: List[Any] = []
                if mineral:
                    sql += " WHERE mineral = ?"
                    params.append(mineral)
                rows = conn.execute(sql, params).fetchall()
        except Exception as exc:
            logger.warning("Cache scan failed (%s)", exc)
            return []

        min_lon, min_lat, max_lon, max_lat = bbox
        grouped: Dict[str, Dict[str, Any]] = {}
        for r in rows:
            try:
                b = cell_id_to_bbox(r["cell_id"])
            except ValueError:
                continue  # a cell id from a previous grid version
            if b[2] < min_lon or b[0] > max_lon or b[3] < min_lat or b[1] > max_lat:
                continue
            g = grouped.setdefault(
                r["cell_id"],
                {
                    "cell_id": r["cell_id"],
                    "resolution_m": r["resolution_m"],
                    "mineral": r["mineral"],
                    "agents": {},
                    "created_at": r["created_at"],
                },
            )
            g["agents"][r["agent_id"]] = {
                "score": r["score"],
                "confidence": r["confidence"],
            }
            g["created_at"] = max(g["created_at"], r["created_at"])
        return list(grouped.values())

    def stats(self) -> Dict[str, Any]:
        try:
            with self._lock:
                conn = self._connect()
                if conn is None:
                    return {"available": False}
                row = conn.execute(
                    "SELECT COUNT(*) n, COUNT(DISTINCT cell_id) cells, "
                    "COUNT(DISTINCT agent_id) agents FROM agent_cell_scores"
                ).fetchone()
                return {
                    "available": True,
                    "path": str(self.path),
                    "rows": row["n"],
                    "cells": row["cells"],
                    "agents": row["agents"],
                }
        except Exception as exc:
            return {"available": False, "error": str(exc)}

    def clear(self) -> None:
        with self._lock:
            conn = self._connect()
            if conn is not None:
                conn.execute("DELETE FROM agent_cell_scores")
                conn.commit()

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None


_CACHE: Optional[CellCache] = None


def get_cache() -> Optional[CellCache]:
    """Process-wide cache handle, or None when caching is disabled."""
    global _CACHE
    if not settings.cache_enabled:
        return None
    if _CACHE is None:
        _CACHE = CellCache()
    return _CACHE
