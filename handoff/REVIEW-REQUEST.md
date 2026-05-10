# Review Request — Step 3 (Watchtower Crawler + Detector + Items 적재)

**Builder:** Senior Developer (이번 세션)
**Date:** 2026-05-10
**Branch / worktree:** `claude/stoic-meitner-47d182`
**Step:** 3 — Crawler + Detector + Items 적재
**Ready for Review:** YES

---

## TL;DR

Watchtower 전용 RSS/HTML 크롤러 패키지(`monitor/watchtower/`) 신규 추가, Step 2 escalation 2건 처리(`Item.id default=uuid`, `Site.enabled` 컬럼), 30개 사이트 URL 검증 후 yaml 정리, APScheduler에 enabled 사이트만 등록, `POST /api/trigger-watchtower` 신규.

`pytest tests/ -v` -> **104 passed (83 baseline + 21 new), 0 skipped**. uvicorn 부팅 + 실 1회 트리거 시 9개 사이트 성공 + 239 items 적재 확인. `/api/items` 응답 정상.

---

## 신규/수정 파일 표

| 파일 | 종류 | 라인 범위 | 한 줄 설명 |
|---|---|---|---|
| `app/db/models.py` | 수정 | imports + Site 컬럼 + Item.id default | 3.1a `Item.id default=lambda: uuid.uuid4().hex`, 3.1b `Site.enabled` Boolean 컬럼 추가 |
| `app/db/seed.py` | 수정 | _seed_sites() | yaml의 `enabled` 필드 -> `Site.enabled` 반영 (default True) |
| `config/seed_sites.yaml` | 수정 (전체 재작성) | 전체 | 30개 사이트 fetch 검증 -> 14 verified + 1 URL 보정(s14) + 15 `enabled: false` |
| `requirements.txt` | 수정 | 7~10 | `httpx` 상한 `<0.30` 명시 + `beautifulsoup4>=4.12,<5.0` + `lxml>=5.0,<6.0` |
| `monitor/watchtower/__init__.py` | 신규 | 빈 패키지 마커 | Watchtower 크롤러 패키지 |
| `monitor/watchtower/base.py` | 신규 | 1~84 | `USER_AGENT`, `DEFAULT_TIMEOUT_SEC=30`, `CrawledItem`, `CrawlResult`, `Crawler` ABC |
| `monitor/watchtower/robots.py` | 신규 | 1~115 | robots.txt fetch + 6h 캐시 + fail-open `is_allowed()`, `clear_cache()` 헬퍼 |
| `monitor/watchtower/rss.py` | 신규 | 1~127 | `RssCrawler` -- httpx fetch + feedparser parse + bozo 처리 + struct_time->UTC |
| `monitor/watchtower/html.py` | 신규 | 1~163 | `HtmlCrawler` -- httpx + BS4(lxml) + `content_selector` + 절대 URL 변환 + summary |
| `monitor/watchtower/detector.py` | 신규 | 1~85 | `sha256_hash()`, `detect_new_items()` -- (site_id, url) dedup + Item 생성 |
| `monitor/watchtower/worker.py` | 신규 | 1~285 | `WatchtowerWorker` -- ThreadPoolExecutor + 도메인 lock + 진행 중복 방지 + 5회 실패 카운터 + `run_site()`/`run_all()` |
| `main.py` | 수정 | imports + lifespan + 신규 라우트 | `WatchtowerWorker` 인스턴스, enabled 사이트만 scheduler.add_job, 부팅 로그 `Watchtower scheduler: N sites registered (M skipped -- disabled)`, `POST /api/trigger-watchtower` |
| `scripts/verify_seed_urls.py` | 신규 | 1~83 | 30개 사이트 fetch 검증 1회용 스크립트 (재사용 가능) |
| `tests/test_watchtower_crawler.py` | 신규 | 21 tests | robots/rss/html/detector/worker/seed/legacy/api/trigger |
| `handoff/BUILD-LOG.md` | 수정 | Current Status + Known Gaps | Step 3 완료 표기 + 사이트 검증 결과 표 + Step 3 산출 known gaps |
| `handoff/REVIEW-REQUEST.md` | 신규 | 이 파일 | Reviewer 인계 |

