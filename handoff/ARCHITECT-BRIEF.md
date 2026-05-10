# Architect Brief

Work specification for Builder.

---

## Step 4 — Watchtower Subscriptions + Notifier + UI 영속

### What

Step 3에서 적재된 items에 대해 (1) 사용자가 카테고리를 구독·해제하고 알림 채널(instant/digest/off)을 설정할 수 있고, (2) 즉시 알림은 60초 이내 메일 발송, 다이제스트는 매일 09:00 KST 묶음 발송, (3) 모든 알림 기록을 `alert_log` 에 영속한다. UI의 별/벨 토글과 채널 설정·읽음 처리는 localStorage에서 서버 영속으로 전환한다.

```
Crawler → detect_new_items → 신규 Item 생성
   ↓ hook
NotifierService
   ├─ instant_send(items)
   │     for category-instant 구독자 → 메일 (60s 이내)
   │     alert_log row 추가 (sent / failed / skipped)
   │
   └─ digest_send(at 09:00 KST)
         direct 24h NEW items
         channel=digest 구독자에게 카테고리별 묶음 메일
         이미 읽은 item은 제외 (FR-NOTIF-008)

UI
   ├─ GET  /api/subscriptions      → [{category_id, subscribed, channel}, ...]
   ├─ PATCH /api/subscriptions/{cid} body {subscribed?, channel?} → 200
   └─ PATCH /api/items/{iid}/read  → 200 (me.id를 read_by CSV에 추가)

5회 실패 site → category owner 메일 (logger.error 대체) + alert_log 기록
```

### Why

- ideation §1 핵심 가치 3개 중 2/3은 Step 4에서 완성: (1) 자기 카테고리 즉시 인지(=구독 영속) (2) 알림 피로 회피(=instant/digest/off 분리)
- spec FR-SUB-001~005 + FR-NOTIF-001~008 + FR-FEED-006(읽음 처리) 충족
- Step 3 `_failure_notified` 의 placeholder logger.error 를 실 메일로 승격
- 폐쇄망 환경에서 SMTP 만이 유일하게 보장된 알림 채널 (사내 메신저 webhook은 Phase 2)

### Requirements

#### 4.1 Step 3 escalation 후속 (선행 작업, 1~2시간)

**HTML 크롤러 selector 보정** — Step 3 BUILD-LOG에 등재된 6개 사이트의 실 selector 확인:

| Site | URL | Step 3 tentative selector | 작업 |
|---|---|---|---|
| s10 하나은행 디지털 | https://www.kebhana.com/cont/news/news/index.jsp | (tentative) | 첫 크롤 후 결과 확인. matched 0 이면 `httpx.get()` 으로 페이지 받아 BS4 분석, 본문 영역 selector 발굴 |
| s12 NH 디지털혁신 | (URL 확인) | (tentative) | 동일 |
| s16 케이뱅크 | https://www.kbanknow.com/ib20/mnu/PBKBKB001 | (tentative) | 동일 |
| s19 MS Foundry | https://learn.microsoft.com/en-us/azure/ai-foundry/ | (tentative) | 동일 |
| s24 KISIA | https://www.kisia.or.kr/board/notice | (tentative) | 동일 |
| s29 KIF | https://www.kif.re.kr/kif4/publication/research_search.aspx | (tentative) | 동일 |

작업 흐름:
1. `python -m monitor.watchtower.html_probe <site_id>` (신규 헬퍼 스크립트, `scripts/probe_site_selector.py` 또는 `monitor/watchtower/__main__.py` — Builder 재량) — 사이트 fetch + 후보 selector 자동 추론
2. 또는 Builder가 직접 brower DevTools 결과를 대신할 fallback selector를 결정 (`main article a`, `.list a`, `.board-list .title a` 등 후보 리스트)
3. yaml의 selector 값 교체 + `# verified selector 2026-05-10` 주석
4. 사이트 접근 차단 시 selector를 합리적 후보 (`main a`)로 두고 `# tentative selector — pending owner verification` 유지 + BUILD-LOG에 등재

