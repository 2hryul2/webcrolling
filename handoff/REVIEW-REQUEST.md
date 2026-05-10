# Review Request — Step 2 (Watchtower Foundation)

**From:** Builder
**To:** Reviewer
**Step:** 2 — DB + Seed + UI Shell
**Branch / Worktree:** `claude/stoic-meitner-47d182`
**Ready for Review:** YES

---

## Summary

Watchtower 제품의 첫 사이클을 구현했습니다. SQLite + SQLAlchemy 도메인 모델(4 테이블), yaml seed(8 카테고리 / 30 사이트 / 1 사용자), 5개 GET API, prototype HTML 정적 통합, Step 1 events.jsonl → Item 임시 import 브리지. Step 1 RSS/JSONL/`/events`/`/alerts` 시스템은 그대로 보존.

## Files Changed

### 신규 파일

| 파일 | 라인 | 역할 |
|---|---:|---|
| `app/db/__init__.py` | 1 | 패키지 마커 |
| `app/db/session.py` | 96 | SQLAlchemy engine + WAL/FK PRAGMA + `init_db`/`get_session`/`engine_for_path`/`sessionmaker_for_engine` |
| `app/db/models.py` | 138 | `User` / `Category` / `Site` / `Item` ORM. `Site` validator로 FR-SITE-003 clamp(<60→60), `Item.read_by` CSV 헬퍼 |
| `app/db/seed.py` | 137 | yaml 로드 + `${VAR:-default}` 치환 + idempotent insert (`run_seed`) |
| `app/db/import_legacy.py` | 152 | events.jsonl → Item 매핑 (`source=dart\|fsc → site_id=s1`, content_hash 32-prefix를 Item.id로 사용해 idempotent) |
| `app/routes/watchtower.py` | 222 | `/api/categories` `/api/sites` `/api/items` `/api/users/me` `/api/health` |
| `config/seed_categories.yaml` | 25 | 8 카테고리 (id `reg`/`gov`/`comp`/`fin`/`ai`/`sec`/`mkt`/`res`) |
| `config/seed_sites.yaml` | 196 | 30 사이트, 모든 URL https + 추정값 `# tentative` 주석 |
| `config/seed_users.yaml` | 9 | 단일 사용자, email은 `${WATCHTOWER_ADMIN_EMAIL:-admin@watchtower.local}` |
| `static/watchtower.html` | 663 | prototype 디자인/CSS/인터랙션 보존 + 인라인 데이터를 `bootData()` fetch로 교체. `escapeHtml` 적용 폭 확대. 원문 링크 href에 `https?://` 화이트리스트. |
| `tests/test_watchtower.py` | 240 | 16개 테스트 (모델·seed·라우트·/ui·/static·legacy import) |

### 수정 파일

| 파일 | 변경 |
|---|---|
| `requirements.txt` | `sqlalchemy>=2.0,<3.0` 추가 |
| `main.py` | `init_db()` + `run_seed()` + `import_legacy_events()` lifespan, watchtower_router include, `/static` mount, `/ui` FileResponse |
| `tests/conftest.py` | `watchtower_db` (격리 SQLite sessionmaker) + `watchtower_app` (FastAPI + TestClient + `get_session` override) 픽스처 |
| `.env.example` | `WATCHTOWER_ADMIN_EMAIL=admin@watchtower.local` |

## Test Results

```
tests\test_database.py .........                                    [ 9]
tests\test_dedup.py .....                                           [ 5]
tests\test_matcher.py .......                                       [ 7]
tests\test_models.py ........                                       [ 8]
tests\test_notifier.py .........                                    [ 9]
tests\test_routes.py ............                                   [12]
tests\test_rss.py .............                                     [13]
tests\test_watchtower.py ................                           [16]
tests\test_worker.py ....                                           [ 4]

=========================== 83 passed in 2.77s ============================
```

- 기존 67 통과 그대로
- 신규 16 추가 (브리프 목표 12 + 4 보강: read_by 헬퍼, env 치환, items limit 검증, me 404)
- skipped: 0

## Smoke Test (Lifespan)

```
$ rm -f data/watchtower.sqlite data/watchtower.sqlite-{wal,shm}
$ python -c "import asyncio, main; ..."
INFO app.db.seed: [seed] inserted: 8 categories / 30 sites / 1 users
INFO main: [seed] watchtower ready — categories=8 sites=30 users=1
INFO app.db.import_legacy: Legacy events.jsonl not found ... — skipping import
INFO app.scheduler: Scheduled dart every 300s
INFO app.scheduler: Scheduled fsc every 600s
INFO main: Application startup complete

$ sqlite3 data/watchtower.sqlite "PRAGMA journal_mode" → wal
$ sqlite3 data/watchtower.sqlite "SELECT COUNT(*) FROM categories" → 8
$ sqlite3 data/watchtower.sqlite "SELECT COUNT(*) FROM sites" → 30
```

