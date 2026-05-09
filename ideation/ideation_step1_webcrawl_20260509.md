# Step 1 Ideation: Webcrawl RSS Monitoring System Architecture
**날짜**: 2026-05-09  
**Scope**: 5가지 핵심 컴포넌트 아키텍처 결정  
**Status**: 디자인 검토 단계 (Step 1 구현 전)

---

## 1. RSS 수집 구현 방식

### 옵션 A: feedparser + Polling + Threading

**Description:**
- `feedparser` 라이브러리로 RSS/Atom XML 파싱
- `threading.Thread`로 각 RSS 소스별 폴링 워커 실행
- `requests` 또는 `httpx`로 HTTP GET 요청
- 폴링 간격은 `sources.yaml`에서 설정 (DART: 300초, 금융위: 600초)
- `concurrent.futures.ThreadPoolExecutor` 활용 (기본 5~10 스레드)

**Constraints:**
- Global Interpreter Lock (GIL) 때문에 CPU 바운드 작업 성능 저하
- I/O 대기 중에만 컨텍스트 스위칭 가능
- 스레드 수 증가 시 메모리 오버헤드 (스레드당 ~1-2MB)
- 복잡한 동시성 디버깅 필요
- 대규모 RSS 소스 추가 시 스레드 수 제한 필요

**Overcome:**
- ThreadPoolExecutor 스레드 수 제한 (기본 min(32, os.cpu_count() + 4) 사용)
- 느린 RSS 서버는 타임아웃 설정 (requests timeout=10초)
- 주기적으로 스레드 풀 상태 모니터링
- Dead thread 감지 및 자동 복구 로직
- 소수의 스레드로 충분 (I/O 바운드 작업이므로 일반적으로 5~10개)

**Pros:**
- 표준 라이브러리 기반 (threading, concurrent.futures)
- feedparser는 RSS/Atom/RDF 표준 완벽 지원
- 구현 복잡도 낮음
- 기존 Python 코드베이스와 호환성 높음
- 디버깅 및 모니터링 용이
- 폴링 구현이 매우 단순 (APScheduler 또는 sleep 루프)

**Cons:**
- GIL로 인한 실제 병렬 처리 불가능
- 수십 개 RSS 소스는 문제없지만, 백 개 이상 소스 추가 시 성능 저하
- 메모리 오버헤드 (스레드당 1-2MB)
- CPU 바운드 작업 추가 시 병목
- 예외 처리가 복잡할 수 있음 (스레드별 에러 격리 필요)

**예상 성능:**
- 10개 RSS 소스, 5 스레드: ~5초 내 완료
- 50개 RSS 소스, 10 스레드: ~15-20초 내 완료
- 타임아웃 10초/소스 적용 시 최악 시나리오 ~100초

---

### 옵션 B: aiohttp + asyncio + Polling

**Description:**
- `aiohttp` 비동기 HTTP 클라이언트로 모든 RSS 요청 동시 처리
- `asyncio` 기반 이벤트 루프
- `feedparser`는 그대로 사용 (파싱은 동기 코드)
- 모든 I/O 작업 await 처리
- APScheduler의 asyncio 실행자 사용

**Constraints:**
- feedparser는 동기 라이브러리 (XML 파싱 시 블로킹)
- asyncio 학습 곡선이 높음 (콜백 지옥, Context Manager 필수)
- Exception 처리가 복잡 (task cancellation, gather with return_exceptions)
- 써드파티 라이브러리 asyncio 미지원 시 동기로 감싸야 함
- 디버깅이 어려움 (asyncio 에러 메시지가 불분명할 수 있음)

**Overcome:**
- feedparser 파싱은 executor에서 실행 (`asyncio.to_thread()` 또는 `loop.run_in_executor()`)
- 체계적인 Exception 처리: `asyncio.gather(..., return_exceptions=True)`
- asyncio 디버깅 활성화: `asyncio.run(..., debug=True)`
- 타임아웃은 `asyncio.wait_for(..., timeout=10)`로 관리
- Structured concurrency 패턴 사용 (contextlib.asynccontextmanager)

**Pros:**
- 높은 동시성: 수백 개 RSS 소스도 효율적 처리 (진정한 병렬 I/O)
- 메모리 효율적 (스레드 생성 오버헤드 없음, 코루틴은 가벼움)
- 현대적 Python 패턴 (3.10+ match/async context manager 활용)
- 확장성 우수 (소스 추가 시 성능 저하 최소)
- asyncio 에코시스템 성숙 (aiohttp, aiofiles 등)

**Cons:**
- 학습 곡선 높음
- 동기 라이브러리와 혼합 시 복잡
- 디버깅 어려움 (Task 상태 추적 필요)
- asyncio.get_event_loop() 폐기 예정 (Python 3.10+)
- 테스트 작성이 더 복잡 (mock, fixture with asyncio)

**예상 성능:**
- 100개 RSS 소스: ~5-8초 (모두 동시 처리)
- 1000개 RSS 소스: ~20-30초 (batch processing으로 분리 필요)
- 메모리: 스레드 기반보다 ~50% 감소

---

### 옵션 C: APScheduler built-in 실행자 + Polling (하이브리드)

