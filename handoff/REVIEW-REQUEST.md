# Review Request — Step 1

## What Was Built

- All 13 modules under `app/` and `monitor/` (plus 4 `__init__.py` package markers and `main.py`)
- 5 test files + `conftest.py` (37 tests total)
- Configuration files (`config/sources.yaml`, `config/keywords.yaml`)
- Updated `requirements.txt` for Python 3.13 compatibility (added `PyYAML`, relaxed pins to `>=`)

## Files Changed (with line counts)

| File | Lines | Notes |
|------|-------|-------|
| `app/__init__.py` | 0 | empty package marker |
| `app/models.py` | ~110 | `ExternalEvent`, `AlertLog`, `KeywordRule`, `SystemState`, `to_jsonl/from_jsonl` |
| `app/database.py` | ~150 | JSONL append/load, state, hash, dedup helpers, per-file locks |
| `app/scheduler.py` | ~38 | `setup_scheduler()` returning `AsyncIOScheduler` |
| `app/routes/__init__.py` | 0 | package marker |
| `app/routes/status.py` | ~85 | 4 routes: `/status`, `/events`, `/alerts`, `/trigger` |
| `monitor/__init__.py` | 0 | marker |
| `monitor/collectors/__init__.py` | 0 | marker |
| `monitor/collectors/rss.py` | ~115 | base `RSSCollector` (fetch / parse / collect) |
| `monitor/collectors/dart.py` | ~45 | `DARTCollector` with `DART_WATCHLIST` filter |
| `monitor/collectors/fsc.py` | ~12 | `FSCCollector` |
| `monitor/matcher.py` | ~70 | pre-compiled regex matcher |
| `monitor/notifier.py` | ~120 | SMTP + file logging (graceful on missing config) |
| `monitor/worker.py` | ~125 | top-level orchestration |
| `main.py` | ~80 | FastAPI app with lifespan |
| `config/sources.yaml` | 13 | DART + FSC |
| `config/keywords.yaml` | 24 | urgent/watch/info |
| `tests/__init__.py` | 0 | marker |
| `tests/conftest.py` | ~80 | shared fixtures |
| `tests/test_models.py` | ~80 | 8 tests |
| `tests/test_database.py` | ~110 | 9 tests |
| `tests/test_rss.py` | ~100 | 8 tests |
| `tests/test_matcher.py` | ~50 | 7 tests |
| `tests/test_dedup.py` | ~70 | 5 tests |
| `requirements.txt` | 11 | Python 3.13 compatible pins |
| `docs/개발노트_step1_20260509.md` | ~140 | updated |

## Self-Review

### What might Reviewer flag?

1. **SMTP 보안:** `_send_email`에서 `starttls`를 try/except로 묶어 실패 시 평문 전송으로 진행함. 실제 운영에서는 `starttls` 실패 시 발송 중단 검토 필요. 또한 SMTP 비밀번호가 평문 환경변수에서 로드됨 — 운영에서는 secrets manager 권장.
2. **Pydantic V2 deprecation 경고:** `model_config = ConfigDict(json_encoders=...)`는 Pydantic V3에서 제거 예정. 현재 동작은 OK이나 향후 마이그레이션 필요.
3. **`event_exists` 시그니처:** 브리프 명세대로 `(filepath, content_hash, cache)`를 받지만 실제로는 `cache`만 사용함. `filepath`는 확장성을 위한 자리표시자.
4. **스케줄러 동시성:** `AsyncIOScheduler`가 동기 함수인 `worker.run_once`를 호출하므로 이벤트 루프를 블록할 수 있음. 대량 RSS 수집 시 문제될 수 있으나 Step 1 범위에서는 acceptable.
5. **`/trigger` 엔드포인트:** `worker.run_once()`를 동기 호출함 — RSS가 느리면 HTTP 타임아웃 가능. Step 4에서 background task로 전환 권장.
6. **DART watchlist 매칭:** 단순 substring 검색으로 corp_code를 매칭함 — 실제 DART RSS 페이로드 구조를 보고 더 정확한 추출 로직이 필요할 수 있음.
7. **`KeywordRule.exclude_keywords`:** 모델에는 정의했으나 `KeywordMatcher`에서 아직 사용하지 않음. Step 2 (고급 매칭) 범위.
8. **로그 파일 경로:** `logs/` 디렉토리는 만들어져 있으나 file handler를 붙이지 않음 — 현재는 stderr only.
9. **버전 핀 완화:** Python 3.13에서 pydantic 2.4.0이 빌드 실패하여 `>=` 으로 완화. 향후 lockfile (`pip-tools` 등) 도입 권장.

