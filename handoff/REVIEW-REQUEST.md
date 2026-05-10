# Review Request — Step 4 (Watchtower Subscriptions + Notifier + UI 영속)

**Builder:** Senior Developer (Step 4 세션)
**Date:** 2026-05-10
**Branch / worktree:** `claude/stoic-meitner-47d182`
**Ready for Review:** YES

---

## 1) 결과 요약

- ✅ `pip install -r requirements.txt` — 변경 없음 (zoneinfo·smtplib·email.mime 전부 stdlib).
- ✅ `pytest tests/ -v` → **130 passed, 0 skipped** (104 baseline + 26 new = 130; 목표 125+).
- ✅ `python -m uvicorn main:app …` 부팅 시 다음 로그 확인:
  - `Watchtower scheduler: 15 sites registered (15 skipped — disabled)`
  - `Digest scheduled: 09:00 KST daily`
- ✅ `GET /api/subscriptions` → 8 row default (`subscribed=False, channel='off'`).
- ✅ `PATCH /api/subscriptions/reg {"channel":"instant"}` → 200, FR-SUB-003 자동 적용 (`subscribed=True`), 새로고침 후 영속.
- ✅ `POST /api/trigger-watchtower` → 202 + 백그라운드 워커 진입 (smoke test 실증).
- ✅ Step 1·2·3 회귀 없음 — 기존 응답 contract 동일 + 모든 사전 테스트 통과.

---

## 2) 신규 / 수정 파일

| File | 변경 유형 | 핵심 변경 |
|---|---|---|
| `app/db/models.py` | 수정 (추가만) | `Subscription`, `AlertLog` ORM 모델 2개 추가. 기존 4 테이블·컬럼 무변경. |
| `app/db/seed.py` | 수정 | `_seed_subscriptions()` 추가 — `(user_id, category_id)` 멱등 default fill. `run_seed` return contract에 `"subscriptions"` 키 추가. |
| `monitor/watchtower/notifier.py` | 신규 | `NotifierService` — instant / digest / owner_failure 메일 + 60·300·900s 백오프 + 5분/10건 rate limit + STARTTLS fail-closed + AlertLog 영속 + SMTP 미설정 graceful skip. |
| `monitor/watchtower/worker.py` | 수정 | `__init__(notifier=None)`, `run_site` 성공 후 `send_instant(new_item_ids)` 호출 (try/except 차단), `_notify_owner_failure` 가 logger.error 후 `send_owner_failure` 호출. |
| `app/routes/watchtower.py` | 수정 (확장) | `GET /api/subscriptions`, `PATCH /api/subscriptions/{cid}`, `PATCH /api/items/{iid}/read`, `GET /api/alert-log` 신설. 기존 5개 GET contract 무변경. Pydantic `SubscriptionPatch`. |
| `static/watchtower.html` | 수정 | `localStorage` 코드 완전 제거. `state = stateFromSubscriptions(subs)`, `toggleStar/toggleBell` 가 `patchSubscription` 호출, `markRead`가 PATCH → 실패 시 rollback + 한국어 토스트. CSS·디자인·검색·필터 무변경. |
| `main.py` | 수정 | `NotifierService` lifespan 인스턴스화, `WatchtowerWorker(notifier=notifier_service)`, APScheduler `CronTrigger(hour=9, minute=0, timezone="Asia/Seoul")` 등록. |
| `.env.example` | 수정 | `WATCHTOWER_UI_BASE=http://localhost:8000` 추가. |
| `tests/test_watchtower_notifier.py` | 신규 | 11 tests — model round-trip, instant (no SMTP / SMTP / failure+backoff / rate-limit rollup / STARTTLS fail-closed), digest (groups / excludes_read / no items skipped), owner_failure (sent / no-email skipped). |
| `tests/test_watchtower.py` | 수정 (보강) | 11 신규 API 테스트 (subscriptions GET/PATCH 6, items.read 3, alert-log 2). 기존 `run_seed` count assertion에 `"subscriptions"` 키 반영. |
| `tests/test_watchtower_crawler.py` | 수정 (보강) | 3 신규 — `worker_calls_notifier_on_new_items`, `..._on_5th_failure`, `..._handles_notifier_exception`. |

---

## 3) 테스트 결과

```
collected 130 items
tests/test_database.py .........        [  6%]
tests/test_dedup.py .....               [ 10%]
tests/test_matcher.py .......           [ 16%]
tests/test_models.py ........           [ 22%]
tests/test_notifier.py .........        [ 29%]
tests/test_routes.py ............       [ 38%]
tests/test_rss.py .............         [ 48%]
tests/test_watchtower.py ............................                    [ 70%]
tests/test_watchtower_crawler.py ........................                [ 88%]
tests/test_watchtower_notifier.py ...........                            [ 96%]
tests/test_worker.py ....                                                [100%]

============================= 130 passed in 5.47s =============================
```