**Description:**
- APScheduler의 `BackgroundScheduler` 사용
- 각 RSS 소스별로 독립적인 scheduled job
- ThreadPoolExecutor 또는 AsyncIOExecutor 선택
- Job 중복 실행 방지 (`coalesce=True`)
- Job store로 상태 관리 (SQLAlchemy 또는 메모리 기반)

**Constraints:**
- APScheduler 설정이 복잡할 수 있음
- Job 상태 관리 필요 (실패한 job 추적)
- Executor 선택이 명확하지 않으면 나중에 변경 비용 높음
- Job 로그가 분산되어 추적 어려움
- 메모리 누수 가능성 (오래된 job 정보 정리 필요)

**Overcome:**
- APScheduler 설정은 설정 파일(YAML) + Python 초기화 분리
- Job 상태는 로그 + 메타데이터 파일로 관리
- 실패한 Job은 최대 재시도 3회 설정
- Misfire handling: `misfire_grace_time=60`초 설정
- 주기적으로 dead job 정리 (cruft_scheduler.remove_job)

**Pros:**
- 프로덕션급 스케줄러 (cron 표현식 지원)
- Job 단위의 세밀한 제어 가능
- 다양한 Trigger 지원 (interval, cron, date)
- 상태 저장/복구 기능
- 실패한 Job 자동 재시도

**Cons:**
- 추가 라이브러리 의존성 (APScheduler)
- 설정 복잡도 증가
- 디버깅 어려움 (background job 추적)
- 메모리 누수 위험 (job 정보 정리 필수)
- 학습 곡선 (다양한 옵션 이해 필요)

---

### 최종 선택: **옵션 A (feedparser + Polling + Threading)**

**Justification:**

1. **복잡도 vs 이득 최적화**
   - 초기 Step 1: 10-20개 RSS 소스 → Threading으로 충분
   - 필요한 라이브러리: feedparser, requests, APScheduler (이미 필수)
   - 구현 시간: 1-2시간 (asyncio는 5-8시간)

2. **운영 비용**
   - 스레드 모니터링이 asyncio 디버깅보다 간단
   - 팀의 Python expertise 고려 (asyncio보다 threading 숙련도 높음)
   - 로그/에러 추적이 선형적

3. **확장 전략**
   - Step 2 또는 3에서 asyncio로 마이그레이션 가능
   - 현재 코드는 서비스 계층 분리 → 단순 교체 가능
   - 성능 문제 발생 시 opt-in 마이그레이션

4. **테스트와 검증**
   - Mock/patch가 단순
   - CI/CD 파이프라인 구성 간단
   - 로컬 테스트 재현성 높음

**Implementation Details:**
```python
# 의사코드: RSS 수집 패턴
from concurrent.futures import ThreadPoolExecutor
from monitor.collectors.rss import fetch_rss_feed

with ThreadPoolExecutor(max_workers=5) as executor:
    futures = {
        executor.submit(fetch_rss_feed, source): source
        for source in active_sources
    }
    for future in as_completed(futures):
        source = futures[future]
        try:
            events = future.result(timeout=15)
            save_events(events)
        except Exception as e:
            log_error(f"RSS fetch failed: {source}", e)
```

---

## 2. 이벤트 정규화 방식

### 옵션 A: Pydantic BaseModel

**Description:**
- `pydantic.BaseModel`로 데이터 모델 정의
- 필드 타입 검증 자동 수행
- `model_dump()` / `model_dump_json()`으로 직렬화
- Validators와 root_validator로 비즈니스 로직 구현
- JSON Schema 자동 생성

**Constraints:**
- Pydantic v2로 마이그레이션하면서 API 변경 큼
- 대량 객체 생성 시 성능 저하 가능 (검증 오버헤드)
- 상속 시 필드 순서 관리 복잡
- Circular reference 처리 어려움
- 메모리 오버헤드 (객체당 __dict__ + annotations)

**Overcome:**
- Pydantic v2 사용 (최신, v1 지원 종료)
- `model_config = ConfigDict(validate_assignment=False)` → 생성 후 변경 금지
- 배치 검증: `TypeAdapter(list[ExternalEvent]).validate_python(data)`
- Lazy validation: `model_validate(obj, from_attributes=True)`
- 메모리: `__slots__` 사용 (선택적)

**Pros:**
- 강력한 타입 검증 (타입 안정성)
- IDE 자동완성 지원
- JSON Schema / OpenAPI 자동 생성
- Validators로 복잡한 비즈니스 로직 통합
- 데이터 직렬화/역직렬화 간편
- FastAPI와 자연스러운 통합
- 문서화 생성 자동 (docstring 기반)

**Cons:**
- 성능 오버헤드 (검증 비용)
- 메모리 사용 증가 (객체당 ~200-500 bytes)
- 에러 메시지가 복잡할 수 있음
- 디버깅 시 validation 에러 추적 어려움
- 간단한 데이터에는 과도할 수 있음

**예상 성능:**
- 단일 객체 검증: ~1-5ms
- 1000개 객체 배치: ~100-500ms
- 메모리: 100개 객체 → ~50KB

---

### 옵션 B: dataclass + 수동 검증

**Description:**
- `dataclasses.dataclass`로 데이터 구조 정의
- 검증은 `__post_init__()` 또는 별도 validator 함수로 수동 구현
- 직렬화는 `asdict()` 또는 `astuple()`
- JSON 직렬화는 custom encoder 필요

