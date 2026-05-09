"""Keyword matching engine — pre-compiled regex per severity."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger(__name__)

# Severity priority — first match wins.
_PRIORITY = ["urgent", "watch", "info"]


class KeywordMatcher:
    """Match text against severity-graded keyword lists."""

    def __init__(self, keywords_path: str) -> None:
        self.keywords_path = keywords_path
        self._compiled: dict[str, list[tuple[str, re.Pattern]]] = {}
        self._load()

    def _load(self) -> None:
        path = Path(self.keywords_path)
        if not path.exists():
            logger.warning("KeywordMatcher: keywords file missing: %s", path)
            self._compiled = {sev: [] for sev in _PRIORITY}
            return

        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        keywords = data.get("keywords", {}) if isinstance(data, dict) else {}

        for severity in _PRIORITY:
            entries: list[tuple[str, re.Pattern]] = []
            for kw in keywords.get(severity, []) or []:
                if not kw:
                    continue
                kw_str = str(kw)
                pattern = re.compile(re.escape(kw_str), re.IGNORECASE)
                entries.append((kw_str, pattern))
            self._compiled[severity] = entries

    def match(self, text: str) -> tuple[str, list[str]]:
        """Match text and return (severity, matched_keywords).

        Severity priority: urgent > watch > info. If a higher-tier keyword
        matches, that severity wins even when lower-tier keywords also match.
        Returns ("info", []) when no keyword matches.
        """
        if not text:
            return ("info", [])

        winning_severity: Optional[str] = None
        all_matched: dict[str, list[str]] = {sev: [] for sev in _PRIORITY}

        for severity in _PRIORITY:
            for kw, pattern in self._compiled.get(severity, []):
                if pattern.search(text):
                    all_matched[severity].append(kw)
            if all_matched[severity] and winning_severity is None:
                winning_severity = severity

        if winning_severity is None:
            return ("info", [])

        return (winning_severity, all_matched[winning_severity])
