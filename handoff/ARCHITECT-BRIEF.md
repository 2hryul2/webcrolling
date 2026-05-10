# Architect Brief

Work specification for Builder.

---

## Step 3 — Watchtower Crawler + Detector + Items 적재

### What

Watchtower 전용 RSS/HTML 크롤러 + NEW 감지기 + 사이트별 스케줄링을 추가하여, Step 2의 `/api/items` 가 실제 외부 사이트에서 수집한 데이터로 채워지도록 한다. Step 1 RSS 시스템(dart/fsc → events.jsonl)은 그대로 보존하면서, 새로 도입되는 Watchtower Worker는 SQLite `items` 테이블에 직접 적재한다.

```
APScheduler (사이트별 interval, 60분 minimum)
   ↓ trigger per site
WatchtowerWorker.run_site(site_id)
   ↓ domain lock (FR-CRL-006)
RobotsChecker.is_allowed(url, UA)  ─── disallowed → site.status='blocked' (FR-SITE-005)
   ↓ allowed
Crawler (rss | html)
   - rss → feedparser
   - html → httpx (timeout 30s) + BeautifulSoup → content_selector
   ↓ 외부 URL · 본문 추출
Detector
   - SHA-256 content_hash
   - (site_id, url) unique check
   - 신규 → Item(type='NEW') 생성 + 기존 read_by='' 빈 CSV
   ↓
SessionLocal.commit() → items 테이블
   ↓
site.status = 'ok' / 'failed' (5회 연속 → failed + 카테고리 owner 1회 알림 (FR-SITE-006))
   ↓
/api/items GET → 실데이터 표시
```

### Why

- Step 2에서 UI shell + DB seed까지 완성. 현재 `/api/items` 가 비어있어 사용자에게 가치 0.
- ideation §9 단계 3+4 (RSS 크롤러 + APScheduler 통합) 의 본 사이클.
- spec FR-CRL-001~008 + FR-DET-001/002 + FR-SITE-005/006 충족.
- Step 4 (Subscriptions/Notifier) 이전에 알림 보낼 데이터가 존재해야 함.

### Requirements

#### 3.1 Step 2 escalation 처리 (선행 작업)

**3.1a. `app/db/models.py` — Item.id default 추가:**
```python
import uuid
from sqlalchemy.orm import mapped_column

class Item(Base):
    id: Mapped[str] = mapped_column(
        String(32),
        primary_key=True,
        default=lambda: uuid.uuid4().hex,
    )
```
- legacy import의 `content_hash[:32]` 명시 세팅과 공존 (default는 명시 세팅 시 미사용).
- 기존 `tests/test_watchtower.py::test_item_legacy_id` 등이 깨지지 않도록 검증.

**3.1b. `config/seed_sites.yaml` — 사이트 URL 검증 + tentative 정리:**
- 30개 사이트 중 `# tentative` 주석 달린 항목들의 URL 정확도 점검.
- Builder 작업 흐름:
  1. 각 URL에 `httpx.head()` 또는 `httpx.get()` (timeout 10s)으로 응답 확인
  2. 200 OK + Content-Type 확인:
     - `crawl_method: rss` → `application/rss+xml`, `application/atom+xml`, `application/xml`, `text/xml` 중 하나여야 통과
     - `crawl_method: html` → `text/html` 통과
  3. 실패한 사이트는 yaml에서 다음 중 하나로 처리:
     - URL 보정 가능 (예: `/news` → `/news/list`) → 새 URL로 교체 + `# verified` 주석
     - 보정 불가 → `enabled: false` 필드 추가 + `# tentative — pending owner review` 주석 유지 (추후 Owner가 검토)
  4. 통과한 사이트는 `# verified 2026-05-10` 주석으로 마킹
- **모델에 `enabled` 컬럼 추가** (Site model + sites 테이블):
  - `enabled: Mapped[bool] = mapped_column(default=True)`
  - 크롤러는 `enabled=False` 사이트를 skip
  - seed 로더가 yaml의 `enabled` 필드를 반영
- 폐쇄망 시뮬레이션: 외부 사이트 접근 차단된 환경에서 작업 시 — Builder 가 접근 가능한 사이트만 우선 검증, 나머지는 `enabled=false` 마킹 후 BUILD-LOG에 결과 표 등재.

