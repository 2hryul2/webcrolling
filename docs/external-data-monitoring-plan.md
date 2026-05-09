# 외부 데이터 모니터링 구현 계획

작성일: 2026-05-09
대상: DART, 국회 열린국회정보, 금융위원회, YouTube Data

## 1. 검토 결론

기본 방향은 타당하다. 단, 운영 시스템으로 만들려면 "RSS/API 수집 코드"보다 먼저 아래 네 가지를 명확히 해야 한다.

1. 소스별 식별자 체계
   - DART: `rcept_no`, `corp_code`
   - 국회: 의안 ID 또는 API별 고유 키
   - 금융위 RSS: 게시글 URL 또는 게시글 번호
   - YouTube: `videoId`, `channelId`

2. 이벤트 정규화 모델
   - 모든 소스의 신규 항목을 `external_event` 하나의 테이블로 모은다.
   - 원문 응답은 별도 JSON 컬럼 또는 원문 테이블에 보관한다.

3. 중복 제거와 재처리
   - `source + external_id`를 유니크 키로 둔다.
   - 재수집 시 제목/본문/상태 변경을 감지할 수 있도록 `content_hash`를 저장한다.

4. 알람 정책
   - "신규 수집"과 "알람 발송"을 분리한다.
   - 같은 이벤트가 여러 키워드에 걸려도 알람은 정책에 따라 1회 또는 채널별 1회만 보낸다.

## 2. 권장 아키텍처

PoC는 단일 워커로 시작하되, 운영 전환 시 수집, 분류, 알람을 분리한다.

```text
Scheduler
  -> Collectors
      -> RSS collector
      -> API collector
  -> Normalizer
  -> PostgreSQL
  -> Classifier / Keyword matcher
  -> Notification router
  -> Slack / Teams / Email / Dashboard
```

실시간성이 중요한 DART와 금융위 보도자료는 RSS 트리거를 먼저 쓰고, API는 본문/메타데이터 보강에 사용한다. 국회와 YouTube 키워드 검색은 비용과 실시간성의 균형상 API 폴링 중심으로 둔다.

## 3. 데이터 모델 초안

### `source_config`

| 컬럼 | 설명 |
|---|---|
| `source_id` | `dart`, `assembly`, `fsc`, `youtube` |
| `channel_id` | 세부 채널. 예: `dart_recent`, `fsc_press`, `yt_shinhan` |
| `type` | `rss` 또는 `api` |
| `endpoint` | RSS URL 또는 API endpoint |
| `poll_interval_sec` | 폴링 주기 |
| `enabled` | 활성 여부 |

### `external_event`

| 컬럼 | 설명 |
|---|---|
| `source_id` | 소스 |
| `external_id` | 소스별 고유 ID |
| `title` | 제목 |
| `url` | 원문 링크 |
| `published_at` | 발행 시각 |
| `fetched_at` | 수집 시각 |
| `summary` | 요약 또는 설명 |
| `raw_payload` | 원본 JSON/XML 파싱 결과 |
| `content_hash` | 변경 감지용 해시 |
| `severity` | `info`, `watch`, `urgent` |

### `alert_log`

| 컬럼 | 설명 |
|---|---|
| `event_id` | 이벤트 ID |
| `channel` | Slack/Teams/Email 등 |
| `recipient` | 채널명 또는 수신 그룹 |
| `sent_at` | 발송 시각 |
| `status` | 성공/실패 |
| `error_message` | 실패 사유 |

## 4. 소스별 구현 전략

### DART

- 수집 방식: RSS 5분 + API 보강 + 일 1회 backfill
- 핵심 API: 공시검색, 기업개황, 공시서류 원본파일, 고유번호
- 운영 포인트:
  - `corp_code` 마스터 파일을 별도 적재한다.
  - 자회사/경쟁사 watchlist는 코드가 아니라 DB 설정으로 관리한다.
  - 정기보고서 누락 알람은 캘린더 기반 룰이 필요하다.

### 국회 열린국회정보

- 수집 방식: API 30~60분 주기
- 운영 포인트:
  - API별 응답 스키마가 다를 수 있으므로 커넥터별 adapter를 둔다.
  - 법안 단계 변경은 신규 항목보다 더 중요하므로 상태 변경 감지를 별도 구현한다.
  - 키워드 사전은 법률명, 금융업권, 자회사명, 리스크 키워드로 나눈다.

### 금융위원회

