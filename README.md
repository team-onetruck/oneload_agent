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
| `LLMVision` | `VisionExtractor.extract(image) -> VisionFeatures` | `create_app(vision=...)` + `LLMAgent(vision=...)` | `NoVision` (항상 503) |
| `rank_location` [제안] | `match_location` 과 같은 입력 · 출력 | (R4 판단) | 완전 일치 개수 |

```python
# 백엔드 연결 예 — 백엔드 엔드포인트는 바꾸지 않는다
from hangil_r2 import LLMAgent, LLMVision, OpenAILLM, MockKnowledgeSearch
llm = OpenAILLM()
vision = LLMVision(llm)  # 같은 Vision 을 Agent(대화 사진) 와 create_app(사진 위치) 양쪽에 넣는다
app = create_app(agent=LLMAgent(llm, search_knowledge=MockKnowledgeSearch(), vision=vision, fallback=RuleAgent()),
                 vision=vision)
```

## 실행

```bash
git clone https://github.com/team-onetruck/oneload_agent.git && cd oneload_agent
python3 -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
pytest -q                                              # 43 passed — API 키 없이 (FakeLLM + 공유 지도 사본)
python eval/run_eval.py agent                          # 평가 17문항 (FakeLLM)

```

**실제 LLM 으로 실행 (키만 넣으면 됨)**

```bash
python -m hangil_r2.config                            # 저장소 루트에 .env 를 만들고 위치를 알려 줌
#  → .env 를 열어 OPENAI_API_KEY= 뒤에 키를 붙여 넣고 저장 (따옴표 · 공백 없이)
python eval/run_eval.py agent --llm openai            # .env 는 자동으로 읽힘
git status                                            # .env 가 목록에 없어야 정상 (.gitignore)
```
키가 비어 있으면 실행이 멈추고 고칠 파일 경로를 알려 준다. 키 값은 화면 · 로그에 출력하지 않는다.

**백엔드와 맞는지 확인** (백엔드 저장소는 읽기만 함)

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

## CI · 배포

`.github/workflows/ci.yml` (테스트는 GitHub 호스트 runner, 배포만 macserver 에 설치된 이 저장소 전용 self-hosted runner)

| job | 언제 | 하는 일 |
| --- | --- | --- |
| `test` | 모든 PR · main push | `pytest -q` (Python 3.12, 백엔드 이미지와 같은 버전) |
| `backend` | 모든 PR · main push | 백엔드 main 에 **이 커밋의 에이전트를 끼워** 백엔드 테스트 전체 + `check_backend_compat.py` (규격 비교, `--e2e`) |
| `deploy` | main push, 위 두 job 통과 후 | oneload-infra 의 공용 배포를 이 저장소의 runner(`macserver-hangil-agent`)에서 돌린다. 서버는 백엔드 main + 이 저장소 main 으로 API 이미지를 다시 만든다 |

- 서버는 **항상 이 저장소의 main** 을 쓴다. 로컬 백엔드는 옆 폴더(`../oneload_agent`)를 editable 로 설치하므로, 이 저장소에서 `git pull` 하거나 고친 내용이 백엔드를 다시 켜면 바로 반영된다. 백엔드 CI 와 배포는 그 폴더가 없어서 이 저장소 main 을 URL 로 고정해서 돈다.
- 되돌리기: main 에서 해당 커밋을 revert 하면 다시 배포된다.
- 서버의 `OPENAI_API_KEY` 는 백엔드 저장소 secret 에만 있다. 이 저장소에서 시작한 배포는 서버 설정을 건드리지 않고, CI 는 키 없이 FakeLLM 으로 돈다.
- 공개 저장소라 포크 PR 워크플로는 관리자 승인 후에만 돈다. `.github/` 를 건드리는 포크 PR 실행은 승인하지 않는다.

## 구조

```
hangil_r2/
  backend/          백엔드 규격 사본 (contracts · mapdata · guard · localization 타입) — 직접 수정 금지
  prompts/          understand · compose · scene · verify (v5) + CHANGELOG.md
  mocks/            패키지에 포함되는 데모 지식 · 가상 시각표
  agent.py          LLMAgent — LangGraph 20노드 (이해 → 슬롯 → 확인 질문 / 답변 / 목적지 → 관련성 검증)
  vision.py         LLMVision — 사진 → 보이는 글자 · 시설
  llm.py            LLM 어댑터 (FakeLLM · OpenAILLM), 최종 응답 관련성 검증, 오류 2종 구분
  knowledge.py      R3 검색 · 시각표 도구 인터페이스 + Mock
  compat.py         백엔드와 같은 클래스여야 하는 예외 고르기 (사진 장애 → 503)
  config.py         .env 만들기 · 읽기 · 키 확인 (python -m hangil_r2.config)
  ranker.py         [제안] match_location 개선안 (드문 문구 가중 · 오타 허용 · 동점 안내)
mocks/maps/         oneload-data 공유 지도 사본
eval/               agent_cases.csv (17문항) · run_eval.py
scripts/            check_backend_compat.py
tests/              agent 29 · vision 7 · ranker 7 · config 4 (파라미터 포함 47건)
docs/               FAQ.md · STEP05.md · localization.md · decisions.md · handoff.md
```

## 문서
- [FAQ (자주 묻는 질문)](docs/FAQ.md) — 처음이면 여기부터
- [인수인계 · 백엔드 연결](docs/handoff.md)
- [설계서 STEP 05 (R2 부분)](docs/STEP05.md)
- [결정 기록](docs/decisions.md)
- [사진 위치 확인 방법](docs/localization.md)

## 데이터 · 보안
- `hangil_r2/mocks/knowledge.json` 의 문구는 **데모**이며 실제 공항철도 이용 방법 · 요금이 아니다. 요금 문서는 일부러 없다 (요금 질문 → `cannot_answer`).
- `mocks/maps/seoul-station.json` 은 `oneload-data/maps/` 공유 지도 사본. 층(B2 · B3 · B7)만 공식 확인, 나머지는 답사 전 임시값 (백엔드 README).
- API 키는 `.env` 에만 둔다. 오류 로그에는 예외 종류만 남긴다. 사진은 저장하지 않는다.

## 알려진 한계 (2026-10-26)
- R2 패키지가 배포 백엔드에서 `LLMAgent` · `LLMVision`으로 활성화됐고 실제 `/messages` OpenAI 호출을 확인했다.
- 실제 Vision은 현장 사진 평가 전이다. 사진 위치는 모델이 읽은 특징을 지도와 대조하며 모델이 위치를 직접 정하지 않는다.
- 실제 LLM(gpt-4o-mini) 평가: 대화 17문항 11~12/17, 근거 41문항 범위 밖 21/21 · 무관한 출처 29 → 0 (PR #11 + v5). 각 1~2회 실행이라 편차가 있다 → [docs/live-llm-eval-20261026.md](docs/live-llm-eval-20261026.md)
- 실제 LLM에서 "있어요/없어요" 같은 짧은 대답을 앞 질문과 잇지 못하는 경우가 있다 (다음 개선).
- 이 저장소의 `MockKnowledgeSearch` 는 데모 문서다. 백엔드는 R3 `hangil_rag` 공식 문서 검색을 쓴다.

## 사용 도구
Python 3.13 · LangGraph · Pydantic · langchain-openai. AI 코딩 도구를 활용해 작성했고, 모든 코드는 테스트와 백엔드 연결 확인으로 동작을 검증했다.