#### 3.2 신규 의존성 (`requirements.txt`)
- `httpx>=0.27,<0.30` — Step 1의 `requests`/`feedparser`와 별개로 신규 추가
- `beautifulsoup4>=4.12,<5.0`
- `lxml>=5.0,<6.0` (BeautifulSoup 파서, Windows wheel 안정)
- 기존 `feedparser`(Step 1) 재사용

#### 3.3 디렉토리 구조 (신규 패키지)

```
monitor/watchtower/
├── __init__.py
├── base.py            # 공통 데이터클래스 + abc Crawler
├── robots.py          # robots.txt fetch + 캐시 + is_allowed
├── rss.py             # RssCrawler (feedparser 기반)
├── html.py            # HtmlCrawler (httpx + BS4)
├── detector.py        # SHA-256 content_hash + NEW 판정
└── worker.py          # WatchtowerWorker — domain lock, 5회 실패 카운터, run_site()
```

기존 `monitor/collectors/{rss,dart,fsc}.py` 와 충돌 없음 — Step 1 collectors는 그대로 두고 신규 watchtower 패키지 추가.

#### 3.4 base.py — 공통 인터페이스

```python
from dataclasses import dataclass, field
from abc import ABC, abstractmethod
from datetime import datetime, timezone

USER_AGENT = "Watchtower/1.0 (+https://watchtower.shinhan.local)"  # FR-CRL-005
DEFAULT_TIMEOUT_SEC = 30  # FR-CRL-008

@dataclass
class CrawledItem:
    title: str
    url: str
    summary: str | None
    published_at: datetime | None  # 사이트가 제공할 때만
    content_for_hash: str  # SHA-256 입력 — title + url + summary

@dataclass
class CrawlResult:
    site_id: str
    items: list[CrawledItem] = field(default_factory=list)
    error: str | None = None  # 실패 시 메시지
    blocked_by_robots: bool = False
    duration_ms: int = 0

class Crawler(ABC):
    @abstractmethod
    def crawl(self, site, *, user_agent: str, timeout_sec: int) -> CrawlResult:
        ...
```

#### 3.5 robots.py — robots.txt 검증

```python
import urllib.robotparser
from urllib.parse import urlparse

# 도메인별 RobotFileParser 캐시 (TTL 6시간)
_robots_cache: dict[str, tuple[float, urllib.robotparser.RobotFileParser]] = {}

def is_allowed(url: str, user_agent: str, *, timeout_sec: int = 10) -> bool:
    """url 의 path가 user_agent에게 robots.txt에서 허용되는지.
    
    - 도메인별로 robots.txt를 1회 fetch 후 6시간 캐시
    - robots.txt 자체가 404/타임아웃이면 True (관용적)
    - Disallow 매칭이면 False
    """
```

- httpx로 fetch (urllib.robotparser는 내부적으로 urllib을 쓰는데, 명시적으로 httpx로 fetch 후 `parse()` 호출하여 timeout 강제)
- Step 1의 retry 패턴 재사용 (3회, 1/2/4s 백오프)
- 실패 시 정책: **fail-open** (robots.txt를 못 읽으면 일단 허용). 폐쇄망에서 robots.txt가 자주 누락됨 + over-blocking 방지.

#### 3.6 rss.py — RssCrawler

```python
class RssCrawler(Crawler):
    def crawl(self, site, *, user_agent, timeout_sec):
        # feedparser는 timeout 직접 미지원 → httpx로 fetch 후 feedparser.parse(content) 호출
        # User-Agent 헤더 명시
        # entries에서 title, link, summary, published_parsed 추출
        # CrawledItem 변환
        # 실패 시 CrawlResult.error 세팅
```

- feedparser는 잘못된 XML도 lenient 파싱 — bozo flag 검사. bozo=1 이고 entries=0 이면 error 처리.
- `published_parsed` (struct_time) → UTC datetime 변환.

#### 3.7 html.py — HtmlCrawler

```python
class HtmlCrawler(Crawler):
    def crawl(self, site, *, user_agent, timeout_sec):
        # httpx.get(url, headers={'User-Agent': ua}, timeout=timeout_sec, follow_redirects=True)
        # BeautifulSoup(html, 'lxml')
        # site.content_selector로 본문 영역 추출
        # 그 안의 <a href> 들을 후보 항목으로
        # 동일 페이지 + 절대 URL 만으로 (urljoin)
        # title = anchor text, url = href, summary = anchor.parent.text 일부
```

- selector가 매칭 0개면 `error="content_selector matched no elements"` 세팅
- redirect 따라가되 최대 5회

#### 3.8 detector.py — NEW 감지