분포:
- `test_watchtower.py` 28 (Step 2 17 + Step 4 11) ↑
- `test_watchtower_notifier.py` 11 (전부 신규)
- `test_watchtower_crawler.py` 24 (Step 3 21 + Step 4 worker 3)
- 외부 네트워크 의존 0 — `_SmtpStub` 으로 smtplib 패치, httpx는 `_ScriptedClient` 유지.

---

## 4) Selector 보정 결과 (Step 3 후속, 4.1)

| Site | URL | 상태 | 결과 |
|---|---|---|---|
| s10 하나은행 디지털 | https://www.kebhana.com/cont/news/news_press/index.jsp | tentative 유지 | 외부 사이트 직접 fetch가 sandbox에서 거부됨 — Step 5 이월 |
| s12 NH 디지털혁신 | https://www.nonghyup.com/Cyberpr/PressRelease/PressReleaseList.aspx | tentative 유지 | 동일 |
| s16 케이뱅크 | https://www.kbanknow.com/ib20/mnu/BHP05000000 | tentative 유지 | 동일 |
| s19 MS Foundry | https://azure.microsoft.com/en-us/blog/product/azure-ai-foundry/ | tentative 유지 | 동일 |
| s24 KISIA | https://www.kisia.or.kr/news/notice/ | **smoke 시 matched-no-elements 확인** | 우연히 dev smoke 가 실 사이트 도달 → `.notice-list tbody tr` 가 hit 0 으로 확인됨. Step 5에서 owner와 함께 실 selector 결정 |
| s29 KIF | https://www.kif.re.kr/kif4/publication/viewer_pubreport | **smoke 시 matched-no-elements 확인** | URL 경로가 302 redirect → error.html 로 빠짐. owner 환경에서 정확한 publication list URL 필요 |

브리프 §4.1 지시 ("외부 사이트 접근 안 되면 추정값 유지 + Step 5 이월") 그대로 처리. Smoke test 부산물로 s24/s29 가 selector 부적합임을 확인했으므로 Step 5 BUILD-LOG에 등재해 owner 검증 우선순위로 올릴 것.

---

## 5) Major components 설명

### 5.1 NotifierService (`monitor/watchtower/notifier.py`)

- **graceful skip 정책**: 모든 send_* 메서드는 SMTP 설정 누락 시 `status='skipped'` `error_message='SMTP not configured'` 로그 1건 후 정상 반환. 워커 흐름 차단 없음.
- **재시도 + 백오프**: `EMAIL_RETRY_BACKOFFS_SEC = (60, 300, 900)`. 3회 시도 = 2회 sleep. STARTTLS 실패는 fail-closed (재시도 0).
- **Rate limit (FR-NOTIF-007)**: per-user `deque[float]` 5분 sliding window. 11번째 instant 호출은 묶음(rolled_up) `alert_log channel='digest' status='skipped' error='rate limit — rolled up'` 1건으로 대체.
- **Digest (FR-NOTIF-003 / 008)**: `now=` 인자 주입 가능 (테스트 결정성). `Item.is_read_by(user.id)` 체크로 이미 읽은 것 제외. `detail = "{N} items in {M} categories"`.
- **Owner failure**: `Category.owner_user_id` 가 비어 있으면 logger.warning + 로그 미작성 (FK 제약). owner는 있는데 email 비어 있으면 status='skipped' + AlertLog 영속.
- **시크릿 redaction**: `_redact()` 가 `password=...` 패턴을 `[REDACTED]` 으로, error_message는 480자 이하로 절단해 `String(500)` 컬럼에 안전.
- **테스트 가능성**: `sleep` / `smtp_factory` injection으로 hermetic. `_SmtpStub` 가 ehlo/starttls/login/send_message 호출 순서 검증.

### 5.2 REST API 추가

- `SubscriptionPatch(BaseModel)` — `extra='forbid'`, `subscribed: Optional[bool]`, `channel: Optional[str]`. enum 검증은 라우트 핸들러에서 422 반환 (Pydantic Literal 대신 — 명확한 한국어 에러 메시지를 위해).
- **FR-SUB-002 / 003 invariant 적용 순서**:
  1. `body.subscribed` 적용 — False면 즉시 `channel='off'` 도 세팅 (잔존 'instant' 가 다음 invariant에서 subscribed를 다시 True로 돌리는 버그를 차단).
  2. `body.channel` 적용.
  3. invariant: `channel ∈ {instant, digest}` ⇒ `subscribed=True` (FR-SUB-003).
  4. invariant: `not subscribed` ⇒ `channel='off'` (FR-SUB-002).