기동 명령: `uvicorn main:app --reload`. 진입점: `http://localhost:8000/ui`.

## Major Components

- **app/db/session.py** — sync 엔진 1개. `event.listen(eng, "connect", _apply_pragmas)` 로 모든 신규 connection에 `PRAGMA journal_mode=WAL` + `PRAGMA foreign_keys=ON` 적용. 테스트는 `engine_for_path(tmp_path/"test.sqlite")` 로 격리 엔진을 받아 production 엔진 미오염.
- **app/db/models.py** — `@validates("crawl_interval_min")` 데코레이터로 ORM 객체 생성 시점부터 60 분 미만 입력값을 60으로 clamp + 경고 로그 (FR-SITE-003). `Item.read_by` 는 CSV 문자열, `mark_read()` 가 sorted CSV로 idempotent 추가.
- **app/db/seed.py** — `_VAR_RE = r"\$\{([A-Z_][A-Z0-9_]*)(?::-([^}]*))?\}"` 로 `${VAR}` 와 `${VAR:-default}` 만 치환 (shell `:?` 등 비지원). `INSERT OR IGNORE` 의미는 PK 사전조회로 구현 (id 중복이면 skip). `categories → users → sites` 순으로 commit (FK 충돌 회피).
- **app/db/import_legacy.py** — 매핑은 `dart|fsc → ('s1', 'reg')` 만. Item.id = `content_hash[:32]` 라서 두 번 import 해도 동일 row. `(site_id, url)` 사전 조회 + `seen_in_batch` 로 같은 파일 안의 중복도 방어.
- **app/routes/watchtower.py** — 5개 GET. `/api/items` 정렬은 SQL ORDER BY로 detected_at DESC를 가져온 뒤, `read` 플래그(me.id ∈ read_by CSV)를 Python에서 계산해 `(read, -ts)` 로 재정렬. limit 1~1000 제약은 FastAPI Query validator. `_resolve_me()` 는 `WATCHTOWER_ADMIN_EMAIL` → 첫 사용자 fallback. Health는 SQLAlchemy 예외 시 `db="error"`.
- **static/watchtower.html** — prototype에서 inline `CATEGORIES`/`SITES`/`ITEMS` 만 fetch로 교체. `bootData()` 가 4개 endpoint 병렬 fetch → render. `formatRelative` 로 ISO → 한국어 상대시간. `markRead()` 는 mock (Step 4에서 PATCH 교체). 원문 링크는 `https?://` 정규식 화이트리스트로 `javascript:` 회피. `escapeHtml` 모든 동적 문자열에 적용.
- **tests/conftest.py** — `watchtower_db` (sessionmaker 픽스처) 와 `watchtower_app` (TestClient + `get_session` dependency override) 두 단으로 분리. 후자는 main.py 의 lifespan(=Worker/scheduler 부팅) 우회.

## Self-Review

### Q1. Reviewer가 가장 먼저 잡을 것 같은 부분

1. **`/api/items` Python 정렬** — SQL `ORDER BY` 한 번으로 끝낼 수 있을 것 같지만, `read` 가 me.id가 read_by CSV에 들어있는지 여부라 SQLite JSON1 미사용 정책상 깔끔하게 표현 안 됨. limit*2 만큼 가져와 Python 정렬 후 limit 슬라이스 — Phase 1 볼륨에서 안전.
2. **30개 사이트 URL 정확도** — Flag §4 에 따라 ideation §부록 A 추정 + `# tentative` 주석. Step 3에서 검증.
3. **`test_ui_html_response` / `test_static_files_mount` 가 main.py 전체 lifespan을 깨운다** — Worker/scheduler 도 부팅됨. 그러나 TestClient `with` 구문이 끝나면 정상 종료되므로 부수 효과 없음. 분리하려면 `/ui` 를 별도 모듈로 빼야 함 → 과한 분리. 그대로 둠.

### Q2. 브리프 항목별 체크

- [x] `sqlalchemy>=2.0,<3.0` 추가 (`aiosqlite`/`alembic` 미추가)
- [x] `app/db/__init__.py`, `session.py` (engine + PRAGMA + init_db + get_session + engine_for_path/sessionmaker_for_engine)
- [x] `app/db/models.py` (`Category`/`Site`/`Item`/`User` + validator + read_by 헬퍼)
- [x] `app/db/seed.py` (`run_seed` 멱등 + env 치환)
- [x] `app/db/import_legacy.py` (try/except, content_hash 기반 idempotent)
- [x] `config/seed_categories.yaml` 8개
- [x] `config/seed_sites.yaml` 30개 (https + tentative 주석)
- [x] `config/seed_users.yaml` 1개 (env 치환)
- [x] `app/routes/watchtower.py` 5 endpoint
- [x] `static/watchtower.html` (prototype 디자인/인터랙션 보존, inline data → fetch)
- [x] `main.py` (StaticFiles, watchtower_router, /ui FileResponse, lifespan에 init_db + run_seed + import_legacy_events)
- [x] `tests/test_watchtower.py` 16개 (브리프 12개 + 4개 보강)
- [x] `tests/conftest.py` `watchtower_db` 픽스처 (+ `watchtower_app` 보강)
- [x] `.env.example` 에 `WATCHTOWER_ADMIN_EMAIL`
- [x] 기존 67 테스트 보존 → 83 통과
- [x] `data/watchtower.sqlite` WAL 모드로 생성 (smoke test 검증)
- [x] `/status` Step 1 응답 회귀 없음 (test_routes.py 12 통과)

