# Review Feedback — Step 2
Date: 2026-05-10
Status: APPROVED

## Conditions
(none)

## Escalate to Architect
- 30개 사이트 URL/selector 다수가 `# tentative` 추정값입니다 (s2~s12, s15~s16, s18~s30 등). 본 Step 2 브리프는 정확도 검증을 Step 3 (Crawler+Detector)로 명시 위임했고 Builder는 그 가이드대로 https + tentative 주석을 달았습니다. 다만 Step 3 진입 전에 Project Owner 측 검토(특히 reg/comp/sec 카테고리 — 컴플라이언스·정보보안 부서가 직접 사용)가 필요한지는 코드 레벨 결정 사항이 아니므로 Architect/Owner가 판단 부탁드립니다.
- `Item.id` 모델 기본값 미설정 — Brief Flag §3은 `uuid.uuid4().hex`를 명시했지만 `app/db/models.py:110` 의 `id` 컬럼에 `default=` 콜백이 없습니다. Builder의 Deviation §2(legacy import idempotency)는 정당하지만, Step 3 크롤러가 Item을 만들 때 매번 `id=uuid.uuid4().hex`를 명시 세팅해야 한다는 사실이 **코드 자체에 강제되지 않습니다**. Step 3 ARCHITECT-BRIEF에 명시 또는 BUILD-LOG의 Known Gaps 등재 여부를 결정 부탁드립니다 — 코드 레벨에서 `default=lambda: uuid.uuid4().hex`를 추가하는 것은 legacy import의 명시적 id 세팅과 충돌 없이 공존 가능하므로 단순 작업이지만, 현 Step 2 스코프 밖이라 Condition으로 잡지 않았습니다.

## Cleared
83개 테스트(67 기존 + 16 신규) 전수 통과를 직접 재확인했고(`pytest tests/ -v` → 83 passed in 2.82s), Step 2 신규 11개 파일(`app/db/__init__.py`·`session.py`·`models.py`·`seed.py`·`import_legacy.py`, `app/routes/watchtower.py`, `config/seed_categories.yaml`·`seed_sites.yaml`·`seed_users.yaml`, `static/watchtower.html`, `tests/test_watchtower.py`)과 수정 4개 파일(`main.py`·`requirements.txt`·`tests/conftest.py`·`.env.example`) 전체를 정독했습니다. 보안 표면(escapeHtml 커버리지·`https?://` URL 화이트리스트로 javascript: 스킴 차단·SQLAlchemy ORM 파라미터 바인딩으로 SQL injection 차단·env 치환 정규식의 좁은 surface·legacy JSONL 방어 코드·`target="_blank" rel="noopener noreferrer"` 적용)·spec compliance(브리프 §2.1~§2.8 + Constraints + Flags §1~§5 모두 매핑)·Korean UX 메시지(NFR-USE-001 — `등록된 사용자가 없습니다`/`데이터를 불러오지 못했습니다`/`표시할 업데이트가 없습니다` 등)·NFR-SEC-005(코드 내 실제 도메인 이메일 없음, `${WATCHTOWER_ADMIN_EMAIL}` env-only)·CON-006(LLM 호출 코드 0건)·Step 1 자산 무파괴(`monitor/`·`app/database.py`·`app/scheduler.py`·`app/routes/status.py` `git diff` 비어있음)를 모두 확인했고 블로킹 결함을 찾지 못했습니다. Builder의 5개 Deviations은 모두 정당하며, 특히 `read_by` CSV 부분문자열 오탐(`u1`이 `u10`의 prefix) 회피를 위한 Python 후처리 결정과 legacy import의 `content_hash[:32]` 기반 idempotency 결정은 합리적입니다. 머지 가능합니다.
