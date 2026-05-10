# Build Log

Progress record for claude_webcroll project.

---

## Current Status

Step 2: ✅ **DEPLOYED** (2026-05-10) — Watchtower Foundation (DB + Seed + UI Shell).
83 tests passing (기존 67 + 신규 16), Reviewer APPROVED (Conditions 0), Project Owner 승인 후 master push.

**Last Updated:** 2026-05-10

---

## Completed Steps

| # | Title | Date | Status |
|---|-------|------|--------|
| — | Project initialization | 2026-05-09 | ✅ Complete |
| 1 | FastAPI 프로젝트 초기화 + RSS 수집 기본 구조 | 2026-05-09 | ✅ Deployed |
| 2 | Watchtower Foundation: DB + Seed + UI Shell | 2026-05-10 | ✅ Deployed |

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

### Step 2 산출 — Step 3 진입 전 처리 필요 (Reviewer escalations)
- **30개 사이트 URL/selector tentative 처리** — `config/seed_sites.yaml` 의 s2~s12·s15~s16·s18~s30 다수가 `# tentative` 주석 (ideation 부록 A 추정값). **Step 3 첫 작업으로 Owner 검토 + 사이트별 실 URL/HTML 구조 확인** 필요. 특히 reg/comp/sec 카테고리(컴플라이언스·정보보안 부서가 직접 사용).
- **`Item.id` 모델 default 미설정** — `app/db/models.py:Item` 의 `id` 컬럼에 `default=lambda: uuid.uuid4().hex` 누락. 현재는 호출자가 명시 세팅 (legacy import는 `content_hash[:32]`). Step 3 brief에 `default` 추가 + 신규 크롤러 적재 시 명시 세팅 명시.

### Step 2 자체의 한계 (다음 Step에서 자연 해결)
- **Watchtower 전용 RSS/HTML 크롤러 부재** — Step 3.
- **CHANGE detection / diff snapshot** — Phase 2.
- **Subscriptions REST API 부재** — Step 4. UI는 localStorage로만 동작.
- **읽음 처리 mock** — UI `markRead()` in-memory만. Step 4에서 PATCH /api/items/{id}/read.
- **`/api/items` 정렬 + read 계산 Python 사이드** — Phase 1 볼륨 (수백 row) 충분. 수만 row 이상이면 분리 검토.
- **Windows에서 `chmod 0o600` 무동작** — Step 1 정책 동일. Linux 배포 시 자동 적용.

---

## Notes

This log is maintained by Architect. Each completed step is documented here with:
- Step number and title
- Date completed
- Files changed
- Key decisions made
- Deploy confirmation date

See ARCHITECT-BRIEF.md for current work in progress.