이 작업은 **best-effort**. 외부 사이트 접근이 안 되는 환경이면 selector 추정값 그대로 두고 Step 5 후속 작업으로 넘김. BUILD-LOG에 결과 표 등재 필수.

#### 4.2 데이터 모델 추가 (`app/db/models.py`)

```python
class Subscription(Base):
    """사용자 × 카테고리 구독 상태. (FR-SUB-001)"""
    __tablename__ = "subscriptions"
    __table_args__ = (
        UniqueConstraint("user_id", "category_id", name="uq_user_category"),
    )
    id: Mapped[str] = mapped_column(String(32), primary_key=True,
                                     default=lambda: uuid.uuid4().hex)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    category_id: Mapped[str] = mapped_column(ForeignKey("categories.id"))
    subscribed: Mapped[bool] = mapped_column(default=False)
    channel: Mapped[str] = mapped_column(String(8), default="off")  # instant|digest|off
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

class AlertLog(Base):
    """모든 알림 발송 시도 기록. (FR-NOTIF-005)"""
    __tablename__ = "alert_log"
    id: Mapped[str] = mapped_column(String(32), primary_key=True,
                                     default=lambda: uuid.uuid4().hex)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    item_id: Mapped[str | None] = mapped_column(ForeignKey("items.id"), nullable=True)
    # item_id는 nullable: 다이제스트는 여러 item을 묶으므로 NULL 가능
    channel: Mapped[str] = mapped_column(String(16))  # instant|digest|owner_failure
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16))  # sent|failed|skipped
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    detail: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # detail: 다이제스트의 경우 "12 items in 3 categories" 같은 요약
```

- 기존 `Item.read_by` CSV 컬럼은 그대로 유지 — 별도 read_state 테이블 도입하지 않음 (Step 2 결정 보존).
- `Subscription.subscribed` + `Subscription.channel` 분리:
  - subscribed=False → 미구독 (사이드바 "기타 카테고리")
  - subscribed=True + channel='off' → 구독만 함 (별 ON, 벨 OFF)
  - subscribed=True + channel='instant' → ⭐+🔔 → 즉시 알림
  - subscribed=True + channel='digest' → ⭐ + 일일 다이제스트
  - FR-SUB-002: ⭐ OFF → channel 자동 'off'. FR-SUB-003: 🔔 ON → subscribed 자동 True.

#### 4.3 Seed (`app/db/seed.py` + `config/seed_users.yaml`)

- 신규: `Subscription` row 8개 (u1 × 8 카테고리, 모두 `subscribed=False, channel='off'` 시작).
- 사용자가 UI에서 직접 토글하여 활성화. (Step 2 prototype default state 패턴 미적용 — Phase 1 prod 보수 정책)
- seed.py 의 멱등 로직: `(user_id, category_id)` 존재하면 skip.

#### 4.4 Notifier 모듈 (`monitor/watchtower/notifier.py`)

```python
class NotifierService:
    """SMTP 즉시·다이제스트·5회 실패 owner 메일 라우팅."""

    def __init__(self, session_factory, smtp_config: dict, *, ui_base_url: str | None = None):
        self._session_factory = session_factory
        self._smtp = smtp_config  # {server, port, user, password, from_email}
        self._ui_base = ui_base_url or "http://localhost:8000"

    def send_instant(self, item_ids: list[str]) -> dict:
        """신규 item에 대해 instant 구독자 전체에게 메일.
        - SMTP 미설정 시 alert_log status='skipped'
        - 발송 실패 시 60s/300s/900s 백오프 (FR-NOTIF-006)
        - 사용자별 5분/10건 rate limit (FR-NOTIF-007) — 11번째부터 묶어서 1건 다이제스트
        """

    def send_digest(self) -> dict:
        """매일 09:00 KST. 직전 24h NEW + 미읽음 item을 카테고리별 묶음.
        - channel='digest' 구독자 대상
        - 이미 읽은 item 제외 (FR-NOTIF-008)
        - alert_log channel='digest', item_id=None, detail='N items in M categories'
        """

    def send_owner_failure(self, site_id: str, consecutive_failures: int) -> dict:
        """5회 연속 실패 시 카테고리 owner 메일.
        - Step 3 worker.py 의 _failure_notified set 으로 1회 보장
        - alert_log channel='owner_failure', item_id=None
        """

    def _build_smtp_client(self) -> smtplib.SMTP | None:
        """STARTTLS, credential redaction. Step 1 monitor/notifier.py 패턴 그대로."""

    def _send_email(self, to_email: str, subject: str, html: str, text: str) -> bool:
        """단일 메일 발송. 3회 retry (FR-NOTIF-006). True=sent, False=failed."""
```

