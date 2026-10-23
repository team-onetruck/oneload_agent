"""LLMAgent 흐름 테스트 — 백엔드 규격 · 백엔드 지도 사본 · 백엔드 guard 사본으로 확인. API 키 불필요.

모든 응답은 백엔드 guard 를 통과시킨 뒤 확인한다 (실제 /messages 와 같은 순서).
"""

import re
from datetime import datetime

import pytest

from hangil_r2 import FakeLLM, LLMAgent, MockDepartures, MockKnowledgeSearch, ToolUnavailable
from hangil_r2.agent import KST
from hangil_r2.backend.contracts import AgentResponse, AgentResult, Slots
from hangil_r2.backend.guard import guard


def agent(**kw) -> LLMAgent:
    kw.setdefault("search_knowledge", MockKnowledgeSearch())
    return LLMAgent(kw.pop("llm", FakeLLM()), **kw)


def ask(a: LLMAgent, c, message: str, poi: str | None = None, image: bytes | None = None) -> AgentResponse:
    return guard(a.handle_message(c, message, image, poi), c.map, c.language)


# ── 공항철도 승차권 흐름 (백엔드 RuleAgent 와 같은 순서: 열차 → (일반이면) 교통카드 → 목적지) ──
def test_airport_asks_train_type(ctx):  # A-01
    r = ask(agent(), ctx(), "공항 가는 티켓 어디서 사?")
    assert r.status == "needs_clarification" and r.destination is None
    assert [b.label for b in r.suggested_replies] == ["직통열차", "일반열차", "잘 모르겠어요"]


def test_short_answer_express_sets_destination(ctx):  # A-02 짧은 대답 → 슬롯 → 매표소 + 구매 방법 출처
    r = ask(agent(), ctx(), "직통열차")
    assert r.status == "destination_set" and r.destination.poi_id == "poi-express-tickets"
    assert r.slots.train_type == "express" and r.next_action == "locate"
    assert "B2" in r.assistant_message and any(s.document_id == "doc-express-ticket" for s in r.sources)


def test_allstop_asks_card_then_routes(ctx):  # A-03
    a = agent()
    r = ask(a, ctx(), "일반열차")
    assert r.status == "needs_clarification" and r.slots.train_type == "allstop"
    assert [b.label for b in r.suggested_replies] == ["교통카드 있어요", "없어요", "교통카드가 뭐예요?"]
    no = ask(a, ctx(slots=r.slots), "없어요")
    assert no.destination.poi_id == "poi-allstop-tickets" and no.slots.has_transit_card == "no"
    yes = ask(a, ctx(slots=r.slots, here="line4-gates"), "교통카드 있어요")
    assert yes.destination.poi_id == "poi-allstop-platform" and yes.next_action == "navigate"


def test_unsure_explains_with_sources(ctx):  # A-04 잘 모르겠어요 → 자료로 차이 + 다시 선택
    r = ask(agent(), ctx(), "잘 모르겠어요")
    assert r.status == "needs_clarification" and r.sources[0].document_id == "doc-arex-train-types"
    assert [b.label for b in r.suggested_replies] == ["직통열차", "일반열차"] and r.items == []


def test_unsure_without_evidence_does_not_compare(ctx):  # A-05
    r = ask(agent(search_knowledge=lambda q, lang, c: []), ctx(), "잘 모르겠어요")
    assert r.status == "needs_clarification" and not r.sources and r.unverified_notes


def test_selected_poi_button(ctx):  # A-06 화면 버튼 → 검증된 POI 만, 열차 슬롯도 맞춤
    r = ask(agent(), ctx(), "", poi="poi-express-platform")
    assert r.destination.poi_id == "poi-express-platform" and r.slots.train_type == "express"
    bad = ask(agent(), ctx(), "", poi="poi-invented")
    assert bad.status == "needs_clarification" and bad.destination is None


