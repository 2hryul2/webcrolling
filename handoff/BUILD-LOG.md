# Build Log

Progress record for claude_webcroll project.

---

## Current Status

Step 1: ✅ **DEPLOYED** (2026-05-09).
모든 핵심 모듈 + 테스트 67/67 통과, Reviewer APPROVED, master 브랜치 push 완료.

**Last Updated:** 2026-05-09

---

## Completed Steps

| # | Title | Date | Status |
|---|-------|------|--------|
| — | Project initialization | 2026-05-09 | ✅ Complete |
| 1 | FastAPI 프로젝트 초기화 + RSS 수집 기본 구조 | 2026-05-09 | ✅ Deployed |

---

## Known Gaps

- **FSC RSS endpoint** — `https://www.fsc.go.kr/rss/pressRelease.xml`이 RSS XML 대신 HTML(1871 bytes)을 반환. URL 변경/폐지 가능성. **Step 2에서 실제 endpoint 조사 + 수정** 필요. 현재는 collector 등록만 되어 있고 entries 0건 상태로 동작 (정상 fallback).
- **DART todayRSS.xml entries 0건** — 토요일 새벽 검증 시점에는 당일 공시 없음. **평일 영업시간 (월~금 09:00~18:00 KST)에 재검증 필요**.
- **Pydantic V3 마이그레이션 대비** — 현재 `@field_serializer`로 V2 마이그레이션 완료. V3 출시 전에 추가 검토.
- **로깅 인프라** — 현재 stderr only. Step 2/3에서 `logs/app.log` 파일 핸들러 추가 검토.
- **DART Watchlist 정밀 매칭** — substring 매칭 유지 중 (Step 1 의도). Step 2에서 `corp_code` 필드 정확 추출 구현.
- **`KeywordRule.exclude_keywords`** — 모델에 정의되어 있으나 matcher 미사용. Step 2 fuzzy/exclusion 매칭에서 활용 예정.

---

## Notes

This log is maintained by Architect. Each completed step is documented here with:
- Step number and title
- Date completed
- Files changed
- Key decisions made
- Deploy confirmation date

See ARCHITECT-BRIEF.md for current work in progress.
