"""출력 규격 규칙 테스트 (docs/STEP05.md 의 규칙 번호와 같음)."""
import pytest
from pydantic import ValidationError

from hangil_r2.contracts import AgentResponse, LocationEstimate


def resp(**kw):
    base = {"status": "answered", "intent": "ask_procedure", "assistant_message": "x", "next_action": "none",
            "sources": [{"document_id": "d", "title": "t"}]}
    return AgentResponse(**{**base, **kw})


def test_valid_answer():
    assert resp().status == "answered"


@pytest.mark.parametrize("kw", [
    {"status": "destination_set", "next_action": "locate", "sources": []},  # 규칙 1: destination 없음
    {"sources": []},  # 규칙 2: answered 인데 출처 없음
    {"status": "denied"},  # 규칙 3: 거절에 출처
    {"next_action": "locate"},  # 규칙 4: 상태와 맞지 않는 next_action
    {"unknown_field": 1},  # 계약 밖 필드
])
def test_rule_violations(kw):
    with pytest.raises(ValidationError):
        resp(**kw)


def test_location_estimate_rules():
    with pytest.raises(ValidationError):
        LocationEstimate(status="no_match", candidates=[{"node_id": "n", "floor_id": "B1", "rank_score": 0.5}])
    with pytest.raises(ValidationError):  # 내림차순 아님
        LocationEstimate(status="needs_confirmation", candidates=[
            {"node_id": "a", "floor_id": "B1", "rank_score": 0.2}, {"node_id": "b", "floor_id": "B1", "rank_score": 0.6}])
