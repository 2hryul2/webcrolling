# Session Checkpoint

Architect writes here at the end of each session.

---

## Template

```
# Session Checkpoint

Date: [date]
Architect: [your name]
Status: [ACTIVE / IDLE]

## What's Done

- Step N: [title] — ✅ deployed [date]
- Step N+1: [title] — ⏸️ awaiting review

## What's Next

1. Reviewer to post REVIEW-FEEDBACK.md
2. [Next action]
3. [Next action]

## Current Brief

See ARCHITECT-BRIEF.md (Step X)

## Key Context

[Any context that future Architect needs to resume immediately]

## Git State

- Branch: main
- Last commit: [commit message]
- Uncommitted files: [list or "none"]
```

---

## When to Write

Architect writes this at the end of their session:
- After Builder completes and hands off to Reviewer
- After Reviewer posts feedback
- After deploying a completed step
- Before ending the session

This lets the next Architect:
1. Skip BUILD-LOG and ARCHITECT-BRIEF
2. Read this file
3. Know exactly what to do next

If the checkpoint is recent and complete, skip all other files.

---

## Current State

# Session Checkpoint

Date: 2026-05-10
Architect: Senior Technical Lead
Status: IDLE (Step 3 deployed)

## What's Done

- Step 1: FastAPI + RSS 수집 — ✅ deployed 2026-05-09
- Step 2: Watchtower Foundation (DB + Seed + UI Shell) — ✅ deployed 2026-05-10
- **Step 3: Watchtower Crawler + Detector + Items 적재 — ✅ deployed 2026-05-10**
  - 신규 패키지 `monitor/watchtower/` (base/robots/rss/html/detector/worker 6 모듈)
  - `scripts/verify_seed_urls.py` (사이트 URL 검증 utility, 정식 home 승격)
  - `tests/test_watchtower_crawler.py` (21 hermetic 테스트)
  - `app/db/models.py`: Item.id `default=lambda: uuid.uuid4().hex`, Site.enabled boolean 컬럼
  - `config/seed_sites.yaml`: 14 verified / 1 URL 보정(s14 토스 RSS) / 15 enabled=false
  - `main.py`: WatchtowerWorker lifespan + enabled 사이트 사이트별 APScheduler interval + `POST /api/trigger-watchtower` 202
  - `requirements.txt`: httpx + beautifulsoup4 + lxml 추가
  - 104 tests passing (83 baseline + 21 new), 0 skipped
  - Reviewer APPROVED, Conditions 0건 (3 escalations 처리)
  - 1회 트리거 검증: 9 사이트 성공 + 239 items 적재

## What's Next

다음 세션 시작 시 → **Step 4: Subscriptions + Notifier**

1. **Step 3 escalation 후속**:
   - HTML 크롤러 selector 추정값 보정 (s10, s12, s16, s19, s24, s29 — `# tentative selector` 표시 사이트)
   - enabled=false 15개 사이트 Owner 검토 (사내망 전용 URL/대체 endpoint 발굴)
2. **Subscriptions REST API** (FR-SUB-001~005)
   - `subscriptions` 테이블 신설 (user_id, category_id, channel)
   - `GET/PATCH /api/subscriptions` (자기 구독만 조회·수정)
   - UI localStorage → 서버 영속 전환 (별/벨 토글, 채널 instant/digest/off)
   - `PATCH /api/items/{id}/read` (읽음 처리 영속)
3. **Notifier — SMTP 즉시 + 다이제스트** (FR-NOTIF-001~008)
   - 즉시: ⭐+🔔 둘 다 켠 카테고리 → 60초 이내 메일
   - 다이제스트: 매일 09:00 KST → 직전 24h 묶음
   - Step 1 SMTP 모듈(`monitor/notifier.py`) 패턴 재사용 검토
   - 5회 실패 시 site owner 알림 (logger.error → 실 메일로 승격)
4. **alert_log 영속** (FR-NOTIF-005) — `alert_log` 테이블 신설
5. **Spec v0.2 보완** (B1~B4) — 별도 사이클 또는 Step 4 후미

