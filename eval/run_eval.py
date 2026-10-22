"""R2 평가 실행기 (R6 와 공유).

사용법
  python eval/run_eval.py agent                       # FakeLLM 으로 흐름 확인 (키 불필요)
  python eval/run_eval.py agent --llm openai          # 실제 LLM (.env 의 OPENAI_API_KEY)
  python eval/run_eval.py localization --photos eval/photos --llm openai
      eval/photos/<정답 node_id>/*.jpg 구조. 현장 답사 사진을 노드별 폴더에 넣으면 top-1 · top-3 를 계산.

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

from hangil_r2 import Agent, FakeLLM, LocalizationContext, OpenAILLM, SessionContext, VisualLocalizer  # noqa: E402
from hangil_r2.tools import MockKnowledgeSearch, MockMapStore, MockPoiSearch  # noqa: E402

MAP_ID, MAP_VERSION = "demo-station", "2026-10-22.1"


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
    agent = Agent(make_llm(llm_name), MockKnowledgeSearch(), MockPoiSearch())
    rows = []
    with open(ROOT / "eval" / "agent_cases.csv", encoding="utf-8") as f:
        for case in csv.DictReader(f):
            ctx = SessionContext(session_id="eval", language=case["language"], map_id=MAP_ID,
                                 map_version=MAP_VERSION, state="idle")
            t0 = time.perf_counter()
            try:
                r = agent.handle_message(ctx, case["message"], case["selected_poi_id"] or None)
                status, poi = r.status, (r.destination.poi_id if r.destination else "")
            except Exception as exc:  # 평가에서는 멈추지 않고 실패로 기록
                status, poi = f"error:{type(exc).__name__}", ""
            ok = status in case["expected_status"].split("|") and (not case["expected_poi_id"] or poi == case["expected_poi_id"])
            rows.append({"id": case["id"], "type": case["type"], "expected": case["expected_status"], "actual": status,
                         "poi": poi, "pass": ok, "ms": round((time.perf_counter() - t0) * 1000)})
    passed = sum(r["pass"] for r in rows)
    for r in rows:
        print(f"{'PASS' if r['pass'] else 'FAIL'} {r['id']} {r['type']}: {r['expected']} -> {r['actual']}")
    print(f"\nagent: {passed} / {len(rows)} 충족 · 결과 {save('agent', rows)}")


def run_localization(llm_name: str, photos: Path) -> None:
    loc = VisualLocalizer(make_llm(llm_name), MockMapStore())
    rows = []
    for img in sorted(photos.glob("*/*.jp*g")):
        truth = img.parent.name
        try:
            est = loc.estimate_location(img.read_bytes(), MAP_ID, MAP_VERSION, LocalizationContext())
            ids = [c.node_id for c in est.candidates]
        except Exception as exc:
            ids = [f"error:{type(exc).__name__}"]
        rows.append({"photo": img.name, "truth": truth, "top": "|".join(ids),
                     "top1": ids[:1] == [truth], "top3": truth in ids})
    if not rows:
        sys.exit(f"사진 없음: {photos}/<node_id>/*.jpg")
    n = len(rows)
    print(f"localization: top-1 {sum(r['top1'] for r in rows)}/{n} · top-3 {sum(r['top3'] for r in rows)}/{n}"
          f" · 결과 {save('localization', rows)}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["agent", "localization"])
    ap.add_argument("--llm", choices=["fake", "openai"], default="fake")
    ap.add_argument("--photos", type=Path, default=ROOT / "eval" / "photos")
    a = ap.parse_args()
    run_agent(a.llm) if a.mode == "agent" else run_localization(a.llm, a.photos)
