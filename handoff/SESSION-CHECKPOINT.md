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
Status: IDLE (Step 2 deployed)

## What's Done

- Step 1: FastAPI + RSS 수집 — ✅ deployed 2026-05-09
- **Step 2: Watchtower Foundation (DB + Seed + UI Shell) — ✅ deployed 2026-05-10**
  - 신규 11 파일 + 수정 5 파일
  - SQLAlchemy 2.0 + SQLite WAL — 4 도메인 모델 (User/Category/Site/Item)
  - yaml seed 멱등 적재 (8 카테고리 / 30 사이트 / 1 사용자)
  - 5 GET API: `/api/categories|sites|items|users/me|health`
  - `static/watchtower.html` (prototype 디자인 보존, 인라인 데이터 → fetch)
  - events.jsonl → Item 임시 import 브리지
  - 83 tests passing (기존 67 + 신규 16, 0 skipped)
  - Reviewer APPROVED, Conditions 0건

## What's Next

다음 세션 시작 시 → **Step 3: Crawler + Detector + Items 적재**

1. **Step 2 escalation 처리** (Step 3 첫 작업):
   - 30 사이트 URL/selector tentative 검토 — Owner 컨펌 후 실 URL/HTML 구조 확인 (특히 reg/comp/sec)
   - `app/db/models.py` Item.id 에 `default=lambda: uuid.uuid4().hex` 추가
2. **Watchtower 전용 RSS/HTML 크롤러 작성** (FR-CRL-001~008, FR-DET-001/002)
   - feedparser RSS path
   - httpx + BS4 HTML path
   - 사이트별 `crawl_interval_min` 강제 (60분 minimum)
   - 도메인당 동시 1 요청 제한
3. **NEW Detector + Items 적재** — content_hash (SHA-256) 기반 dedup
4. **APScheduler Watchtower 통합** — 사이트별 interval로 스케줄링
5. **legacy import 단계 deprecate** — Step 3 크롤러로 전환

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
| 3 | Crawler + Detector + Items | FR-CRL-001~008, FR-DET-001/002, FR-SITE-005/006 | 2일 |
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
- Last commit: `[Step 2] Watchtower Foundation: DB + Seed + UI Shell`
- Uncommitted files: (push 완료 후 깨끗)

## Resume Prompt (다음 세션 시작 시 사용)

```
You are the Architect on claude_webcroll project.
Read handoff/SESSION-CHECKPOINT.md first.
If active and recent, skip BUILD-LOG and ARCHITECT-BRIEF.
Otherwise read those files for context.
Report status to Project Owner.
```
