"""R2 평가 실행기 (R6 와 공유). 백엔드 규격 · 백엔드 지도 사본 · 백엔드 guard 사본으로 채점한다.

사용법
  python eval/run_eval.py agent                       # FakeLLM 으로 흐름 확인 (키 불필요)
  python eval/run_eval.py agent --llm openai          # 실제 LLM (.env 의 OPENAI_API_KEY)
  python eval/run_eval.py localization --photos eval/photos --llm openai
      eval/photos/<정답 node_id>/*.jpg 구조 (현장 답사 사진). 사진 읽기(LLMVision) → rank_location 으로 top-1 · top-3

결과는 표준 출력 + eval/results/<모드>_<시각>.csv. 숫자는 docs/experiments.md 에 옮겨 적는다.
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from hangil_r2 import FakeLLM, LLMAgent, LLMVision, MockKnowledgeSearch, OpenAILLM, rank_location  # noqa: E402
from hangil_r2.backend.contracts import AgentContext, Slots  # noqa: E402
from hangil_r2.backend.guard import guard  # noqa: E402
from hangil_r2.backend.localization_types import Hints  # noqa: E402
from hangil_r2.backend.mapdata import MapStore  # noqa: E402

SEOUL = MapStore.load(ROOT / "mocks" / "maps").get("seoul-station")


def make_llm(name: str):
    return OpenAILLM() if name == "openai" else FakeLLM()


def save(mode: str, rows: list[dict]) -> Path:
    out = ROOT / "eval" / "results" / f"{mode}_{datetime.now():%Y%m%d_%H%M%S}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    return out


def run_agent(llm_name: str) -> None:
    agent = LLMAgent(make_llm(llm_name), search_knowledge=MockKnowledgeSearch())
    rows = []
    with open(ROOT / "eval" / "agent_cases.csv", encoding="utf-8") as f:
        for case in csv.DictReader(f):
            slots = Slots(
                train_type=case["train_type"] or "unknown", has_transit_card=case["has_transit_card"] or "unknown"
            )
            ctx = AgentContext(
                language=case["language"],
                map=SEOUL,
                state="idle",
                destination_poi_id=None,
                confirmed_node_id=None,
                slots=slots,
            )
            t0 = time.perf_counter()
            try:
                r = guard(
                    agent.handle_message(ctx, case["message"], None, case["selected_poi_id"] or None),
                    SEOUL,
                    case["language"],
                )
                status, poi = r.status, (r.destination.poi_id if r.destination else "")
            except Exception as exc:  # 평가는 멈추지 않고 실패로 기록
                status, poi = f"error:{type(exc).__name__}", ""
            ok = status in case["expected_status"].split("|") and (
                not case["expected_poi_id"] or poi == case["expected_poi_id"]
            )
            rows.append(
                {
                    "id": case["id"],
                    "type": case["type"],
                    "expected": case["expected_status"],
                    "actual": status,
                    "poi": poi,
                    "pass": ok,
                    "ms": round((time.perf_counter() - t0) * 1000),
                }
            )
    for r in rows:
        print(f"{'PASS' if r['pass'] else 'FAIL'} {r['id']} {r['type']}: {r['expected']} -> {r['actual']} {r['poi']}")
    print(f"\nagent: {sum(r['pass'] for r in rows)} / {len(rows)} 충족 · 결과 {save('agent', rows)}")


def run_localization(llm_name: str, photos: Path) -> None:
    vision = LLMVision(make_llm(llm_name))
    rows = []
    for img in sorted(photos.glob("*/*.jp*g")):
        truth = img.parent.name
        try:
            ids = [c.node_id for c in rank_location(vision.extract(img.read_bytes()), SEOUL, Hints(), "ko")]
        except Exception as exc:
            ids = [f"error:{type(exc).__name__}"]
        rows.append(
            {"photo": img.name, "truth": truth, "top": "|".join(ids), "top1": ids[:1] == [truth], "top3": truth in ids}
        )
    if not rows:
        sys.exit(f"사진 없음: {photos}/<node_id>/*.jpg")
    n = len(rows)
    print(
        f"localization: top-1 {sum(r['top1'] for r in rows)}/{n} · top-3 {sum(r['top3'] for r in rows)}/{n}"
        f" · 결과 {save('localization', rows)}"
    )


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["agent", "localization"])
    ap.add_argument("--llm", choices=["fake", "openai"], default="fake")
    ap.add_argument("--photos", type=Path, default=ROOT / "eval" / "photos")
    a = ap.parse_args()
    run_agent(a.llm) if a.mode == "agent" else run_localization(a.llm, a.photos)