- 수집 방식: RSS 10분 + 공공데이터포털 API 일 1회 이상
- 운영 포인트:
  - 보도자료/보도설명/공지사항 RSS를 분리 구독한다.
  - 공공데이터포털 API는 데이터셋별 한도와 갱신주기가 다르므로 dataset catalog를 둔다.
  - 정책 알람은 제목 키워드만으로 오탐이 많으므로 본문/첨부 텍스트 추출이 필요하다.

### YouTube

- 수집 방식: 채널 RSS 15분 + `search.list` 일 4회 이하
- 운영 포인트:
  - `search.list`는 quota 비용이 높으므로 최소화한다.
  - 채널 기반 모니터링은 RSS와 `playlistItems.list` 중심으로 구성한다.
  - 영상 댓글 모니터링은 개인정보/약관 검토 후 별도 기능으로 분리한다.

## 5. PoC 범위

2~4주 PoC에서는 아래까지만 구현한다.

1. SQLite 기반 단일 워커
2. RSS 수집기 공통 모듈
3. DART OpenAPI 보강 수집기
4. 금융위 RSS 수집기
5. YouTube 채널 RSS 수집기
6. 키워드 매칭 v1
7. Slack 또는 Teams Webhook 알람
8. 실패/신규/알람 발송 로그

국회 API와 YouTube 검색 API는 PoC 후반에 붙인다. 이유는 스키마 확인과 quota 설계가 먼저 필요하기 때문이다.

## 6. 운영 전환 기준

PoC가 아래 조건을 만족하면 PostgreSQL 기반 파일럿으로 전환한다.

- 2주 연속 수집 장애 0건 또는 자동 복구
- 주요 소스별 누락 0건
- 알람 중복률 5% 이하
- 키워드 오탐률 20% 이하
- 수집/알람 로그로 장애 원인 추적 가능

## 7. 개발 작업 분해

### Sprint 1

- 프로젝트 스캐폴딩
- 환경변수/secret 로딩
- DB schema migration
- RSS collector
- dedup/upsert 구현
- 기본 로그 구현

### Sprint 2

- DART collector
- FSC RSS collector
- YouTube RSS collector
- keyword matcher
- notification router
- Slack/Teams webhook 발송

### Sprint 3

- 국회 API collector
- DART backfill
- retry/backoff
- health check
- 운영자용 수집 현황 리포트

### Sprint 4

- PostgreSQL 전환
- 알람 정책 고도화
- 본문/첨부 텍스트 추출
- dashboard 또는 BI 연동용 view
- 운영 runbook 작성

## 8. 주요 리스크와 보완책

| 리스크 | 영향 | 보완책 |
|---|---|---|
| RSS URL 변경 | 신규 탐지 누락 | RSS health check, 24시간 무수집 알람 |
| API 응답 스키마 변경 | 파서 실패 | raw payload 보관, 필드 누락 알람 |
| YouTube quota 초과 | 검색 중단 | RSS/playlist 우선, search 호출 예산제 |
| 키워드 오탐 | 알람 피로 | severity 룰, 부서별 라우팅, feedback loop |
| 인증키 노출 | 보안 사고 | Vault/Secret Manager, 키 회전 절차 |
| 공시 누락 알람 오판 | 업무 혼선 | 영업일 캘린더, 예정일 룰, 담당자 확인 단계 |

## 9. 다음 결정 사항

1. PoC 저장소 기술 스택: Python 단일 워커 또는 FastAPI + worker
2. 알람 채널: Slack, Teams, 이메일 중 1차 채널
3. DB: PoC SQLite 유지 여부와 PostgreSQL 전환 시점
4. DART watchlist 최종 범위
5. 키워드 사전 v1 승인 기준
6. 사내 보안 기준: API key 저장 위치와 로그 보관 기간

## 10. 추천 첫 구현

가장 먼저 `RSS -> external_event -> keyword_match -> webhook` 경로를 만든다. 이 경로가 안정화되면 DART API 보강, 국회 API, YouTube 검색 API를 같은 이벤트 모델에 꽂으면 된다.

첫 산출물:

- `config/sources.yaml`
- `config/keywords.yaml`
- `monitor/db.py`
- `monitor/collectors/rss.py`
- `monitor/matcher.py`
- `monitor/notifier.py`
- `monitor/worker.py`
- `tests/test_dedup.py`
- `tests/test_keyword_match.py`
