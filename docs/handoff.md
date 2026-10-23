# 인수인계 (R2 정유은 → R4 · R3 · R6, 10/23 부재 대비)

기준: oneload-backend `a82b7be` · oneload-data `606f648` (2026-10-22). `scripts/check_backend_compat.py` 로 규격 일치 · 실제 API 연결을 확인함.

## R4 (Backend) — 연결 방법

`app/main.py` 의 `create_app()` 기본값만 바꾸면 된다. 엔드포인트 · 스키마 변경 없음.

```python
from hangil_r2 import LLMAgent, LLMVision, OpenAILLM, MockKnowledgeSearch
from app.ai.rule_agent import RuleAgent

llm = OpenAILLM()                                   # APP_OPENAI_API_KEY → OPENAI_API_KEY 로 읽힘 (아래 주의)
vision = LLMVision(llm)                             # 같은 Vision 하나를 두 곳에 넣는다
agent = LLMAgent(llm, search_knowledge=MockKnowledgeSearch(), vision=vision, fallback=RuleAgent())
app = create_app(agent=agent, vision=vision)
```
- `create_app(vision=...)` → 사진 위치 `/localizations` 에서 사용
- `LLMAgent(vision=...)` → 대화 `/messages` 에 첨부한 사진을 읽을 때 사용. **빠뜨리면 대화 사진은 읽지 않고 글자로만 답한다** (오류 없이 200 이라 놓치기 쉬움)

| 상황 | R2 동작 | 백엔드에 보이는 결과 |
|---|---|---|
| LLM 장애 · 형식 위반 | `fallback`(RuleAgent)이 대신 답함 | 평소처럼 200 |
| 사진 인식 장애 | `VisionUnavailable` | 503 `AI_SERVICE_UNAVAILABLE` → 화면이 수동 위치 선택 |
| 사진 응답 형식 위반 | 읽은 글자 없음으로 반환 | `no_match` + 재촬영 안내 |
| 자료 없음 · 검색 장애 | `cannot_answer` | 200, 추측하지 않음 |

**주의**
- 백엔드 설정은 `OPENAI_API_KEY` 를 `Settings` 에 두지 않았다. `langchain-openai` 는 환경 변수 `OPENAI_API_KEY` 를 읽으므로,
  `~/hangil-api.env` 에 그 이름으로 들어가는지 확인 필요 (deploy.sh 가 `APP_` 접두사를 떼고 씀 → `OPENAI_API_KEY` 로 들어감).
- 백엔드 의존성에 `langgraph==1.2.16`, `langchain-openai==1.7.2` 추가 필요.
- `/messages` 처리 예산(명세 3장 15초) 안에서 R2 는 LLM 최대 2회(이해 1 + 문장 1, 사진이 있으면 +1). 호출당 `LLM_TIMEOUT_S`(기본 6초).

## R3 (RAG) — 맞출 형식
- `search_knowledge(query, language, context) -> list[KnowledgeHit]` (명세 10장 필드). `verified_at` 필수 (백엔드 Source 규격).
- `context["slots"]["train_type"]` 이 `express` · `allstop` 이면 **다른 열차 전용 문서는 빼고** 돌려줘야 한다
  (일반열차 안내에 직통열차 문서가 근거로 붙는 문제 — 테스트 A-21).
- 요금 문서는 `scope="fare"` 로 표시. R2 는 요금 질문에 `scope="fare"` 문서만 근거로 쓴다 (없으면 `cannot_answer`).
- 대역: `hangil_r2/knowledge.py MockKnowledgeSearch` + `hangil_r2/mocks/knowledge.json`. 같은 시그니처 함수로 교체.

## R6 (QA) — 평가 실행
```bash
pip install -r requirements.txt
pytest -q                                             # 기능 테스트 33건 (키 불필요)
python eval/run_eval.py agent --llm openai            # 질문 평가 17문항 (eval/agent_cases.csv)
python eval/run_eval.py localization --llm openai     # 사진 평가 (eval/photos/<node_id>/*.jpg, 답사 사진)
```
- 프롬프트 버전 비교: `.env` 의 `PROMPT_VERSION` → `hangil_r2/prompts/CHANGELOG.md` 에 기록
- 문항의 `train_type` · `has_transit_card` 열 = 그 질문 직전 세션 슬롯 (여러 턴 대화를 한 줄로 시험)

## 남은 일 (R2)
- [x] 실제 LLM 1회 호출 확인 — 배포 `/messages`에서 규칙 에이전트가 처리하지 못하는 표현으로 확인
- [ ] 실제 LLM 으로 평가 17문항 baseline → 실패 분류 → 프롬프트 v4 실험 1건
- [ ] R3 실제 검색 연결 후 같은 평가 재실행
- [ ] (R4 판단) `rank_location` 을 `match_location` 대신 쓸지 — `tests/test_ranker.py` 가 백엔드 기존 기대를 지킴