**Constraints:**
- 검증 로직이 분산됨 (클래스별로 구현)
- JSON 직렬화가 기본 미지원 (custom encoder 작성 필요)
- 복잡한 중첩 구조 검증 시 코드 양 증가
- IDE 지원 미약 (자동 검증 힌트 없음)
- 필드 초기화 순서 관리 필요

**Overcome:**
- `dataclasses.field(default_factory=...)` 활용
- `typing.get_type_hints()`로 타입 반영
- json.JSONEncoder 상속으로 custom encoder 작성
- validators 모듈 별도 작성 (util.validators)
- type checking: `isinstance()` 또는 `typing.get_origin()`

**Pros:**
- 가벼움 (메모리 효율, 성능 우수)
- 표준 라이브러리만 사용 (의존성 없음)
- 세밀한 제어 가능 (검증 로직 커스터마이징)
- 직렬화/역직렬화 자유도 높음
- 간단한 코드 (boilerplate 적음)

**Cons:**
- 검증이 수동 (일관성 유지 어려움)
- JSON 직렬화 별도 구현 필요
- IDE 지원 부족
- 타입 체킹 도구(mypy) 설정 필요
- 복잡한 중첩 구조 관리 어려움

**예상 성능:**
- 단일 객체 생성: ~0.1-0.5ms (검증 최소)
- 1000개 객체 배치: ~50-100ms
- 메모리: 100개 객체 → ~20KB (Pydantic 대비 75% 감소)

---

### 옵션 C: dict + 스키마 검증 (orjson)

**Description:**
- RSS 파싱 결과를 dict 유지 (JSON 네이티브)
- `orjson` 라이브러리로 고속 직렬화/역직렬화
- JSON Schema (draft-7) 기반 검증 (jsonschema)
- 필요한 필드만 추출, 나머지는 raw_payload에 보존

**Constraints:**
- 타입 안정성 없음 (dict 키 접근 시 runtime 에러 가능)
- IDE 자동완성 지원 불가
- 복잡한 중첩 구조 검증 복잡
- JSON Schema 정의/관리 오버헤드
- 런타임 에러 추적 어려움

**Overcome:**
- `TypedDict` + type hints로 구조 문서화
- `dict.get()` 또는 defensive 접근으로 KeyError 방지
- JSON Schema는 YAML에 정의하고 로드 (config/schemas.yaml)
- Validation 전용 계층 (app/validation.py)
- 로깅: dict 구조 변경 전/후 기록

**Pros:**
- 최고 성능 (검증 최소, orjson은 C 기반)
- 메모리 효율 (dict는 네이티브)
- 유연성 (스키마 외 데이터도 보존)
- JSON 직렬화/역직렬화 자연스러움
- 외부 API 응답 그대로 처리 가능

**Cons:**
- 타입 안정성 없음
- IDE 지원 전무
- 검증 로직 분산 (추적 어려움)
- 필드 접근 시 None 체크 필수
- 프로젝트 복잡도 증가 시 유지보수 어려움

**예상 성능:**
- 단일 객체 검증: ~0.05-0.2ms (최소)
- 1000개 객체 배치: ~20-50ms
- 메모리: 100개 객체 → ~10KB (최소)

---

### 콘텐츠 해싱 전략 비교

**MD5 vs SHA-256 vs xxHash:**

| 방식 | 속도 | 충돌 위험 | 용도 |
|------|------|---------|------|
| MD5 | 빠름 | 이론적 충돌 가능 (무시할 수준) | 체크섬, 중복 제거 (권장) |
| SHA-256 | 중간 | 충돌 거의 불가능 | 암호화 필요 시 |
| xxHash | 매우 빠름 | 무시할 수준 | 고성능 중복 제거 |

**권장 구현:**
```python
from hashlib import md5

content_hash = md5(f"{title}{url}".encode()).hexdigest()
# 또는 title + summary + url 조합
content_hash = md5(f"{title}{summary}{url}".encode()).hexdigest()
```

---

### 최종 선택: **옵션 A (Pydantic BaseModel)**

**Justification:**

1. **타입 안정성 우선**
   - FastAPI와의 자연스러운 통합
   - API 응답 문서화 자동
   - IDE 지원으로 개발 속도 향상

2. **운영 비용**
   - Validation 에러 추적 명확
   - 데이터 손상 조기 감지
   - 테스트 작성 간편

3. **확장성**
   - Step 2에서 추가 필드 추가 용이
   - Validator로 비즈니스 로직 통합 가능
   - JSON Schema로 문서화 자동

4. **성능**
   - 초기 Step 1: RSS 100-200개/폴링 사이클
   - Pydantic 오버헤드 무시할 수준 (<100ms)
   - 메모리: 1000개 이벤트 기준 ~500KB (무시할 수준)

**데이터 모델:**
```python
from pydantic import BaseModel, Field, field_validator
from datetime import datetime
from typing import Optional

class ExternalEvent(BaseModel):
    source: str  # "dart", "fsc"
    external_id: str
    title: str
    url: str
    published_at: datetime
    fetched_at: datetime
    summary: Optional[str] = None
    raw_payload: dict
    content_hash: str
    severity: str  # "urgent", "watch", "info"
    matched_keywords: list[str] = Field(default_factory=list)
    
    @field_validator('severity')
    @classmethod
    def validate_severity(cls, v: str) -> str:
        valid = {"urgent", "watch", "info"}
        if v not in valid:
            raise ValueError(f"severity must be one of {valid}")
        return v
    
    @field_validator('source')
    @classmethod
    def validate_source(cls, v: str) -> str:
        valid = {"dart", "fsc"}
        if v not in valid:
            raise ValueError(f"source must be one of {valid}")
        return v
```

