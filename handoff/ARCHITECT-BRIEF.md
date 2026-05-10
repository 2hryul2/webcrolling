# Architect Brief

Work specification for Builder.

---

## Step 2 — Watchtower Foundation: DB + Seed + UI Shell

### What

Watchtower 제품의 첫 사이클. SQLite + SQLAlchemy 도메인 모델 + yaml seed + 5개 GET API + prototype HTML을 정적 자원으로 통합. Step 1 RSS 시스템(JSONL/`/events`/`/alerts`)은 그대로 보존하며 새 영역만 추가.

```
사용자(브라우저) → /ui (FastAPI FileResponse)
                    ↓
            static/watchtower.html
                    ↓ fetch
              /api/categories | /api/sites | /api/items | /api/users/me | /api/health
                    ↓
              SQLAlchemy ORM (sync session)
                    ↓
              data/watchtower.sqlite (WAL)
                    ↑ startup
              seed_categories.yaml + seed_sites.yaml + seed_users.yaml
              + (선택) data/events.jsonl → Item import
```

### Why

- ideation `subscribe-watch_20260510_1029.md` + spec `spec_20260510_1029.md` 의 Phase 1 MVP 진입
- prototype UI를 FastAPI 백엔드와 처음 통합 → 시각적 마일스톤
- Step 3 (크롤러+Detector)·Step 4 (Subscriptions+Notifier) 의 토대 마련

### Requirements

#### 1. 의존성 (`requirements.txt`)
- `sqlalchemy>=2.0,<3.0` 추가
- `aiosqlite`/`alembic` 미추가 (Step 5에서 검토)
- 나머지 Step 1 의존성 그대로

#### 2. DB 인프라 (`app/db/`)

`app/db/__init__.py` — 빈 패키지

`app/db/session.py`:
- `engine = create_engine("sqlite:///data/watchtower.sqlite", future=True, connect_args={"check_same_thread": False})`
- connection 시 `PRAGMA journal_mode=WAL`, `PRAGMA foreign_keys=ON`
- `SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)`
- `def get_session() -> Iterator[Session]` (FastAPI `Depends`)
- `def init_db() -> None` — `Base.metadata.create_all(engine)` + 데이터 파일 권한 0o600 (Windows no-op)

`app/db/models.py`:
```python
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

class Base(DeclarativeBase): pass

class Category(Base):
    __tablename__ = "categories"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    owner_dept: Mapped[str] = mapped_column(String(100))
    owner_user_id: Mapped[str | None] = mapped_column(String(32), ForeignKey("users.id"), nullable=True)
    sites: Mapped[list["Site"]] = relationship(back_populates="category")

class Site(Base):
    __tablename__ = "sites"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    url: Mapped[str] = mapped_column(String(500))
    category_id: Mapped[str] = mapped_column(ForeignKey("categories.id"))
    crawl_method: Mapped[str] = mapped_column(String(8))  # 'rss'|'html'|'js'
    content_selector: Mapped[str | None] = mapped_column(String(200), nullable=True)
    crawl_interval_min: Mapped[int] = mapped_column(default=60)  # FR-SITE-003 — 최소 60 강제 (validator)
    status: Mapped[str] = mapped_column(String(16), default="ok")  # ok|delayed|failed|blocked
    last_ok_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    category: Mapped["Category"] = relationship(back_populates="sites")
    items: Mapped[list["Item"]] = relationship(back_populates="site")

class Item(Base):
    __tablename__ = "items"
    __table_args__ = (UniqueConstraint("site_id", "url", name="uq_site_url"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id"))
    type: Mapped[str] = mapped_column(String(8), default="NEW")  # NEW|CHANGE
    title: Mapped[str] = mapped_column(String(500))
    summary: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    url: Mapped[str] = mapped_column(String(500))
    content_hash: Mapped[str] = mapped_column(String(64))  # SHA-256
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    read_by: Mapped[str] = mapped_column(String(500), default="")  # CSV of user_ids
    site: Mapped["Site"] = relationship(back_populates="items")

class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    dept: Mapped[str] = mapped_column(String(100))
    email: Mapped[str] = mapped_column(String(200), unique=True)
    messenger_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    role: Mapped[str] = mapped_column(String(16), default="member")  # member|owner|operator
```

- FR-SITE-003 강제: `Site.__init__` 에서 `crawl_interval_min < 60` 시 60으로 clamp + 경고 로그.
- `Item.read_by` 는 CSV 문자열 (예: `"u1,u2"`); set 변환 헬퍼 `Item.read_by_set()` / `Item.mark_read(user_id)` 메서드 추가.

#### 3. Seed (`config/seed_categories.yaml` · `config/seed_sites.yaml` · `config/seed_users.yaml`)