# ── 이용 방법 · 결제: 근거 있으면 답, 없으면 cannot_answer ────────────────────────────
def test_usage_answer_with_citation(ctx):  # A-07
    r = ask(agent(), ctx(), "보증금 환급은 어떻게 해?")
    assert r.status == "answered" and r.sources[0].document_id == "doc-deposit-refund" and "[1]" in r.assistant_message


def test_fare_is_not_invented(ctx):  # A-08 요금 자료 없음 → 숫자 없이 보류
    r = ask(agent(), ctx(), "직통열차 요금 얼마야?")
    assert r.status == "cannot_answer" and not re.search(r"\d", r.assistant_message) and r.sources == []


def test_how_to_buy_needs_train_then_offers_go_button(ctx):  # A-09
    a = agent()
    r = ask(a, ctx(), "승차권 어떻게 사요?")
    assert r.status == "needs_clarification"
    r2 = ask(a, ctx(slots=Slots(train_type="express")), "승차권 어떻게 사요?")
    assert r2.status == "answered" and r2.suggested_replies[0].selected_poi_id == "poi-express-tickets"


def test_knowledge_down_means_cannot_answer(ctx):  # A-10
    def down(*a):
        raise ToolUnavailable("rag down")

    r = ask(agent(search_knowledge=down), ctx(), "보증금 환급은 어떻게 해?")
    assert r.status == "cannot_answer"


# ── 안전 · 범위 · 위치 ────────────────────────────────────────────────────────────
def test_unsafe_is_refused(ctx):  # A-11
    r = ask(agent(), ctx(), "선로로 내려가는 지름길 알려줘")
    assert r.status == "out_of_scope" and r.destination is None and r.suggested_replies == []


def test_out_of_scope(ctx):  # A-12
    r = ask(agent(), ctx(), "오늘 날씨 어때?")
    assert r.status == "out_of_scope" and r.suggested_replies


def test_lost_relocalizes(ctx):  # A-13
    r = ask(agent(), ctx(), "길을 잘못 든 것 같아")
    assert r.status == "relocalize" and r.next_action == "locate"


def test_english(ctx):  # A-14
    r = ask(agent(), ctx(lang="en"), "Where do I buy an express ticket?")
    assert r.destination.poi_id == "poi-express-tickets" and r.assistant_message.endswith("where you are.")


# ── 사진과 함께 온 질문 (발매기) ──────────────────────────────────────────────────
def test_ticket_machine_photo(ctx):  # A-15 사진에 발매기 → 승차권 질문으로 이해, 열차를 알면 구매 방법
    class Vision:
        def extract(self, image):
            from hangil_r2.backend.localization_types import VisionFeatures

            return VisionFeatures(image_quality="usable", facilities=["ticket_machine"])

    r = ask(agent(vision=Vision()), ctx(slots=Slots(train_type="express")), "이거 뭐 눌러?", image=b"\xff\xd8\xff")
    assert r.status == "answered" and r.sources


# ── 장애 · 형식 위반 ───────────────────────────────────────────────────────────────
class Rule:  # 백엔드 RuleAgent 대역
    def handle_message(self, c, message, image, poi):
        return AgentResult(
            AgentResponse(
                status="answered", intent="usage_question", assistant_message="rule", slots=c.slots, next_action="none"
            ),
            frozenset(),
        )


@pytest.mark.parametrize("llm", [FakeLLM(down=True), FakeLLM(broken=True)])
def test_llm_failure_uses_fallback(ctx, llm):  # A-16
    assert ask(agent(llm=llm, fallback=Rule()), ctx(), "공항 가는 표").assistant_message == "rule"
    r = ask(agent(llm=llm), ctx(), "공항 가는 표")
    assert r.status == "cannot_answer"


def test_guard_keeps_only_retrieved_sources(ctx):  # A-17 인용한 문서 = 이번에 검색한 문서
    res = agent().handle_message(ctx(), "보증금 환급은 어떻게 해?", None, None)
    assert {s.document_id for s in res.response.sources} <= res.retrieved_document_ids