---

## 3. JSONL 파일 I/O 방식

### 옵션 A: 표준 file I/O + Lock (threading.Lock)

**Description:**
- `open(filepath, 'a')` 모드로 append-only
- 각 쓰기 전에 `threading.Lock` 획득
- `json.dumps()` + `\n` 직렬화
- 읽기는 `open(filepath, 'r')`로 라인 단위 파싱

**Constraints:**
- Lock contention: 높은 동시 쓰기 시 대기 발생
- 파일 손상 가능: 프로세스 크래시 시 부분 쓰기 가능
- 대용량 파일 읽기 시 메모리 오버헤드
- Windows와 POSIX의 파일 locking 동작 차이
- Lock timeout 설정 어려움

**Overcome:**
- Lock timeout: `lock.acquire(timeout=5)` (5초 초과 시 에러)
- Atomic write: 임시 파일에 쓴 후 rename (원자적)
- 파일 손상 감지: 부분 JSON 라인 감지 후 삭제
- 읽기 최적화: 필요한 라인만 읽기 (tail -n 100 방식)
- Lock 경합 모니터링 (로깅)

**Pros:**
- 구현 간단 (표준 라이브러리만 사용)
- Python 어디서나 동작 (Windows/Linux/Mac)
- JSONL 형식 표준 준수
- 프로세스 간 안전성 (OS 파일 시스템 보호)

**Cons:**
- Lock contention이 성능 병목
- 부분 쓰기로 인한 파일 손상 가능성
- 대용량 파일 읽기 시 성능 저하
- Lock timeout 메커니즘 추가 복잡도
- 분산 시스템(NFS)에서 동작 불안정

**예상 성능:**
- 단일 쓰기: ~1-5ms (lock 대기 제외)
- 100 concurrent writes: ~500-1000ms (lock contention)
- 읽기 (최근 100줄): ~10-50ms

---

### 옵션 B: asyncio + aiofiles (비동기)

**Description:**
- `aiofiles` 라이브러리로 비동기 파일 I/O
- `asyncio.Lock` 대신 파일 시스템 수준 lock 사용 (fcntl on Unix)
- 모든 파일 작업을 await 처리
- 높은 동시성 처리

**Constraints:**
- aiofiles는 실제로 스레드풀에 위임 (진정한 비동기 아님)
- Windows에서 fcntl 미지원
- asyncio 학습 곡선
- JSONL 파싱도 asyncio 필요 (I/O bound아님)
- 에러 처리 복잡 (Task cancellation)

**Overcome:**
- aiofiles는 convenience, 실제 성능은 thread기반과 유사
- Windows: portalocker 또는 msvcrt 사용
- JSONL 파싱: `asyncio.to_thread(parse_jsonl_line, ...)`
- 체계적 exception handling: `try/finally`로 resource cleanup
- Lock은 여전히 필요 (파일 시스템 수준)

**Pros:**
- 높은 동시성 (lock contention 최소)
- asyncio 에코시스템과 통합
- I/O 작업 during 네트워크 요청 (event loop 활용)

**Cons:**
- 실제 async가 아님 (aiofiles는 threadpool wrapper)
- 추가 라이브러리 의존성
- 복잡도 증가
- Windows 호환성 문제
- 성능 이득이 크지 않음 (옵션 A와 유사)

---

### 옵션 C: 메모리 버퍼 + 배치 쓰기

**Description:**
- 메모리에 버퍼 유지 (deque 또는 list)
- 일정 크기 도달 시 한 번에 파일에 쓰기 (배치)
- 주기적 flush (예: 30초마다)
- 프로세스 종료 시 모든 버퍼 flush

**Constraints:**
- 프로세스 크래시 시 버퍼 내용 손실 (메모리 휘발성)
- 버퍼 크기 관리 필요 (OOM 방지)
- 재시작 후 상태 복구 불가능
- 시스템 메모리 의존적
- Real-time 알람 요구 시 latency 증가

**Overcome:**
- WAL (Write-Ahead Logging): 메모리 쓰기 전 임시 파일에 기록
- 버퍼 크기 한도: 10000 이벤트 또는 100MB
- 주기적 flush: 30초 또는 버퍼 50% 도달 시
- Graceful shutdown: 프로세스 종료 전 flush 보장
- 모니터링: 버퍼 크기 로깅

**Pros:**
- 높은 성능 (배치 쓰기로 I/O 횟수 최소)
- Lock contention 없음
- 메모리 효율 (정렬된 쓰기)

**Cons:**
- 프로세스 크래시 시 데이터 손실 위험
- 복잡도 증가 (WAL, flush 로직)
- 상태 관리 필요
- Real-time 요구 시 latency
- 디버깅 어려움 (메모리 상태 추적)

---

### 파일 손상 복구 전략

**문제:** 프로세스 크래시 시 마지막 JSONL 라인이 불완전할 수 있음