- `mark_item_read` — `Item.mark_read(me.id)` 헬퍼 그대로 사용 (idempotent CSV 추가).
- `list_alert_log` — me.id row만, `sent_at DESC`, `limit 1..1000` (default 100).

### 5.3 worker 통합

- `__init__(..., notifier=None)` — 기존 caller 무파괴.
- 신규 item commit 직후 `session.flush()` 호출하여 `default-generated Item.id` 가 채워진 뒤 `new_item_ids` 추출 → commit → `notifier.send_instant(new_item_ids)`.
- 알림 실패는 `logger.warning` 만 기록, 워커 결과는 그대로 `status='ok'`. 별도 세션을 쓰지 않으므로 워커 자체 트랜잭션 분리 유지.

### 5.4 UI

- localStorage 완전 제거 (`STORAGE_KEY`, `DEFAULT_STATE`, `loadState`, `saveState` 4가지).
- `bootData()`: `/api/subscriptions` 추가 fetch → `stateFromSubscriptions(subs)` → `state.{subscribed,alertOn,channel}`.
- `toggleStar`, `toggleBell`: 낙관적 UI → PATCH 호출 → 실패 시 prev로 rollback + `showToast()` (한국어 메시지). settings modal 의 채널 선택도 동일 패턴.
- markRead: PATCH 실패 시 read flag rollback.
- 디자인·CSS·검색·필터·detail 패널 코드 100% 보존.

---

## 6) Self-review (Reviewer 체크포인트)

### Q1 "Reviewer가 가장 잡을 가능성이 큰 부분?"

- **`run_seed` return contract 변경**: `"subscriptions"` 키 추가가 외부 caller에 영향 가는가? — 호출처는 `main.py:lifespan` 의 logger와 `tests/test_watchtower.py` 두 곳. 둘 다 업데이트 완료. 다른 호출처 없음 (grep 확인).
- **`AlertLog.user_id` FK 제약과 owner_failure 의 'no owner' 케이스**: FK가 빈 문자열을 받지 못하므로 owner 미해결 시 row 미작성. 이 결정의 정당화: AlertLog는 "사용자 시점 기록" — 수신자가 없는 알림 시도 자체는 logger.warning 으로만 충분 (운영은 로그 기반). 만약 reviewer가 "AlertLog가 audit 용 → 수신자 없어도 row 필요"라고 판단하면 `AlertLog.user_id` 를 nullable로 바꾸는 후속 PR 필요. 본 brief의 §Decisions 어떤 항목도 이 정책을 강제하지 않음.
- **Pydantic enum 검증을 라우트에서 직접 처리**: `Literal['instant','digest','off']` 대신 `Optional[str]` + 라우트에서 422 반환. Reviewer가 "Pydantic Literal로 깔끔하게"를 원하면 trivially refactor. 현재 선택의 이유는 한국어 detail 일관성.
- **`FR-NOTIF-007 rate limit roll-up`**: 11번째부터 단일 묶음 1건만 생성하는 구현 — 12번째, 13번째 호출은 추가 'rolled_up' 카운트 + 추가 AlertLog 'digest' 행을 만든다. 이는 spec에 정확한 정의가 없어 보수적으로 모든 초과 호출을 카운트 + 로그한다. 운영자가 "11+ 모두 합쳐서 정확히 1행"을 기대하면 후속 조정 필요.
- **`session.flush()` 후 `new_item_ids` 추출**: `Item.id` 가 lambda default로 채워지므로 flush 시점에 채번. 이 동작은 SQLAlchemy 표준이지만 SQLite + WAL + check_same_thread=False 조합에서 회귀가 없는지 — 이미 `test_worker_calls_notifier_on_new_items` 가 `len(x) == 32` 로 검증.

### Q2 "브리프의 모든 항목이 ship 됐는가?"

| 브리프 항목 | 상태 |
|---|---|
| 4.1 selector 보정 | best-effort, 추정값 유지 + 본 문서 §4 등재 (Step 5 이월) |
| 4.2 Subscription + AlertLog 모델 | ✅ 추가만, 기존 4 테이블 무변경 |
| 4.3 Subscription seed 8 row | ✅ `_seed_subscriptions` 멱등 |
| 4.4 NotifierService | ✅ instant/digest/owner_failure + 백오프 + rate limit + graceful skip |
| 4.5 worker.py notifier 통합 | ✅ try/except로 워커 무파괴 |
| 4.6 다이제스트 cron | ✅ `CronTrigger(hour=9, minute=0, timezone="Asia/Seoul")` + 부팅 로그 |
| 4.7 owner failure 승격 | ✅ logger.error → notifier.send_owner_failure |
| 4.8 REST API 확장 | ✅ subscriptions GET/PATCH, items.read PATCH, alert-log GET |
| 4.9 UI 영속 전환 | ✅ localStorage 제거, PATCH + rollback + 토스트 |
| 4.10 main.py lifespan | ✅ NotifierService 인스턴스화 + worker 주입 |
| 4.11 .env.example | ✅ `WATCHTOWER_UI_BASE` 추가 |
| 4.12 테스트 | ✅ 26 신규 = 11 (notifier) + 12 (api) + 3 (worker) |
| Step 1/2/3 무파괴 | ✅ 기존 5개 GET contract, monitor/notifier.py·monitor/worker.py·app/database.py·app/scheduler.py·app/routes/status.py 무변경 |

