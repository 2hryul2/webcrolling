# Build Log

Progress record for claude_webcroll project.

---

## Current Status

Step 3: ✅ **DEPLOYED** (2026-05-10) — Watchtower Crawler + Detector + Items 적재.
Reviewer APPROVED (Conditions 0, 3 architect escalations 처리), Project Owner 승인 후 master push.

테스트: 104 passed (83 baseline + 21 new), 0 skipped.
부팅 검증: `Watchtower scheduler: 15 sites registered (15 skipped — disabled)`.
1회 트리거: 9 사이트 성공 + 239 items 적재.

**Last Updated:** 2026-05-10

---

## Completed Steps

| # | Title | Date | Status |
|---|-------|------|--------|
| — | Project initialization | 2026-05-09 | ✅ Complete |
| 1 | FastAPI 프로젝트 초기화 + RSS 수집 기본 구조 | 2026-05-09 | ✅ Deployed |
| 2 | Watchtower Foundation: DB + Seed + UI Shell | 2026-05-10 | ✅ Deployed |
| 3 | Watchtower Crawler + Detector + Items 적재 | 2026-05-10 | ✅ Deployed |

---

## Watchtower MVP Roadmap (Step 2~5)

| Step | 주제 | Spec FR 범위 | 예상 |
|---|---|---|---|
| 2 | DB + Seed + UI Shell (현재) | FR-CAT-001/002, FR-SITE-001/003, FR-USR-001, FR-FEED-001/002/004/005, IR-INT-001/002 | 2일 |
| 3 | Crawler + Detector + Items 적재 | FR-CRL-001~008, FR-DET-001/002, FR-SITE-005/006 | 2일 |
| 4 | Subscriptions + Notifier | FR-SUB-001~005, FR-NOTIF-001~008 | 2일 |
| 5 | Audit + Auth + Deploy | FR-AUDIT-001~003, FR-USR-002~004, NFR-COMP-002 | 2일 |

기준 문서:
- `ideation/ideation_subscribe-watch_20260510_1029.md`
- `spec_20260510_1029.md`
- `ideation/watchtower-prototype.html`

---

## Known Gaps

### Step 1 잔여 (Step 3 이후 처리)
- **FSC RSS endpoint** — `https://www.fsc.go.kr/rss/pressRelease.xml`이 RSS XML 대신 HTML(1871 bytes)을 반환. URL 변경/폐지 가능성. **Step 3 Watchtower 크롤러 작업 시 함께 endpoint 재조사**.
- **DART todayRSS.xml entries 0건** — 평일 영업시간 (월~금 09:00~18:00 KST) 재검증 필요.
- **Pydantic V3 마이그레이션 대비** — `@field_serializer`로 V2 완료. V3 출시 전 재검토.
- **로깅 인프라** — 현재 stderr only. Step 5에서 `logs/app.log` 파일 핸들러 검토.
- **DART Watchlist 정밀 매칭** — substring 매칭 유지. Step 3에서 `corp_code` 필드 정확 추출.
- **`KeywordRule.exclude_keywords`** — 모델 정의만 있고 matcher 미사용. Phase 2에서 활용.

### Step 2 산출 — Step 3 진입 전 처리 필요 (Reviewer escalations) ✅ 처리됨
- ~~**30개 사이트 URL/selector tentative 처리**~~ → Step 3에서 `scripts/verify_seed_urls.py` 로 30개 fetch 검증. 14개 verified, 1개 URL 보정(s14 → /rss.xml), 15개 `enabled: false` (HTTP 4xx/5xx/ConnectError). 결과 표는 아래 「Step 3 사이트 검증 결과」 섹션 참조.
- ~~**`Item.id` 모델 default 미설정**~~ → `app/db/models.py:Item.id` 에 `default=lambda: uuid.uuid4().hex` 추가. 기존 legacy import의 `content_hash[:32]` 명시 세팅과 공존 (default는 명시 세팅 시 미사용). 회귀 테스트 통과.

### Step 3 사이트 검증 결과 (2026-05-10)

| 결과 | 사이트 IDs |
|---|---|
| ✅ verified (14개) | s2, s3, s10, s12, s14(보정), s16, s17, s19, s20, s22, s24, s25, s26, s27, s29 |
| ⚠️ enabled=false (15개) | s1, s4, s5, s6, s7, s8, s9, s11, s13, s15, s18, s21, s23, s28, s30 |

