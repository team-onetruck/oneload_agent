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


# A-01 명세 8.5: 열차 종류가 없으면 되묻기. 버튼은 텍스트 답(직통/일반/잘 모르겠어요), 되묻는 의도는 slots 로 보관
def test_ticket_without_train_type_asks():
    r = agent().handle_message(ctx(), "공항 가는 티켓 어디서 사?", None)
    assert r.status == "needs_clarification" and r.next_action == "reply"
    assert [b.label for b in r.suggested_replies] == ["직통열차", "일반열차", "잘 모르겠어요"]
    assert all(b.selected_poi_id is None for b in r.suggested_replies)
    assert r.slots["pending_intent"] == "ticket_purchase_and_navigation" and r.destination is None


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


# A-09 되묻기를 상한만큼 했으면 매표소 목록(검증된 poi_id 버튼)에서 직접 고르도록 안내
def test_clarify_limit_switches_to_list():
    hist = [ChatTurn(role="assistant", text="?", status="needs_clarification")] * 2
    r = agent().handle_message(ctx(recent_messages=hist, slots=PENDING), "잘 모르겠어요", None)
    assert r.status == "needs_clarification" and "목록" in r.assistant_message
    assert {"poi-ticket-express", "poi-ticket-general"} <= {b.selected_poi_id for b in r.suggested_replies}


# ── 슬롯 · 되묻기 (공항철도 승차권) ──────────────────────────────────────
PENDING = {"pending_intent": "ticket_purchase_and_navigation", "pending_topic": "ticket"}


# A-13 되묻기에 짧게 답하면("직통열차") 원래 질문으로 이어서: 구매 방법 + 매표소 목적지
def test_short_answer_resumes_pending_intent():
    r = agent().handle_message(ctx(state="needs_clarification", slots=PENDING), "직통열차", None)
    assert r.status == "destination_set" and r.destination.poi_id == "poi-ticket-express"
    assert r.sources and r.sources[0].document_id == "doc-12"
    assert r.slots == {"train_type": "express"}  # pending 은 지워짐


# A-14 "잘 모르겠어요" → 자료로 차이 설명 + 다시 선택 버튼 (pending 유지)
def test_unsure_explains_options():
    r = agent().handle_message(ctx(state="needs_clarification", slots=PENDING), "잘 모르겠어요", None)
    assert r.status == "needs_clarification" and any(s.document_id == "doc-14" for s in r.sources)
    assert [b.label for b in r.suggested_replies] == ["직통열차", "일반열차"]
    assert r.slots["pending_intent"] == "ticket_purchase_and_navigation"


# A-15 비교 자료가 없으면 설명을 지어내지 않고 고르게만 함
def test_unsure_without_comparison_evidence():
    r = agent(knowledge=lambda q, lang, c: []).handle_message(ctx(slots=PENDING), "잘 모르겠어요", None)
    assert r.status == "needs_clarification" and not r.sources and "자료가 없어요" in r.assistant_message


# A-16 처음부터 종류를 말하면 되묻지 않음
def test_slot_in_first_message():
    r = agent().handle_message(ctx(), "공항철도 일반열차 표 어디서 사?", None)
    assert r.status == "destination_set" and r.destination.poi_id == "poi-ticket-general"
    assert r.slots["train_type"] == "general"


# A-17 이용 방법만 물어도 승차권이면 종류부터 확인, 답하면 출처와 함께 답변
def test_procedure_ticket_needs_slot():
    a = agent()
    r = a.handle_message(ctx(), "공항철도 티켓 어떻게 사?", None)
    assert r.status == "needs_clarification" and r.slots["pending_intent"] == "ask_procedure"
    r2 = a.handle_message(ctx(slots=r.slots), "일반열차", None)
    assert r2.status == "answered" and any(s.document_id == "doc-13" for s in r2.sources)