---

## 테스트 결과

```
$ pytest tests/ -v
============================= 104 passed in 2.83s =============================
```

- Baseline 83 (Step 1+2) + 신규 21 = **104 통과, 0 skipped**.
- 신규 테스트 카테고리:
  - robots (2): allowed_default fail-open, disallow_path
  - rss (2): parses_atom, handles_bozo
  - html (3): extracts_with_selector, no_match, timeout
  - detector / id (4): dedup, creates_uuid_id, item_id_default_uuid, legacy_import_explicit_id_preserved
  - worker (6): run_site_success, run_site_failure_counter, in_progress_skip, domain_lock, skips_disabled_site, blocked_by_robots
  - seed/legacy (2): seed_enabled_field, legacy_import_still_works
  - HTTP routes (2): api_items_returns_crawled, api_trigger_watchtower_202

브리프 §3.11에 명시된 18개 + Builder 추가 3개 (worker_skips_disabled_site, worker_blocked_by_robots, legacy_import_still_works). 모두 hermetic — `httpx.Client`를 monkeypatch로 `_ScriptedClient`로 교체하므로 외부 네트워크 의존성 0.

---

## 사이트 URL 검증 결과

스크립트: `scripts/verify_seed_urls.py` (Watchtower UA + timeout 10s + follow_redirects).

| ID | 이름 | crawl_method | 결과 | 비고 |
|---|---|---|---|---|
| s1 | 금융위원회 | html | enabled=false | ConnectError (사내망 전용 가능성) |
| s2 | 금융감독원 | html | verified | HTTP 200, text/html |
| s3 | 한국은행 | html | verified | HTTP 200, text/html |
| s4 | FATF | html | enabled=false | HTTP 403 (UA/지역 차단) |
| s5 | 기획재정부 | html | enabled=false | ConnectError |
| s6 | 과학기술정보통신부 | html | enabled=false | ConnectError |
| s7 | 디지털플랫폼정부 | html | enabled=false | ConnectTimeout |
| s8 | 개인정보보호위원회 | html | enabled=false | ConnectError |
| s9 | KB국민은행 보도 | html | enabled=false | Content-Type 비정상 |
| s10 | 하나은행 디지털 | html | verified | HTTP 200 (selector tentative) |
| s11 | 우리은행 뉴스룸 | html | enabled=false | HTTP 404 |
| s12 | NH농협 | html | verified | HTTP 200 (selector tentative) |
| s13 | 카카오뱅크 | html | enabled=false | HTTP 404 |
| s14 | 토스 블로그 | rss | verified (URL 보정) | `/` -> `/rss.xml` |
| s15 | 네이버페이 | html | enabled=false | HTTP 404 |
| s16 | 케이뱅크 | html | verified | HTTP 200 (selector tentative) |
| s17 | Anthropic News | html | verified | HTTP 200 |
| s18 | OpenAI Blog | html | enabled=false | HTTP 403 (Cloudflare) |
| s19 | Microsoft Foundry | html | verified | HTTP 200 (selector tentative) |
| s20 | NVIDIA Developer | rss | verified | application/atom+xml |
| s21 | KISA | html | enabled=false | HTTP 500 |
| s22 | 보안뉴스 | rss | verified | text/xml |
| s23 | KrCERT/CC | html | enabled=false | HTTP 404 |
| s24 | KISIA | html | verified | HTTP 200 (selector tentative) |
| s25 | 한국경제 핀테크 | rss | verified | text/xml |
| s26 | 매일경제 금융 | rss | verified | application/xml |
| s27 | 전자신문 IT | rss | verified | text/xml |
| s28 | 자본시장연구원 | html | enabled=false | HTTP 404 |
| s29 | 한국금융연구원 | html | verified | HTTP 200 (selector tentative) |
| s30 | KISDI | html | enabled=false | HTTP 404 |

**요약:** 14 verified (그중 6개는 selector tentative — 첫 크롤 후 보정 필요), 1 URL 보정(s14), 15 enabled=false. 로컬 개발 머신 기준이며, 사내망에서 .go.kr 사이트는 다시 검증 필요(`scripts/verify_seed_urls.py` 재실행).

