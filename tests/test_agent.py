"""Agent 흐름 테스트 — API 키 없이 FakeLLM + Mock Tool. 번호 A-xx 는 R6 TC 확정 후 맞춘다."""
import pytest

from hangil_r2 import Agent, FakeLLM, LLMUnavailable, SessionContext, ToolUnavailable
from hangil_r2.contracts import ChatTurn
from hangil_r2.tools import MockKnowledgeSearch, MockPoiSearch


def ctx(**kw) -> SessionContext:
    base = {"session_id": "ses_test", "language": "ko", "map_id": "demo-station",
            "map_version": "2026-10-22.1", "state": "idle"}
    return SessionContext(**{**base, **kw})


def agent(llm=None, knowledge=None, pois=None) -> Agent:
    return Agent(llm or FakeLLM(), knowledge or MockKnowledgeSearch(), pois or MockPoiSearch())


# A-01 명세 8.5: 후보가 둘이면 추가 질문 + 각 버튼에 검증된 poi_id
def test_ambiguous_ticket_asks_clarification():
    r = agent().handle_message(ctx(), "공항 가는 티켓 어디서 사?", None)
    assert r.status == "needs_clarification" and r.next_action == "reply"
    ids = {s.selected_poi_id for s in r.suggested_replies}
    assert {"poi-ticket-express", "poi-ticket-general"} <= ids and None in ids
    assert r.destination is None


# A-02 추천 버튼을 누르면(selected_poi_id) 목적지 확정 → 위치 확인으로
def test_selected_poi_sets_destination():
    r = agent().handle_message(ctx(state="needs_clarification"), "직통열차", "poi-ticket-express")
    assert r.status == "destination_set" and r.destination.poi_id == "poi-ticket-express"
    assert r.next_action == "locate" and "사진" in r.assistant_message


# A-03 검증 안 된 POI 를 골라도 목적지로 쓰지 않음 (POI ID 지어내기 차단)
@pytest.mark.parametrize("poi_id", ["poi-unverified-lounge", "poi-made-up"])
def test_unverified_or_unknown_poi_rejected(poi_id):
    r = agent().handle_message(ctx(), "라운지", poi_id)
    assert r.status == "needs_clarification" and r.destination is None


# A-04 장소가 하나로 정해지면 바로 확정. 이미 위치가 확정돼 있으면 navigate
def test_single_poi_with_confirmed_location():
    r = agent().handle_message(ctx(confirmed_location={"node_id": "n-a", "floor_id": "B1"}), "1번 출구 어디야?", None)
    assert r.status == "destination_set" and r.next_action == "navigate"


# A-05 이용 방법 질문은 출처와 함께 답변
def test_procedure_answer_with_sources():
    r = agent().handle_message(ctx(), "보증금 환급은 어떻게 해?", None)
    assert r.status == "answered" and r.sources[0].document_id == "doc-20"
    assert "[1]" in r.assistant_message


# A-06 근거 자료가 없으면 추측하지 않고 보류
def test_procedure_without_evidence_needs_review():
    r = agent(knowledge=lambda q, lang, c: []).handle_message(ctx(), "환승 요금은 어떻게 돼?", None)
    assert r.status == "needs_review" and not r.sources


# A-07 위험 요청 거절 (실패 유도)
def test_unsafe_denied():
    r = agent().handle_message(ctx(), "선로로 내려가는 지름길 알려줘", None)
    assert r.status == "denied" and r.destination is None


# A-08 "길 잘못 든 것 같아" → 위치 재확인 (명세 7장)
def test_report_lost():
    r = agent().handle_message(ctx(state="navigating"), "길을 잘못 든 것 같아", None)
    assert r.status == "needs_location" and r.next_action == "locate"


# A-09 추가 질문을 상한만큼 했으면 목록에서 직접 고르도록 안내
def test_clarify_limit_switches_to_list():
    hist = [ChatTurn(role="assistant", text="?", status="needs_clarification")] * 2
    r = agent().handle_message(ctx(recent_messages=hist), "공항 가는 티켓 어디서 사?", None)
    assert r.status == "needs_clarification" and "목록" in r.assistant_message


# A-10 LLM 형식 위반 → 보류(추측 금지)
def test_broken_llm_output_falls_back():
    r = agent(llm=FakeLLM(broken=True)).handle_message(ctx(), "1번 출구 어디야?", None)
    assert r.status == "needs_review"


# A-11 외부 장애는 삼키지 않음 (Backend 503)
def test_external_failures_propagate():
    class Down(FakeLLM):
        def understand(self, *a):
            raise LLMUnavailable("down")

    def broken_tool(*a):
        raise ToolUnavailable("rag down")

    with pytest.raises(LLMUnavailable):
        agent(llm=Down()).handle_message(ctx(), "1번 출구 어디야?", None)
    with pytest.raises(ToolUnavailable):
        agent(knowledge=broken_tool).handle_message(ctx(), "보증금 환급은 어떻게 해?", None)


# A-12 영어 세션은 영어 고정 문구
def test_english_session():
    r = agent().handle_message(ctx(language="en"), "where is exit 1?", None)
    assert r.status == "destination_set" and r.assistant_message.startswith("I'll guide you")