**메일 템플릿** (한국어, NFR-USE-001):

즉시 알림:
```
Subject: [Watchtower 즉시] {category.name} — {site.name}
Body (HTML + text):
  새로운 업데이트가 감지되었습니다.

  카테고리: 금융 규제·감독
  사이트: 금융위원회
  제목: 금융권 AI 리스크 관리 가이드라인 행정예고
  요약: 생성형 AI 도입 시 ...

  원문: {item.url}
  Watchtower에서 보기: {ui_base}/ui

  ---
  알림이 너무 많다면 {ui_base}/ui 에서 채널을 '다이제스트'로 변경하세요.
```

다이제스트:
```
Subject: [Watchtower 일간] {date} 외부 모니터링 요약 ({총건수}건)
Body:
  지난 24시간 동안 신규 업데이트 {N}건이 감지되었습니다.

  ▼ 금융 규제·감독 (3건)
    1. [NEW] 금융위원회 — 금융권 AI 리스크 관리 가이드라인
       ...
    2. ...

  ▼ AI·기술 동향 (5건)
    ...

  Watchtower 전체 보기: {ui_base}/ui
```

owner 실패:
```
Subject: [Watchtower 경고] {site.name} 5회 연속 수집 실패
Body:
  사이트: 금융위원회 (s1)
  카테고리: 금융 규제·감독
  최근 실패: 5회 연속
  마지막 정상 시각: 2026-05-09T...

  사이트 URL/selector를 점검해주세요.
  Watchtower 헬스체크: {ui_base}/api/health
```

**SMTP 미설정 시**: Step 1 `monitor/notifier.py` 와 동일하게 graceful skip — `alert_log` 에 `status='skipped'`, `error_message='SMTP not configured'` 기록 후 정상 반환.

#### 4.5 즉시 알림 통합 (`monitor/watchtower/worker.py` 수정)

`WatchtowerWorker.run_site()` 의 commit 직후:
```python
new_items = detector.detect_new_items(session, site.id, crawled.items)
session.add_all(new_items)
session.flush()  # ID 채번
new_item_ids = [it.id for it in new_items]
session.commit()

if new_item_ids and self._notifier is not None:
    try:
        self._notifier.send_instant(new_item_ids)
    except Exception as exc:
        logger.warning("Instant notify failed: %s", exc)
```

- `WatchtowerWorker.__init__` 에 `notifier: NotifierService | None = None` 추가
- 알림 실패가 워커 진행을 막지 않도록 try/except (Step 1 패턴)

#### 4.6 다이제스트 스케줄링 (`main.py` 수정)

```python
from apscheduler.triggers.cron import CronTrigger

scheduler.add_job(
    notifier_service.send_digest,
    trigger=CronTrigger(hour=9, minute=0, timezone="Asia/Seoul"),
    id="watchtower_digest_daily",
    replace_existing=True,
    coalesce=True,
    max_instances=1,
)
logger.info("Digest scheduled: 09:00 KST daily")
```

- `Asia/Seoul` zoneinfo 사용 (Python 3.9+ stdlib `zoneinfo`)
- 첫 크롤 사이클 직후 매일 09:00 동작

#### 4.7 5회 실패 owner 알림 승격 (`monitor/watchtower/worker.py`)

기존 logger.error 만 하던 `_notify_owner_failure` 를 `NotifierService.send_owner_failure()` 호출로 교체:
```python
def _on_failure_threshold(self, site: Site) -> None:
    logger.error("Site %s failed 5 times — alerting owner", site.id)
    if self._notifier is not None:
        try:
            self._notifier.send_owner_failure(site.id, self._failure_counters[site.id])
        except Exception as exc:
            logger.warning("Owner failure notify error: %s", exc)
```

