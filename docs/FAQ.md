# FAQ — 한길 R2 (AI Agent · Vision)

> 담당: R2 AI Engineer (흐름) 정유은 · 기준: oneload-backend `a82b7be` · oneload-data `606f648` (2026-10-22 호환 확인) · 저장소: `team-onetruck/oneload_agent`
> 더 자세한 내용: 연결 [handoff.md](handoff.md) · 구조와 흐름 [STEP05.md](STEP05.md) · 결정 이유 [decisions.md](decisions.md) · 사진 위치 [localization.md](localization.md)

## 한눈에 보기

R2는 **한길의 AI 부품 두 개**를 만든다.

| 부품 | 하는 일 | 백엔드에서 불리는 곳 |
|---|---|---|
| `LLMAgent` | 손님 말을 이해해서 되묻기 · 근거 있는 답변 · 목적지 지정 | `POST /sessions/{id}/messages` |
| `LLMVision` | 사진에서 안내판 글자 · 시설만 읽기 | `POST /sessions/{id}/localizations` |

둘 다 **백엔드 규격(`app/ai/contracts.py`) 그대로** 만들었다. R4가 `create_app()` 에 두 줄만 넣으면 붙는다.

**원칙: AI는 이해하고 말만 한다. 목적지 · 위치 · 경로는 지도 데이터와 코드가 정한다.**
→ AI가 없는 장소 · 요금 · 통로를 지어낼 수 없다.

## 요청 하나가 지나가는 길

**대화 (`/messages`)**
```
앱(R5) → 백엔드(R4) → [R2 LLMAgent] ─────────────────────────────→ 백엔드 guard → 앱
                       ① 사진이 있으면 글자 읽기
                       ② LLM이 말 이해 (의도 · 슬롯)
                       ③ 슬롯 합치기 ("직통열차" 같은 짧은 대답 이어 받기)
                       ④ 코드가 다음 행동 결정 (되묻기 / 답변 / 목적지 / 거절)
                       ⑤ 답변이 필요하면 R3 자료 검색 → LLM이 자료로만 문장 작성
```

**사진 위치 (`/localizations`)**
```
앱 → 백엔드 → [R2 LLMVision: 사진 속 글자 · 시설만 읽음] → 백엔드 match_location: 지도와 대조 → 후보 3개 → 손님이 확정
```

## 용어

| 용어 | 뜻 | 한길에서의 예 |
|---|---|---|
| 슬롯 | 답하려면 알아야 하는 빈칸 | 열차 종류(직통 · 일반), 교통카드 유무 |
| 되묻기 | 빈칸이 비면 버튼과 함께 질문 | "직통열차와 일반열차 중 어떤 열차로 갈까요?" |
| 규격(계약) | 주고받는 데이터 모양 | 백엔드 `AgentResponse` 의 status · intent · slots |
| verified POI | 지도에 '확인됨'으로 등록된 장소 | 이 목록에 있는 곳만 목적지가 됨 |
| guard | 백엔드의 마지막 검문소 | 지도에 없는 목적지 · 검색하지 않은 출처를 지움 |
| RAG · 근거 | 자료를 찾아 그 내용으로만 답하기 | R3 자료가 없으면 `cannot_answer` |
| fallback | AI가 고장 나면 대신 답하는 것 | 백엔드 RuleAgent(키워드 규칙)가 대신 답함 |
| mock · FakeLLM | 테스트용 가짜 자료 · 가짜 AI | 키 · 서버 없이 매번 같은 결과로 흐름 시험 |

**status 값 (화면이 보고 판단)**

| 값 | 뜻 |
|---|---|
| `needs_clarification` | 되묻는 중 (버튼을 보여 줌) |
| `destination_set` | 목적지가 정해짐 → 위치 확인(`locate`) 또는 출발(`navigate`) |
| `answered` | 자료로 답함 (출처 있음) |
| `cannot_answer` | 근거가 없어 답하지 않음 |
| `relocalize` | 위치를 다시 확인 |
| `out_of_scope` | 범위 밖이거나 위험한 요청 |

## 연결 · 실행

### Q1. R2 코드를 백엔드에 어떻게 붙이나요? (R4)
`create_app()` 에서 agent 와 vision 만 바꾼다. 엔드포인트 · 스키마는 바뀌지 않는다.
```python
from hangil_r2 import LLMAgent, LLMVision, OpenAILLM, MockKnowledgeSearch
from app.ai.rule_agent import RuleAgent

llm = OpenAILLM()
vision = LLMVision(llm)                             # 같은 Vision 하나를 두 곳에 넣는다
agent = LLMAgent(llm, search_knowledge=MockKnowledgeSearch(), vision=vision, fallback=RuleAgent())
app = create_app(agent=agent, vision=vision)
```
- `create_app(vision=...)` → 사진 위치 `/localizations` 에서 사용
- `LLMAgent(vision=...)` → 대화 `/messages` 에 첨부한 사진을 읽을 때 사용. **빠뜨리면 대화 사진은 읽지 않고 글자로만 답한다** (오류 없이 200 이라 놓치기 쉬움)
백엔드 의존성에 `langgraph==1.2.16`, `langchain-openai==1.7.2`, `python-dotenv==1.2.4` 를 추가해야 한다. 자세한 내용은 [handoff.md](handoff.md).