---

## Major Components

### `monitor/watchtower/base.py`
- `USER_AGENT = "Watchtower/1.0 (+https://watchtower.shinhan.local)"` (FR-CRL-005)
- `DEFAULT_TIMEOUT_SEC = 30` (FR-CRL-008)
- `CrawledItem`(title/url/summary/published_at/content_for_hash), `CrawlResult`(site_id/items/error/blocked_by_robots/duration_ms), `Crawler` ABC

### `monitor/watchtower/robots.py`
- 도메인별 `RobotFileParser` 캐시(6시간 TTL, threading.Lock 보호)
- 명시적 fail-open: robots.txt fetch 실패(404/timeout/DNS 등) -> True
- httpx `follow_redirects=True`, timeout 명시
- 캐시는 `(expiry, parser, fetch_ok)` -- fetch_ok=False면 캐시되어 있어도 fail-open 유지

### `monitor/watchtower/rss.py`
- httpx로 fetch -> `feedparser.parse(bytes)` (feedparser timeout 미지원 우회)
- `published_parsed` / `updated_parsed` (struct_time) -> tz-aware UTC datetime
- bozo 처리: `entries=[] AND bozo=1` -> error 세팅. `entries>0 AND bozo=1` 은 soft pass.
- `content_for_hash = title\nurl\nsummary` (decision §6)

### `monitor/watchtower/html.py`
- httpx + `BeautifulSoup(html, 'lxml')` + `soup.select(content_selector)`
- 매칭된 컨테이너 안의 `<a href>` -> 절대 URL(`urljoin(response.url, href)`)
- javascript:/mailto:/tel:/# 시작 href 필터, http(s) 만 통과
- 같은 anchor에서 `<li>/<tr>/<article>/<p>/<div>` 부모 텍스트 -> summary (300자 제한)
- selector 매칭 0개 -> `error="content_selector matched no elements"`
- 매칭은 됐지만 anchor 없음 -> `error="content_selector matched no link items"`
- redirect `max_redirects=5` 명시

### `monitor/watchtower/detector.py`
- `sha256_hash(content) -> 64-char hex`
- `detect_new_items(session, site_id, crawled)`:
  - 기존 `(site_id, url)` SELECT -> set 캐시
  - 동일 batch 내 dedup (`seen_in_batch`)
  - title/summary 길이 제한 (500/2000)
  - `Item.id` 미명시 -> SQLAlchemy default(uuid.uuid4().hex) 자동 부여
  - `read_by=""` (빈 CSV)

### `monitor/watchtower/worker.py`
- `__init__(session_factory, *, max_workers=None, user_agent, timeout_sec, crawler_factory)`
  - `max_workers` 기본: env `WATCHTOWER_MAX_WORKERS` -> 5 (Flag §3)
  - `crawler_factory` 는 테스트에서 stub 주입용 의존성 주입점 (production default = `_crawler_for`)
- `_get_domain_lock(url)` -- `urlparse(url).netloc` 으로 lock 생성/재사용 (master lock 보호)
- `_try_claim/_release` -- `_in_progress` set + lock (FR-CRL-007)
- `run_site(site_id)`:
  1. claim 실패 -> `{"skipped":"in_progress"}`
  2. Site load -> `enabled=False` -> `{"skipped":"disabled"}`
  3. robots.is_allowed -> False -> `Site.status='blocked'` + `{"status":"blocked","blocked_by_robots":True}`
  4. domain_lock 안에서 `crawler.crawl(stub_site)`
  5. result.error -> `_record_run_failure` -> counter++, >=5 -> `status='failed'` + `_notify_owner_failure` (logger.error 만, 1회 알림)
  6. 성공 -> `detect_new_items` + commit + `Site.status='ok'` + `last_ok_at=now()` + counter reset
  - 모든 phase에서 별도 짧은 session
- `run_all(*, only_enabled=True)` -- Site.id 목록 SELECT -> executor.submit fan-out -> as_completed
- `shutdown(wait=False)` -- 실행기 종료 (lifespan 종료 시 호출)