실패 분류:
- ConnectError (사내망 전용 가능성): s1, s5, s6, s8 (모두 `.go.kr`)
- ConnectTimeout: s7
- HTTP 403 (UA/지역 차단): s4(FATF), s18(OpenAI Cloudflare)
- HTTP 404 (URL 추정값 무효): s11, s13, s15, s23, s28, s30
- HTTP 500 (서버 오류): s21
- Content-Type 비정상: s9 (kbstar.com 빈 응답)

URL 보정 1건: s14 (토스 블로그) — 원래 `https://blog.toss.im/` (HTML), 실제 RSS 경로 `https://blog.toss.im/rss.xml` 로 교체.

Owner 검토 필요: enabled=false 사이트 15개. 사내망 전용 URL/대안 RSS endpoint/올바른 정책 페이지 경로 확인 후 yaml 보정.

### Step 2 자체의 한계 (다음 Step에서 자연 해결)
- ~~**Watchtower 전용 RSS/HTML 크롤러 부재**~~ → Step 3 완료.
- **CHANGE detection / diff snapshot** — Phase 2.
- **Subscriptions REST API 부재** — Step 4. UI는 localStorage로만 동작.
- **읽음 처리 mock** — UI `markRead()` in-memory만. Step 4에서 PATCH /api/items/{id}/read.
- **`/api/items` 정렬 + read 계산 Python 사이드** — Phase 1 볼륨 (수백 row) 충분. 수만 row 이상이면 분리 검토.
- **Windows에서 `chmod 0o600` 무동작** — Step 1 정책 동일. Linux 배포 시 자동 적용.

### Step 3 산출 — 다음 Step (Step 4) 에서 처리
- **5회 실패 시 owner 알림이 logger.error 만** — Step 4 Notifier 도입 시 실 메일/메신저로 라우팅. 현재는 `_failure_notified` set이 1회 알림 보장.
- **HTML 크롤러 selector 추정값** — `# tentative selector — verify after first crawl` 표시된 사이트 (s10, s12, s16, s19, s24, s29) 는 실제 HTML 구조 확인 후 보정 필요. 첫 크롤 시 "content_selector matched no link items" 또는 "matched no elements" 에러 → site.status='delayed'.
- **legacy import deprecate 결정** — Step 1 events.jsonl → Item 매핑은 그대로 보존. Step 4 Notifier 도입 후 deprecate 결정.
- **단일 프로세스 robots.txt 캐시** — 6시간 TTL, 프로세스 로컬. Step 5 다중 워커 시 외부 캐시(Redis) 검토.
- **`Site.status` 의미 분리** — `enabled=False` (운영자 의도) vs `status='blocked'` (robots) vs `status='failed'` (5회 연속 실패) vs `status='delayed'` (1~4회 실패). UI 노출 정책은 Step 4에서.

### Step 3 Reviewer Escalations 처리 결정 (Architect)
1. **`scripts/` 디렉토리 신설** — 정식 utility 디렉토리로 승격. 폐기하지 않음. 향후 운영 스크립트(시드 검증, 데이터 마이그레이션, 배포 헬퍼 등) 의 home. `verify_seed_urls.py` 가 첫 입주.
2. **`Site.status='delayed'` 도입** — 채택. enum 의미 분리 (위 항목 참조). UI 노출은 Step 4 정책 결정 시 통합 (현재 UI 는 status 미렌더링).
3. **`test_worker_domain_lock` 약한 단정** — 현재 검증 그대로 유지 (brief §3.11 "직렬화 확인" 만 요구). Step 4·5 multi-domain 통합 테스트 시 강한 ordering 단정으로 보강 — known gap 등재.

### Watchtower MVP 진행 상황 (2026-05-10 기준)
| Step | 상태 | 비고 |
|---|---|---|
| 2 | ✅ DEPLOYED | DB + Seed + UI Shell |
| 3 | ✅ DEPLOYED | Crawler + Detector + Items 적재 |
| 4 | ⏸️ 대기 | Subscriptions + Notifier (이메일 즉시/다이제스트, 5회 실패 알림) |
| 5 | ⏸️ 대기 | Audit + Auth + Deploy |

---

## Notes

This log is maintained by Architect. Each completed step is documented here with:
- Step number and title
- Date completed
- Files changed
- Key decisions made
- Deploy confirmation date

See ARCHITECT-BRIEF.md for current work in progress.