**솔루션:**
```python
def validate_jsonl_file(filepath):
    """부분 쓰기 라인 제거 및 파일 재작성"""
    valid_lines = []
    with open(filepath, 'r') as f:
        for line_num, line in enumerate(f, 1):
            try:
                json.loads(line.strip())
                valid_lines.append(line)
            except json.JSONDecodeError:
                logging.warning(f"Invalid JSON at line {line_num}: {line[:50]}")
                # 마지막 라인이라면 스킵, 중간이라면 에러
                if line_num == sum(1 for _ in open(filepath)):
                    continue
                else:
                    raise
    
    # 재작성
    with open(filepath, 'w') as f:
        f.writelines(valid_lines)
```

---

### 최종 선택: **옵션 A (표준 file I/O + Lock) + 배치 쓰기 하이브리드**

**Justification:**

1. **신뢰성 우선**
   - JSONL은 엄격한 append-only 포맷
   - OS 파일 시스템이 부분 쓰기 보호 (대부분)
   - Lock으로 명시적 동시성 제어

2. **복잡도 vs 이득**
   - 옵션 B (aiofiles): 복잡도만 증가, 성능 이득 미미
   - 옵션 C (메모리 버퍼): 크래시 시 데이터 손실 위험
   - 옵션 A: 균형잡힌 선택

3. **성능**
   - RSS 폴링 사이클: 300-600초
   - 배치 쓰기로 초당 I/O 횟수 최소화
   - Lock contention은 무시할 수준 (동시 쓰기 드뭄)

**구현 패턴:**
```python
from threading import Lock
from pathlib import Path
import json
import tempfile
import os

class JSONL:
    def __init__(self, filepath):
        self.filepath = Path(filepath)
        self.lock = Lock()
        self.filepath.parent.mkdir(parents=True, exist_ok=True)
    
    def append(self, record: dict) -> None:
        """원자적 추가"""
        with self.lock:
            # 임시 파일에 쓰기 (atomic)
            with tempfile.NamedTemporaryFile(
                mode='w', dir=self.filepath.parent, delete=False
            ) as tmp:
                tmp.write(json.dumps(record) + '\n')
                tmp_path = tmp.name
            
            # 원본 파일에 덧붙이기
            with open(self.filepath, 'a') as f:
                with open(tmp_path, 'r') as tmp_f:
                    f.write(tmp_f.read())
            
            os.unlink(tmp_path)
    
    def append_batch(self, records: list[dict]) -> None:
        """배치 추가"""
        with self.lock:
            with open(self.filepath, 'a') as f:
                for record in records:
                    f.write(json.dumps(record) + '\n')
    
    def read_recent(self, limit: int = 100) -> list[dict]:
        """최근 N개 읽기 (효율적)"""
        with self.lock:
            # 파일 끝에서 역순으로 읽기
            with open(self.filepath, 'rb') as f:
                try:
                    f.seek(-2, os.SEEK_END)
                    while f.read(1) != b'\n':
                        f.seek(-2, os.SEEK_CUR)
                except OSError:
                    f.seek(0)
                
                lines = f.readlines()
        
        records = []
        for line in reversed(lines[-limit:]):
            try:
                records.insert(0, json.loads(line))
            except json.JSONDecodeError:
                pass
        return records
```

---

## 4. 키워드 매칭 엔진

### 옵션 A: 정규식 (regex) + Pre-compiled

**Description:**
- `keywords.yaml`에서 키워드 로드
- 각 키워드를 정규식으로 컴파일 (한 번만)
- `re.findall()` 또는 `re.search()`로 매칭
- Case-insensitive 및 단어 경계 처리

**Constraints:**
- 정규식 엔진 복잡도 (ReDoS 공격 가능)
- 특수 문자 이스케이핑 필요 (복잡한 패턴)
- 성능: 복잡한 정규식은 느릴 수 있음
- 한글/중일 텍스트에서 단어 경계 정의 어려움
- 동적 키워드 추가 시 정규식 컴파일 비용

**Overcome:**
- 간단한 정규식만 사용 (백트래킹 최소화)
- `re.escape()`로 특수 문자 자동 이스케이핑
- 단어 경계: `\b` 또는 공백/구두점 기준
- 캐싱: 컴파일된 정규식을 global dict에 저장
- 정규식 복잡도 검증: `re.compile(...).pattern` 길이 제한

**Pros:**
- 정확한 매칭 (strict mode)
- 복잡한 패턴 지원 (정규식 문법)
- 성능 우수 (C 기반 구현)
- 단일 패스로 처리 가능

**Cons:**
- 특수 문자 이스케이핑 복잡
- ReDoS 공격 가능성
- 한글 단어 경계 처리 어려움
- 정규식 문법 학습 필요
- 유지보수 어려움 (패턴이 복잡하면)

**성능:**
- 단일 키워드 매칭: ~0.01-0.1ms
- 100개 키워드 vs 200글자 텍스트: ~1-5ms

---

### 옵션 B: Fuzzy Matching (difflib 또는 fuzzywuzzy)

**Description:**
- `difflib.SequenceMatcher` 또는 `fuzzywuzzy` (Levenshtein 거리)
- 오타나 유사 표기 감지 가능
- 유사도 임계값 설정 (예: 80%)
- 느슨한 매칭 (loose mode)

**Constraints:**
- 성능 저하 (각 키워드마다 문자열 비교)
- False positive 증가 (유사도 설정 민감)
- 한글 형태소 분석 필요 (정확도 향상)
- fuzzywuzzy는 외부 의존성 (python-Levenshtein)