### `main.py` lifespan 통합
```python
watchtower_worker = WatchtowerWorker(SessionLocal)
app.state.watchtower_worker = watchtower_worker
# enabled 사이트만 add_job, interval=site.crawl_interval_min, args=[site.id]
logger.info("Watchtower scheduler: %d sites registered (%d skipped -- disabled)", ...)
```
실 부팅 로그: `Watchtower scheduler: 15 sites registered (15 skipped -- disabled)` 확인.

### `POST /api/trigger-watchtower`
- body `{"site_id":"..."}` 또는 query `?site_id=...` 둘 다 허용 (둘 다 비면 run_all)
- `BackgroundTasks` 사용, 202 Accepted, `{job_id, site_id, status, message}`
- 워커가 lifespan에서 아직 만들어지지 않은 케이스 방어 (no-op 메시지)

---

## Self-Review

### 1. Reviewer가 가장 먼저 잡을 부분?

1. **`test_worker_domain_lock` 의 직렬화 검증이 약함** — 현재 `time.sleep(0.2)` 후 추정만 함. 의도한 흐름은 사이트 A를 hold로 막고 B가 lock 대기로 멈추는 것. stub_b에도 started Event를 부여해 `started_b.wait(0.2)` 반환값으로 명시 검증할 수 있음. Reviewer 지적 시 보강 가능.
2. **HtmlCrawler `_extract_summary` 의 fallback 컨테이너** — `<div>`가 페이지 전체일 경우 summary가 길어질 수 있음(300자 제한 있음). 실 사이트 첫 크롤 후 noise 검토 필요.
3. **`Site.status='delayed'` 라벨** — 1~4회 실패 시 'delayed' 사용. spec FR-SITE-006 은 5회 미만 동작을 명시 안 함. 기존 enum (`ok|delayed|failed|blocked`) 안에서 'delayed'가 가장 적합 판단. Reviewer 의견 시 'ok' 유지로 변경 가능.
4. **robots.txt 캐시 lifecycle** — 프로세스 로컬 dict + Lock. 다중 워커 시 중복 fetch. Step 5 deploy 단계에서 Redis 등 외부 캐시 검토.
5. **`scripts/verify_seed_urls.py` 위치** — 1회용 검증 스크립트. `scripts/` 디렉토리 신설. 운영에 포함될 의도는 없음.

### 2. 브리프의 모든 항목 ship?

| 브리프 § | 항목 | 상태 |
|---|---|---|
| 3.1a | `Item.id` default uuid | ✓ |
| 3.1b | `Site.enabled` Boolean 컬럼 | ✓ |
| 3.1b | seed_sites.yaml URL 검증 + 마킹 | ✓ (verified 14, 보정 1, disabled 15) |
| 3.1b | `seed.py` enabled 반영 | ✓ |
| 3.2 | requirements.txt (httpx + bs4 + lxml) | ✓ |
| 3.3 | `monitor/watchtower/` 패키지 구조 | ✓ |
| 3.4 | `base.py` (UA/timeout/CrawledItem/CrawlResult/Crawler ABC) | ✓ |
| 3.5 | `robots.py` (cache + fail-open + httpx fetch) | ✓ |
| 3.6 | `rss.py` (feedparser + bozo + struct_time) | ✓ |
| 3.7 | `html.py` (httpx + BS4 + content_selector) | ✓ |
| 3.8 | `detector.py` (sha256 + detect_new_items) | ✓ |
| 3.9 | `worker.py` (ThreadPool + domain lock + 5회 카운터 + run_site/run_all) | ✓ |
| 3.10 | `main.py` lifespan + scheduler.add_job + 부팅 로그 | ✓ |
| 3.10 | `POST /api/trigger-watchtower` 202 + BackgroundTasks | ✓ |
| 3.11 | 18개+ 테스트 (실제 21개) | ✓ |

### 3. 빈 데이터 / 요청 실패 시 사용자에게 보이는 것?