### Q2. API 키는 어디에 넣나요?
- **내 컴퓨터**: 저장소 맨 위에서 `python -m hangil_r2.config` → `.env` 가 생기고 위치를 알려 준다 → `OPENAI_API_KEY=` 뒤에 키를 넣고 저장.
  `.env` 는 `.gitignore` 로 제외되어 git 에 올라가지 않는다.
- **서버**: GitHub Secrets → 배포 시 서버 파일로 들어간다 (백엔드 README 방식). 이미 설정된 환경 변수는 `.env` 가 덮어쓰지 않는다.
- 키를 채팅 · 이슈 · PR · 코드에 붙여 넣지 않는다. 공개됐다면 바로 폐기하고 새로 발급한다.

### Q3. 테스트 · 평가는 어떻게 돌리나요?
```bash
pip install -r requirements.txt
pytest -q                                      # 47건, API 키 불필요
python eval/run_eval.py agent                  # 평가 17문항 (가짜 LLM)
python eval/run_eval.py agent --llm openai     # 실제 LLM (키 필요)
```

### Q4. 평가 17/17이면 정확도 100%인가요?
**아니다.** 가짜 LLM(FakeLLM)으로 **흐름이 맞게 짜였는지**만 확인한 숫자다. 실제 LLM(gpt-4o-mini, 2026-10-26)으로는 **11~12/17**이다. 놓친 문항은 주로 "있어요/없어요" 같은 짧은 대답 이어받기다. 자세한 결과: [live-llm-eval-20261026.md](live-llm-eval-20261026.md)

### Q5. 백엔드가 바뀌면 R2 코드는 어떻게 확인하나요?
```bash
# 같은 부모 폴더에 세 저장소를 둔다 (백엔드 README 와 같은 배치)
git clone https://github.com/team-onetruck/oneload-backend.git ../oneload-backend
git clone https://github.com/team-onetruck/oneload-data.git ../oneload-data      # 공유 지도 (maps/)
uv run --project ../oneload-backend --with langgraph==1.2.16 --with python-dotenv==1.2.4 \
  python scripts/check_backend_compat.py ../oneload-backend
# 지도 폴더를 따로 두었다면 --map-dir <폴더> (우선순위: --map-dir > MAP_DIR > ../oneload-data/maps)
# DB 가 있으면 --e2e: 대화 · 사진 위치 · 위치 확정 + 대화 첨부 사진 읽기 + 사진 서버 장애 시 503 까지
DATABASE_URL=postgresql+psycopg://hangil:hangil@localhost:54329/hangil_test \
uv run --project ../oneload-backend --with langgraph==1.2.16 --with python-dotenv==1.2.4 \
  python scripts/check_backend_compat.py ../oneload-backend --e2e
```
검사 항목: 타입 스키마 · 허용 값 · **예외 클래스가 백엔드와 같은지** · R2 지도 사본(`mocks/maps/`)이 공유 지도(`oneload-data/maps/`)와 같은지.
지도가 없거나 다르면 종료 코드 1 (성공으로 처리하지 않음). 백엔드 `tests/fixtures/maps/` 는 시험용이라 참고로만 비교한다.
`hangil_r2/backend/` 는 백엔드 규격 **사본**이라 직접 고치지 않는다. 다르다고 나오면 다시 복사한다.
단, 사본 클래스는 모양이 같아도 백엔드와 **다른 클래스**다. 백엔드가 `except` 로 잡는 예외는 `hangil_r2/compat.py` 가 백엔드의 진짜 클래스를 골라 던진다.

## 동작 방식

### Q6. 왜 AI가 직접 길을 찾지 않나요?
AI는 없는 통로나 계단을 그럴듯하게 지어낼 수 있어서, 역 안에서는 위험하다. 경로는 지도 데이터로 계산하고(R4), AI는 이해와 문장만 맡는다.

### Q7. 공항 승차권은 어떤 순서로 안내하나요?
```
"공항 가는 티켓 어디서 사?" → 열차 종류 되묻기 [직통열차] [일반열차] [잘 모르겠어요]
  ├ 직통열차                 → 직통열차 표 사는 곳(B2)
  └ 일반열차 → 교통카드 있나요?
       ├ 있어요              → 일반열차 승강장 (카드를 찍고 탐)
       └ 없어요              → 일반열차 표 사는 곳(B3, 1회용 카드)
```
백엔드 RuleAgent 와 같은 순서다. 층 정보는 백엔드 지도 기준이다 (층만 공식 확인, 위치는 답사 전 임시값).

### Q8. "잘 모르겠어요"를 누르면?
R3 자료로 두 열차의 차이를 설명하고 다시 고르게 한다. 비교 자료가 없으면 비교하지 않고 "확인 전"이라고 표시한다.