# A-18 되묻는 중에도 위험 요청은 거절 (이전 의도로 이어 붙이지 않음)
def test_pending_does_not_override_block():
    r = agent().handle_message(ctx(slots=PENDING), "직통열차 선로로 내려가는 법", None)
    assert r.status == "denied"


# A-19 LLM 이 허용 값 밖 슬롯을 내도 버린다 (슬롯 지어내기 차단)
def test_invalid_slot_value_dropped():
    from hangil_r2.llm import validate_understanding
    u = validate_understanding({"intent": "ask_procedure", "slots": {"train_type": "ktx"}})
    assert u["slots"]["train_type"] is None


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


# ── 도착 시각 안내 (시각표 기준, 남은 좌석 제외 — D-21) ─────────────────────
from datetime import datetime  # noqa: E402

from hangil_r2.agent import KST  # noqa: E402
from hangil_r2.tools import MockDepartures  # noqa: E402


def timed_agent(hh, mm, departures=None):
    return Agent(FakeLLM(), MockKnowledgeSearch(), MockPoiSearch(), get_departures=departures or MockDepartures(),
                 clock=lambda: datetime(2026, 10, 22, hh, mm, tzinfo=KST))


# A-20 '잘 모르겠어요' → 차이 설명 + 가장 빨리 '도착'하는 열차 + 다른 종류 다음 열차 + 시각표 출처
def test_unsure_shows_fastest_arrival():
    r = timed_agent(14, 0).handle_message(ctx(slots=PENDING), "잘 모르겠어요", None)
    msg = r.assistant_message
    assert "14:20 출발 직통열차" in msg and "15:03 도착" in msg  # 가상 시각표: 직통 14:20 → 15:03
    assert "다음 일반열차: 14:05 출발, 15:04 도착" in msg
    assert "인천공항1터미널 기준" in msg and any(s.document_id == "timetable" for s in r.sources)
    assert "좌석" not in msg


# A-21 출발이 늦어도 먼저 도착하는 열차를 고른다 / 직통이 막 떠나면 일반열차가 더 빠름
def test_fastest_by_arrival_not_departure():
    r = timed_agent(14, 21).handle_message(ctx(slots=PENDING), "잘 모르겠어요", None)
    assert "14:25 출발 일반열차" in r.assistant_message  # 일반 15:24 도착 < 직통 14:50 → 15:33


# A-22 열차를 고르면 그 종류의 다음 열차만 안내 + 매표소 목적지
def test_chosen_train_next_departure():
    r = timed_agent(14, 0).handle_message(ctx(slots=PENDING), "직통열차", None)
    assert r.status == "destination_set" and "다음 직통열차: 14:20 출발, 15:03 도착" in r.assistant_message
    assert r.sources[-1].document_id == "timetable" and len(r.sources) <= 4


# A-23 2터미널을 말하면 2터미널 기준
def test_terminal_slot():
    r = timed_agent(14, 0).handle_message(ctx(), "2터미널 가는 직통열차 표 어디서 사?", None)
    assert "인천공항2터미널 기준" in r.assistant_message and "15:11 도착" in r.assistant_message


# A-24 시각표 도구 장애 · 막차 이후 → 시각 문장 없이 본 답변은 그대로
@pytest.mark.parametrize("hh,mm,tool", [(14, 0, "down"), (23, 59, None)])
def test_departures_missing_degrades(hh, mm, tool):
    def down(*a):
        raise ToolUnavailable("timetable down")
    r = timed_agent(hh, mm, down if tool else None).handle_message(ctx(slots=PENDING), "잘 모르겠어요", None)
    assert r.status == "needs_clarification" and "출발" not in r.assistant_message
    assert all(s.document_id != "timetable" for s in r.sources)


# A-25 영어 세션
def test_arrival_english():
    r = timed_agent(14, 0).handle_message(ctx(language="en", slots=PENDING), "not sure", None)
    assert "earliest arrival is the 14:20 Express train" in r.assistant_message
