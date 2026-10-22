"""[제안] rank_location — 백엔드 tests/test_localization.py 의 기대를 그대로 지키고, 더 나아진 점을 확인."""

from hangil_r2 import ambiguity_hint, rank_location
from hangil_r2.backend.localization_types import Hints, VisionFeatures, VisionText


def feats(*texts, quality="usable"):
    return VisionFeatures(image_quality=quality, texts=[VisionText(text=t) for t in texts])


# ── 백엔드 기존 테스트와 같은 기대 ─────────────────────────────────────
def test_more_matching_sign_text_ranks_higher(seoul):  # R-01
    c = rank_location(feats("공항철도", "Airport Railroad", "4호선"), seoul, Hints(), "en")
    assert [x.node_id for x in c] == ["line4-gates", "line1-gates"] and c[0].rank_score > c[1].rank_score
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