## Current Brief

(Step 2 brief는 `handoff/ARCHITECT-BRIEF.md`에 보존. Step 3 시작 시 새로 작성)

## Key Context

### Step 2 추가 사항
- **DB**: SQLite WAL (`data/watchtower.sqlite`), SQLAlchemy 2.0 sync, `Base.metadata.create_all` (Alembic Step 5)
- **모델**: User/Category/Site/Item (subscriptions·alerts·snapshot·audit는 Step 4·5에서)
- **read 추적**: `Item.read_by` CSV 문자열 (JSON1 회피)
- **`/api/items` 정렬**: SQL ORDER BY detected_at DESC + Python read_by 후처리
- **단일 사용자**: `WATCHTOWER_ADMIN_EMAIL` 환경변수, fallback `admin@watchtower.local`
- **legacy import**: `events.jsonl` → Item.id = `content_hash[:32]` 로 idempotent

### Step 1 자산 (보존됨)
- `monitor/`, `app/database.py` (JSONL), `app/scheduler.py`, `app/routes/status.py` 변경 없음
- `/`, `/status`, `/events`, `/alerts`, `/trigger` 기존 엔드포인트 그대로

### 핵심 문서
- `ideation/ideation_subscribe-watch_20260510_1029.md` (Watchtower ideation)
- `spec_20260510_1029.md` (EARS spec, FR-CAT~FR-NOTIF + NFR)
- `ideation/watchtower-prototype.html` (UI 원본, `static/watchtower.html` 로 이식)

## Known Gaps (자세히는 BUILD-LOG.md 참조)

### Step 3 진입 전 처리
- 30 사이트 URL/selector tentative — Owner 검토 + 실 검증 필요
- `Item.id` default 미설정 — Step 3에서 `default=` 추가

### Step 1 잔여 (Step 3에서 통합 처리)
- FSC RSS endpoint HTML 반환 — Watchtower 크롤러 작성 시 함께 재조사
- DART RSS 평일 영업시간 재검증
- 로깅 stderr only (파일 핸들러 Step 5)

### 다음 Step에서 자연 해결
- Watchtower 크롤러 부재 (Step 3) / CHANGE detection (Phase 2) / Subscriptions API (Step 4) / SMTP 알림 (Step 4) / 토큰 인증·Docker (Step 5)

## Watchtower MVP Roadmap

| Step | 주제 | Spec FR | 예상 |
|---|---|---|---|
| 2 ✅ | DB + Seed + UI Shell | FR-CAT-001/002, FR-SITE-001/003, FR-USR-001, FR-FEED-001/002/004/005 | 완료 |
| 3 ✅ | Crawler + Detector + Items | FR-CRL-001~008, FR-DET-001/002, FR-SITE-005/006 | 완료 |
| 4 | Subscriptions + Notifier | FR-SUB-001~005, FR-NOTIF-001~008 | 2일 |
| 5 | Audit + Auth + Deploy | FR-AUDIT-001~003, FR-USR-002~004, NFR-COMP-002 | 2일 |

## Spec 보완 작업 (Step 2 완료 후 별도 사이클)

`spec_20260510_1029.md` v0.2 작성 (B1~B4):
- B1: Phase 1/2/3 매핑 표 추가
- B2: OpenAPI sketch (Step 2 API 응답 형식 등재)
- B3: 8 카테고리 + 30 사이트 seed 부록 등재
- B4: FR-NOTIF-007 우선순위 명시

## Git State

- Branch: claude/stoic-meitner-47d182 (worktree) → master push 완료
- Remote: `https://github.com/2hryul2/webcrolling.git`
- Last commit: `[Step 3] Watchtower Crawler + Detector + Items 적재`
- Uncommitted files: (push 완료 후 깨끗)

## Resume Prompt (다음 세션 시작 시 사용)

```
You are the Architect on claude_webcroll project.
Read handoff/SESSION-CHECKPOINT.md first.
If active and recent, skip BUILD-LOG and ARCHITECT-BRIEF.
Otherwise read those files for context.
Report status to Project Owner.
```
