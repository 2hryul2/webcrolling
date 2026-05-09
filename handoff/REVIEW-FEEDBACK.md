# Review Feedback — Step 1 (Re-review)
Date: 2026-05-09
Status: APPROVED

## Re-review Summary

All 17 Conditions and all 4 Architect decisions are properly applied at the
code level — verified against the actual source, not Builder's claims. Test
suite goes from 37 → 67 (`pytest tests/ -v` reports 67 passed in ~1s). The
`pytest.ini` filter is correctly scoped to a third-party feedparser
DeprecationWarning only; no warnings originate from project code. The Pydantic
V2 migration is complete: `model_config` / `ConfigDict` / `json_encoders` are
fully removed from `app/models.py`, replaced by `@field_serializer` on the
three datetime-bearing models. No drift, no regression, no new findings.

## Conditions Verified (17/17)

- C1 — PASS — `monitor/collectors/rss.py:43-106` — 3-attempt retry loop with `[1, 2, 4]` backoff, body fetched via `httpx.Client(timeout=self.timeout_seconds)`, then handed to `feedparser.parse(body)`.
- C2 — PASS — `monitor/notifier.py:38-39, 99-159, 201` — `SMTP_TIMEOUT_SECONDS=10`, `EMAIL_RETRY_BACKOFFS=(1,2,4)`, 3-attempt loop with `time.sleep(backoff)` between attempts, `failed` AlertLog written only after exhaustion.
- C3 — PASS — `app/models.py:43` (`is_duplicate: bool = False`) + `monitor/worker.py:167-178, 196-199` — duplicate path appends `dup_event` with `is_duplicate=True` via `append_jsonl` and skips notify; "Duplicate detected" log line present.
- C4 — PASS — `app/database.py:140-187` — `validate_jsonl_file(filepath) -> int` rewrites file in place via `.tmp` + `os.replace`, logs dropped line numbers. Called from `main.py:60-64` for both `events.jsonl` and `alerts.jsonl` during lifespan startup.
- C5 — PASS — `app/routes/status.py:150-152, 172-174` — `Query(default=100, ge=1, le=1000)` for `limit` on both `/events` and `/alerts`; `source` (events), `severity` (alerts), and `days` query params present and filtered.
- C6 — PASS — `app/routes/status.py:204-230` — `@router.post("/trigger", status_code=202)`, `BackgroundTasks` injected, `_trigger_run` fired via `background_tasks.add_task`, body returns `{job_id, source, status: "queued", message}`.
- C7 — PASS — `app/routes/status.py:117-130` — returns `uptime_seconds`, `failed_alert_count`, per-source `{status, event_count, alert_count, error_count}`, plus `memory_mb`/`cpu_percent` (psutil; primed once at import time).
- C8 — PASS — `config/keywords.yaml` — urgent: 19 entries (≥14 spec'd), watch: 13 entries (≥9), info: 7 entries (≥4). All spec keywords present (폐지/청산/부도/상장폐지/거래정지/분할/합병/영업양수도/자산양도/워크아웃/기업개선약관/부채재조정 in urgent; 유상증자/무상증자/증자/신주발행/상호변경/본점이전/사업목적변경 in watch; 보도자료/뉴스 in info).
- C9 — PASS — `config/sources.yaml` — keys are `url`, `poll_interval_seconds`, `timeout_seconds`, `retry_attempts`. DART URL is `https://dart.fss.or.kr/api/todayRSS.xml` per Architect Esc-1.
- C10 — PASS — `monitor/notifier.py:201-211, 121-137, 240-245` — STARTTLS exception caught inside `_send_one_attempt`, raised as `_StarttlsFailedError`. Caller branch in `_send_email` returns `failed` AlertLog with `error_message="STARTTLS failed"` and never reaches `smtp.login`. Confirmed by `test_starttls_failure_does_not_login_plaintext` (`smtp.login.assert_not_called()`).
- C11 — PASS — `monitor/notifier.py:139-144, 24-32` — `logger.warning("Email send failed for %s: %s", event.external_id, type(exc).__name__)`. AlertLog `error_message` runs through `_redact_password_substrings()` which substitutes `password=...` → `password=[REDACTED]` (helper test verifies the regex).
- C12 — PASS — `app/database.py:37-43, 67-68, 98-99, 175, 199` — `_ensure_owner_only_perms` swallows `OSError`/`NotImplementedError` (Windows no-op) and is invoked on first creation of `events.jsonl`, `alerts.jsonl`, and `state.json`, plus after `validate_jsonl_file` rewrites.
- C13 — PASS — `monitor/worker.py:31-37, 142-158` — `_resolve_thread_pool_size()` reads `THREAD_POOL_SIZE` env (default 5, hard cap 32), used as `max_workers` for `ThreadPoolExecutor` running `c.collect`. Per-collector exceptions handled in `as_completed` loop without aborting siblings.
- C14 — PASS — `app/database.py:222-224` — `event_exists(content_hash, cache)` — `filepath` parameter removed. Tests in `tests/test_database.py:test_event_exists_with_cache` and `tests/test_dedup.py:test_event_exists_with_cache_set` updated to the new signature.
- C15 — PASS — `app/database.py:75-104` — `append_if_new(filepath, data, cache, content_hash)` performs the hash check inside the same `with lock:` as the file append and only mutates `cache` after a successful write. `monitor/worker.py:188-199` uses it; on race-loss (`inserted is False`) writes the duplicate row with `is_duplicate=True`.
- C16 — PASS — `app/models.py:42` (`matched_keywords: list[str] = Field(default_factory=list)`) + `monitor/worker.py:172, 183` (`matched_keywords: matched`, no `or None` coercion). Verified by `test_matched_keywords_always_list_even_when_empty`.
- C17 — PASS — `app/scheduler.py:26-30` — `cfg.get("poll_interval_seconds")` is read first, `poll_interval_sec` is only a backward-compat fallback; `sources.yaml` uses the canonical key.

## Architect Decisions Verified (4/4)

- Esc-1 (DART URL → todayRSS.xml) — PASS — `config/sources.yaml:5` uses `https://dart.fss.or.kr/api/todayRSS.xml`.
- Esc-2 (DART substring match + startup warning) — PASS — `monitor/collectors/dart.py:14-25, 50-51` — `_warn_substring_match_once()` logs once per process when a watchlist is configured. Substring matching retained (line 68: `any(code in haystack for code in self.watchlist)`).
- Esc-3 (Pydantic `@field_serializer` migration) — PASS — `app/models.py` has `@field_serializer` on `ExternalEvent.published_at`/`fetched_at`, `AlertLog.sent_at`, and `SystemState.last_poll`. `model_config`, `ConfigDict`, and `json_encoders` are not present in `app/`. `pytest tests/` reports 0 Pydantic deprecation warnings.
- Esc-4 (KeywordRule.exclude_keywords retained with comment) — PASS — `app/models.py:93-99` — field kept with comment "Step 2 scaffolding: matcher uses this in Step 2 for fuzzy/exclusion matching."

## New Test Files Verified (T1–T4)

- T1 `tests/test_notifier.py` — 9 tests covering STARTTLS fail-closed (login.assert_not_called), 3-attempt retry, 10s timeout assertion, password redaction (helper + integration), missing-config graceful skip, urgent/info routing, and final failed-AlertLog persistence.
- T2 `tests/test_worker.py` — 4 tests covering duplicate-write-with-flag (rows length 2, second `is_duplicate=True`, single notify), collector failure isolation across ThreadPoolExecutor, `matched_keywords=[]` when no match, and severity escalation when matched.
- T3 `tests/test_routes.py` — 11 tests covering 422 on out-of-range limits, source/severity filters, /trigger 202 + job_id + BackgroundTask invocation, /status spec shape (uptime_seconds, failed_alert_count, memory_mb, cpu_percent, per-source counts), and ok/error transitions.
- T4 `tests/test_rss.py` (extended +5) — 3-attempt-then-empty path with `sleep_calls == [1, 2]`, retry-then-succeed (2 calls), parametrized real-feedparser end-to-end on DART RSS 2.0 and FSC RSS 2.0 fixtures (verifies FeedParserDict + struct_time handling), and full pipeline via mocked httpx → real feedparser → ExternalEvent.

## Test Run

```
67 passed in 0.99s
```

- `configfile: pytest.ini` (filter scoped narrowly to feedparser's own DeprecationWarning).
- 0 warnings from project code.
- 0 failures.

## New Findings

None. No drift, no regressions, no missed edge cases. Builder's mapping in `## Re-submission` matches the code on disk.

## Cleared

All 17 Conditions and all 4 Architect decisions are confirmed in code; the
new `tests/test_notifier.py`, `tests/test_worker.py`, `tests/test_routes.py`,
and the T4 additions in `tests/test_rss.py` cover the previously-untested
high-risk modules (notifier STARTTLS/retry/redaction, worker duplicate write
+ collector isolation, route Query validation/filters/202 contract, real
feedparser parsing). Step 1 ships.