### Q3. 데이터가 비거나 실패할 때 사용자 화면

- API 4개 중 하나라도 실패 시 `bootData()` catch → `<div class="empty">데이터를 불러오지 못했습니다. 잠시 후 다시 시도해주세요.</div>` (한국어, NFR-USE-001).
- DB는 비어 있어도(`/api/items` → `[]`) UI는 `표시할 업데이트가 없습니다.` 빈 상태 카드 표시.
- `/api/users/me` 가 404일 때 (`등록된 사용자가 없습니다`) UI는 fetch 단에서 throw → 동일한 빈 상태로 fallback. 아바타는 `·` 점으로 유지.
- DB 연결 실패 시 `/api/health` 가 `{ok:false, db:"error"}` 반환 (lifespan은 그대로 진행).

## Deviations from Brief (with Justification)

1. **`item_count_unread` 계산 위치** — 브리프는 "subquery 또는 Python 후처리". CSV `read_by` 컬럼에 me.id 포함 여부 판정이라 SQL `LIKE` 보단 Python 후처리가 정확 (`u1` 이 `u10` 의 부분문자열로 오탐될 위험). 메모리 비용은 Phase 1 볼륨에서 무시.
2. **`Item.id` 형식** — 브리프 Flag §3 `uuid.uuid4().hex`. legacy import에서는 idempotency 위해 `content_hash[:32]` 사용 (uuid4 면 두 번 import 시 다른 id로 중복). 신규(Step 3) 크롤러 적재 시 uuid4 사용 가능. 모델 자체의 PK 형식 제약은 String(32)로 동일.
3. **Item.url, title length clamping** — legacy import 시 String(500) 컬럼 한계 초과 row 가 있을 수 있어 `[:500]` 슬라이스 방어. 데이터 손실 가능성은 있지만 demo 매핑 단계라 허용.
4. **lifespan 로그 포맷** — 브리프 예시 `[seed]`. 실제로는 `app.db.seed: [seed] inserted: 8 categories / 30 sites / 1 users` (`run_seed` 내부) + `main: [seed] watchtower ready — categories=8 sites=30 users=1` (lifespan에서 한 번 더 요약). 두 줄 모두 grep `[seed]` 가능.
5. **테스트 픽스처 1개 추가 (`watchtower_app`)** — 브리프엔 `watchtower_db` 만 명시. HTTP route 테스트마다 `app.dependency_overrides[get_session]` 를 반복하지 않게 컴포지트 픽스처 추가.

## Known Gaps / Limitations (Next Step)

- **Watchtower 전용 RSS/HTML 크롤러 부재** — Step 3. 현재 `/api/items` 는 비어 있거나 legacy import 결과만 표시.
- **CHANGE detection / diff snapshot** — Phase 2. UI는 `item.diff` 가 없으므로 항상 `NEW` 만 표시.
- **Subscriptions REST API 부재** — Step 4. UI는 localStorage `subscribed`/`alertOn`/`channel` 로만 동작 (페이지 새로고침 후에도 유지되지만 다른 디바이스로 전이 불가).
- **읽음 처리는 mock** — UI `markRead()` 가 in-memory 상태만 업데이트. Step 4에서 `PATCH /api/items/{id}/read` 로 교체.
- **30개 사이트 URL/selector 추정 다수** — `# tentative` 주석으로 마킹. 실 URL/HTML 구조 검증은 Step 3.
- **`/api/items` 정렬 + read 계산이 Python 사이드** — Phase 1 (~수백 row) 에는 충분. 수만 row 이상이면 SQLite JSON1 도입 또는 read 상태 별도 테이블 분리 검토 필요.
- **Windows에서 `chmod 0o600` 무동작** — Step 1 정책과 동일. Linux 배포 시 자동 적용.

## Open Questions / Uncertainties

없음. 브리프 Flags §1~§5 모두 명시된 가이드대로 구현했습니다. Reviewer가 추가 의문 시 본 문서에 코멘트 부탁드립니다.

---

작성일: 2026-05-10
Builder: Senior Developer