**Overcome:**
- 높은 유사도 임계값 (85-90%)
- 정확한 키워드 먼저 매칭 (regex), 그 후 fuzzy
- 한글 텍스트: 형태소 분석 (konlpy 또는 mecab)
- 캐싱: 계산 결과 저장

**Pros:**
- 오타나 변형된 표기 감지
- 사용자 친화적 (정확하지 않아도 매칭)
- 한국어 자연어 처리 가능

**Cons:**
- 느린 성능 (각 조합마다 비교)
- False positive 많음
- 추가 라이브러리 (외부 의존성)
- 임계값 튜닝 필요

**성능:**
- 단일 키워드 매칭: ~1-10ms
- 100개 키워드 vs 200글자 텍스트: ~100-1000ms (느림)

---

### 옵션 C: Trie (Prefix Tree) 기반 매칭

**Description:**
- 모든 키워드를 Trie 구조로 빌드 (한 번)
- 텍스트 탐색 시 O(n) 시간복잡도
- `pyahocorasick` 또는 자체 구현
- 다중 키워드 동시 매칭 효율적

**Constraints:**
- 사전 빌드 비용 (초기)
- 메모리 오버헤드 (Trie 구조)
- 동적 키워드 추가 시 재빌드 필요
- 한글 처리를 위해 형태소 분석 필요
- 라이브러리 추가 (pyahocorasick)

**Overcome:**
- 앱 시작 시 한 번만 빌드 (startup cost)
- 메모리: 1000개 키워드 기준 ~1MB (무시할 수준)
- 동적 추가: 런타임에 Trie 재빌드 (비용 없음 - 거의 변경 안 함)
- 형태소 분석: 파이썬 형태소 분석기 (konlpy)

**Pros:**
- 최고 성능 (O(n) 선형 시간)
- 다중 키워드 동시 매칭
- 메모리 효율 (압축된 구조)
- 정확한 매칭

**Cons:**
- 추가 라이브러리 (pyahocorasick)
- 사전 빌드 복잡도
- 한글 처리 필요 (형태소 분석)
- 라이브러리 설치 (native extension)

**성능:**
- 텍스트 길이 N, 키워드 M개: O(N + Z) (Z = matches)
- 실제: 200글자 텍스트, 100개 키워드: ~0.5-2ms (매우 빠름)

---

### 키워드 전략 비교

| 방식 | 속도 | 정확도 | 유연성 | 복잡도 |
|------|------|--------|--------|--------|
| Regex | 매우 빠름 | 높음 | 중간 | 높음 |
| Fuzzy | 느림 | 중간 | 높음 | 중간 |
| Trie | 매우 빠름 | 높음 | 중간 | 중간 |

---

### 최종 선택: **옵션 A (정규식 + Pre-compiled) + Trie 확장 경로**

**Justification:**

1. **초기 Step 1 구현**
   - 키워드 수: 20-50개 (keywords.yaml)
   - 정규식으로 충분한 성능
   - 복잡도 낮음 (regex는 표준 라이브러리)

2. **단계적 최적화**
   - Step 1: 정규식 (20-50개 키워드)
   - Step 2 또는 3: Trie 마이그레이션 (1000+개 키워드)
   - 코드 변경 최소 (matcher 인터페이스 동일)

3. **한글 처리**
   - 한글은 형태소 분석 필수 (정규식만으로는 부족)
   - 사전 전처리: 텍스트 정규화 (특수 문자, 공백 제거)
   - 예: "신한금융그룹" → ["신한", "금융", "그룹"] (형태소 분석)

**구현 패턴:**
```python
import re
from typing import NamedTuple
from enum import Enum

class Severity(str, Enum):
    URGENT = "urgent"
    WATCH = "watch"
    INFO = "info"

class KeywordMatch(NamedTuple):
    keyword: str
    severity: Severity
    position: int  # 텍스트 내 위치

class KeywordMatcher:
    def __init__(self, keywords_yaml_path: str):
        self.patterns = {}
        self.keywords_by_severity = {}
        self._load_keywords(keywords_yaml_path)
    
    def _load_keywords(self, yaml_path: str) -> None:
        """keywords.yaml 로드 및 정규식 컴파일"""
        with open(yaml_path) as f:
            keywords = yaml.safe_load(f)
        
        for severity, kw_list in keywords.items():
            self.keywords_by_severity[severity] = kw_list
            self.patterns[severity] = [
                re.compile(
                    r'\b' + re.escape(kw) + r'\b',
                    re.IGNORECASE | re.UNICODE
                )
                for kw in kw_list
            ]
    
    def match(self, title: str, summary: str = "") -> tuple[Severity, list[str]]:
        """텍스트에서 키워드 매칭"""
        text = f"{title} {summary}".lower()
        matched_keywords = []
        highest_severity = Severity.INFO
        
        for severity in [Severity.URGENT, Severity.WATCH, Severity.INFO]:
            for pattern in self.patterns[severity]:
                if pattern.search(text):
                    matched_keywords.extend([
                        kw for kw in self.keywords_by_severity[severity]
                        if pattern.pattern.lower() in text
                    ])
                    if severity.value > highest_severity.value:
                        highest_severity = severity
        
        return highest_severity, matched_keywords
```

---

## 5. 알람 라우팅 & 신뢰성