```python
import hashlib

def sha256_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()  # 64-char hex

def detect_new_items(
    session, site_id: str, crawled: list[CrawledItem]
) -> list[Item]:
    """이미 (site_id, url) 존재하면 skip. 신규는 Item 생성."""
    existing_urls = {row.url for row in
                     session.execute(select(Item.url).where(Item.site_id == site_id)).all()}
    new_items = []
    for c in crawled:
        if c.url in existing_urls:
            continue
        item = Item(
            site_id=site_id,
            type="NEW",
            title=c.title[:500],
            summary=(c.summary or "")[:2000],
            url=c.url,
            content_hash=sha256_hash(c.content_for_hash),
            detected_at=datetime.now(timezone.utc),
            read_by="",
        )
        new_items.append(item)
    return new_items
```

- Item.id 는 default lambda(uuid.uuid4().hex)로 자동 부여 (3.1a).
- (site_id, url) 유니크 제약은 Step 2에서 이미 존재.

#### 3.9 worker.py — WatchtowerWorker

```python
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse

class WatchtowerWorker:
    def __init__(self, session_factory, *, max_workers: int = 5):
        self._session_factory = session_factory
        self._executor = ThreadPoolExecutor(max_workers=max_workers)
        self._domain_locks: dict[str, threading.Lock] = {}
        self._domain_locks_master = threading.Lock()
        self._in_progress: set[str] = set()  # site_id 진행중 (FR-CRL-007)
        self._in_progress_lock = threading.Lock()
        self._failure_counters: dict[str, int] = {}  # site_id → 연속 실패 수
        self._failure_notified: set[str] = set()  # FR-SITE-006: 1회만 알림

    def _get_domain_lock(self, url: str) -> threading.Lock:
        domain = urlparse(url).netloc
        with self._domain_locks_master:
            if domain not in self._domain_locks:
                self._domain_locks[domain] = threading.Lock()
            return self._domain_locks[domain]

    def run_site(self, site_id: str) -> dict:
        # FR-CRL-007: 이미 진행 중이면 skip
        # robots.py 검증
        # crawl_method 별 Crawler 선택 (rss → RssCrawler, html → HtmlCrawler)
        # detect_new_items
        # commit + site.status='ok' + last_ok_at=now
        # 실패 시 _failure_counters[site_id] += 1; ≥5 → site.status='failed' + (옵션) notify owner
        # 성공 시 _failure_counters.pop(site_id, None); _failure_notified.discard(site_id)

    def run_all(self) -> dict:
        # site.enabled=True인 모든 사이트 병렬 (도메인 lock 적용)
        # 결과 dict 반환
```

- `_notify_owner_failure(site)` — 이번 Step에서는 **로그 출력만** (`logger.error(...)`). 실제 메일/메신저는 Step 4 Notifier에서 통합. BUILD-LOG에 등재.
- 동시성 정책 (FR-CRL-006): 같은 도메인은 lock으로 직렬, 다른 도메인은 병렬.
- 진행 중복 방지 (FR-CRL-007): `_in_progress` set으로 site_id 단위.

#### 3.10 main.py 수정 — Watchtower scheduler 통합

기존 `app.scheduler.setup_scheduler` 는 dart/fsc만 다룸. Watchtower는 사이트 30개를 사이트별 interval로 스케줄링하므로 별도 함수 추가:

```python
# main.py lifespan 내부
from monitor.watchtower.worker import WatchtowerWorker

watchtower_worker = WatchtowerWorker(SessionLocal)
app.state.watchtower_worker = watchtower_worker

# 사이트별 cron 스케줄링
with SessionLocal() as session:
    sites = session.execute(select(Site).where(Site.enabled == True)).scalars().all()
    for site in sites:
        scheduler.add_job(
            watchtower_worker.run_site,
            "interval",
            minutes=site.crawl_interval_min,
            args=[site.id],
            id=f"watchtower_{site.id}",
            replace_existing=True,
            coalesce=True,
            max_instances=1,
        )
```

- `legacy import`(events.jsonl → Item) 는 **유지** (Step 2 동작 보존). Step 4 Notifier 도입 후 deprecate 결정.
- 새로 `POST /api/trigger-watchtower` 엔드포인트 추가:
  - `{site_id?: str}` body — 단일 사이트 또는 전체 trigger
  - 202 Accepted 반환, BackgroundTasks 사용
  - Step 1의 `/trigger`와 별개 endpoint

#### 3.11 테스트 (`tests/test_watchtower_crawler.py` 신규)