#### 4.8 REST API 확장 (`app/routes/watchtower.py`)

**`GET /api/subscriptions`** → `[{category_id, subscribed, channel}, ...]` (8 row 보장)
- 사용자 권한 분리 (FR-SUB-004): me.id 기준만 반환
- subscriptions 테이블에 row 없으면 default `subscribed=False, channel='off'` 로 채워서 반환 (8개 카테고리 모두)

**`PATCH /api/subscriptions/{category_id}`** body `{subscribed?: bool, channel?: 'instant'|'digest'|'off'}` → `{category_id, subscribed, channel}`
- Pydantic 입력 검증 (channel enum 강제)
- 없으면 row 생성 + 있으면 update + updated_at 갱신
- FR-SUB-002: subscribed=False 로 변경 시 channel='off' 강제
- FR-SUB-003: channel='instant' or 'digest' 로 변경 시 subscribed=True 자동 전환

**`PATCH /api/items/{item_id}/read`** body 없음 → `{id, read: true}`
- me.id 를 Item.read_by CSV 에 추가 (`mark_read` 헬퍼 사용)
- 이미 추가되어 있으면 idempotent (200 그대로 반환)
- 존재하지 않는 item_id → 404

기존 GET endpoints 응답 contract 유지. `GET /api/items` 는 me.id 의 read 상태를 그대로 반영 (Step 2 로직).

**`GET /api/alert-log?limit=100`** (선택, 운영자 조회용) → `[{id, user_id, item_id, channel, sent_at, status}]`
- limit 1~1000, default 100
- 운영자만 조회 가능? Phase 1 단일 사용자라 권한 분리 미필요 — 모든 사용자에게 본인 row만. (FR-SUB-004 정신 따라)
- **이번 Step 에 포함**. UI 노출은 Step 5에서.

#### 4.9 UI 영속 전환 (`static/watchtower.html` 수정)

기존 localStorage `watchtower_state_v1` 코드를 API 호출로 교체.

```javascript
// bootData() 확장
async function bootData() {
  const [cats, sites, items, me, subs] = await Promise.all([
    fetch('/api/categories').then(r => r.json()),
    fetch('/api/sites').then(r => r.json()),
    fetch('/api/items?limit=200').then(r => r.json()),
    fetch('/api/users/me').then(r => r.json()),
    fetch('/api/subscriptions').then(r => r.json()),  // 신규
  ]);
  // ... ITEMS 변환
  state = stateFromSubscriptions(subs);  // {subscribed, alertOn, channel}
  ME = me;
  renderAll();
}

function stateFromSubscriptions(subs) {
  const subscribed = {}, alertOn = {}, channel = {};
  for (const s of subs) {
    subscribed[s.category_id] = s.subscribed;
    alertOn[s.category_id] = s.subscribed && s.channel === 'instant';
    channel[s.category_id] = s.channel;
  }
  return { subscribed, alertOn, channel };
}

// saveState() 를 API 호출로
async function patchSubscription(catId, patch) {
  const resp = await fetch(`/api/subscriptions/${catId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  });
  if (!resp.ok) throw new Error(`patch failed: ${resp.status}`);
  return resp.json();
}

// 기존 star/bell 핸들러:
//   state.subscribed[catId] = !state.subscribed[catId]; saveState();
// 로 변경:
async function toggleStar(catId) {
  const current = state.subscribed[catId];
  const next = !current;
  const result = await patchSubscription(catId, {
    subscribed: next,
    channel: next ? state.channel[catId] || 'off' : 'off',
  });
  state.subscribed[catId] = result.subscribed;
  state.channel[catId] = result.channel;
  state.alertOn[catId] = result.subscribed && result.channel === 'instant';
  renderAll();
}

