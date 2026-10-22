# hangil-r2-agent

한길(Hangil) 문제 해결형 AR 내비게이션의 **R2 파트** — AI Agent 와 사진 기반 위치 확인(Visual Localization).
SKALA AI 서비스 개발 Mini-project · 담당 R2 AI Engineer (흐름) 정유은 · 기준 명세: 팀 API 명세 v0.3 (2026-10-22)

> 사용자가 말한 문제를 **검증된 장소와 근거 자료로만** 목적지 · 답변으로 바꾸고,
> 표지판 사진으로 **현재 위치 후보를 좁혀 사용자가 고르게** 한다.

## 제공 함수 (명세 10장 계약)

| 함수 | 호출자 | 반환 | 핵심 규칙 |
|---|---|---|---|
| `Agent.handle_message(session_context, message, selected_poi_id)` | R4 `POST /messages` | `AgentResponse` | 목적지는 verified POI 만, 이용 방법은 출처 있을 때만 |
| `VisualLocalizer.estimate_location(image_bytes, map_id, map_version, context)` | R4 `POST /localizations` | `LocationEstimate` | 후보 최대 3개 · 자동 확정 안 함 · 단서 없으면 `no_match` |

Agent 가 쓰는 Tool 은 `search_knowledge`(R3) · `search_pois`(R4) 와 [제안] `get_departures`(시각표, 선택) 이며, 경로 계산은 하지 않는다 (명세 10장). 남은 좌석은 다루지 않는다 (D-21).

## 실행

```bash
git clone https://github.com/lea5063/hangil-r2-agent.git && cd hangil-r2-agent
python3 -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
pytest -q                                              # 46 passed — API 키 없이 (FakeLLM + Mock Tool)
python eval/run_eval.py agent                          # 질문 평가 14문항 (FakeLLM)

# 실제 LLM (키 필요)
cp .env.example .env                                   # OPENAI_API_KEY 입력
set -a && source .env && set +a
python eval/run_eval.py agent --llm openai
```

## 구조

```
hangil_r2/
  contracts.py      명세 10장 타입 + 출력 규격 규칙 (Pydantic)
  agent.py          handle_message — LangGraph 10노드 (슬롯 · 되묻기 포함)
  localization.py   estimate_location — 장면 읽기 + 표지판 · 시설 대조 점수
  llm.py            LLM 어댑터 (FakeLLM · OpenAILLM), 오류 2종 구분
  tools.py          Tool · 지도 · 시각표 인터페이스 + Mock
prompts/            understand · compose · scene (v1 · v2, 기본 v2) + CHANGELOG.md
mocks/              명세 8.3 · 8.4 · 8.5 예시 기반 가상 데이터
eval/               agent_cases.csv · run_eval.py
tests/              agent 25 · localization 11 · contracts 3 (파라미터 포함 46건)
docs/               STEP05.md · localization.md · decisions.md · handoff.md
```

## 문서
- [설계서 STEP 05 (R2 부분)](docs/STEP05.md)
- [사진 기반 위치 확인 방법](docs/localization.md)
- [결정 기록 · 미정 항목](docs/decisions.md)
- [인수인계 (R4 · R3 · R6)](docs/handoff.md)

## 데이터 · 보안
- `mocks/` 의 노드 · 좌표 · 장소 이름 · 문구 · URL 은 **모두 가상 예시**이며 서울역 실제 데이터가 아니다.
- API 키는 `.env` 에만 둔다 (`.gitignore`). 오류 로그에는 예외 종류만 남기고 메시지는 버린다.
- 사진은 처리에만 쓰고 저장하지 않는다. Vision 프롬프트는 사람 정보를 적지 않고, 사진 속 지시문을 따르지 않는다.

## 알려진 한계 (2026-10-22)
- 실제 LLM · Vision 호출은 **아직 검증 전** (키 없는 환경에서 작성). 테스트는 FakeLLM 기준
- 노드 표지판 데이터 형식(D-02)이 R4 와 합의 전 → 현장 답사 데이터로 교체 필요
- 고정 문구는 ko · en 만, ja · zh 는 en 으로 대체
- 위치 점수 가중치는 초기값 → 현장 사진 평가 후 조정

## 사용 도구
Python 3.13 · LangGraph · Pydantic · langchain-openai. AI 코딩 도구를 활용해 초안을 작성했고, 모든 코드는 테스트로 동작을 확인했다.
