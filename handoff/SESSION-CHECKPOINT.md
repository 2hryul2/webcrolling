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
Status: IDLE (Step 4 deployed)

## What's Done

- Step 1: FastAPI + RSS 수집 — ✅ deployed 2026-05-09
- Step 2: Watchtower Foundation (DB + Seed + UI Shell) — ✅ deployed 2026-05-10
- Step 3: Watchtower Crawler + Detector + Items 적재 — ✅ deployed 2026-05-10
- **Step 4: Watchtower Subscriptions + Notifier + UI 영속 — ✅ deployed 2026-05-10**
  - 신규 모델 `Subscription` (user_id, category_id, subscribed, channel) + `AlertLog` (item_id nullable, channel, status, error)
  - 신규 모듈 `monitor/watchtower/notifier.py` — NotifierService:
    - send_instant: 신규 item → instant 구독자 메일 (60s/300s/900s 백오프, STARTTLS fail-closed, _redact, 5분/10건 rate limit)
    - send_digest: 09:00 KST cron → channel='digest' 사용자에게 24h 카테고리별 묶음 (이미 읽은 item 제외, FR-NOTIF-008)
    - send_owner_failure: 5회 연속 실패 site → category owner 메일 (1회 보장)
    - SMTP 미설정 시 graceful skip (status='skipped'), AlertLog 영속
  - 4 신규 REST endpoints:
    - `GET /api/subscriptions` (8 row default fill)
    - `PATCH /api/subscriptions/{cid}` (FR-SUB-002/003 자동 전환)
    - `PATCH /api/items/{id}/read` (idempotent CSV 추가)
    - `GET /api/alert-log?limit=100` (me.id 권한 분리)
  - UI localStorage 완전 제거 → 서버 영속 (낙관적 UI + rollback + 한국어 토스트)
  - main.py: NotifierService lifespan + WatchtowerWorker(notifier=...) + APScheduler `CronTrigger(hour=9, timezone="Asia/Seoul")`
  - `.env.example`: WATCHTOWER_UI_BASE 추가
  - 130 tests passing (104 baseline + 26 new), 0 skipped
  - Reviewer APPROVED, Conditions 0건, Escalations 0건
  - Smoke 검증: 8 default subs → PATCH reg=instant → 영속 → trigger 202 → AlertLog 적재

## What's Next

다음 세션 시작 시 → **Step 5: Audit + Auth + Deploy**

Watchtower MVP 마지막 사이클. 운영 가능 상태 (1.0 release candidate).

> **배포 조건 변경 (2026-05-11 확정)**: 기존 가정 `사내 폐쇄망 + 단일 프로세스 + Windows 개발/Linux 운영` → **`일반 인터넷 망 + Windows 개발 및 운영`**. Docker/Harbor 라인 폐기, Windows 네이티브 서비스로 전환. 단일 프로세스는 코드 레벨 사실(in-memory rate limit, single APScheduler)로 유지되지만, 배포가 강제하지 않음 — Phase 2에서 멀티 프로세스 필요 시 rate limit 외부 캐시화 별도 의제.

1. **Step 3 잔여 처리**:
   - HTML selector 보정 (s10/s12/s16/s19/s24/s29) — Step 4에서도 미완. 외부 환경 의존.
   - enabled=false 15개 사이트 — 일반 인터넷 URL 기준 재검증 (사내망 URL 가정 제거)
2. **Audit Log** (FR-AUDIT-001~003)
   - `audit_log` 테이블 (append-only, 변조 불가)
   - 권한 변경, 카테고리/사이트 등록·수정·삭제, 알림 발송 결과 기록
   - 1년 보관 (FR-AUDIT-002)
   - SIEM 연동은 옵션 (FR-AUDIT-003)
3. **인증** (FR-USR-002~004)
   - Phase 1 단순 토큰 (env `WATCHTOWER_TOKEN`)
   - 미인증 요청 401 (FR-USR-003)
   - 사내 SSO (SAML/OIDC) 는 Phase 2 (CON-006)
   - **인터넷 노출 가산점**: HTTPS 필수 (리버스 프록시 IIS/nginx-for-Windows/Caddy), 토큰 로테이션 가이드, 로그인 시도 IP 카운팅 — Phase 2 본격 대응, Phase 1은 토큰 + 외부 노출 시 reverse proxy 권고만 명시.
4. **사이트 등록 API** (FR-SITE-002, FR-SITE-007)
   - Owner의 `POST/PATCH/DELETE /api/sites` (자기 카테고리만)
   - selector 검증 시뮬레이션 (옵션)
5. **Windows 네이티브 배포** (NFR-COMP-002) — Docker 폐기
   - **서비스 등록**: NSSM(Non-Sucking Service Manager) 기반 Windows Service 등록 스크립트 (`scripts/install_service.ps1`, `scripts/uninstall_service.ps1`). `uvicorn main:app --host 0.0.0.0 --port 8000 --workers 1` 을 자동 재시작 옵션과 함께 service 등록.
   - **인스톨러**: PyInstaller one-folder 빌드 또는 Inno Setup 기반 `dist\setup\setup_v{VERSION}.exe` (CLAUDE.md 표준 경로). Python 런타임 임베디드 포함 or 사전 요구사항 명시 (Python 3.11+).
   - **데이터/로그 경로** (CLAUDE.md 정책):
     - 데이터: `%APPDATA%\claude_webcroll\` — SQLite, JSONL, state.json
     - 로그: `%LOCALAPPDATA%\claude_webcroll\logs\` — 일자별 rotate, 30일 보관
     - 현재 `BASE_DIR/data` 하드코딩은 `WATCHTOWER_DATA_DIR` 환경변수로 빼고, 기본값을 위 경로로 조정 (개발은 `--data-dir ./data` 오버라이드)
   - **설정 파일**: `.env`를 `%APPDATA%\claude_webcroll\.env` 로 이동 (인스톨러가 `.env.example` 복사 후 사용자 편집 유도)
   - **방화벽 / 인터넷 노출**: 인터넷 직접 노출은 비권장 → 동일 호스트의 reverse proxy(Caddy/Nginx) 뒤 배치, Windows Firewall 인바운드 규칙은 8000 포트 localhost 전용 권장. `docs/deploy.md` 에 표준 토폴로지(Caddy + 자동 HTTPS) 1개 예시 등재.
   - **배포 가이드**: `docs/deploy.md` (인스톨러 실행 → service 등록 확인 → `.env` 설정 → 초기 부팅 검증 → 헬스체크 `GET /status`)
6. **운영 매뉴얼**: README 보강 + `docs/operations.md`
   - Windows event log / nssm 로그 확인 방법
   - 백업/복원 (SQLite + JSONL + `.env`)
   - 업그레이드 절차 (service 중지 → 인스톨러 실행 → 마이그레이션 → service 재기동)
7. **Spec v0.2 보완** (B1~B4) — Step 5 후미 또는 별도 사이클

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
| 4 ✅ | Subscriptions + Notifier + UI 영속 | FR-SUB-001~005, FR-NOTIF-001~008, FR-FEED-006 | 완료 |
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
- Last commit: `[Step 4] Watchtower Subscriptions + Notifier + UI 영속`
- Uncommitted files: (push 완료 후 깨끗)

## Resume Prompt (다음 세션 시작 시 사용)

```
You are the Architect on claude_webcroll project.
Read handoff/SESSION-CHECKPOINT.md first.
If active and recent, skip BUILD-LOG and ARCHITECT-BRIEF.
Otherwise read those files for context.
Report status to Project Owner.
```
