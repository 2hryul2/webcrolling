# Architect Brief

Work specification for Builder.

---

## Step 1 — FastAPI 프로젝트 초기화 + RSS 수집 기본 구조

### What

FastAPI 기반 웹 모니터링 시스템의 기본 틀 + RSS 수집 파이프라인 구현

```
Scheduler (APScheduler)
  → RSS Collector (DART, FSC)
  → Normalizer (external_event 정규화)
  → JSONL 저장 (data/events.jsonl)
  → Keyword Matcher (keyword matching)
  → Notification Router (이메일 + 파일 로깅)
  → Alert Log (data/alerts.jsonl)
```

### Why

- 실시간 모니터링 웹 대시보드 기초
- DB 없이 파일 기반으로 간단하게 운영
- RSS 기반 빠른 수집 (DART, 금융위)
- 향후 국회/YouTube API 추가 시 같은 이벤트 모델에 꽂을 수 있도록

### Requirements

1. **프로젝트 구조**
   ```
   D:\source\claude_webcroll\
   ├── main.py                   # FastAPI 앱 진입점
   ├── config/
   │   ├── sources.yaml          # RSS/API 소스 설정
   │   ├── keywords.yaml         # 키워드 사전 (표준형)
   │   └── settings.py           # 환경변수 로드
   ├── app/
   │   ├── scheduler.py          # APScheduler 설정
   │   ├── models.py             # Pydantic 데이터 모델
   │   ├── database.py           # JSONL 파일 I/O 유틸
   │   └── routes/
   │       ├── __init__.py
   │       └── status.py         # GET /status, /events, /alerts
   ├── monitor/
   │   ├── __init__.py
   │   ├── collectors/
   │   │   ├── __init__.py
   │   │   ├── rss.py            # RSS 수집 공통 모듈
   │   │   ├── dart.py           # DART RSS 수집기
   │   │   └── fsc.py            # 금융위 RSS 수집기
   │   ├── matcher.py            # 키워드 매칭 엔진
   │   ├── notifier.py           # 이메일 + 파일 발송
   │   └── worker.py             # 메인 워커 (수집 → 정규화 → 매칭 → 발송)
   ├── data/
   │   ├── events.jsonl          # 수집된 이벤트 (추가 후 생성)
   │   ├── alerts.jsonl          # 발송된 알람 로그 (추가 후 생성)
   │   └── state.json            # 마지막 수집 시각 (추가 후 생성)
   ├── logs/
   │   └── (로그 파일)
   ├── tests/
   │   ├── test_rss.py
   │   ├── test_matcher.py
   │   └── test_dedup.py
   ├── requirements.txt
   ├── .env.example
   └── README.md
   ```

2. **sources.yaml 구조**
   ```yaml
   sources:
     dart:
       name: "DART OpenAPI RSS"
       type: "rss"
       endpoint: "https://dart.fss.or.kr/api/rssFeeds.json"
       poll_interval_sec: 300
       enabled: true
       category: "compliance"
     
     fsc:
       name: "금융위 보도자료"
       type: "rss"
       endpoint: "https://www.fsc.go.kr/rss/pressRelease.xml"
       poll_interval_sec: 600
       enabled: true
       category: "policy"
   ```

3. **keywords.yaml 구조**
   ```yaml
   keywords:
     urgent:
       - "구조조정"
       - "자진퇴출"
       - "감시대상"
       - "규제"
       - "자본금 증감"
     
     watch:
       - "인수합병"
       - "지배구조 변경"
       - "임원 교체"
       - "전략적 제휴"
       - "부실채권"
     
     info:
       - "기업 실적"
       - "배당"
       - "신규 상품"
       - "순환 보직"
   ```

4. **데이터 모델 (Pydantic)**
   ```python
   # ExternalEvent
   - source: str                 # "dart", "fsc"
   - external_id: str           # 소스별 고유 ID
   - title: str
   - url: str
   - published_at: datetime
   - fetched_at: datetime
   - summary: str (선택)
   - raw_payload: dict          # 원본 JSON/XML 파싱 결과
   - content_hash: str          # MD5 hash for dedup
   - severity: str              # "urgent", "watch", "info"
   
   # AlertLog
   - event_id: str              # external_id
   - channel: str               # "email", "file"
   - recipient: str             # 이메일 주소 또는 파일 경로
   - sent_at: datetime
   - status: str                # "sent", "failed"
   - error_message: str (선택)
   ```

5. **JSONL 파일 포맷**
   
   `data/events.jsonl`:
   ```
   {"source":"dart","external_id":"20260509-001","title":"신한금융 보고서","url":"...","published_at":"2026-05-09T10:30:00Z","fetched_at":"2026-05-09T10:35:00Z","content_hash":"abc123","severity":"info"}
   {"source":"fsc","external_id":"20260509-press-001","title":"금융위 정책 보도","url":"...","published_at":"2026-05-09T11:00:00Z","fetched_at":"2026-05-09T11:05:00Z","content_hash":"def456","severity":"urgent"}
   ```
   
   `data/alerts.jsonl`:
   ```
   {"event_id":"20260509-001","channel":"email","recipient":"alerts@company.com","sent_at":"2026-05-09T10:36:00Z","status":"sent"}
   {"event_id":"20260509-press-001","channel":"file","recipient":"data/alerts.jsonl","sent_at":"2026-05-09T11:06:00Z","status":"sent"}
   ```