`config/seed_categories.yaml` (8개, prototype CATEGORIES와 동일):
```yaml
categories:
  - id: reg
    name: 금융 규제·감독
    owner_dept: 컴플라이언스
  - id: gov
    name: 정부·정책
    owner_dept: 전략기획
  - id: comp
    name: 경쟁사 동향
    owner_dept: AX팀
  - id: fin
    name: 핀테크·빅테크
    owner_dept: 디지털전략
  - id: ai
    name: AI·기술 동향
    owner_dept: AX팀
  - id: sec
    name: 보안·인프라
    owner_dept: 정보보안
  - id: mkt
    name: 산업·시장
    owner_dept: 리서치
  - id: res
    name: 학회·연구
    owner_dept: 리서치
```

`config/seed_sites.yaml` (30개, prototype SITES 배열 그대로 — id `s1`~`s30`):
- 각 항목: `id`, `name`, `url`, `category_id`, `crawl_method`(`rss`|`html`), `content_selector`(html이면 명시, rss는 null), `crawl_interval_min`
- URL은 ideation §부록 A 그대로 (없는 항목은 합리적 추정 — 단, Builder가 명시적으로 가짜 URL인지 마킹). 실제 URL 검증은 Step 3.
- 예시:
  ```yaml
  sites:
    - id: s1
      name: 금융위원회
      url: https://www.fsc.go.kr/no010101
      category_id: reg
      crawl_method: html
      content_selector: "#content .board-list"
      crawl_interval_min: 120
    - id: s17
      name: Anthropic News
      url: https://www.anthropic.com/news
      category_id: ai
      crawl_method: html
      content_selector: "main article"
      crawl_interval_min: 240
    # ... s2~s30
  ```

`config/seed_users.yaml`:
```yaml
users:
  - id: u1
    name: 운영자
    dept: AX팀
    email: ${WATCHTOWER_ADMIN_EMAIL:-admin@watchtower.local}
    role: operator
```
- yaml 로더에서 `${VAR:-default}` 치환 처리.

`app/db/seed.py`:
- `def run_seed(session: Session) -> dict` — 카테고리·사이트·사용자 로드. `INSERT OR IGNORE` 의미 (id 중복 시 skip). 변경된 행 count 반환.
- yaml 로드 시 `${VAR}` 치환은 `os.path.expandvars` + 정규식 `${VAR:-default}` 처리.
- 멱등: 두 번 실행해도 row 수 동일.

#### 4. Step 1 데이터 브리지 (`app/db/import_legacy.py`, 선택적)
- `def import_legacy_events(session: Session, jsonl_path: str) -> int` — events.jsonl 읽어 Item 생성
- 매핑: `source='dart' or 'fsc' → site_id='s1'` (임시), `category_id='reg'`
- `content_hash` 는 events.jsonl의 동일 필드 사용
- 이미 동일 `(site_id, url)` 존재 시 skip (UniqueConstraint)
- detected_at 은 events.jsonl 의 `fetched_at` 사용
- 실패해도 startup 진행 (try/except + warning)

#### 5. REST API (`app/routes/watchtower.py`)

응답은 모두 JSON, UTF-8.

`GET /api/categories`:
```json
[
  {"id": "reg", "name": "금융 규제·감독", "owner_dept": "컴플라이언스",
   "sites_count": 4, "item_count_unread": 12}
]
```
- `sites_count` = 해당 카테고리의 활성 site 수
- `item_count_unread` = me.id가 read_by에 없는 item 수 (subquery 또는 Python 후처리)

`GET /api/sites`:
```json
[{"id": "s1", "name": "금융위원회", "url": "...", "category_id": "reg",
  "crawl_method": "html", "status": "ok", "last_ok_at": null}]
```

`GET /api/items?category=reg&type=NEW&limit=200`:
```json
[{"id": "...", "site_id": "s1", "site_name": "금융위원회",
  "category_id": "reg", "type": "NEW", "title": "...", "summary": "...",
  "url": "...", "detected_at": "2026-05-09T...Z", "read": false}]
```
- 정렬: `read ASC, detected_at DESC` (FR-FEED-004)
- `limit` 1~1000 (default 200)
- `category` 누락 시 전체

`GET /api/users/me`:
```json
{"id": "u1", "name": "운영자", "dept": "AX팀",
 "email": "...", "role": "operator"}
```
- 환경변수 `WATCHTOWER_ADMIN_EMAIL` 매칭되는 사용자 반환. 없으면 첫 사용자.

`GET /api/health`:
```json
{"ok": true, "db": "connected", "sites_total": 30, "sites_failed": 0,
 "uptime_seconds": 1234, "now": "..."}
```

기존 `/`, `/status`, `/events`, `/alerts`, `/trigger` 보존.