### 옵션 A: 이메일 + 파일 로깅 (순차 처리)

**Description:**
- 이벤트 매칭 후 severity에 따라 분기
- `severity = urgent/watch` → 이메일 발송 시도
- 동시에 `data/alerts.jsonl`에 항상 기록
- 이메일 실패 시에도 파일 로그는 유지

**Constraints:**
- 이메일 발송 지연 (네트워크 I/O)
- SMTP 서버 의존성 (다운 시 전체 시스템 영향)
- 이메일 재시도 로직 없음 (1회 시도만)
- 이메일 rate limiting 미처리
- 알람 발송 순서 보장 어려움

**Overcome:**
- 이메일과 파일 로깅 병렬화 (스레드 풀)
- SMTP timeout 설정 (10초)
- 재시도 로직: 최대 3회 (exponential backoff)
- Rate limiting: 초당 최대 1개 이메일 (throttle)
- 발송 실패는 로그에 기록 (나중에 수동 확인)

**Pros:**
- 구현 간단
- 파일 로그는 항상 유지 (이메일 실패해도)
- 이메일은 추가 알림 (옵션)
- 외부 의존성 최소 (SMTP만)

**Cons:**
- 이메일 발송 지연 (폴링 사이클 블로킹)
- SMTP 서버 다운 시 전체 시스템 영향
- 재시도 로직 없음 (메시지 손실 가능)
- 발송 상태 추적 어려움

---

### 옵션 B: 메시지 큐 (celery + redis)

**Description:**
- Celery task queue로 비동기 발송
- Redis/RabbitMQ로 메시지 저장
- 워커가 백그라운드에서 처리
- 자동 재시도 + deadletter queue

**Constraints:**
- 의존성 추가 (celery, redis)
- 운영 복잡도 증가 (redis 서버 필요)
- 개발/테스트 환경 구성 복잡
- 메시지 순서 보장 어려움
- 큰 프로젝트용 (over-engineering)

**Overcome:**
- Redis 단순 설정 (기본값만 사용)
- Celery 설정 최소화 (scheduler만)
- 로컬 개발: in-memory queue 사용 (eagerly execute)
- 순서 보장: task priority 설정
- 모니터링: flower (Celery UI)

**Pros:**
- 높은 신뢰성 (재시도, deadletter)
- 비동기 처리 (폴링 블로킹 없음)
- 프로덕션급 아키텍처
- 확장성 우수

**Cons:**
- 복잡도 매우 높음
- Step 1에서는 오버엔지니어링
- 운영 비용 증가
- 메모리 사용 증가

---

### 옵션 C: 간단한 재시도 + 파일 큐 (내장 구현)

**Description:**
- 발송 실패 이벤트를 별도 파일(`data/failed_alerts.jsonl`)에 기록
- 주기적으로 실패 파일 다시 시도 (30분마다)
- 성공하면 alerts.jsonl로 이동
- 메모리에 큐 유지 (프로세스 메모리 한도 내)

**Constraints:**
- 메모리 큐는 프로세스 크래시 시 손실
- 파일 기반 큐는 성능 저하 (I/O 많음)
- 순서 보장 어려움
- 만료된 이벤트 정리 로직 필요

**Overcome:**
- 프로세스 시작 시 failed_alerts.jsonl 로드
- 메모리 큐 크기 한도 (10000 이벤트)
- 초과 시 파일 큐로 오프로드
- Graceful shutdown: 메모리 큐 파일로 저장
- 재시도 횟수 추적: retry_count 필드

**Pros:**
- 구현 간단 (외부 의존성 없음)
- 메모리/파일 혼합 (성능 + 신뢰성)
- 운영 간단
- 단계적 구현 가능

**Cons:**
- 신뢰성 낮음 (프로세스 크래시 시 손실)
- 재시도 간격 제한적 (fixed interval)
- 모니터링 어려움
- 확장성 낮음

---

### 이메일 발송 구현 (SMTP)

**문제:** Gmail은 앱 비밀번호 필요, 기업 이메일은 인증 방식 다양

**권장 구현:**
```python
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import logging

class EmailNotifier:
    def __init__(self, smtp_server: str, smtp_port: int, user: str, password: str):
        self.smtp_server = smtp_server
        self.smtp_port = smtp_port
        self.user = user
        self.password = password
    
    def send_alert(self, recipient: str, event: ExternalEvent, max_retries: int = 3) -> bool:
        """재시도 로직 포함"""
        for attempt in range(max_retries):
            try:
                self._send_smtp(recipient, event)
                return True
            except Exception as e:
                logging.warning(f"Email send failed (attempt {attempt+1}/{max_retries}): {e}")
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)  # exponential backoff
        
        return False
    
    def _send_smtp(self, recipient: str, event: ExternalEvent) -> None:
        """단일 이메일 발송"""
        with smtplib.SMTP(self.smtp_server, self.smtp_port, timeout=10) as server:
            server.starttls()
            server.login(self.user, self.password)
            
            msg = MIMEMultipart()
            msg['From'] = self.user
            msg['To'] = recipient
            msg['Subject'] = f"[{event.severity.upper()}] {event.title}"
            
            body = f"""
            Source: {event.source}
            Published: {event.published_at}
            URL: {event.url}
            
            Summary: {event.summary or 'N/A'}
            """
            msg.attach(MIMEText(body, 'plain'))
            
            server.send_message(msg)
```

