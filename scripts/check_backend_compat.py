"""oneload-backend 와 맞는지 확인한다. 백엔드 저장소는 읽기만 하고 수정하지 않는다.

1) 규격 비교 : hangil_r2/backend/ 사본이 백엔드 원본과 같은지 (타입 스키마 · 허용 값 · 지도 파일)
2) --e2e     : 백엔드 앱을 그대로 띄우고 R2 Agent · Vision 을 끼워 실제 API 순서로 대화 · 사진 위치를 돌려 본다
               (DB 필요: 백엔드 README 의 docker compose 또는 로컬 PostgreSQL)

사용법 (백엔드 의존성을 쓰기 위해 백엔드 프로젝트 환경에서 실행)
  uv run --project <backend> --with langgraph python scripts/check_backend_compat.py <backend>
  DATABASE_URL=postgresql+psycopg://hangil:hangil@localhost:54329/hangil_test \\
  uv run --project <backend> --with langgraph python scripts/check_backend_compat.py <backend> --e2e [--llm openai]
"""

from __future__ import annotations

import argparse
import filecmp
import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


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
    if not filecmp.cmp(backend / "data/maps/seoul-station.json", ROOT / "mocks/maps/seoul-station.json", shallow=False):
        problems.append("지도 파일 다름: data/maps/seoul-station.json → mocks/maps/ 로 다시 복사")
    return problems


def run_e2e(llm_name: str) -> None:
    from alembic import command
    from alembic.config import Config
    from app.ai.rule_agent import RuleAgent
    from app.config import ROOT as BACKEND_ROOT
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
    agent = LLMAgent(llm, search_knowledge=MockKnowledgeSearch(), vision=LLMVision(llm), fallback=RuleAgent())
    jpeg = b"\xff\xd8\xff\xe0" + b"\x00" * 64

    with TestClient(create_app(agent=agent, vision=LLMVision(llm))) as http:

        def post(path, *, token=None, **kw):
            headers = {"Idempotency-Key": str(uuid.uuid4())}
            if token:
                headers["Authorization"] = f"Bearer {token}"
            r = http.post(f"/api/v1{path}", headers=headers, **kw)
            assert r.status_code in (200, 201), f"{path} → {r.status_code} {r.text}"
            return r.json()

        s = post(
            "/sessions",
            json={
                "language": "ko",
                "map_id": "seoul-station",
                "client": {"platform": "web", "app_version": "r2-compat"},
            },
        )
        sid, tok, ver = s["session_id"], s["session_token"], s["map_version"]
        for msg in ("공항 가는 티켓 어디서 사?", "잘 모르겠어요", "일반열차", "없어요"):
            r = post(f"/sessions/{sid}/messages", token=tok, json={"message": msg})
            print(
                f"손님: {msg}\n  한길[{r['status']} · {r['session']['state']}]: {r['assistant_message']}"
                f"\n  버튼: {[b['label'] for b in r['suggested_replies']]}  목적지: {r['destination']}"
                f"  출처: {[x['document_id'] for x in r['sources']]}"
            )
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


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("backend", type=Path)
    ap.add_argument("--e2e", action="store_true")
    ap.add_argument("--llm", choices=["fake", "openai"], default="fake")
    a = ap.parse_args()
    sys.path[:0] = [str(a.backend.resolve()), str(ROOT)]
    problems = compare_contracts(a.backend.resolve())
    print("규격 비교:", "일치" if not problems else json.dumps(problems, ensure_ascii=False))
    if a.e2e:
        run_e2e(a.llm)
    sys.exit(1 if problems else 0)