- `test_robots_allowed_default` — robots.txt 404 시 fail-open True
- `test_robots_disallow_path` — 명시적 Disallow 매칭 False
- `test_rss_crawler_parses_atom` — fixture XML 입력 → CrawledItem N개
- `test_rss_crawler_handles_bozo` — invalid XML 입력 → error 세팅
- `test_html_crawler_extracts_with_selector` — fixture HTML + selector → 항목 N개
- `test_html_crawler_no_match` — selector 매칭 0개 → error
- `test_html_crawler_timeout` — `httpx.TimeoutException` 발생 → error
- `test_detector_dedup` — 같은 url 두 번 입력 시 새 Item 1개만
- `test_detector_creates_uuid_id` — Item.id 자동 32-hex
- `test_worker_run_site_success` — mocked crawler + DB → site.status='ok'
- `test_worker_run_site_failure_counter` — 5회 연속 실패 → site.status='failed'
- `test_worker_in_progress_skip` — 진행 중 동일 site_id 재호출 시 skip
- `test_worker_domain_lock` — 같은 도메인 사이트 동시 호출 직렬화 확인
- `test_seed_enabled_field` — yaml의 `enabled: false` 사이트 skip
- `test_legacy_import_still_works` — Step 2 import 회귀 없음
- `test_api_items_returns_crawled` — 실제 적재 후 /api/items 응답
- `test_api_trigger_watchtower_202` — POST 202 + BackgroundTasks
- `test_item_id_default_uuid` — 명시 세팅 없이 Item() 생성 시 id 자동 부여
- `test_legacy_import_explicit_id_preserved` — content_hash[:32] 그대로 유지

기존 83 tests + 신규 18 = **101 통과** 목표.

테스트는 외부 네트워크 의존성 회피 — `respx` 또는 `pytest-httpx` 또는 monkeypatch로 httpx mocking. Step 1 패턴 (`unittest.mock`) 재사용 가능.

### Constraints

- **Step 1 자산 무파괴**: `monitor/collectors/`, `monitor/worker.py`, `monitor/notifier.py`, `monitor/matcher.py`, `app/database.py`, `app/scheduler.py` 변경 금지. Step 1 `/events`·`/alerts`·`/trigger`·`/status` 회귀 없음.
- **Step 2 자산 무파괴**: `app/db/models.py`는 Item.id default + Site.enabled 추가 외 변경 없음. `app/routes/watchtower.py` 의 5개 GET endpoint 응답 contract 유지. legacy import 동작 유지.
- **외부 LLM 금지** (CON-006).
- **외부 사이트 접근**: 실제 fetch 시 `User-Agent` 명시 필수 (FR-CRL-005). robots.txt 검증 필수.
- **Timeout 30초** (FR-CRL-008): httpx 모든 요청에 적용.
- **신규 의존성 3개만** (httpx, beautifulsoup4, lxml). aiohttp/Playwright/Scrapy 도입 금지.
- **CHANGE 감지 미구현**: 모든 신규 Item.type='NEW'. CHANGE는 Phase 2.
- **Snapshot 저장 미구현**: HTML diff용 snapshot 테이블·gzip은 Phase 2.
- **알림 발송 미구현**: 5회 실패 시 owner 알림은 logger.error 로만. 실 메일/webhook은 Step 4.

### Success Criteria

- ✅ `pip install -r requirements.txt` 성공 (httpx + bs4 + lxml 추가)
- ✅ `pytest tests/` → 83 (기존) + 18+ (신규) = 101+ 통과
- ✅ `uvicorn main:app --reload` 정상 부팅 + 사이트 30개(또는 enabled=true 만큼) 스케줄링 로그
- ✅ `curl -X POST http://localhost:8000/api/trigger-watchtower` → 202 응답
- ✅ 트리거 후 `data/watchtower.sqlite` 의 items 테이블에 신규 row 생성 (적어도 1개 이상)
- ✅ `curl http://localhost:8000/api/items?limit=200` → 적재된 items 반환
- ✅ 브라우저 `/ui` → 카드 피드에 실 데이터 표시
- ✅ Step 1 `/status`, `/events`, `/alerts`, `/trigger` 응답 회귀 없음
- ✅ `/api/health` 의 `sites_failed` 카운터가 실패한 사이트 수 정확히 반영
- ✅ 같은 도메인 사이트 2개 동시 트리거 시 직렬화 확인 (테스트로 검증)
- ✅ Site.enabled=False 사이트는 스케줄링·crawl 양쪽에서 skip

