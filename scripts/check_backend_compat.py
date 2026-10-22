"""oneload-backend 와 맞는지 확인한다. 백엔드 저장소는 읽기만 하고 수정하지 않는다.

1) 규격 비교 : hangil_r2/backend/ 사본이 백엔드 원본과 같은지
               - 타입 스키마 · 허용 값
               - 예외 클래스 동일성: R2 가 던지는 VisionUnavailable 이 백엔드가 잡는 클래스와 '같은 클래스' 인지 (이슈 #1)
               - 지도: R2 사본(mocks/maps/)이 서버가 쓰는 공유 지도(oneload-data/maps/)와 같은지
                 백엔드 시험용 사본(tests/fixtures/maps/)은 참고로만 비교 (달라도 실패 아님)
2) --e2e     : 백엔드 앱을 그대로 띄우고 R2 Agent · Vision 을 끼워 실제 API 순서로 확인
               대화 4턴 · 사진 위치 · 위치 확정 + 대화 첨부 사진을 실제로 읽는지(이슈 #2) + 사진 서버 장애 시 503(이슈 #1)
               (DB 필요: 백엔드 README 의 docker compose 또는 로컬 PostgreSQL)

저장소 배치 (백엔드 README 와 같음): 같은 부모 폴더에 oneload_agent · oneload-backend · oneload-data
지도 폴더 우선순위: --map-dir > 환경 변수 MAP_DIR > <backend>/../oneload-data/maps (백엔드 기본값과 같음)

사용법 (백엔드 의존성을 쓰기 위해 백엔드 프로젝트 환경에서 실행)
  uv run --project <backend> --with langgraph==1.2.16 python scripts/check_backend_compat.py <backend> [--map-dir <dir>]
  DATABASE_URL=postgresql+psycopg://hangil:hangil@localhost:54329/hangil_test \\
  uv run --project <backend> --with langgraph==1.2.16 python scripts/check_backend_compat.py <backend> --e2e [--llm openai]

종료 코드: 0 = 일치 · 1 = 다른 점 또는 지도 없음 (성공으로 처리하지 않음)
"""

from __future__ import annotations

import argparse
import filecmp
import json
import os
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


MAP_FILE = "seoul-station.json"


def resolve_map_dir(backend: Path, map_dir: Path | None) -> Path:
    """비교할 공유 지도 폴더. 백엔드 Settings.map_dir 과 같은 규칙."""
    if map_dir:
        return map_dir.resolve()
    if os.environ.get("MAP_DIR"):
        return Path(os.environ["MAP_DIR"]).resolve()
    return (backend / ".." / "oneload-data" / "maps").resolve()


def compare_maps(backend: Path, map_dir: Path) -> tuple[list[str], list[str]]:
    """(문제, 참고) 를 돌려준다. 공유 지도가 없으면 문제로 처리 (예외로 멈추지 않음)."""
    problems, notes = [], []
    ours = ROOT / "mocks/maps" / MAP_FILE
    shared = map_dir / MAP_FILE
    if not shared.is_file():
        problems.append(
            f"지도 없음: {shared} — oneload-data 를 백엔드 옆에 clone 하거나 --map-dir 로 지도 폴더를 지정하세요"
        )
    elif not filecmp.cmp(shared, ours, shallow=False):
        problems.append(f"지도 파일 다름: {shared} → mocks/maps/ 로 다시 복사")
    fixture = backend / "tests/fixtures/maps" / MAP_FILE
    if fixture.is_file() and not filecmp.cmp(fixture, ours, shallow=False):
        notes.append(f"참고: 백엔드 시험용 지도({fixture})는 R2 사본과 다름 (시험 전용이라 실패로 보지 않음)")
    return problems, notes


