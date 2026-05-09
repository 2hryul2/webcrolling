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


def _ensure_owner_only_perms(filepath: str) -> None:
    """Apply 0600 permissions where supported (no-op on Windows). SEC-4."""
    try:
        os.chmod(filepath, 0o600)
    except (NotImplementedError, OSError):
        # Windows or restricted filesystems — silently skip; documented limitation.
        pass


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
            file_existed = os.path.exists(filepath)
            with open(filepath, "a", encoding="utf-8") as f:
                f.write(line + "\n")
            if not file_existed:
                _ensure_owner_only_perms(filepath)
        return True
    except Exception as exc:  # pragma: no cover - logged for diagnosis
        logger.warning("append_jsonl failed for %s: %s", filepath, exc)
        return False


def append_if_new(filepath: str, data: BaseModel, cache: set, content_hash: str) -> bool:
    """Atomic dedup-check + append under a single per-file lock.

    Returns True if the event was newly appended (not a duplicate).
    Returns False if the content_hash already exists in `cache` (duplicate).
    On dedup hit, the caller is still responsible for any duplicate-write handling.
    Mutates `cache` to add the new hash on success.
    """
    lock = _get_lock(filepath)
    if hasattr(data, "to_jsonl"):
        line = data.to_jsonl()
    else:
        line = json.dumps(
            data.model_dump(), default=_serialize_default, ensure_ascii=False
        )
    try:
        _ensure_parent(filepath)
        with lock:
            if content_hash in cache:
                return False
            file_existed = os.path.exists(filepath)
            with open(filepath, "a", encoding="utf-8") as f:
                f.write(line + "\n")
            if not file_existed:
                _ensure_owner_only_perms(filepath)
            cache.add(content_hash)
        return True
    except Exception as exc:  # pragma: no cover
        logger.warning("append_if_new failed for %s: %s", filepath, exc)
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


def validate_jsonl_file(filepath: str) -> int:
    """Validate a JSONL file in place. Removes corrupt lines, rewrites the file.

    Returns the number of dropped (corrupted) lines. Logs the line numbers of
    each removed line. Missing file → returns 0.

    Implements Spec FR-12 / Case D / Edge B (corrupted JSONL recovery).
    """
    if not os.path.exists(filepath):
        return 0

    lock = _get_lock(filepath)
    valid_lines: list[str] = []
    dropped_line_numbers: list[int] = []

    try:
        with lock:
            with open(filepath, "r", encoding="utf-8") as f:
                for lineno, raw in enumerate(f, start=1):
                    stripped = raw.rstrip("\n")
                    if not stripped.strip():
                        # preserve genuinely empty lines? we drop them silently
                        continue
                    try:
                        json.loads(stripped)
                        valid_lines.append(stripped)
                    except json.JSONDecodeError:
                        dropped_line_numbers.append(lineno)

            if dropped_line_numbers:
                tmp = filepath + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    for line in valid_lines:
                        f.write(line + "\n")
                os.replace(tmp, filepath)
                _ensure_owner_only_perms(filepath)
                logger.warning(
                    "validate_jsonl_file: dropped %d corrupted line(s) from %s "
                    "(line numbers: %s)",
                    len(dropped_line_numbers),
                    filepath,
                    dropped_line_numbers,
                )
    except Exception as exc:  # pragma: no cover
        logger.warning("validate_jsonl_file failed for %s: %s", filepath, exc)
        return 0

    return len(dropped_line_numbers)


def save_state(filepath: str, state: dict) -> None:
    """Save state dict to JSON (atomic-ish write)."""
    lock = _get_lock(filepath)
    _ensure_parent(filepath)
    with lock:
        tmp = filepath + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, default=_serialize_default, ensure_ascii=False, indent=2)
        os.replace(tmp, filepath)
        _ensure_owner_only_perms(filepath)


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


def event_exists(content_hash: str, cache: set) -> bool:
    """Check if a content_hash has already been seen (in-memory set lookup)."""
    return content_hash in cache


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


__all__ = [
    "append_jsonl",
    "append_if_new",
    "load_jsonl",
    "validate_jsonl_file",
    "save_state",
    "load_state",
    "compute_content_hash",
    "event_exists",
    "load_existing_hashes",
]
