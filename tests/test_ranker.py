"""[제안] rank_location — 백엔드 tests/test_localization.py 의 기대를 그대로 지키고, 더 나아진 점을 확인."""

import json
from pathlib import Path

import pytest

from hangil_r2 import ambiguity_hint, rank_location
from hangil_r2.backend.localization_types import Hints, VisionFeatures, VisionText
from hangil_r2.backend.mapdata import MapData


def feats(*texts, quality="usable"):
    return VisionFeatures(image_quality=quality, texts=[VisionText(text=t) for t in texts])


# ── 백엔드 기존 테스트와 같은 기대 ─────────────────────────────────────
def test_more_matching_sign_text_ranks_higher(seoul):  # R-01
    c = rank_location(feats("공항철도", "Airport Railroad", "4호선"), seoul, Hints(), "en")
    # The 2F elevator sign also reads "Airport Railroad" (map 2026-10-25.1), so more places may follow.
    assert [x.node_id for x in c][:2] == ["line4-gates", "line1-gates"] and c[0].rank_score > c[1].rank_score
    assert "Sign reads “4호선”" in c[0].evidence


def test_matching_ignores_case_spacing_and_dashes(seoul):  # R-02
    c = rank_location(feats("airport  railroad", "LINE-1"), seoul, Hints(), "ko")
    assert c[0].node_id == "line1-gates" and c[0].evidence == ["“Airport Railroad” 안내판", "“Line 1” 안내판"]


def test_floor_hint_breaks_a_tie(seoul):  # R-03
    tied = rank_location(feats("Airport Railroad"), seoul, Hints(), "en")
    hinted = rank_location(feats("Airport Railroad", "Express Train"), seoul, Hints(floor_hint="B2"), "en")
    assert tied[0].rank_score == tied[1].rank_score and hinted[0].node_id == "arex-concourse"


def test_unreadable_or_unknown_gives_nothing(seoul):  # R-04
    assert rank_location(feats("4호선", quality="blurry"), seoul, Hints(), "en") == []
    assert rank_location(feats("Myeongdong"), seoul, Hints(), "en") == []


# ── 개선점 ───────────────────────────────────────────────────────────
def test_rare_text_outweighs_common_text(seoul):  # R-05 '4호선'(1곳) 하나가 '공항철도'(2곳) 하나보다 강함
    c = rank_location(feats("공항철도", "4호선"), seoul, Hints(), "en")
    assert c[0].node_id == "line4-gates" and c[0].rank_score - c[1].rank_score > 0.1


def test_ocr_typo_still_matches(seoul):  # R-06 백엔드 완전 일치 방식은 0건
    c = rank_location(feats("Airport Raiload", "Line 4"), seoul, Hints(), "en")
    assert c[0].node_id == "line4-gates"


def test_tie_gives_hint_to_retake(seoul):  # R-07
    c = rank_location(feats("Airport Railroad"), seoul, Hints(), "ko")
    hint = ambiguity_hint(c, seoul, "ko")
    assert hint and "한 장 더" in hint


def test_place_with_several_signs_is_one_candidate(seoul):  # R-08 line4-gates 는 표지판 3개 (지도 2026-10-25.1)
    c = rank_location(feats("4호선", "Transfer", "Line 4"), seoul, Hints(), "en")
    ids = [x.node_id for x in c]
    assert ids[0] == "line4-gates" and len(ids) == len(set(ids))
    assert c[0].evidence == ["Sign reads “4호선”", "Sign reads “Transfer”", "Sign reads “Line 4”"]


def test_sign_without_text_does_not_crash(seoul):  # R-09 지도 규격은 빈 sign_texts 를 허용한다
    raw = seoul.model_dump(by_alias=True)
    node = "line4-gates"
    raw["landmarks"] = [lm for lm in raw["landmarks"] if lm["node_id"] != node]
    raw["landmarks"].append({"landmark_id": "lm-empty", "node_id": node, "sign_texts": []})
    c = rank_location(feats("4호선"), MapData.model_validate(raw), Hints(), "ko")
    assert node not in [x.node_id for x in c]


# ── R3 합성 입력 24개 (tests/data/localization_cases.json) ─────────────────
# 실제 사진 정확도가 아니라 '읽힌 글자 → 지점' 매칭 단계만 본다. 문구를 R3 카드에서 가져와 점수가 후하다.
CASES = json.loads((Path(__file__).parent / "data/localization_cases.json").read_text(encoding="utf-8"))["cases"]
# 지도 데이터에 문구가 없어서 틀리는 케이스 — 매처 문제가 아니다. oneload-data 에 문구가 들어오면 통과로 바뀐다.
DATA_GAPS = {
    "L05": "지도 arex-concourse 에 '트래블센터 · Travel Center' 문구 없음 (R3 카드 lm-b2-travel-center 에는 있음)",
    "L06": "지도에서 '도심공항터미널' 은 allstop-tickets · ktx-hall 에만 있음 (R3 카드는 arex-concourse 에 추정 배치)",
}


def _ids(seoul, case):
    f = feats(*case["texts"], quality=case.get("quality", "usable"))
    return [x.node_id for x in rank_location(f, seoul, Hints(), "ko")]


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_r3_synthetic_case(seoul, case):  # R-10 정답이 상위 3개 안 · 같은 지점 중복 없음 · NO_MATCH 는 후보 없음
    if case["id"] in DATA_GAPS:
        pytest.xfail(DATA_GAPS[case["id"]])
    ids = _ids(seoul, case)
    assert len(ids) == len(set(ids)), f"같은 지점이 후보에 두 번: {ids}"
    if case.get("expect") == "NO_MATCH":
        assert ids == []
    else:
        assert set(case.get("any_of") or [case["expect"]]) & set(ids[:3]), ids


def test_r3_synthetic_summary(seoul):  # R-11 점수 하한 (2026-10-25.1 지도 기준 top-1 16/20 · top-3 18/20 · NO_MATCH 4/4)
    top1 = top3 = 0
    for c in CASES:
        ids = _ids(seoul, c)
        if c.get("expect") == "NO_MATCH":
            assert ids == [], c["id"]
            continue
        answer = set(c.get("any_of") or [c["expect"]])
        top1 += bool(ids) and ids[0] in answer
        top3 += bool(answer & set(ids[:3]))
    assert top1 >= 16 and top3 >= 18, (top1, top3)