### Q9. 요금을 물어보면 왜 모른다고 하나요?
확인된 요금 자료가 없어서다. **근거 없는 숫자는 말하지 않는 것이 규칙**이다. R3가 요금 문서를 `scope="fare"` 로 넣으면 그 자료로 답한다.

### Q10. LLM이 고장 나면?
- 대화: 백엔드 RuleAgent 가 대신 답한다. 서비스는 멈추지 않는다.
- 사진 위치: 백엔드가 503 `AI_SERVICE_UNAVAILABLE` 을 내고, 화면이 '직접 위치 고르기'로 넘어간다 (`check_backend_compat.py --e2e` 로 확인).
- 대화에 붙인 사진: 사진 없이 말로만 답한다.
- 사진 응답 형식이 깨지면 글자를 읽지 못한 것으로 처리 → 재촬영 안내 (위치를 추측하지 않음).

### Q11. RuleAgent와 무엇이 다른가요?
RuleAgent 는 정해진 단어만 알아듣는다. LLMAgent 는 표현이 달라도, 영어여도, 짧게 답해도 알아듣는다.
판단과 목적지 결정은 둘 다 코드가 하므로 안전성은 같다.

### Q12. 사진 위치는 누가 정하나요?
백엔드 `match_location`(R4)이 정한다. R2는 글자 · 시설만 읽는다.
R2는 더 정확한 방식 `rank_location`(드문 글자 우선 · 오타 허용 · 동점이면 한 장 더 요청)을 **제안**만 해 두었다 (같은 입력 · 출력, 백엔드 기존 테스트 기대를 지킴).

### Q13. 도착 시각 · 남은 좌석은?
- **남은 좌석**: 공개 잔여석 API 를 찾지 못해 다루지 않는다.
- **도착 시각 비교**: 기능은 있지만 **기본 꺼짐**이다. 공식 시각표가 생기면 시각표 도구를 넘겨 켠다 (출발이 아니라 도착이 가장 빠른 열차를 추천).

## 팀원별 요청

### Q14. R3 자료는 어떻게 연결하나요? (R3)
`search_knowledge(query, language, context) -> list[KnowledgeHit]` 모양의 함수를 주면 된다 (명세 v0.3 10장 필드, `verified_at` 필수).
- `context["slots"]["train_type"]` 이 정해져 있으면 **다른 열차 전용 문서는 빼고** 돌려준다.
- 요금 문서는 `scope="fare"` 로 표시한다.
- 지금은 `MockKnowledgeSearch` + `hangil_r2/mocks/knowledge.json`(데모 문서)으로 동작한다.

### Q15. 평가는 어떻게 함께 하나요? (R6)
- 질문 평가: `eval/agent_cases.csv` (17문항). `train_type` · `has_transit_card` 열은 그 질문 직전 세션 슬롯이다.
- 사진 평가: 답사 사진을 `eval/photos/<node_id>/*.jpg` 로 두고 `python eval/run_eval.py localization --llm openai`.
- 결과 숫자는 `docs/experiments.md` 와 검증 결과서에 같은 값으로 적는다.

## 고치기 · 늘리기

### Q16. 새 질문 유형을 추가하려면?
1. `hangil_r2/prompts/understand_v*.md` 에 의도 · 주제 추가
2. `hangil_r2/llm.py` 허용 값 · `hangil_r2/agent.py` 분기 추가
3. `tests/test_agent.py` 테스트 1개
4. `eval/agent_cases.csv` 문항 1개
5. `hangil_r2/prompts/CHANGELOG.md` 기록

### Q17. 프롬프트를 바꾸려면?
새 버전 파일(예: `understand_v4.md`)을 만들고 `.env` 의 `PROMPT_VERSION` 으로 바꾼다.
**한 번에 규칙 하나만** 바꾸고 같은 평가로 전후를 비교한다. 점수가 떨어진 버전도 지우지 않고 이유를 `CHANGELOG.md` 에 남긴다.

## 현재 상태 (2026-10-26)

| 항목 | 상태 |
|---|---|
| 백엔드 규격 일치 · 실제 API 흐름 | 완료 (백엔드 `2ebd94d` + PR #11 브랜치로 백엔드 72건 통과, e2e 통과 · 2026-10-25 R2 재실행) |
| 테스트 · 평가 (가짜 LLM) | 47건 통과 · 17/17 (흐름 확인용) |
| 실제 LLM 평가 (gpt-4o-mini) | 대화 17문항 **11~12/17** · 근거 41문항: 범위 밖 **21/21**, 무관한 출처 **29 → 0** (PR #11 + v5) · [결과](live-llm-eval-20261026.md) |
| 백엔드에 실제 연결 | `LLMAgent` · `LLMVision` 활성화 완료 |
| R3 실제 자료 | R3 `hangil_rag` 공식 문서 검색 (백엔드 의존성 v0.3.0). 이 저장소 평가 17문항은 데모 문서, 41문항은 `hangil_rag` |
| 시연 서버 접속 확인 | `/api/v1/docs` · 세션 · 실제 OpenAI 메시지 확인 완료 |
