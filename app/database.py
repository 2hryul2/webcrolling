"""JSONL file I/O with simple per-file locking."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from collections import deque
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel

logger = logging.getLogger(__name__)

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _get_lock(filepath: str) -> threading.Lock:
    """Return (and lazily create) a per-filepath lock."""
    key = os.path.abspath(filepath)
    with _locks_guard:
        if key not in _locks:
            _locks[key] = threading.Lock()
        return _locks[key]


def _ensure_parent(filepath: str) -> None:
    parent = os.path.dirname(os.path.abspath(filepath))
    if parent and not os.path.exists(parent):
        os.makedirs(parent, exist_ok=True)


def _serialize_default(obj: Any) -> Any:
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Type {type(obj)} not serializable")


def append_jsonl(filepath: str, data: BaseModel) -> bool:
    """Append a single Pydantic model as one JSONL line. Returns True on success."""
    lock = _get_lock(filepath)
    try:
        _ensure_parent(filepath)
        if hasattr(data, "to_jsonl"):
            line = data.to_jsonl()
        else:
            line = json.dumps(
                data.model_dump(), default=_serialize_default, ensure_ascii=False
            )
        with lock:
            with open(filepath, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        return True
    except Exception as exc:  # pragma: no cover - logged for diagnosis
        logger.warning("append_jsonl failed for %s: %s", filepath, exc)
        return False


def load_jsonl(filepath: str, limit: Optional[int] = None) -> list[dict]:
    """Load JSONL records. If limit given, return last N records.

    Corrupted lines are skipped with a warning log.
    """
    if not os.path.exists(filepath):
        return []
    lock = _get_lock(filepath)
    results: list[dict] = []
    try:
        with lock:
            with open(filepath, "r", encoding="utf-8") as f:
                if limit is not None:
                    # Use deque for O(1) tail
                    tail = deque(f, maxlen=limit)
                    iterable = list(tail)
                else:
                    iterable = f.readlines()
        for raw in iterable:
            raw = raw.strip()
            if not raw:
                continue
            try:
                results.append(json.loads(raw))
            except json.JSONDecodeError as exc:
                logger.warning("Skipping corrupted JSONL line in %s: %s", filepath, exc)
                continue
    except Exception as exc:  # pragma: no cover
        logger.warning("load_jsonl failed for %s: %s", filepath, exc)
        return []
    return results


def save_state(filepath: str, state: dict) -> None:
    """Save state dict to JSON (atomic-ish write)."""
    lock = _get_lock(filepath)
    _ensure_parent(filepath)
    with lock:
        tmp = filepath + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, default=_serialize_default, ensure_ascii=False, indent=2)
        os.replace(tmp, filepath)


def load_state(filepath: str) -> dict:
    """Load state dict from JSON. Returns {} if missing or invalid."""
    if not os.path.exists(filepath):
        return {}
    lock = _get_lock(filepath)
    try:
        with lock:
            with open(filepath, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as exc:
        logger.warning("load_state failed for %s: %s", filepath, exc)
        return {}


def compute_content_hash(title: str, url: str) -> str:
    """Deterministic MD5 of title + url for dedup."""
    payload = f"{title}|{url}".encode("utf-8")
    return hashlib.md5(payload).hexdigest()


def event_exists(filepath: str, content_hash: str, cache: set) -> bool:
    """Check if an event with this hash has already been recorded.

    Uses an in-memory set for fast lookup. Returns True if present.
    Mutates cache when filepath is read.
    """
    if content_hash in cache:
        return True
    return False


def load_existing_hashes(filepath: str) -> set:
    """Build a set of all content_hash values from an existing events.jsonl."""
    cache: set = set()
    if not os.path.exists(filepath):
        return cache
    lock = _get_lock(filepath)
    try:
        with lock:
            with open(filepath, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                        h = rec.get("content_hash")
                        if h:
                            cache.add(h)
                    except json.JSONDecodeError:
                        continue
    except Exception as exc:  # pragma: no cover
        logger.warning("load_existing_hashes failed for %s: %s", filepath, exc)
    return cache