---

### 최종 선택: **옵션 A (이메일 + 파일 로깅) + 옵션 C 간단한 재시도**

**Justification:**

1. **신뢰성 vs 복잡도**
   - Step 1: 파일 로깅이 primary (이메일은 secondary)
   - alerts.jsonl은 항상 유지 (이메일 실패 무관)
   - 재시도는 간단한 파일 기반 (failed_alerts.jsonl)

2. **운영 비용**
   - 외부 의존성 최소 (Celery, Redis 불필요)
   - SMTP만 설정 필요 (선택적)
   - 모니터링 간단 (로그 파일 읽기)

3. **확장 전략**
   - Step 2 또는 3에서 Celery 도입 가능 (코드 변경 최소)
   - 현재는 파일 큐로 충분
   - 이메일 발송 지연은 무시할 수준 (폴링 600초 주기)

4. **구현 순서**
   1. 파일 로깅 먼저 (data/alerts.jsonl)
   2. 이메일 발송 (try/except, 실패 무시)
   3. 실패 파일 (data/failed_alerts.jsonl)
   4. 재시도 워커 (선택적, Step 2에서)

**알람 라우팅 구조:**
```
Scheduler (300-600초마다)
  ↓
RSS Collector
  ↓
Normalizer (ExternalEvent 생성)
  ↓
KeywordMatcher (severity 할당)
  ↓
NotificationRouter (분기)
  ├─ severity = urgent/watch
  │  ├─ EmailNotifier (비동기 스레드, 실패 무시)
  │  └─ AlertLogger (항상 성공)
  │
  └─ severity = info
     └─ AlertLogger (파일에만 기록)
```

---

## 최종 아키텍처 결정 요약

| 컴포넌트 | 선택 | 이유 |
|---------|------|------|
| **RSS 수집** | feedparser + Threading | 단순, 확장 가능, 성능 충분 |
| **이벤트 정규화** | Pydantic BaseModel | 타입 안정성, FastAPI 통합 |
| **JSONL I/O** | Lock + append-only | 신뢰성, 간결성 |
| **키워드 매칭** | Regex + Pre-compiled | 초기 성능 충분, Trie 확장 경로 |
| **알람 라우팅** | Email + File Logging | 단순, 신뢰성, 파일 primary |

---

## 구현 체크리스트

### Phase 1: 기본 구조 (Week 1)
- [ ] FastAPI 프로젝트 초기화
- [ ] config/ (sources.yaml, keywords.yaml, settings.py)
- [ ] app/models.py (ExternalEvent, AlertLog)
- [ ] app/database.py (JSONL 유틸)
- [ ] monitor/collectors/rss.py (기본 RSS 수집)

### Phase 2: 코어 로직 (Week 1-2)
- [ ] monitor/worker.py (메인 파이프라인)
- [ ] monitor/matcher.py (키워드 매칭)
- [ ] monitor/notifier.py (이메일 + 파일 로깅)
- [ ] app/scheduler.py (APScheduler 설정)

### Phase 3: API & 테스트 (Week 2)
- [ ] app/routes/status.py (GET /status, /events, /alerts)
- [ ] tests/test_rss.py
- [ ] tests/test_matcher.py
- [ ] tests/test_dedup.py

### Phase 4: 통합 & 배포 (Week 2-3)
- [ ] 환경변수 설정 (.env.example)
- [ ] CI/CD 파이프라인 (GitHub Actions)
- [ ] 로컬 테스트 (uvicorn main:app --reload)
- [ ] 프로덕션 배포 (gunicorn)

---

## 의존성 최종 목록

```
fastapi>=0.100.0
uvicorn>=0.23.0
pydantic>=2.0.0
pydantic-settings>=2.0.0
feedparser>=6.0.0
requests>=2.31.0
aiofiles>=23.0.0 (optional for Step 2)
APScheduler>=3.10.0
python-dotenv>=1.0.0
pytest>=7.0.0
pytest-asyncio>=0.21.0 (if asyncio)
pyyaml>=6.0.0
```

---

## 리스크 및 완화 전략

| 리스크 | 확률 | 영향 | 완화 |
|--------|------|------|------|
| SMTP 서버 다운 | 낮음 | 중간 | 이메일 실패 무시, 파일 로그 유지 |
| RSS URL 변경 | 낮음 | 중간 | sources.yaml 중앙화, 에러 모니터링 |
| 파일 손상 (부분 쓰기) | 매우낮음 | 높음 | 부분 JSON 라인 감지 및 제거 |
| 메모리 누수 | 중간 | 낮음 | 주기적 재시작, 로깅 모니터링 |
| 키워드 정확도 | 중간 | 낮음 | Step 2에서 형태소 분석 추가 |

---

## 다음 단계

**Step 1 완료 후:**
1. DART OpenAPI 보강 수집기 (더 상세한 정보)
2. 국회 API / YouTube RSS 수집기
3. 고급 키워드 매칭 (제외 키워드, 형태소 분석)
4. PostgreSQL 마이그레이션
5. 웹 대시보드 UI

---

**작성일**: 2026-05-09  
**Status**: 준비 완료 (Step 1 구현 시작 가능)  
**Reviewer**: Architect  
**Builder**: 이 문서를 검토하고 명확하지 않은 부분 질문 후 구현 시작