# ── [선택] 시각표 기준 도착 시각 (기본 꺼짐, D-25) ─────────────────────────────────
def test_arrival_items_when_timetable_given(ctx):  # A-18
    a = agent(get_departures=MockDepartures(), clock=lambda: datetime(2026, 10, 22, 14, 0, tzinfo=KST))
    r = ask(a, ctx(), "잘 모르겠어요")
    assert r.items[0] == "가장 빨리 도착 (인천공항1터미널 기준): 14:20 출발 직통열차, 15:03 도착 예정"
    assert any(s.document_id == "timetable" for s in r.sources) and "좌석" not in " ".join(r.items)


def test_no_arrival_by_default(ctx):  # A-19 공식 시각표 전에는 비교하지 않음
    assert ask(agent(), ctx(), "잘 모르겠어요").items == []


# ── 백엔드 연결 회귀 테스트 ─────────────────────────────────────────────────────
def test_accepts_backends_own_slots_class(ctx):  # A-20 백엔드는 '이름만 같은 다른' Slots 클래스를 넘긴다
    from typing import Literal

    from pydantic import BaseModel

    class BackendSlots(BaseModel):  # app.ai.contracts.Slots 와 같은 필드, 다른 클래스
        train_type: Literal["express", "allstop", "unknown"] = "unknown"
        has_transit_card: Literal["yes", "no", "unknown"] = "unknown"

    class Boom:
        def handle_message(self, *a):
            raise AssertionError("fallback 으로 넘어가면 안 됨")

    c = ctx(slots=BackendSlots(train_type="allstop"))
    r = ask(agent(fallback=Boom()), c, "없어요")
    assert r.destination.poi_id == "poi-allstop-tickets" and r.slots.has_transit_card == "no"


def test_allstop_destination_does_not_cite_express_doc(ctx):  # A-21 다른 열차 문서를 근거로 붙이지 않음
    r = ask(agent(), ctx(slots=Slots(train_type="allstop")), "없어요")
    ids = [s.document_id for s in r.sources]
    assert (
        r.destination.poi_id == "poi-allstop-tickets"
        and "doc-express-ticket" not in ids
        and "doc-allstop-ticket" in ids
    )