### Did every item in the brief ship?

- ✅ 프로젝트 구조 (app/, monitor/, config/, data/, logs/, tests/)
- ✅ sources.yaml (DART + FSC)
- ✅ keywords.yaml (urgent / watch / info, 7+7+5 entries)
- ✅ Pydantic 모델 (ExternalEvent, AlertLog, KeywordRule, SystemState) + `to_jsonl/from_jsonl`
- ✅ JSONL 포맷 (events.jsonl, alerts.jsonl, state.json)
- ✅ 중복 제거 (`compute_content_hash` MD5(title+url), in-memory cache, `event_exists`)
- ✅ RSS 수집 (User-Agent `claude_webcroll/1.0`, XML 파싱, 정규화, append-only)
- ✅ 키워드 매칭 (severity 우선순위, matched_keywords)
- ✅ 알람 발송 (이메일 urgent/watch, 파일 모든 이벤트, SMTP 누락 시 graceful)
- ✅ 환경변수 (.env.example 이미 존재; main.py가 dotenv 로드)
- ✅ FastAPI 진입점 + 4 routes (`/status`, `/events`, `/alerts`, `/trigger`)
- ✅ APScheduler 등록 (소스별 interval)
- ✅ 5 테스트 (`test_rss`, `test_matcher`, `test_dedup` + bonus `test_models`, `test_database`)
- ✅ pytest 100% pass (37/37)

### What does the user see if data is empty or a request fails?

- **Empty data:** `GET /events`, `GET /alerts` → `{"limit": N, "count": 0, "events": []}` (200 OK, empty list).
- **No worker (pre-startup):** `/events`, `/alerts`, `/trigger` returns count 0 / `status: "no-worker"` (graceful, no crash).
- **RSS down:** Collector returns `[]`, worker logs warning, scheduler keeps running, no events stored.
- **SMTP missing/down:** `Notifier` logs warning, writes a `failed` AlertLog with `error_message`, file log still records the event.
- **Corrupted JSONL line:** `load_jsonl` skips bad lines with a logged warning, returns valid records.
- **Trigger error:** `POST /trigger` returns `{"status": "error", "error": "...", "new_events": 0}` (200 OK, no 5xx).

## Open Questions

1. **DART RSS endpoint:** 브리프의 `https://dart.fss.or.kr/api/rssFeeds.json`은 `.json` 확장자임에도 RSS로 처리. 실제 응답 형식 확인 필요 — 운영 시 endpoint 변경 가능성 있음.
2. **Notifier 반환값:** 현재 이메일 시도 시 email log 우선, 아니면 file log 반환. Reviewer가 "양쪽 모두 반환"을 원하면 시그니처 변경 필요.
3. **로깅 인프라:** stderr-only로 충분한가, 아니면 `logs/` 디렉토리에 파일 핸들러 추가가 필요한가?
4. **버전 핀 정책:** `>=` 완화가 OK한가, 또는 정확 핀 + lockfile이 필요한가?

## Test Results

```
============================= test session starts =============================
platform win32 -- Python 3.13.13, pytest-9.0.3, pluggy-1.6.0
plugins: anyio-4.9.0, asyncio-1.3.0
collected 37 items

tests/test_database.py ......... [ 24%]   (9 passed)
tests/test_dedup.py .....         [ 37%]   (5 passed)
tests/test_matcher.py .......     [ 56%]   (7 passed)
tests/test_models.py ........     [ 78%]   (8 passed)
tests/test_rss.py ........        [100%]   (8 passed)

======================= 37 passed, 4 warnings in 1.39s ========================
```

Warnings are Pydantic 2.x `json_encoders` deprecation notices (functional, not breaking).

Import check: `python -c "from main import app"` → `OK claude_webcroll 0.1.0`

## Ready for Review

YES
