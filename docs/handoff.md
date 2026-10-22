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
- **`AgentResponse.slots` 를 세션에 저장했다가 다음 `/messages` 때 `SessionContext.slots` 로 그대로 넘겨 주세요** (D-16). 이게 없으면 "직통열차" 같은 짧은 대답을 이전 질문과 잇지 못합니다.
- 열차 종류 버튼(`selected_poi_id: null`)은 버튼 글자를 `message` 로 보내면 됩니다 (D-17).
- `search_pois` 에 `filters={"category": ...}` 지원 필요 (D-19).
- 시각표 도구 (D-22): `get_departures(origin, destination, now, limit) -> list[Departure]` 를 만들어
  `Agent(..., get_departures=r4_get_departures)` 로 넘겨 주세요. 형식은 `hangil_r2/contracts.py Departure`,
  대역은 `tools.py MockDepartures`. 공공데이터포털에 공항철도 시각표 API 가 있는지 먼저 확인 (없으면 R3 공식 시각표 파일로).
  넘기지 않으면 도착 시각 안내만 빠지고 나머지는 그대로 동작합니다. 남은 좌석은 다루지 않습니다 (D-21).
- `handle_message` 는 동기 함수다. FastAPI 에서는 `run_in_threadpool` + 15초 타임아웃으로 감싼다.
- 합의 필요 항목: `docs/decisions.md` D-02 · D-08 ~ D-11.

## R3 (RAG) — 맞출 형식
- `search_knowledge(query, language, context) -> list[KnowledgeHit]` (명세 10장 필드 그대로)
- 공항철도 승차권 자료 요청: 직통열차 구매 방법 · 일반열차 구매 방법 · **두 열차 비교**(되묻기 "잘 모르겠어요" 설명용) · 결제 수단 · 환급. 항목마다 공식 출처 URL 과 확인일
- 공항철도 공식 시각표(직통 · 일반, 서울역 → 인천공항 1 · 2터미널 출발 시각 · 소요 시간)와 출처 · 확인일 (D-22)
- R2 는 검색어에 슬롯 이름을 붙여 보냄 (예: "직통열차 승차권 구매 방법"), `context["slots"]` 로도 전달
- `excerpt` 가 비어 있으면 근거로 쓰지 않는다. 0건이면 R2 는 `needs_review` 로 보류한다.
- 대역: `hangil_r2/tools.py MockKnowledgeSearch` → 같은 시그니처 함수로 교체하면 끝.

## R6 (QA) — 평가 실행
```bash
pip install -r requirements.txt
python eval/run_eval.py agent --llm openai            # 질문 평가 (eval/agent_cases.csv)
python eval/run_eval.py localization --llm openai     # 사진 평가 (eval/photos/<node_id>/*.jpg)
pytest -q                                             # 기능 테스트 (키 불필요)
```
- 프롬프트 버전 비교: `.env` 의 `PROMPT_VERSION=v1` · `v2` 처럼 바꾸고 같은 평가 재실행 → `prompts/CHANGELOG.md` 에 기록
- 위치 기준값 실험: `AMBIGUITY_MARGIN` (기본 0.1)

## 남은 일 (R2)
- [ ] 실제 LLM 1회 호출 확인 (CP②) — 키 필요
- [ ] D-02 표지판 데이터 형식 R4 합의 → 답사 데이터로 `mocks/demo_map.json` 교체
- [ ] 평가 baseline 측정 후 프롬프트 v2 실험 1건
- [ ] 팀 저장소로 옮기는 PR (본인 이름)