def compare_contracts(backend: Path) -> list[str]:
    from app.ai import contracts as theirs
    from app.domain import localization as their_loc

    from hangil_r2.backend import contracts as ours
    from hangil_r2.backend import localization_types as our_loc

    problems = []
    pairs = [(theirs, ours, n) for n in ("AgentResponse", "Slots", "Source", "SuggestedReply", "Destination")]
    pairs += [(their_loc, our_loc, n) for n in ("VisionFeatures", "VisionText", "Candidate")]
    for a, b, name in pairs:
        if getattr(a, name).model_json_schema() != getattr(b, name).model_json_schema():
            problems.append(f"스키마 다름: {name}")
    for name in ("Intent", "ReplyStatus", "NextAction", "TrainSlot", "CardSlot"):
        if getattr(theirs, name).__args__ != getattr(ours, name).__args__:
            problems.append(f"허용 값 다름: {name}")
    # 모양이 같아도 클래스가 다르면 백엔드 except 에 안 잡힌다 (이슈 #1: 503 대신 500)
    from hangil_r2 import FakeLLM, LLMVision
    from hangil_r2.compat import vision_unavailable_types

    try:  # 실제 LLMVision 에 장애를 넣어 던지는 예외를 그대로 확인 (DB 없이)
        LLMVision(FakeLLM(down=True)).extract(b"img")
        problems.append("사진 장애인데 LLMVision 이 예외를 던지지 않음")
    except theirs.VisionUnavailable:
        pass
    except Exception as exc:  # noqa: BLE001 — 어떤 클래스인지 보고하려고 넓게 잡음
        problems.append(
            f"예외 클래스 다름: LLMVision 이 {type(exc).__module__}.{type(exc).__name__} 을 던져 백엔드가 잡지 못함 (→ 500)"
        )
    if theirs.VisionUnavailable not in vision_unavailable_types():
        problems.append("예외 클래스 다름: LLMAgent 가 백엔드 VisionUnavailable 을 잡지 못함 (hangil_r2/compat.py)")
    return problems


def _poster(http):
    """멱등 키 · 토큰을 붙여 POST 하고, 200/201 이 아니면 바로 멈춘다."""

    def post(path, *, token=None, **kw):
        headers = {"Idempotency-Key": str(uuid.uuid4())}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        r = http.post(f"/api/v1{path}", headers=headers, **kw)
        assert r.status_code in (200, 201), f"{path} → {r.status_code} {r.text}"
        return r.json()

    return post


def _session_with_destination(post, verbose: bool) -> tuple[str, str, str]:
    """세션을 만들고 승차권 질문 4턴으로 목적지를 정한다. (session_id, token, map_version)"""
    s = post(
        "/sessions",
        json={"language": "ko", "map_id": "seoul-station", "client": {"platform": "web", "app_version": "r2-compat"}},
    )
    sid, tok = s["session_id"], s["session_token"]
    for msg in ("공항 가는 티켓 어디서 사?", "잘 모르겠어요", "일반열차", "없어요"):
        r = post(f"/sessions/{sid}/messages", token=tok, json={"message": msg})
        if verbose:
            print(
                f"손님: {msg}\n  한길[{r['status']} · {r['session']['state']}]: {r['assistant_message']}"
                f"\n  버튼: {[b['label'] for b in r['suggested_replies']]}  목적지: {r['destination']}"
                f"  출처: {[x['document_id'] for x in r['sources']]}"
            )
    return sid, tok, s["map_version"]