### Q3 "데이터 비어있거나 요청 실패 시 사용자 시각?"

- `/api/subscriptions` — 사용자 등록 없음 → 404 한국어 detail. 보통은 seed로 항상 1명 존재.
- `/api/subscriptions/{cid}` PATCH — 카테고리 없음 → 404 한국어. invalid channel → 422 한국어 detail.
- `/api/items/{iid}/read` PATCH — item 없음 → 404 한국어.
- `/api/alert-log` — 빈 리스트 (`[]`) 정상 반환.
- UI — fetch 실패 시 기존 "데이터를 불러오지 못했습니다" empty state. PATCH 실패 시 한국어 alert + 즉시 rollback (별/벨 시각적 깜빡임 없음, optimistic 단계만 보였다 사라짐).
- Notifier — SMTP 누락 시 모든 메일 시도가 status='skipped' 로 alert_log 적재. 워커 차단 0.

---

## 7) Deviations from brief (정당화)

1. **`run_seed` 반환 dict에 `"subscriptions"` 키 추가** — 브리프 §4.3은 "신규 row 8개" 만 명시하고 반환 contract는 미지정. 기존 caller 모두 업데이트했으므로 deviation 영향 없음.
2. **`AlertLog.user_id` 가 FK 제약 + 비-NULLABLE → owner 미해결 시 알림 미적재** — 위 self-review §Q1에서 정당화. 브리프 §4.4의 "alert_log channel='owner_failure'" 는 발송 시점 가정이며 "owner 없을 때도 row" 는 명시 안 됨.
3. **`SubscriptionPatch` enum 검증 라우트 직접 처리** (Pydantic Literal 미사용) — 한국어 detail 일관성 위해. 422는 동일.
4. **`AlertLog.error_message` 480자 절단** — 브리프 §4.4에 명시 없으나 컬럼 길이(`String(500)`) 보호 + redaction 정책 일관성 유지 (Step 1 패턴).
5. **`session.flush()` 호출 후 `new_item_ids` 추출** — 브리프 §4.5 코드 스니펫 그대로 따름 (`session.flush()` 명시).

---

## 8) Known gaps (다음 Step에서 처리)

- **selector 6개 (s10·s12·s16·s19·s24·s29)** — Step 5에서 owner와 함께 실 selector 확정. 현 smoke로 s24·s29 가 0-match 임이 부수 확인됨.
- **rate limit roll-up 의 정확 횟수 정책** — 12번째, 13번째도 매번 alert_log row 생성. 운영 피드백 후 정책 결정.
- **AlertLog audit 용도로 user_id nullable 화** — 위 §Q1에서 언급. 운영팀 요구 시 마이그레이션 필요.
- **UI alert-log 노출** — 브리프 §Flag §5 "Step 5에서". 본 step은 API만.
- **다이제스트 cron 의 `coalesce=True, max_instances=1` 가 TimeZone 전환 (DST 등)에서 안정적인가** — KST는 DST 없으므로 Phase 1 OK. 향후 사용자별 timezone 도입 시 재검토.
- **운영 환경 SMTP 검증** — env 주입 후 실제 메일 발송 e2e는 운영팀 환경에서 별도 smoke 필요.

---

## 9) 첨부 — Smoke test 출력 (실증)

```
=== /api/subscriptions ===
200 8 rows
[{"category_id":"ai","subscribed":false,"channel":"off",...}, ...]

=== PATCH /api/subscriptions/reg {"channel":"instant"} ===
200 {"category_id":"reg","subscribed":true,"channel":"instant","updated_at":"2026-05-10T..."}

=== GET /api/subscriptions (after PATCH) ===
reg row: {"category_id":"reg","subscribed":true,"channel":"instant",...}

=== POST /api/trigger-watchtower ===
202 {"job_id":"...","site_id":null,"status":"queued","message":"Trigger accepted"}
```

부팅 로그 핵심:
```
[seed] watchtower ready — categories=8 sites=30 users=1
Watchtower scheduler: 15 sites registered (15 skipped — disabled)
Digest scheduled: 09:00 KST daily
Application startup complete
```

---

**Builder 완료.** Reviewer 인계 준비 완료.