# ── 연결 예제 그대로 조립했을 때 대화 첨부 사진을 실제로 읽는지 (이슈 #2) ─────────────────────
class CountingLLM(FakeLLM):
    """read_scene 호출 수와 understand 에 넘어간 photo 를 기록한다."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.scene_calls, self.photos = 0, []

    def read_scene(self, image):
        self.scene_calls += 1
        return super().read_scene(image)

    def understand(self, message, history, language, photo):
        self.photos.append(photo)
        return super().understand(message, history, language, photo)


def test_faq_wiring_reads_message_photo(ctx):  # A-22 FAQ Q1 · handoff 예제와 같은 조립
    from hangil_r2 import LLMVision

    llm = CountingLLM(scene={"image_quality": "usable", "facilities": ["ticket_machine"]})
    vision = LLMVision(llm)
    a = LLMAgent(llm, search_knowledge=MockKnowledgeSearch(), vision=vision, fallback=None)
    r = ask(a, ctx(slots=Slots(train_type="express")), "이거 뭐 눌러?", image=b"\xff\xd8\xff")
    assert llm.scene_calls == 1 and llm.photos[-1] and "ticket_machine" in llm.photos[-1]
    assert r.status == "answered"


def test_missing_vision_is_logged_and_text_still_works(ctx, caplog):  # A-23 vision 을 빠뜨린 연결 → 로그로 드러남
    llm = CountingLLM()
    a = LLMAgent(llm, search_knowledge=MockKnowledgeSearch())
    with caplog.at_level("WARNING", logger="hangil_r2.agent"):
        r = ask(a, ctx(), "공항 가는 티켓 어디서 사?", image=b"\xff\xd8\xff")
    assert llm.scene_calls == 0 and llm.photos[-1] is None
    assert "photo_ignored_no_vision" in caplog.text and r.status == "needs_clarification"


def test_text_only_does_not_warn(ctx, caplog):  # A-24 사진 없는 질문은 그대로, 경고 없음
    with caplog.at_level("WARNING", logger="hangil_r2.agent"):
        r = ask(agent(), ctx(), "공항 가는 티켓 어디서 사?")
    assert r.status == "needs_clarification" and "photo_ignored_no_vision" not in caplog.text




def test_exit_flow_checks_single_use_card_and_return(ctx):
    a = agent()
    history: list[tuple[str, str]] = []
    first_message = "1번 출구로 가고 싶어요"
    first = ask(a, ctx(), first_message)
    assert first.status == "needs_clarification" and first.slots.exit_number == "1"
    assert "1회용 교통카드" in first.assistant_message

    history.extend([("user", first_message), ("assistant", first.assistant_message)])
    has_card_message = "가지고 있어요"
    has_card = ask(a, ctx(slots=first.slots, history=history), has_card_message)
    assert has_card.slots.has_single_use_card == "yes"
    assert "반납했나요" in has_card.assistant_message

    history.extend([("user", has_card_message), ("assistant", has_card.assistant_message)])
    not_returned_message = "아직 안 했어요"
    not_returned = ask(a, ctx(slots=has_card.slots, history=history), not_returned_message)
    assert not_returned.slots.single_use_card_returned == "no"
    assert not_returned.destination.poi_id == "poi-single-use-card-refund"

    history.extend([("user", not_returned_message), ("assistant", not_returned.assistant_message)])
    returned = ask(a, ctx(slots=not_returned.slots, history=history), "반납했어요")
    assert returned.slots.single_use_card_returned == "yes"
    assert returned.destination.poi_id == "poi-exit-1"


def test_arex_ticket_purchase_routes_to_ticket_counter(ctx):
    a = agent()
    first_message = "공항철도 표를 구입하고 싶어요"
    first = ask(a, ctx(), first_message)
    assert first.status == "needs_clarification" and first.intent == "purchase_arex_ticket"

    history = [("user", first_message), ("assistant", first.assistant_message)]
    result = ask(a, ctx(slots=first.slots, history=history), "직통열차")
    assert result.intent == "purchase_arex_ticket"
    assert result.destination.poi_id == "poi-express-tickets"


def test_arex_boarding_checks_purchase_then_chains_to_platform(ctx):
    a = agent()
    history: list[tuple[str, str]] = []
    first_message = "공항철도 타러 가고 싶어요"
    first = ask(a, ctx(), first_message)
    history.extend([("user", first_message), ("assistant", first.assistant_message)])

    train_message = "직통열차"
    train = ask(a, ctx(slots=first.slots, history=history), train_message)
    assert train.intent == "board_arex" and "구입했나요" in train.assistant_message
    history.extend([("user", train_message), ("assistant", train.assistant_message)])

    no_ticket_message = "아직 안 샀어요"
    no_ticket = ask(a, ctx(slots=train.slots, history=history), no_ticket_message)
    assert no_ticket.slots.has_ticket == "no"
    assert no_ticket.destination.poi_id == "poi-express-tickets"
    history.extend([("user", no_ticket_message), ("assistant", no_ticket.assistant_message)])

    bought = ask(a, ctx(slots=no_ticket.slots, history=history), "구입했어요")
    assert bought.slots.has_ticket == "yes"
    assert bought.intent == "board_arex"
    assert bought.destination.poi_id == "poi-express-platform"


def test_irrelevant_final_response_becomes_cannot_answer(ctx):
    llm = FakeLLM(relevant=False)
    response = ask(agent(llm=llm), ctx(), "1번 출구로 가고 싶어요")
    assert llm.verify_calls == 1
    assert response.status == "cannot_answer"
    assert response.destination is None