6. **중복 제거 (Deduplication)**
   - `content_hash = MD5(title + url)` → `source + external_id` 검증
   - `data/state.json`에 마지막 수집 시각 저장
   - 이미 수집된 이벤트는 skip

7. **RSS 수집 흐름**
   - `sources.yaml`에서 활성 RSS 소스 읽기
   - 각 RSS URL 폴링 (headers: User-Agent 설정)
   - XML 파싱 → `ExternalEvent` 모델로 정규화
   - `source + external_id` 유니크 체크
   - `data/events.jsonl`에 append (한 줄 한 항목)
   - `data/state.json` 업데이트 (마지막 수집 시각)

8. **키워드 매칭**
   - 새로운 이벤트의 title + summary에서 keywords.yaml의 키워드 검색
   - severity 할당 (urgent/watch/info)
   - 매칭된 키워드 목록 저장

9. **알람 발송**
   - 이메일 (SMTP): `severity=urgent` 또는 `severity=watch`만 발송
   - 파일 로깅: 모든 이벤트를 `data/alerts.jsonl`에 기록
   - 동시에 발송 (이메일 실패 시에도 파일 로그는 남음)

10. **환경변수 (.env)**
    ```
    SMTP_SERVER=smtp.gmail.com
    SMTP_PORT=587
    SMTP_USER=your_email@gmail.com
    SMTP_PASSWORD=your_app_password
    ALERT_EMAIL=alerts@company.com
    
    DART_WATCHLIST=00123456,00654321,00987654
    FSC_WATCHLIST=
    
    LOG_LEVEL=INFO
    ```

### Constraints

- **파일 기반만 사용** (DB 없음)
- **외부 의존성 최소화**: FastAPI, APScheduler, requests, feedparser만 사용
- **환경변수로 보안 정보 관리**: API 키, 이메일 비밀번호는 `.env`에서만 로드
- **JSONL 증분 추가 (append only)**: 기존 파일을 다시 파싱하지 않음
- **실패에 강함**: 이메일 발송 실패 시에도 파일 로그는 남고, 워커는 계속 진행

### Success Criteria

- ✅ FastAPI 앱 시작 가능 (`uvicorn main:app --reload`)
- ✅ `GET /status` → 시스템 상태, 마지막 수집 시각, 수집된 이벤트 수 반환
- ✅ `GET /events` → 최근 100개 이벤트 JSONL로 반환
- ✅ `GET /alerts` → 최근 100개 알람 JSONL로 반환
- ✅ DART RSS 수집 가능 (최소 5개 이상 이벤트)
- ✅ 금융위 RSS 수집 가능 (최소 3개 이상 이벤트)
- ✅ 중복 제거 정상 작동 (같은 이벤트 재수집 시 skip)
- ✅ 키워드 매칭 정상 (매칭된 키워드 severity 할당)
- ✅ 이메일 발송 성공 (SMTP 설정 후)
- ✅ 파일 로깅 성공 (data/alerts.jsonl 기록)
- ✅ 테스트 성공: `pytest tests/test_*.py`

### Out of Scope

- ❌ PostgreSQL 전환 (Step 3)
- ❌ 국회 API 수집 (Step 2)
- ❌ YouTube API 수집 (Step 2)
- ❌ DART API (OpenAPI 보강) (Step 2)
- ❌ 웹 대시보드 UI (Step 4)
- ❌ 알람 정책 고도화 (severity 채널별 라우팅) (Step 4)
- ❌ 본문/첨부 텍스트 추출 (Step 4)

### Decisions

1. **RSS 우선**: 최초 구현은 RSS만 (빠르고 안정)
2. **APScheduler 사용**: 백그라운드 작업 관리 (간단하고 경량)
3. **이메일은 선택적**: 구성 후 SMTP 설정 없어도 동작 (파일 로그만 남음)
4. **JSONL append-only**: 성능과 단순성 (파싱 비용 최소화)
5. **severity 3단계**: urgent (이메일 발송) / watch (이메일 발송) / info (파일 로그만)

### Flags

1. **DART Watchlist**: external-data-monitoring-plan.md에서 기업 코드 확정 필요
   - 현재 임시값: `00123456,00654321,00987654`
   - 실제 기업 코드로 교체 필요

2. **SMTP 설정**: 이메일 발송은 SMTP 설정 후 테스트 필요
   - Gmail 사용 시: 앱 비밀번호 생성 필수
   - 기업 이메일 사용 시: IT 팀 확인 필수

3. **RSS URL 확인**: DART/금융위 RSS URL이 변경될 수 있으므로 최신 확인
   - DART: https://dart.fss.or.kr/api/rssFeeds.json
   - 금융위: https://www.fsc.go.kr/rss/pressRelease.xml

---

## How This Works

Builder:
1. 이 Brief를 읽고 완전히 이해할 때까지 확인
2. 불명확한 부분이 있으면 **여기에 질문 추가** (Edit ARCHITECT-BRIEF.md)
3. 완벽히 이해되면 "Brief 확인 완료" 신호
4. 구현 시작

---

## 다음 Step (Step 2)

Step 1 배포 후:
- DART OpenAPI 보강 수집기 (더 상세한 정보)
- 국회 API 수집기
- YouTube 채널 RSS 수집기
- 고급 키워드 매칭 (제외 키워드, 정규식)

---

작성일: 2026-05-09
Architect: Senior Technical Lead
Status: Ready for Builder