// markRead 도 API 로
async function markRead(itemId) {
  await fetch(`/api/items/${itemId}/read`, { method: 'PATCH' });
  const item = ITEMS.find(i => i.id === itemId);
  if (item) item.read = true;
  renderAll();
  document.getElementById('detail-panel').classList.remove('open');
}
```

- localStorage 완전 제거 — 단일 진실은 서버 DB.
- 네트워크 실패 시 사용자에게 한국어 토스트(또는 alert) 표시. UI는 변경 직전 상태로 rollback.
- 검색·필터·detail 패널 등 client-side state(`view.*`)는 그대로 유지.

#### 4.10 main.py lifespan 수정

```python
from monitor.watchtower.notifier import NotifierService

# lifespan 내부:
notifier_service = NotifierService(
    session_factory=SessionLocal,
    smtp_config=smtp_config,  # 기존 _build_smtp_config() 재사용
    ui_base_url=os.getenv("WATCHTOWER_UI_BASE", "http://localhost:8000"),
)
app.state.notifier = notifier_service

watchtower_worker = WatchtowerWorker(SessionLocal, notifier=notifier_service)
# ... 기존 watchtower_worker.run_site 스케줄링 그대로

# 다이제스트 cron
scheduler.add_job(
    notifier_service.send_digest,
    trigger=CronTrigger(hour=9, minute=0, timezone="Asia/Seoul"),
    id="watchtower_digest_daily",
    ...
)
```

- `WATCHTOWER_UI_BASE` 환경변수 신설 (기본 `http://localhost:8000`). 메일 본문 링크용.

#### 4.11 .env.example 보강

```
# Watchtower notifier
WATCHTOWER_UI_BASE=http://localhost:8000
# SMTP_* 변수는 Step 1 그대로 재사용
```

#### 4.12 테스트 (`tests/test_watchtower_notifier.py` 신규 + 기존 파일 추가)

신규 파일 `tests/test_watchtower_notifier.py`:
- `test_subscription_model` — Subscription / AlertLog 생성 + relationship
- `test_notifier_send_instant_no_smtp` — SMTP 미설정 시 alert_log status='skipped'
- `test_notifier_send_instant_with_smtp` — mocked smtplib → status='sent'
- `test_notifier_send_instant_smtp_failure` — mocked SMTP 3회 실패 → status='failed' + 백오프 호출 검증
- `test_notifier_send_digest_groups_by_category` — 24h items × 3 카테고리 → 카테고리별 그룹
- `test_notifier_send_digest_excludes_read` — 이미 read_by에 me 포함된 item은 메일에서 제외 (FR-NOTIF-008)
- `test_notifier_send_owner_failure` — 5회 실패 site → category owner 메일
- `test_notifier_rate_limit_5min_10` — 동일 user 5분 내 11번째 instant → 묶음 1건 (FR-NOTIF-007)

`tests/test_watchtower.py` 추가 (기존 파일):
- `test_api_subscriptions_get_returns_8_rows` — 빈 DB에서도 8개 default row 반환
- `test_api_subscriptions_patch_creates` — row 없을 때 PATCH → 생성
- `test_api_subscriptions_patch_updates` — row 있을 때 PATCH → updated_at 갱신
- `test_api_subscriptions_unsubscribe_forces_off` — subscribed=False PATCH → channel='off' 강제
- `test_api_subscriptions_instant_forces_subscribe` — channel='instant' PATCH → subscribed=True 자동
- `test_api_subscriptions_validates_channel_enum` — channel='invalid' → 422
- `test_api_items_read_marks_user` — PATCH /api/items/{id}/read → read_by CSV 에 me.id 추가
- `test_api_items_read_idempotent` — 같은 PATCH 두 번 → 한 번만 추가
- `test_api_items_read_404` — 없는 item_id → 404
- `test_api_alert_log_returns_user_rows_only` — me.id row만 반환

`tests/test_watchtower_crawler.py` 보강 (기존 파일):
- `test_worker_calls_notifier_on_new_items` — mocked notifier — run_site 후 send_instant 호출 검증
- `test_worker_calls_notifier_on_5th_failure` — 5회 실패 시 send_owner_failure 호출
- `test_worker_handles_notifier_exception` — notifier가 raise해도 worker 정상 진행

기존 104 + 신규 ~21 = **125+ pass** 목표. 0 skipped.