def run_e2e(llm_name: str, map_dir: Path) -> list[str]:
    """실제 API 순서로 확인. 기대와 다른 점을 돌려준다."""
    from alembic import command
    from alembic.config import Config
    from app.ai.rule_agent import RuleAgent
    from app.config import ROOT as BACKEND_ROOT
    from app.domain.mapdata import MapStore
    from app.main import create_app
    from fastapi.testclient import TestClient

    from hangil_r2 import FakeLLM, LLMAgent, LLMVision, MockKnowledgeSearch, OpenAILLM

    command.upgrade(Config(str(BACKEND_ROOT / "alembic.ini")), "head")
    llm = (
        OpenAILLM()
        if llm_name == "openai"
        else FakeLLM(
            scene={
                "image_quality": "usable",
                "texts": [{"text": "공항철도", "kind": "line_name"}, {"text": "4호선", "kind": "line_number"}],
            }
        )
    )
    # FAQ Q1 · handoff 예제와 같은 조립: 같은 Vision 하나를 Agent 와 create_app 양쪽에 (이슈 #2)
    vision = LLMVision(llm)
    agent = LLMAgent(llm, search_knowledge=MockKnowledgeSearch(), vision=vision, fallback=RuleAgent())
    jpeg = b"\xff\xd8\xff\xe0" + b"\x00" * 64
    maps = MapStore.load(map_dir)
    problems: list[str] = []

    # read_scene 호출 횟수를 세어 대화 첨부 사진을 실제로 읽는지 확인 (응답 status 만으로는 누락이 안 보임)
    calls = {"read_scene": 0}
    original_read_scene = llm.read_scene

    def counting_read_scene(image):
        calls["read_scene"] += 1
        return original_read_scene(image)

    llm.read_scene = counting_read_scene

    with TestClient(create_app(map_store=maps, agent=agent, vision=vision)) as http:
        post = _poster(http)
        sid, tok, ver = _session_with_destination(post, verbose=True)
        loc = post(
            f"/sessions/{sid}/localizations",
            token=tok,
            data={"map_version": ver},
            files={"image": ("sign.jpg", jpeg, "image/jpeg")},
        )
        print(
            f"사진 위치: {loc['status']} 후보 {[c['node_id'] for c in loc['candidates']]} · 상태 {loc['session']['state']}"
        )
        if loc["candidates"]:
            c = loc["candidates"][0]
            conf = post(
                f"/sessions/{sid}/location-confirmations",
                token=tok,
                json={
                    "expected_revision": loc["session"]["revision"],
                    "method": "photo_candidate",
                    "localization_id": loc["localization_id"],
                    "selected_node_id": c["node_id"],
                },
            )
            print(f"위치 확정: {conf['location']['node_id']} · 상태 {conf['session']['state']}")

        # 대화에 사진을 붙이면 Agent 가 Vision 으로 읽어야 한다 (이슈 #2)
        before = calls["read_scene"]
        r = post(
            f"/sessions/{sid}/messages",
            token=tok,
            data={"message": "이거 뭐 눌러?"},
            files={"image": ("sign.jpg", jpeg, "image/jpeg")},
        )
        read = calls["read_scene"] - before
        print(f"대화 사진: read_scene {read}회 · {r['status']}")
        if read < 1:
            problems.append("대화 첨부 사진을 읽지 않음: LLMAgent(vision=...) 연결 확인")

    # 사진 서버 장애 → 503 AI_SERVICE_UNAVAILABLE (500 이면 화면이 수동 위치 선택으로 못 넘어감, 이슈 #1)
    # 세션 생성 → 목적지 설정(대화) → 사진 위치 순서 (QA INT-08 과 같음). 대화는 정상 LLM, 사진만 장애
    down = LLMVision(FakeLLM(down=True))
    # 500 도 결과로 보고하도록 서버 예외를 다시 던지지 않게 한다
    with TestClient(create_app(map_store=maps, agent=agent, vision=down), raise_server_exceptions=False) as http:
        sid, tok, ver = _session_with_destination(_poster(http), verbose=False)
        r = http.post(
            f"/api/v1/sessions/{sid}/localizations",
            headers={"Idempotency-Key": str(uuid.uuid4()), "Authorization": f"Bearer {tok}"},
            data={"map_version": ver},
            files={"image": ("sign.jpg", jpeg, "image/jpeg")},
        )
        code = (r.json().get("error") or {}).get("code") if r.headers.get("content-type", "").startswith("application/json") else None
        print(f"사진 서버 장애: HTTP {r.status_code} · {code}")
        if r.status_code != 503 or code != "AI_SERVICE_UNAVAILABLE":
            problems.append(f"사진 서버 장애가 503 AI_SERVICE_UNAVAILABLE 이 아님: HTTP {r.status_code} · {code}")
    return problems


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("backend", type=Path)
    ap.add_argument("--e2e", action="store_true")
    ap.add_argument("--llm", choices=["fake", "openai"], default="fake")
    ap.add_argument("--map-dir", type=Path, help="공유 지도 폴더 (기본: MAP_DIR 또는 <backend>/../oneload-data/maps)")
    a = ap.parse_args()
    backend = a.backend.resolve()
    if not (backend / "app/ai/contracts.py").is_file():
        print(f"백엔드 저장소가 아님: {backend} (app/ai/contracts.py 없음)")
        sys.exit(1)
    sys.path[:0] = [str(backend), str(ROOT)]
    map_dir = resolve_map_dir(backend, a.map_dir)
    print(f"지도 폴더: {map_dir}")
    problems = compare_contracts(backend)
    map_problems, notes = compare_maps(backend, map_dir)
    problems += map_problems
    for n in notes:
        print(n)
    print("규격 비교:", "일치" if not problems else json.dumps(problems, ensure_ascii=False))
    if a.e2e:
        if map_problems:
            print("e2e 건너뜀: 지도가 없거나 달라서")
        else:
            e2e_problems = run_e2e(a.llm, map_dir)
            print("e2e:", "통과" if not e2e_problems else json.dumps(e2e_problems, ensure_ascii=False))
            problems += e2e_problems
    sys.exit(1 if problems else 0)