#### 6. 정적 자원 (`static/watchtower.html`)

- `ideation/watchtower-prototype.html` 을 `static/watchtower.html` 로 복사
- `<script>` 안의 `const CATEGORIES = [...]; const SITES = [...]; const ITEMS = [...];` 를 startup fetch 로 교체:
  ```js
  let CATEGORIES = [];
  let SITES = [];
  let ITEMS = [];
  let ME = null;

  async function bootData() {
    const [cats, sites, items, me] = await Promise.all([
      fetch('/api/categories').then(r => r.json()),
      fetch('/api/sites').then(r => r.json()),
      fetch('/api/items?limit=200').then(r => r.json()),
      fetch('/api/users/me').then(r => r.json()),
    ]);
    CATEGORIES = cats;
    SITES = sites;
    // /api/items 응답을 prototype의 ITEMS 형식으로 매핑
    ITEMS = items.map(it => ({
      id: it.id,
      siteId: it.site_id,
      cat: it.category_id,
      type: it.type,
      title: it.title,
      summary: it.summary || '',
      detected: formatRelative(it.detected_at),  // 1시간 전 등
      read: it.read,
    }));
    ME = me;
    renderAll();
  }

  function formatRelative(iso) {
    const d = new Date(iso);
    const diffMin = Math.floor((Date.now() - d.getTime()) / 60000);
    if (diffMin < 60) return `${diffMin}분 전`;
    if (diffMin < 1440) return `${Math.floor(diffMin/60)}시간 전`;
    if (diffMin < 2880) return '어제';
    return `${Math.floor(diffMin/1440)}일 전`;
  }

  // 기존 init 호출(`renderAll()`)을 `bootData()` 로 교체.
  ```
- subscriptions·alertOn·channel localStorage 그대로 (Step 4에서 API 통합)
- markRead → 일단 mock (`item.read = true; renderAll()`). Step 4에서 `PATCH /api/items/{id}/read` 로 교체.
- 검색 input 동작 그대로
- topbar avatar `AT` → `ME.name[0]` 동적

#### 7. main.py 변경

- `from fastapi.staticfiles import StaticFiles` / `FileResponse`
- lifespan 내부:
  ```python
  init_db()
  with SessionLocal() as session:
      run_seed(session)
      try:
          import_legacy_events(session, str(DATA_DIR / "events.jsonl"))
      except Exception as e:
          logger.warning("Legacy import skipped: %s", e)
  ```
- `app.include_router(watchtower_router)`
- `app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")`
- `@app.get("/ui")` → `FileResponse(BASE_DIR / "static" / "watchtower.html")`

#### 8. 테스트 (`tests/test_watchtower.py`)

`tests/conftest.py` 에 fixture 추가:
```python
@pytest.fixture
def watchtower_db(tmp_path):
    from app.db.session import Base, engine_for_path, sessionmaker_for_engine
    db_path = tmp_path / "test.sqlite"
    engine = engine_for_path(str(db_path))
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker_for_engine(engine)
    yield SessionLocal
    Base.metadata.drop_all(engine)
```
(이를 위해 `app/db/session.py` 에 `engine_for_path()`·`sessionmaker_for_engine()` 헬퍼 추가)

테스트 항목:
- `test_models_create_relationships` — Category·Site·Item·User 생성 + relationship 확인
- `test_seed_idempotent` — `run_seed` 2회 실행 후 row 수 동일
- `test_seed_loads_8_categories_30_sites_1_user`
- `test_site_crawl_interval_clamp` — 30분 입력 시 60으로 clamp
- `test_api_categories_smoke` — TestClient `GET /api/categories` 200 + 8건
- `test_api_sites_smoke` — `GET /api/sites` 200 + 30건
- `test_api_items_smoke` — 빈 DB에서 200 + 빈 배열, 항목 추가 후 정렬 확인
- `test_api_users_me_smoke` — `WATCHTOWER_ADMIN_EMAIL` 환경변수 매칭
- `test_api_health_smoke` — ok=true
- `test_ui_html_response` — `GET /ui` 200 + content-type text/html + body에 `Watchtower` 포함
- `test_static_files_mount` — `GET /static/watchtower.html` 200
- `test_legacy_import_idempotent` — 동일 events.jsonl 2회 import 시 Item count 동일

기존 67 tests 모두 통과 유지.

### Constraints

