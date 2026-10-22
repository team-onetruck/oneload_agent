# 인수인계 (R2 정유은 → R4 · R3 · R6, 10/23 부재 대비)

## R4 (Backend) — 연결 방법

```python
from hangil_r2 import Agent, VisualLocalizer, OpenAILLM, SessionContext, LocalizationContext
from hangil_r2 import LLMUnavailable, ToolUnavailable, InvalidImage, MapVersionMismatch

llm = OpenAILLM()                                    # .env: OPENAI_API_KEY, OPENAI_MODEL
agent = Agent(llm, search_knowledge=r3_search_knowledge, search_pois=r4_search_pois)
localizer = VisualLocalizer(llm, map_store=r4_map_store)   # get_graph(map_id, map_version) -> 8.3 형식 dict

# POST /sessions/{id}/messages
resp = agent.handle_message(session_context, body.message, body.selected_poi_id)   # -> AgentResponse

# POST /sessions/{id}/localizations
est = localizer.estimate_location(image_bytes, map_id, map_version,
                                  LocalizationContext(floor_hint=..., last_node_id=...))  # -> LocationEstimate
```

| R2 예외 | Backend 응답 |
|---|---|
| `LLMUnavailable`, `ToolUnavailable` | 503 `AI_SERVICE_UNAVAILABLE` (retryable) |
| `InvalidImage` | 413 `IMAGE_TOO_LARGE` / 415 `UNSUPPORTED_IMAGE` |
| `MapVersionMismatch` | 409 `MAP_VERSION_MISMATCH` |

- 세션 state 전이는 Backend 가 `AgentResponse.status` 를 보고 결정한다 (명세 4장):
  `needs_clarification` → needs_clarification, `destination_set` → needs_location 또는 ready_to_navigate, `needs_location` → needs_location, 나머지 → 변화 없음.
- `destination.poi_id` 는 Backend 가 한 번 더 검증한다 (명세 8.5). R2 도 verified POI 만 쓰지만 이중 확인 유지.
- `handle_message` 는 동기 함수다. FastAPI 에서는 `run_in_threadpool` + 15초 타임아웃으로 감싼다.
- 합의 필요 항목: `docs/decisions.md` D-02 · D-08 ~ D-11.

## R3 (RAG) — 맞출 형식
- `search_knowledge(query, language, context) -> list[KnowledgeHit]` (명세 10장 필드 그대로)
- `excerpt` 가 비어 있으면 근거로 쓰지 않는다. 0건이면 R2 는 `needs_review` 로 보류한다.
- 대역: `hangil_r2/tools.py MockKnowledgeSearch` → 같은 시그니처 함수로 교체하면 끝.

## R6 (QA) — 평가 실행
```bash
pip install -r requirements.txt
python eval/run_eval.py agent --llm openai            # 질문 평가 (eval/agent_cases.csv)
python eval/run_eval.py localization --llm openai     # 사진 평가 (eval/photos/<node_id>/*.jpg)
pytest -q                                             # 기능 테스트 (키 불필요)
```
- 프롬프트 버전 비교: `.env` 의 `PROMPT_VERSION=v2` 처럼 바꾸고 같은 평가 재실행 → `prompts/CHANGELOG.md` 에 기록
- 위치 기준값 실험: `AMBIGUITY_MARGIN` (기본 0.1)

## 남은 일 (R2)
- [ ] 실제 LLM 1회 호출 확인 (CP②) — 키 필요
- [ ] D-02 표지판 데이터 형식 R4 합의 → 답사 데이터로 `mocks/demo_map.json` 교체
- [ ] 평가 baseline 측정 후 프롬프트 v2 실험 1건
- [ ] 팀 저장소로 옮기는 PR (본인 이름)