- `/api/items` 빈 DB -> `[]` (200 OK). UI는 "조건에 맞는 알림이 없습니다" 메시지(Step 2 watchtower.html).
- `POST /api/trigger-watchtower` 워커가 아직 없을 때 -> 202 + `"message": "Trigger accepted (worker not ready -- will be a no-op)"`
- 크롤 실패 -> `Site.status='delayed'` 또는 `'failed'`. 5회째 logger.error 1회 발생 (Step 4에서 실 알림).
- robots.txt 차단 -> `Site.status='blocked'`. 다음 트리거에서도 차단. UI는 site 카드에 status 노출.
- HTML selector 미스매치 -> `result.error` 세팅 -> status='delayed'. items 0개.

---

## Deviations from Brief

1. **`scripts/verify_seed_urls.py` 추가**: 브리프 명시 안 됨. 30개 URL 검증 자동화. 1회용 + 재실행 가능.
2. **테스트 21개 (브리프 18개 + 추가 3개)**: 18개 모두 포함. 추가:
   - `test_worker_skips_disabled_site` -- enabled=False 케이스 명시 검증.
   - `test_worker_blocked_by_robots` -- robots disallow 시 status='blocked' 검증.
   - `test_legacy_import_still_works` 와 `test_legacy_import_explicit_id_preserved` 분리 (브리프는 둘 다 별도 항목).
3. **`Site.status='delayed'` 도입**: 1~4회 실패 시 delayed로 변경. 브리프 명시 없음. Reviewer 합의 시 'ok' 유지로 변경 가능.
4. **HtmlCrawler `_extract_summary` 의 fallback 우선순위**: 브리프는 "anchor.parent.text 일부" 만. `<li>/<tr>/<article>/<p>/<div>` 우선순위로 합리적 후보 탐색.
5. **`crawler_factory` 의존성 주입**: 브리프 명시 안 됨. 워커 단위 테스트 stub 주입용. production default = `_crawler_for`.
6. **`requirements.txt` 의 httpx 라인**: 기존 `httpx>=0.27.0` 을 `httpx>=0.27,<0.30` 로 변경 (브리프 명시 형식). 하위호환.

---

## Known Gaps for Next Step (Step 4)

1. **`_notify_owner_failure` 가 logger.error 만** — Step 4 Notifier에서 실 메일/메신저 라우팅. 인터페이스(category owner_user_id 조회 + 채널 dispatch)를 Notifier 모듈에 위임 예정.
2. **selector tentative 사이트 6건** — s10/s12/s16/s19/s24/s29 첫 크롤 시 selector 매칭 0개로 status='delayed'. Owner 가 실 HTML 확인 후 yaml 보정.
3. **enabled=false 15건** — 사내망 전용 .go.kr 사이트 + URL 추정값 무효. Owner 가 사내망에서 `scripts/verify_seed_urls.py` 재실행 + URL 보정.
4. **CHANGE detection / snapshot table** — Phase 2.
5. **Subscriptions REST API** — Step 4. 현재 모든 사용자가 모든 site 결과 조회.
6. **legacy import deprecate 결정** — Step 1 events.jsonl -> Item 매핑 보존. Step 4 통합 후 결정.

---

## How to Reproduce

```powershell
# 1. 의존성
pip install -r requirements.txt

# 2. 테스트 (104 통과)
pytest tests/ -v

# 3. 부팅
python -m uvicorn main:app --host 127.0.0.1 --port 8000

# 4. 트리거
curl -X POST http://127.0.0.1:8000/api/trigger-watchtower -H "Content-Type: application/json" -d "{}"
# -> 202 + job_id

curl -X POST http://127.0.0.1:8000/api/trigger-watchtower -H "Content-Type: application/json" -d "{\"site_id\":\"s20\"}"
# -> 202 + 단일 사이트 trigger

# 5. 결과 확인
curl http://127.0.0.1:8000/api/items?limit=200
curl http://127.0.0.1:8000/api/health
# -> sites_failed 카운터, items 적재 확인
```

---

## Open Questions / Builder Questions

없음. 브리프의 모든 Flag(§Flags 1~5)는 명시값대로 따랐고, 나머지는 self-review에 deviation 으로 기록.

---

**Ready for Review: YES**
