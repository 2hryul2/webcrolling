# Review Feedback — Step 4
Date: 2026-05-10
Status: APPROVED

## Conditions
(none)

## Escalate to Architect
(none)

## Cleared

Step 4 (Subscriptions + Notifier + UI 영속) 통과. Reviewer가 직접 검증한 항목:

**테스트 — Builder 주장과 일치 (실측):**
- `pytest tests/ -v` → **130 passed in 5.37s, 0 skipped** (104 baseline + 26 new). Builder가 보고한 분포(notifier 11 + watchtower API 12 + worker 3)와 일치.

**브리프 §4.1–4.12 전부 충족:**
- 4.1 selector 보정 — best-effort, 외부 sandbox 차단으로 추정값 유지 + REVIEW-REQUEST §4 등재 (Step 5 이월). 브리프 지시(외부 접근 불가 시 추정값 유지) 그대로 따름.
- 4.2 `Subscription`, `AlertLog` 모델 — 브리프 스니펫 그대로, 기존 4 테이블·컬럼 무변경 (`git diff`로 확인).
- 4.3 `_seed_subscriptions` 멱등 — 1 user × 8 categories = 8 row default `subscribed=False, channel='off'`.
- 4.4 `NotifierService` — instant/digest/owner_failure 메서드 + 60·300·900s 백오프 + `_StarttlsFailedError` fail-closed (재시도 0) + per-user 5분/10건 deque rate limit (threading.Lock 보호) + `_redact()` 로 password 토큰 → `[REDACTED]` + 480자 절단 + SMTP 미설정 시 `status='skipped'` + `_html_escape()` 가 모든 사용자 입력 escape (XSS 표면 차단).
- 4.5 worker `_run_site_locked` — `session.flush()` 후 `new_item_ids` 추출 → commit → `notifier.send_instant(new_item_ids)` (try/except로 워커 격리). `notifier=None` 기본값으로 기존 caller 무파괴.
- 4.6 다이제스트 cron — `CronTrigger(hour=9, minute=0, timezone="Asia/Seoul")` + 부팅 로그 `Digest scheduled: 09:00 KST daily`.
- 4.7 owner failure 승격 — `_notify_owner_failure` 내부에서 `logger.error` + `notifier.send_owner_failure(site.id, count)` + try/except.
- 4.8 REST API 4종 — `GET /api/subscriptions` (8 row 기본 채움), `PATCH /api/subscriptions/{cid}` (FR-SUB-002/003 invariant 순서 정확: subscribed=False 즉시 channel='off' wipe → channel patch → invariant 재적용), `PATCH /api/items/{iid}/read` (idempotent 404), `GET /api/alert-log` (me.id only, limit 1~1000 ge/le 검증).
- 4.9 UI — `localStorage`/`STORAGE_KEY`/`loadState`/`saveState`/`DEFAULT_STATE` 전부 제거 (`grep` 결과 1건은 주석). `bootData()` 가 `/api/subscriptions` fetch → `stateFromSubscriptions()`. `toggleStar`/`toggleBell`/채널 segmented control/`markRead` 모두 prev 스냅샷 → 낙관적 UI → PATCH → 실패 시 `state.{subscribed,channel,alertOn}` rollback + `showToast()` 한국어 메시지. CSS·검색·필터·detail 패널 무변경.
- 4.10 `main.py` — `NotifierService(SessionLocal, smtp_config=_build_smtp_config(), ui_base_url=os.getenv("WATCHTOWER_UI_BASE", ...))` 인스턴스화 + `WatchtowerWorker(SessionLocal, notifier=notifier_service)` + digest cron 등록.
- 4.11 `.env.example` — `WATCHTOWER_UI_BASE=http://localhost:8000` 추가.
- 4.12 26 신규 테스트 — notifier 11 + API 12 (subscriptions 7 + items.read 3 + alert-log 2) + worker 3, 모두 hermetic (`_SmtpStub` + sleep injection, 외부 네트워크 0).

**보안:**
- `_redact()` — `password=...` 패턴 → `[REDACTED]` + 480자 절단으로 `String(500)` 컬럼 안전.
- `_send_one()` — STARTTLS 실패 시 `_StarttlsFailedError` raise → `_send_with_retry`가 즉시 (0회 재시도) `(False, "STARTTLS failed")` 반환. fail-closed 명확.
- `_html_escape()` — instant/digest/owner_failure 메일 본문 모두에서 `cat.name`, `site.name`, `item.title`, `item.summary`, `item.url`, `_ui_base` escape. XSS via mail client 차단.
- Rate limit `_rl_buckets` — `threading.Lock` 보호. SQL injection 표면 0 (전부 SQLAlchemy ORM).
- `_resolve_me` 기반 권한 분리 — `/api/alert-log` 가 `WHERE user_id == me.id` 강제로 다른 user row 노출 0 (`test_api_alert_log_returns_user_rows_only` 가 u1/u2 분리 검증).
- `main.py` — `_build_smtp_config()` 결과를 NotifierService 에 직접 주입; 비번 로깅 없음.

**Step 1·2·3 무파괴 (`git diff` 확인):**
- `monitor/notifier.py`, `monitor/worker.py`, `monitor/matcher.py`, `app/database.py`, `app/scheduler.py`, `app/routes/status.py` — 변경 0 byte.
- `monitor/watchtower/{base,robots,rss,html,detector}.py` — 변경 0 byte.
- `app/routes/watchtower.py` 의 기존 5 GET (`/categories|sites|items|users/me|health`) — 응답 contract 동일 (테스트 17개 모두 그대로 통과).
- `app/db/models.py` — 기존 4 테이블 (Category, Site, Item, User) 컬럼/제약 변경 0; Subscription + AlertLog 신규 추가만.

**제약 준수:**
- 신규 의존성 0 (`zoneinfo`, `smtplib`, `email.mime` 전부 stdlib; `apscheduler.triggers.cron` 은 Step 1 부터 존재).
- 외부 LLM/외부 사이트 호출 0.
- 시크릿은 env-only (`SMTP_*`, `WATCHTOWER_UI_BASE`).
- SMTP 미설정 시 graceful skip — 워커/스케줄러 차단 0, alert_log `status='skipped'` 영속.

**Builder의 self-review 정당화 검토:**
- `AlertLog.user_id` FK 제약으로 owner 미해결 시 row 미작성 → 운영 로그(`logger.warning`)로만 보존. 브리프 §4.4 가 "owner 없을 때도 row" 를 명시하지 않으므로 deviation 아님. 향후 audit 정책 변경 시 컬럼 nullable 화 마이그레이션은 별도 step.
- `SubscriptionPatch` 의 `Optional[str]` + 라우트에서 422 — 한국어 detail 일관성. Pydantic Literal 로의 미세 리팩터는 contract-equivalent.
- Rate-limit 11+ 모두 alert_log digest row 적재 — 브리프 미명시 부분, 보수적 선택. 운영 피드백 후 후속 조정.

Smoke test (REVIEW-REQUEST §9) 와 부팅 로그 모두 일치. Step 4 deploy 가능.