- **Step 1 자산 무파괴**: `monitor/`, `app/database.py`(JSONL), `app/scheduler.py`(APScheduler), 기존 라우트 그대로.
- **단일 사용자 가정** (ASM-005): Phase 1은 환경변수 기반 1명. `users` 테이블 행 1개.
- **외부 의존 추가 최소화**: `sqlalchemy` 만. `aiosqlite`·`alembic` 보류.
- **NFR-USE-001 (한국어)**: 모든 UI·에러 메시지 한국어.
- **NFR-SEC-005 (시크릿)**: 환경변수 (`WATCHTOWER_ADMIN_EMAIL`) 만. 코드 하드코딩 금지.
- **CON-006 (외부 LLM 금지)**: 자동 요약·분류 코드 일체 금지.
- **파일 권한 0o600**: `data/watchtower.sqlite` (Windows no-op, Step 1과 동일 정책).

### Success Criteria

- ✅ `pip install -r requirements.txt` 성공
- ✅ `uvicorn main:app --reload` 정상 부팅 + lifespan 로그에 `[seed]` 8 categories / 30 sites / 1 user 출력
- ✅ `data/watchtower.sqlite` 생성 + WAL 파일 동반
- ✅ 브라우저 `http://localhost:8000/ui` → prototype UI 정상 렌더 + 사이드바 8 카테고리 + 사이트 30개 정상 fetch
- ✅ 카드 피드: events.jsonl import 결과 (없으면 빈 상태)
- ✅ 별/벨/필터/상세패널 mock 동작 (localStorage)
- ✅ `pytest tests/` → 기존 67 + 신규 12개 = 79 통과 (또는 그 이상)
- ✅ `curl http://localhost:8000/status` → Step 1 응답 회귀 없음
- ✅ `curl http://localhost:8000/api/health` → `{"ok": true, ...}`

### Out of Scope (다음 Step)

- ❌ Watchtower 전용 RSS/HTML 크롤러 (Step 3)
- ❌ NEW Detector 자동 적재 (Step 3)
- ❌ CHANGE 감지·diff·snapshot (Phase 2)
- ❌ subscriptions API + 권한 분리 (Step 4)
- ❌ SMTP 즉시·다이제스트 (Step 4)
- ❌ alert_log·audit_log 영속 (Step 4·5)
- ❌ 토큰 인증 / SSO (Step 5)
- ❌ Docker compose / Harbor (Step 5)
- ❌ webhook (Phase 2)

### Decisions

1. **Sync SQLAlchemy**: FastAPI async 컨텍스트에서 sync session 사용. `Depends(get_session)` 패턴. async DB는 Step 5 검토.
2. **WAL 모드**: `PRAGMA journal_mode=WAL` 영구 적용 (재연결 시 유지).
3. **단일 사용자**: `users` 테이블 행 1개. `WATCHTOWER_ADMIN_EMAIL` 매칭으로 me 결정.
4. **read_by CSV**: JSON 컬럼 대신 CSV 문자열 — SQLite JSON1 의존 회피.
5. **prototype HTML 무수정**: 가능한 한 인라인 데이터만 fetch로 교체. 디자인·인터랙션은 그대로.
6. **legacy import 임시**: dart/fsc → s1 매핑은 데모용. Step 3에서 정식 매핑.
7. **테스트 DB**: `tmp_path / 'test.sqlite'` 사용. 기존 테스트의 JSONL fixture와 충돌 없음.

### Flags (추측 금지)

1. **`WATCHTOWER_ADMIN_EMAIL`** — `.env.example` 에 `admin@watchtower.local` 더미 추가. 운영 시 사내 이메일로 교체.
2. **카테고리/사이트 ID 형식** — prototype과 동일 (`reg`, `s1` 등). UI JS가 이 ID로 매칭하므로 임의 변경 금지.
3. **Item ID** — `uuid.uuid4().hex` (32자 hex). ULID 미도입.
4. **사이트 30개 상세 정보** — prototype `SITES` 배열의 `name`·`cat`·`status`만 있음. URL·crawl_method·content_selector·interval은 ideation §부록 A 추정 + 필요 시 Builder가 합리적 기본값으로 채움. **모든 URL은 https로 통일 + 추정값에는 yaml 주석 `# tentative`** 표시.
5. **/api/items.detected_at 형식** — ISO 8601 UTC (`...Z`). 프론트에서 상대시간 변환.

### Branch / Worktree

- 현재 worktree: `claude/stoic-meitner-47d182` (이미 active)
- 작업은 이 브랜치에서 진행 → master로 PR/머지

---

## How This Works

Builder:
1. 본 brief 정독 → 불명확한 부분 ARCHITECT-BRIEF.md 끝에 질문 추가
2. 완전 이해 시 "Brief 확인 완료" 신호
3. 위 작업 분할(2.1~2.8) 순서대로 구현
4. `pytest` 전체 통과 확인 후 `handoff/REVIEW-REQUEST.md` 작성

---

작성일: 2026-05-10
Architect: Senior Technical Lead
Status: Ready for Builder
