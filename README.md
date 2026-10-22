# oneload_agent — 한길 R2 (AI Agent · Vision)

한길(Hangil) 서울역 · 공항철도 안내 서비스의 **R2 파트**. 백엔드 [oneload-backend](https://github.com/team-onetruck/oneload-backend)의
AI 규격(`app/ai/contracts.py`)에 맞춘 구현이며, 백엔드 `create_app()` 에 그대로 꽂힌다.
SKALA AI 서비스 개발 Mini-project · 담당 R2 AI Engineer (흐름) 정유은

> LLM 은 **말을 이해하고, 자료로 문장을 만들고, 사진에서 보이는 글자를 읽는 것**만 한다.
> 목적지 · 위치 · 경로는 지도 데이터와 코드가 정한다.

## 제공하는 것

| 구현 | 백엔드 규격 | 백엔드 자리 (`app/main.py`) | 지금 백엔드 기본값 |
|---|---|---|---|
| `LLMAgent` | `Agent.handle_message(ctx, message, image, selected_poi_id) -> AgentResult` | `create_app(agent=...)` | `RuleAgent` (키워드 규칙) |
| `LLMVision` | `VisionExtractor.extract(image) -> VisionFeatures` | `create_app(vision=...)` | `NoVision` (항상 503) |
| `rank_location` [제안] | `match_location` 과 같은 입력 · 출력 | (R4 판단) | 완전 일치 개수 |

```python
# 백엔드 연결 예 — 백엔드 엔드포인트는 바꾸지 않는다
from hangil_r2 import LLMAgent, LLMVision, OpenAILLM, MockKnowledgeSearch
llm = OpenAILLM()
app = create_app(agent=LLMAgent(llm, search_knowledge=MockKnowledgeSearch(), fallback=RuleAgent()),
                 vision=LLMVision(llm))
```

## 실행

```bash
git clone https://github.com/team-onetruck/oneload_agent.git && cd oneload_agent
python3 -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
pytest -q                                              # 33 passed — API 키 없이 (FakeLLM + 백엔드 지도 사본)
python eval/run_eval.py agent                          # 평가 17문항 (FakeLLM)

# 실제 LLM (키 필요)
cp .env.example .env                                   # OPENAI_API_KEY 입력
set -a && source .env && set +a
python eval/run_eval.py agent --llm openai
```

**백엔드와 맞는지 확인** (백엔드 저장소는 읽기만 함)

```bash
git clone https://github.com/team-onetruck/oneload-backend.git ../oneload-backend
uv run --project ../oneload-backend --with langgraph==1.2.16 python scripts/check_backend_compat.py ../oneload-backend
# DB 가 있으면 실제 API 순서로 대화 · 사진 위치까지
DATABASE_URL=postgresql+psycopg://hangil:hangil@localhost:54329/hangil_test \
uv run --project ../oneload-backend --with langgraph==1.2.16 python scripts/check_backend_compat.py ../oneload-backend --e2e
```

## 구조

```
hangil_r2/
  backend/          백엔드 규격 사본 (contracts · mapdata · guard · localization 타입) — 직접 수정 금지
  agent.py          LLMAgent — LangGraph 12노드 (이해 → 슬롯 → 되묻기 / 답변 / 목적지)
  vision.py         LLMVision — 사진 → 보이는 글자 · 시설
  llm.py            LLM 어댑터 (FakeLLM · OpenAILLM), 오류 2종 구분
  knowledge.py      R3 검색 · 시각표 도구 인터페이스 + Mock
  ranker.py         [제안] match_location 개선안 (드문 문구 가중 · 오타 허용 · 동점 안내)
prompts/            understand · compose · scene (v3) + CHANGELOG.md
mocks/              maps/seoul-station.json (백엔드 사본) · knowledge.json (데모 문서) · timetable.json (가상)
eval/               agent_cases.csv (17문항) · run_eval.py
scripts/            check_backend_compat.py
tests/              agent 22 · vision 4 · ranker 7 (파라미터 포함 33건)
docs/               STEP05.md · localization.md · decisions.md · handoff.md
```

## 데이터 · 보안
- `mocks/knowledge.json` 의 문구는 **데모**이며 실제 공항철도 이용 방법 · 요금이 아니다. 요금 문서는 일부러 없다 (요금 질문 → `cannot_answer`).
- `mocks/maps/seoul-station.json` 은 백엔드 지도 사본. 층(B2 · B3 · B7)만 공식 확인, 나머지는 답사 전 임시값 (백엔드 README).
- API 키는 `.env` 에만 둔다. 오류 로그에는 예외 종류만 남긴다. 사진은 저장하지 않는다.

## 알려진 한계 (2026-10-22)
- 실제 LLM · Vision 호출은 **아직 검증 전** (키 없는 환경). 테스트 · 평가는 FakeLLM 기준
- 백엔드 시연 서버(`/api/v1/docs`)는 이 작업 환경에서 접속되지 않아, 같은 백엔드 코드를 로컬에서 띄워 확인함
- R3 RAG 가 아직 없어 이용 안내는 데모 문서로만 동작

## 사용 도구
Python 3.13 · LangGraph · Pydantic · langchain-openai. AI 코딩 도구를 활용해 작성했고, 모든 코드는 테스트와 백엔드 연결 확인으로 동작을 검증했다.