테스트는 모두 hermetic — `unittest.mock` 으로 smtplib 패치, 외부 네트워크 0.

### Constraints

- **Step 1 자산 무파괴**: `monitor/notifier.py` (Step 1 키워드 알림용), `monitor/worker.py`, `app/database.py`, `app/scheduler.py`, `app/routes/status.py` 변경 금지. Step 1 `/events|alerts|status|trigger` 회귀 없음.
- **Step 2/3 자산 무파괴**:
  - `app/db/models.py` 는 Subscription + AlertLog 추가만, 기존 4 테이블 컬럼/제약 변경 금지
  - `app/routes/watchtower.py` 의 5개 GET 응답 contract 유지 (`/api/categories|sites|items|users/me|health`)
  - `monitor/watchtower/{base,robots,rss,html,detector}.py` 변경 금지 (`worker.py` 만 notifier 통합)
  - `static/watchtower.html` 디자인/CSS/주요 인터랙션 보존 — 데이터 fetch 부분과 핸들러만 교체
- **외부 LLM 금지** (CON-006)
- **외부 사이트 접근 금지** (Notifier는 사내 SMTP 만)
- **시크릿 env-only** — SMTP 비번, WATCHTOWER_UI_BASE 등 (NFR-SEC-005)
- **SMTP 미설정 시 graceful skip** — workflow 중단 금지
- **단일 사용자 가정 (Phase 1 ASM-005)** — me 분리는 코드에 명시하되 실 row는 1개
- **신규 의존성 0** — `zoneinfo` (stdlib), `smtplib` (stdlib), `email.mime` (stdlib) 만 사용
- **rate limit 5분/10건** (FR-NOTIF-007) — 단순 in-memory deque 으로 충분 (Phase 1 단일 프로세스)

### Success Criteria

- ✅ `pip install -r requirements.txt` 성공 (변경 없음 또는 미세)
- ✅ `pytest tests/ -v` → 104 + 21+ = 125+ 통과, 0 skipped
- ✅ `python -m uvicorn main:app --host 127.0.0.1 --port 8000` 정상 부팅 + 다음 로그:
  - `Watchtower scheduler: N sites registered`
  - `Digest scheduled: 09:00 KST daily`
- ✅ `curl http://localhost:8000/api/subscriptions` → 8 row (default `subscribed=False, channel='off'`)
- ✅ `curl -X PATCH http://localhost:8000/api/subscriptions/reg -d '{"channel":"instant"}'` → 200 + subscribed=True 자동 반영
- ✅ `curl -X PATCH http://localhost:8000/api/items/{id}/read` → 200 + read_by 업데이트
- ✅ `curl -X POST http://localhost:8000/api/trigger-watchtower` 후 alert_log 에 row 적재 (SMTP 미설정이면 status='skipped')
- ✅ 브라우저 `/ui`:
  - 초기 로드 시 모든 카테고리 별/벨 OFF (서버 default)
  - 별 토글 → PATCH 호출 → DB 영속 → 페이지 새로고침 후 동일 상태
  - 카드 클릭 → 상세 → 읽음 처리 → DB 영속 → 새로고침 후 read 상태 유지
  - 네트워크 실패 시 한국어 토스트 + UI rollback
- ✅ Step 1 `/status|events|alerts|trigger` 회귀 없음
- ✅ Step 2 `/api/categories|sites|items|users/me|health` 응답 contract 동일
- ✅ Step 3 `POST /api/trigger-watchtower` 동작 동일

### Out of Scope (다음 Step / Phase)

- ❌ 사내 메신저 webhook (Phase 2)
- ❌ HTML snapshot + diff (Phase 2)
- ❌ CHANGE detection (Phase 2)
- ❌ 사내 SSO + 토큰 인증 (Step 5)
- ❌ Audit log 영속 (Step 5)
- ❌ Docker compose / Harbor (Step 5)
- ❌ 다중 사용자 권한 (Step 5)
- ❌ 사내 Ollama / vLLM 자동 분류·요약 (Phase 3)

### Decisions