### Out of Scope (다음 Step)

- ❌ Subscriptions REST API + 권한 분리 (Step 4)
- ❌ SMTP 즉시·다이제스트 (Step 4)
- ❌ alert_log 영속 + 5회 실패 시 실 owner 알림 (Step 4)
- ❌ CHANGE detection / diff snapshot / 30일 보관 (Phase 2)
- ❌ JS 렌더링 사이트 (Playwright, Phase 2)
- ❌ 사내 SSO / 토큰 인증 (Step 5)
- ❌ Docker compose / Harbor (Step 5)
- ❌ 사내 메신저 webhook (Phase 2)

### Decisions

1. **Synchronous httpx 사용**: FastAPI async/sync 혼합 회피. WatchtowerWorker는 ThreadPoolExecutor 패턴 (Step 1과 일관).
2. **robots.txt fail-open**: 폐쇄망 + over-blocking 방지. spec FR-SITE-005 충실히 따르되 robots.txt 자체 fetch 실패는 허용.
3. **5회 실패 알림은 logger.error만**: Step 4 Notifier 통합 전까지. BUILD-LOG에 known gap 등재.
4. **Site.enabled 컬럼 신설**: yaml에서 disabled 사이트를 표현. 기존 `status='blocked'` 와 의미 분리 — `enabled=False` 는 운영자가 의도적으로 끔, `status='blocked'` 는 robots.txt에 의해 자동 차단.
5. **legacy import 보존**: Step 2 동작 그대로. Step 4 이후 deprecate 결정.
6. **content_hash 입력 = title + url + summary**: HTML 본문 전체가 아니라 메타데이터만. CHANGE 감지(Phase 2)에서는 별도 page_hash 사용 예정.
7. **별도 endpoint `/api/trigger-watchtower`**: 기존 `/trigger`(Step 1)와 분리. URL 충돌 없고 의도 명확.

### Flags (추측 금지)

1. **사이트 URL 검증 결과 등재 위치**: `handoff/REVIEW-REQUEST.md` 의 Self-Review 섹션 + BUILD-LOG.md 의 "사이트 검증 결과" 표. yaml 주석은 `# verified 2026-05-10` 또는 `# tentative — pending owner review`.
2. **Builder가 폐쇄망/Proxy 환경에서 작업하는 경우**: 외부 사이트 접근 불가 시, 모든 30개 사이트를 `enabled=false` 로 두고 fixture/mock 기반 테스트로만 검증. BUILD-LOG에 명시. 실 검증은 Owner가 별도 환경에서 수행.
3. **`max_workers` 기본값 5**: Step 1과 일관. env `WATCHTOWER_MAX_WORKERS` override 가능.
4. **scheduler에 등록할 site 갯수**: Site.enabled=True 만. 기동 로그에 `Watchtower scheduler: N sites registered (M skipped — disabled)` 명시.
5. **신규 신규 endpoint 추가 시 OpenAPI doc 자동 등록**: FastAPI 기본 동작. `/docs` 에서 확인 가능.

### Branch / Worktree

- 현재 worktree: `claude/stoic-meitner-47d182`
- Step 2가 master로 push 완료 (61a4f23). 이번 Step 3 commit은 동일 worktree에 추가 후 master push.

---

## How This Works

Builder:
1. 본 brief 정독 → 불명확한 부분은 본 파일 끝에 "## Builder Questions" 섹션으로 추가
2. "Brief 확인 완료" 신호
3. 작업 순서:
   - 3.1 (escalation 처리: Item.id default, Site.enabled, 사이트 URL 검증)
   - 3.2 (의존성 추가)
   - 3.3~3.9 (크롤러 + detector + worker)
   - 3.10 (main.py scheduler 통합)
   - 3.11 (테스트)
4. `pytest tests/ -v` 전체 통과 확인 후 `handoff/REVIEW-REQUEST.md` 작성

---

## Watchtower MVP Roadmap (참고)

| Step | 주제 | 상태 |
|---|---|---|
| 2 | DB + Seed + UI Shell | ✅ 2026-05-10 |
| **3** | **Crawler + Detector + Items 적재 (현재)** | 🚧 |
| 4 | Subscriptions + Notifier | ⏸️ 대기 |
| 5 | Audit + Auth + Deploy | ⏸️ 대기 |

---

작성일: 2026-05-10
Architect: Senior Technical Lead
Status: Ready for Builder
