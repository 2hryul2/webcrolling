"""FSC (금융위) RSS collector — no watchlist filtering."""

from __future__ import annotations

from monitor.collectors.rss import RSSCollector


class FSCCollector(RSSCollector):
    """FSC press-release RSS collector."""

    def __init__(
        self,
        name: str,
        endpoint: str,
        timeout_seconds: int = 30,
        retry_attempts: int = 3,
    ) -> None:
        super().__init__(
            source_id="fsc",
            name=name,
            endpoint=endpoint,
            timeout_seconds=timeout_seconds,
            retry_attempts=retry_attempts,
        )