1. **Subscriptions default = OFF**: 보수 정책. 사용자 명시 토글로 활성화. Step 2 prototype `default state` (reg/ai/comp 켜진 상태) 는 demo 용으로 채택 안 함.
2. **localStorage 완전 제거**: 단일 진실 = 서버 DB. fallback 옵션 미도입 (네트워크 실패 시 토스트만).
3. **`Item.read_by` CSV 유지**: 별도 read_state 테이블 도입 안 함 (Step 2 결정 보존).
4. **AlertLog.item_id nullable**: 다이제스트는 여러 items 묶음이라 NULL.
5. **Rate limit in-memory deque**: 프로세스 재시작 시 카운터 리셋. Phase 1 단일 프로세스 가정.
6. **다이제스트 시간대 KST 고정**: Asia/Seoul. 향후 Step 5에서 사용자별 timezone 도입 검토.
7. **owner failure 메일은 즉시**: 5회 실패 트리거 시점에 즉시 발송 (다이제스트 묶음 미포함).
8. **알림 미수신 정책 재확인**: `subscribed=False` 또는 `channel='off'` → 어떤 메일도 발송 안 함 (FR-NOTIF-004 충족).
9. **SMTP retry 구현**: Step 1 `monitor/notifier.py` 의 `_send_with_retry` 패턴 직접 재사용 (코드 복제 OK) 또는 헬퍼 함수로 추출. Builder 재량.
10. **selector 보정 best-effort**: 외부 사이트 접근 가능하면 검증, 안 되면 추정값 유지 + Step 5로 이월.

### Flags (추측 금지)

1. **SMTP credential** — `.env.example` 의 SMTP_* 변수 그대로 사용. 실제 값은 운영 시 주입. 테스트는 mock.
2. **`WATCHTOWER_UI_BASE`** — 기본 `http://localhost:8000`. 운영 시 `https://watchtower.shinhan.local` 등으로 교체.
3. **다이제스트 발송 시각 KST 09:00** — spec FR-NOTIF-003 그대로. 변경 시 spec 수정 필요.
4. **메일 본문 한국어 템플릿** — 본 brief §4.4 의 템플릿 그대로 사용. 회사 브랜딩(로고, 서명) 은 Phase 2.
5. **`/api/alert-log` UI 노출 시점** — 이번 Step API만 추가, UI는 Step 5.
6. **selector 검증 실패 사이트** — Step 3 enabled=false 15개는 손대지 말 것 (Owner 환경 의존). 보정은 Step 3 verified 6개 사이트 selector만.

### Branch / Worktree

- 현재 worktree: `claude/stoic-meitner-47d182`
- Step 3 master push 완료 (e2bbdd6). Step 4 commit은 동일 worktree에 추가 후 master push.

---

## How This Works

Builder:
1. 본 brief 정독 → 불명확 부분은 본 파일 끝 "## Builder Questions" 섹션에 추가
2. "Brief 확인 완료" 신호
3. 작업 순서:
   - 4.1 (selector 보정 — best-effort)
   - 4.2 (모델 추가)
   - 4.3 (seed)
   - 4.4 (Notifier 모듈)
   - 4.5 (worker.py 수정)
   - 4.6, 4.7 (다이제스트 + owner failure)
   - 4.8 (REST API 확장)
   - 4.9 (UI 영속 전환)
   - 4.10, 4.11 (main.py + .env)
   - 4.12 (테스트)
4. `pytest tests/ -v` 전체 통과 확인 후 `handoff/REVIEW-REQUEST.md` 작성
5. Smoke test: uvicorn 부팅 → 8 subscriptions GET → PATCH 토글 → 다시 GET 으로 영속 확인

---

## Watchtower MVP Roadmap (참고)

| Step | 주제 | 상태 |
|---|---|---|
| 2 | DB + Seed + UI Shell | ✅ 2026-05-10 |
| 3 | Crawler + Detector + Items 적재 | ✅ 2026-05-10 |
| **4** | **Subscriptions + Notifier + UI 영속 (현재)** | 🚧 |
| 5 | Audit + Auth + Deploy | ⏸️ 대기 |

---

작성일: 2026-05-10
Architect: Senior Technical Lead
Status: Ready for Builder
