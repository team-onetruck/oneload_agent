"""LLM 기반 대화 Agent — 백엔드 `app.ai.contracts.Agent` 규격 구현.

백엔드 연결 (oneload-backend README "Swapping one in is a change to create_app() defaults"):
    vision = LLMVision(llm)  # 같은 Vision 을 Agent(대화 사진) 와 create_app(사진 위치) 양쪽에 넣는다
    create_app(agent=LLMAgent(llm, vision=vision, fallback=RuleAgent()), vision=vision)

흐름 (노드 이름 = docs/STEP05.md 그림의 단계 이름)

    START → read_photo → understand → update_slots ─┬─ deny ────────────→ END  (위험 · 범위 밖)
                                                    ├─ relocalize ──────→ END  ("길을 잘못 들었어")
                                                    ├─ select_poi ──────→ END  (화면에서 고른 장소)
                                                    ├─ ask_train ───────→ END  (직통 / 일반 되묻기)
                                                    ├─ explain_options ─→ END  ("잘 모르겠어요" → 자료로 차이 설명)
                                                    ├─ ask_card ────────→ END  (일반열차 → 교통카드 있나요?)
                                                    ├─ answer ──────────→ END  (이용 방법: 자료 있으면 답변, 없으면 cannot_answer)
                                                    ├─ arex_destination → END  (열차 · 카드에 맞는 매표소 / 승강장)
                                                    └─ find_poi ────────→ END  (그 밖의 장소 검색)

설계 원칙
- LLM 은 '이해' 와 '자료 기반 문장' 만 한다. 목적지 · 다음 단계 결정은 코드가 지도 · 슬롯으로 한다.
- 목적지는 ctx.map 의 verified POI 에서만 고른다 (백엔드 guard 가 한 번 더 막는다).
- 이용 방법은 검색된 자료가 있을 때만 답한다. 없으면 cannot_answer — 요금 · 시간을 지어내지 않는다.
- 자료가 검색돼도 그 안에 질문의 답이 없으면(compose 가 NO_ANSWER) cannot_answer. 출처는 답에 인용한 자료만 붙인다.
- 대화 상태는 Agent 가 들고 있지 않다. 슬롯은 응답의 slots 로 돌려주고, 백엔드가 세션에 저장해 다음 ctx.slots 로 준다.
- LLM 장애 · 형식 위반이면 fallback(백엔드 RuleAgent)으로 넘긴다. fallback 이 없으면 cannot_answer.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError

from .backend.contracts import (
    Agent,
    AgentContext,
    AgentResponse,
    AgentResult,
    Slots,
    Source,
    VisionExtractor,
)
from .backend.mapdata import Poi
from .compat import vision_unavailable_types
from .knowledge import DepartureSearch, KnowledgeHit, KnowledgeSearch, ToolUnavailable
from .llm import LLM, NO_ANSWER, LLMError, LLMUnavailable

log = logging.getLogger("hangil_r2.agent")

KST = timezone(timedelta(hours=9))
DEFAULT_TERMINAL = "인천공항1터미널"  # 시각표 기준 (백엔드 Slots 에 행선지 슬롯이 없어 고정, 문장에 항상 표시)

MSG = {  # 백엔드 Lang 은 en · ko 두 가지
    "deny": (
        "For your safety I can't help with that. Please ask station staff.",
        "안전을 위해 그 요청은 안내할 수 없어요. 역 직원에게 문의해 주세요.",
    ),
    "scope": (
        "I can help with the Airport Railroad at Seoul Station: tickets, transit cards and the way there.",
        "지금은 서울역 공항철도 구역을 도와드려요. 승차권 · 교통카드 · 가는 길을 물어보세요.",
    ),
    "relocalize": (
        "No problem. I won't guess where you are; let's check again with a photo of a nearby sign.",
        "괜찮아요. 위치를 짐작하지 않고, 주변 안내판 사진으로 다시 확인할게요.",
    ),
    "ask_train": (
        "Two AREX trains go to Incheon Airport. Which one do you want?",
        "인천공항까지는 공항철도 열차가 두 종류예요. 어떤 열차로 갈까요?",
    ),
    "pick_train": ("Which train would you like?", "어떤 열차로 안내할까요?"),
    "no_compare": (
        "I don't have a verified comparison, so I won't compare them. Which train would you like?",
        "확인된 비교 자료가 없어서 비교하지 않을게요. 어떤 열차로 갈까요?",
    ),
    "ask_card": (
        "For the All-stop train you tap a transit card at the gate. Do you have one?",
        "일반열차는 개찰구에서 교통카드를 찍어요. 교통카드가 있나요?",
    ),
    "no_evidence": (
        "I don't have verified information on that, so I won't guess. Please ask the information center.",
        "확인된 자료가 없어서 추측하지 않을게요. 역 안내센터에 문의해 주세요.",
    ),
    "unknown_place": (
        "I couldn't find that place on the verified map. Where would you like to go?",
        "확인된 지도에서 그 장소를 찾지 못했어요. 어디로 가고 싶으세요?",
    ),
    "choose_place": ("Which one do you mean?", "어느 곳을 찾으세요?"),
    "dest": ("I'll take you to the {name}, on {floor}.", "{name}(으)로 안내할게요. {floor}에 있어요."),
    "go_now": ("You're already located, so we can go now.", "위치가 확인돼 있어 바로 출발할 수 있어요."),
    "locate_first": (
        "First, take a photo of a nearby sign to confirm where you are.",
        "먼저 주변 안내판을 찍어서 지금 위치를 확인해 주세요.",
    ),
    "go_to": ("Go to the {name}", "{name}(으)로 가기"),
    "fallback": (
        "I couldn't make an answer right now. Please try again shortly.",
        "지금은 답변을 만들지 못했어요. 잠시 후 다시 시도해 주세요.",
    ),
    "unverified_compare": ("Fares and travel times aren't verified yet.", "요금 · 소요시간은 아직 확인 전이에요."),
    "fastest": (
        "Earliest arrival ({dest}): {dep} {train}, arrives {arr}.",
        "가장 빨리 도착 ({dest} 기준): {dep} 출발 {train}, {arr} 도착 예정",
    ),
    "next_other": ("Next {train}: leaves {dep}, arrives {arr}.", "다음 {train}: {dep} 출발, {arr} 도착 예정"),
    "ask_exit_number": ("Which numbered exit do you want?", "몇 번 출구로 나가고 싶으세요?"),
    "ask_single_use_card": (
        "Do you currently have a single-use transit card?",
        "지금 1회용 교통카드를 가지고 있나요?",
    ),
    "ask_card_returned": (
        "Have you returned the single-use transit card?",
        "1회용 교통카드를 반납했나요?",
    ),
    "unsupported_exit": (
        "That exit isn't in the verified map yet. Choose Exit 1 or ask station staff.",
        "해당 출구는 아직 확인된 지도에 없어요. 1번 출구를 선택하거나 역 직원에게 문의해 주세요.",
    ),
    "ask_ticket": (
        "Before I guide you to the platform: have you already bought the ticket or travel card you need?",
        "승강장으로 안내하기 전에 확인할게요. 필요한 승차권이나 교통카드를 이미 구입했나요?",
    ),
    "buy_first": (
        "You need to buy the ticket or travel card first.",
        "먼저 필요한 승차권이나 교통카드를 구입해야 해요.",
    ),
    "return_first": (
        "Return the single-use transit card first, then I can guide you to the exit.",
        "먼저 1회용 교통카드를 반납한 뒤 출구로 안내할게요.",
    ),
}
BUTTONS = {
    "express": ("Express", "직통열차"),
    "allstop": ("All-stop", "일반열차"),
    "unsure": ("Not sure", "잘 모르겠어요"),
    "card_yes": ("I have a transit card", "교통카드 있어요"),
    "card_no": ("I don't have one", "없어요"),
    "card_what": ("What's a transit card?", "교통카드가 뭐예요?"),
    "airport": ("Incheon Airport", "인천공항"),
    "pay": ("How do I pay?", "돈은 어떻게 내요?"),
    "exit_1": ("Exit 1", "1번 출구"),
    "single_yes": ("I have one", "가지고 있어요"),
    "single_no": ("I don't have one", "없어요"),
    "returned_yes": ("I returned it", "반납했어요"),
    "returned_no": ("Not yet", "아직 안 했어요"),
    "ticket_yes": ("I bought it", "구입했어요"),
    "ticket_no": ("Not yet", "아직 안 샀어요"),
}
TRAIN_NAME = {"express": ("Express train", "직통열차"), "allstop": ("All-stop train", "일반열차")}
DEST_EN = {"인천공항1터미널": "Incheon Airport T1", "인천공항2터미널": "Incheon Airport T2"}


def say(pair: tuple[str, str], lang: str, **kw) -> str:
    return (pair[0] if lang == "en" else pair[1]).format(**kw)


class State(TypedDict, total=False):
    ctx: AgentContext
    message: str
    image: bytes | None
    selected_poi_id: str | None
    photo: str | None  # 사진에서 읽힌 내용 요약 (understand 에 전달)
    u: dict  # understand 결과
    slots: Slots
    new_slot: bool  # 이번 말로 슬롯이 채워졌나 (짧은 대답 이어 가기)
    retrieved: set  # 이번 턴에 실제로 검색된 문서 id (백엔드 guard 가 인용을 이 안으로 제한)
    result: dict  # 최종 AgentResponse


class LLMAgent:
    def __init__(
        self,
        llm: LLM,
        search_knowledge: KnowledgeSearch | None = None,
        vision: VisionExtractor | None = None,
        fallback: Agent | None = None,
        get_departures: DepartureSearch | None = None,
        origin_station: str = "서울역",
        clock: Callable[[], datetime] | None = None,
    ):
        """search_knowledge 가 없으면 이용 방법 질문은 cannot_answer. get_departures 는 공식 시각표가 생기기 전까지 넘기지 않는다."""
        self.llm, self.search_knowledge, self.vision, self.fallback = llm, search_knowledge, vision, fallback
        self.get_departures, self.origin_station = get_departures, origin_station
        self.clock = clock or (lambda: datetime.now(KST))
        self.graph = self._build()

    # ── 진입점 (백엔드 POST /messages 가 호출) ─────────────────────────
    def handle_message(
        self, ctx: AgentContext, message: str, image: bytes | None, selected_poi_id: str | None
    ) -> AgentResult:
        try:
            out = self.graph.invoke(
                {
                    "ctx": ctx,
                    "message": (message or "").strip(),
                    "image": image,
                    "selected_poi_id": selected_poi_id,
                    "retrieved": set(),
                }
            )
            payload = {**out["result"], "slots": out["slots"].model_dump()}
            question = (message or "").strip() or f"selected_poi_id={selected_poi_id}"
            if not self.llm.verify(question, json.dumps(payload, ensure_ascii=False), ctx.language):
                log.warning("response_relevance_rejected intent=%s", payload.get("intent"))
                payload = {
                    "status": "cannot_answer",
                    "intent": payload.get("intent", "out_of_scope"),
                    "assistant_message": say(MSG["no_evidence"], ctx.language),
                    "slots": out["slots"].model_dump(),
                    "next_action": "none",
                }
                out["retrieved"] = set()
            response = AgentResponse.model_validate(payload)
            return AgentResult(response, frozenset(out["retrieved"]))
        except (LLMError, ValidationError) as exc:  # LLMUnavailable 포함
            log.warning("agent_fallback err=%s: %s", type(exc).__name__, exc)
            if self.fallback is not None:
                return self.fallback.handle_message(ctx, message, image, selected_poi_id)
            return AgentResult(
                AgentResponse(
                    status="cannot_answer",
                    intent="out_of_scope",
                    assistant_message=say(MSG["fallback"], ctx.language),
                    slots=ctx.slots.model_dump(),
                    next_action="none",
                ),
                frozenset(),
            )

    # ── 그래프 ───────────────────────────────────────────────────────
    def _build(self):
        g = StateGraph(State)
        names = (
            "read_photo",
            "understand",
            "update_slots",
            "deny",
            "relocalize",
            "select_poi",
            "ask_train",
            "explain_options",
            "ask_card",
            "ask_exit_number",
            "ask_single_use_card",
            "ask_card_returned",
            "ask_ticket",
            "answer",
            "purchase_destination",
            "board_destination",
            "exit_destination",
            "card_refund_destination",
            "arex_destination",
            "find_poi",
        )
        for name in names:
            g.add_node(name, getattr(self, f"_{name}"))
        g.add_edge(START, "read_photo")
        g.add_edge("read_photo", "understand")
        g.add_edge("understand", "update_slots")
        g.add_conditional_edges("update_slots", self._route, list(names[3:]))
        for n in names[3:]:
            g.add_edge(n, END)
        return g.compile()

    # ── 노드: 이해 ───────────────────────────────────────────────────
    def _read_photo(self, s: State):
        if not s.get("image"):
            return {"photo": None}
        if self.vision is None:
            # 연결 실수(LLMAgent 에 vision 을 안 넘김)를 로그로 드러낸다 — 응답은 200 이라 겉으로는 안 보임 (이슈 #2)
            log.warning("photo_ignored_no_vision: LLMAgent(vision=...) 가 없어 대화 사진을 읽지 않음")
            return {"photo": None}
        try:
            f = self.vision.extract(s["image"])
        except vision_unavailable_types():  # 사본 · 백엔드 클래스 모두 (compat.py)
            return {"photo": None}  # 사진 없이 말로만 처리
        texts = ", ".join(t.text for t in f.texts) or "-"
        return {"photo": f"상태: {f.image_quality} / 글자: {texts} / 시설: {', '.join(f.facilities) or '-'}"}

    def _understand(self, s: State):
        if s.get("selected_poi_id"):  # 화면 버튼으로 고른 장소 → 해석 불필요
            return {
                "u": {
                    "intent": "navigate_to_poi",
                    "topic": None,
                    "train_type": None,
                    "has_transit_card": None,
                    "exit_number": None,
                    "has_single_use_card": None,
                    "single_use_card_returned": None,
                    "has_ticket": None,
                    "unsure": False,
                    "blocked": False,
                    "poi_query": None,
                    "knowledge_query": None,
                }
            }
        ctx = s["ctx"]
        return {"u": self.llm.understand(s["message"], ctx.recent_messages, ctx.language, s.get("photo"))}

    def _update_slots(self, s: State):
        # 백엔드는 자기 Slots 클래스로 넘긴다. 사본 클래스와 이름만 같고 다른 클래스라 dict 를 거쳐 바꾼다.
        u, slots = s["u"], Slots.model_validate(s["ctx"].slots.model_dump())
        new = False
        for field in (
            "train_type",
            "has_transit_card",
            "exit_number",
            "has_single_use_card",
            "single_use_card_returned",
            "has_ticket",
        ):
            value = u.get(field)
            if value is not None and value != getattr(slots, field):
                setattr(slots, field, value)
                new = True
        return {"slots": slots, "new_slot": new}

    def _route(self, s: State) -> str:
        u, slots = s["u"], s["slots"]
        if u["blocked"]:
            return "deny"
        if u["intent"] == "report_off_route":
            return "relocalize"
        if s.get("selected_poi_id"):
            return "select_poi"
        history = " ".join(text.lower() for _, text in s["ctx"].recent_messages[-4:])
        exit_flow = u["intent"] == "navigate_to_exit" or (
            u["intent"] == "out_of_scope"
            and slots.exit_number is not None
            and (u.get("has_single_use_card") or u.get("single_use_card_returned") or "출구" in history or "exit" in history)
        )
        if exit_flow:
            if slots.exit_number is None:
                return "ask_exit_number"
            if self._exit_poi(s["ctx"], slots.exit_number) is None:
                return "ask_exit_number"
            if slots.has_single_use_card == "unknown":
                return "ask_single_use_card"
            if slots.has_single_use_card == "yes" and slots.single_use_card_returned == "unknown":
                return "ask_card_returned"
            if slots.has_single_use_card == "yes" and slots.single_use_card_returned == "no":
                return "card_refund_destination"
            return "exit_destination"
        train_unknown = slots.train_type == "unknown"
        if u["intent"] in ("usage_question", "payment_question"):
            if u["topic"] == "tickets" and train_unknown:
                return "explain_options" if u["unsure"] else "ask_train"
            return "answer"
        board_pending = any(word in history for word in ("타러", "승강장", "탑승", "board", "platform"))
        board_flow = u["intent"] == "board_arex" or u.get("has_ticket") is not None or (
            u["intent"] == "out_of_scope" and board_pending and s.get("new_slot")
        )
        if board_flow:
            if train_unknown:
                return "ask_train"
            if slots.has_ticket == "unknown":
                return "ask_ticket"
            return "purchase_destination" if slots.has_ticket == "no" else "board_destination"
        purchase_pending = any(word in history for word in ("구입", "구매", "매표", "buy", "purchase"))
        purchase_flow = u["intent"] == "purchase_arex_ticket" or (
            u["intent"] == "out_of_scope" and purchase_pending and s.get("new_slot")
        )
        if purchase_flow:
            if train_unknown:
                return "ask_train"
            if slots.train_type == "allstop" and slots.has_transit_card == "unknown":
                return "ask_card"
            return "purchase_destination"
        arex = (
            u["intent"] == "ticket_purchase_and_navigation"
            or u["topic"] in ("tickets", "platform")
            or (s.get("new_slot") and (u.get("train_type") or u.get("has_transit_card")))
            or u["unsure"]
        )
        if arex:
            if train_unknown:
                return "explain_options" if u["unsure"] else "ask_train"
            if slots.train_type == "allstop" and slots.has_transit_card == "unknown" and u["topic"] != "platform":
                return "ask_card"
            return "arex_destination"
        if u["intent"] == "navigate_to_poi":
            return "find_poi"
        return "deny"

    # ── 노드: 응답 ───────────────────────────────────────────────────
    def _deny(self, s: State):
        lang = s["ctx"].language
        blocked = s["u"]["blocked"]
        return {
            "result": {
                "status": "out_of_scope",
                "intent": "out_of_scope",
                "assistant_message": say(MSG["deny" if blocked else "scope"], lang),
                "suggested_replies": [] if blocked else self._buttons(lang, "airport", "pay"),
                "next_action": "none",
            }
        }

    def _relocalize(self, s: State):
        return {
            "result": {
                "status": "relocalize",
                "intent": "report_off_route",
                "assistant_message": say(MSG["relocalize"], s["ctx"].language),
                "next_action": "locate",
            }
        }

    def _select_poi(self, s: State):
        ctx, slots = s["ctx"], s["slots"].model_copy()
        poi = ctx.map.verified_poi(s["selected_poi_id"])
        if poi is None:
            return {"result": self._clarify(ctx.language, MSG["unknown_place"], "navigate_to_poi")}
        if poi.train_type:
            slots.train_type = poi.train_type
        return {"slots": slots, "result": self._destination(s, poi, "navigate_to_poi")}

    def _ask_train(self, s: State):
        lang = s["ctx"].language
        intent = s["u"]["intent"] if s["u"]["intent"] != "out_of_scope" else "ticket_purchase_and_navigation"
        return {
            "result": {
                **self._clarify(lang, MSG["ask_train"], intent),
                "suggested_replies": self._buttons(lang, "express", "allstop", "unsure"),
            }
        }

    def _explain_options(self, s: State):
        """'잘 모르겠어요' → 자료로 차이 설명 (+ 시각표가 있으면 도착 시각) 후 다시 묻기."""
        lang = s["ctx"].language
        hits = self._search(s, say(("Express vs All-stop difference", "직통열차 일반열차 차이"), lang))
        items, sources, notes = [], [], []
        if answer := self._grounded(s, hits):
            message, sources = f"{answer[0]} {say(MSG['pick_train'], lang)}", answer[1]
        else:
            message, notes = say(MSG["no_compare"], lang), [say(MSG["unverified_compare"], lang)]
        arrival = self._arrival_items(s)
        if arrival:
            items, src = arrival
            sources.append(src)
            s["retrieved"].add(src["document_id"])
        return {
            "result": {
                "status": "needs_clarification",
                "intent": "ticket_purchase_and_navigation",
                "assistant_message": message[:800],
                "items": items,
                "unverified_notes": notes,
                "suggested_replies": self._buttons(lang, "express", "allstop"),
                "sources": sources,
                "next_action": "reply",
            },
            "retrieved": s["retrieved"],
        }

    def _ask_card(self, s: State):
        lang = s["ctx"].language
        intent = s["u"]["intent"]
        if intent not in ("purchase_arex_ticket", "ticket_purchase_and_navigation"):
            intent = "payment_question"
        return {
            "result": {
                **self._clarify(lang, MSG["ask_card"], intent),
                "suggested_replies": self._buttons(lang, "card_yes", "card_no", "card_what"),
            }
        }

    def _ask_exit_number(self, s: State):
        lang = s["ctx"].language
        pair = MSG["unsupported_exit"] if s["slots"].exit_number else MSG["ask_exit_number"]
        return {
            "result": {
                **self._clarify(lang, pair, "navigate_to_exit"),
                "suggested_replies": self._buttons(lang, "exit_1"),
            }
        }

    def _ask_single_use_card(self, s: State):
        lang = s["ctx"].language
        return {
            "result": {
                **self._clarify(lang, MSG["ask_single_use_card"], "navigate_to_exit"),
                "suggested_replies": self._buttons(lang, "single_yes", "single_no"),
            }
        }

    def _ask_card_returned(self, s: State):
        lang = s["ctx"].language
        return {
            "result": {
                **self._clarify(lang, MSG["ask_card_returned"], "navigate_to_exit"),
                "suggested_replies": self._buttons(lang, "returned_yes", "returned_no"),
            }
        }

    def _ask_ticket(self, s: State):
        lang = s["ctx"].language
        return {
            "result": {
                **self._clarify(lang, MSG["ask_ticket"], "board_arex"),
                "suggested_replies": self._buttons(lang, "ticket_yes", "ticket_no"),
            }
        }

    def _answer(self, s: State):
        """이용 방법 · 결제 질문: 자료가 있으면 인용해서 답하고, 없으면 cannot_answer."""
        ctx, u, lang = s["ctx"], s["u"], s["ctx"].language
        hits = self._search(s, self._with_slots(u["knowledge_query"] or s["message"], s["slots"], lang))
        if u["topic"] == "fare":  # '직통열차' 같은 단어로 구매 안내 문서가 걸려도 요금 근거는 아니다
            hits = [h for h in hits if h.scope == "fare"]
        answer = self._grounded(s, hits)
        if answer is None:
            return {
                "result": {
                    "status": "cannot_answer",
                    "intent": u["intent"],
                    "assistant_message": say(MSG["no_evidence"], lang),
                    "next_action": "none",
                },
                "retrieved": s["retrieved"],
            }
        replies = []
        if u["topic"] == "tickets" and s["slots"].train_type != "unknown":  # 표 사는 곳으로 바로 가는 버튼
            poi = self._arex_poi(ctx, s["slots"].train_type, "tickets")
            if poi:
                replies = [{"label": say(MSG["go_to"], lang, name=poi.name(lang))[:40], "selected_poi_id": poi.poi_id}]
        return {
            "result": {
                "status": "answered",
                "intent": u["intent"],
                "assistant_message": answer[0],
                "sources": answer[1],
                "suggested_replies": replies,
                "next_action": "none",
            },
            "retrieved": s["retrieved"],
        }

    def _purchase_destination(self, s: State):
        ctx, slots = s["ctx"], s["slots"]
        poi = self._arex_poi(ctx, slots.train_type, "tickets")
        if poi is None:
            return {"result": self._clarify(ctx.language, MSG["unknown_place"], "purchase_arex_ticket")}
        hits = self._search(
            s, self._with_slots(say(("how to buy a ticket", "승차권 구매 방법"), ctx.language), slots, ctx.language)
        )
        answer = self._grounded(s, hits)
        if s["u"]["intent"] == "board_arex" or s["u"].get("has_ticket") == "no":
            prefix = say(MSG["buy_first"], ctx.language)
            answer = (f"{prefix} {answer[0]}" if answer else prefix, answer[1] if answer else [])
            intent = "board_arex"
        else:
            intent = "purchase_arex_ticket"
        return {"result": self._destination(s, poi, intent, answer), "retrieved": s["retrieved"]}

    def _board_destination(self, s: State):
        ctx, slots = s["ctx"], s["slots"]
        poi = self._arex_poi(ctx, slots.train_type, "platform")
        if poi is None:
            return {"result": self._clarify(ctx.language, MSG["unknown_place"], "board_arex")}
        return {"result": self._destination(s, poi, "board_arex")}

    def _exit_destination(self, s: State):
        ctx, slots = s["ctx"], s["slots"]
        poi = self._exit_poi(ctx, slots.exit_number)
        if poi is None:
            return {"result": self._clarify(ctx.language, MSG["unsupported_exit"], "navigate_to_exit")}
        return {"result": self._destination(s, poi, "navigate_to_exit")}

    def _card_refund_destination(self, s: State):
        ctx = s["ctx"]
        poi = next((p for p in ctx.map.pois if p.verified and p.category == "card_refund"), None)
        if poi is None:
            return {"result": self._clarify(ctx.language, MSG["unknown_place"], "navigate_to_exit")}
        answer = (say(MSG["return_first"], ctx.language), [])
        return {"result": self._destination(s, poi, "navigate_to_exit", answer)}

    def _arex_destination(self, s: State):
        ctx, slots, u = s["ctx"], s["slots"], s["u"]
        if slots.train_type == "allstop":
            category = "platform" if (slots.has_transit_card == "yes" or u["topic"] == "platform") else "tickets"
        else:
            category = "platform" if u["topic"] == "platform" else "tickets"
        poi = self._arex_poi(ctx, slots.train_type, category)
        if poi is None:
            return {"result": self._clarify(ctx.language, MSG["unknown_place"], "navigate_to_poi")}
        intent = "payment_question" if s.get("new_slot") and u["has_transit_card"] else "ticket_purchase_and_navigation"
        answer = None
        if category == "tickets":  # 표 사는 곳으로 갈 때는 사는 방법 자료도 함께
            hits = self._search(
                s, self._with_slots(say(("how to buy a ticket", "승차권 구매 방법"), ctx.language), slots, ctx.language)
            )
            answer = self._grounded(s, hits)
        return {"result": self._destination(s, poi, intent, answer), "retrieved": s["retrieved"]}

    def _find_poi(self, s: State):
        ctx, lang = s["ctx"], s["ctx"].language
        pois = ctx.map.search_pois(s["u"]["poi_query"] or "") if s["u"]["poi_query"] else []
        if len(pois) == 1:
            return {"result": self._destination(s, pois[0], "navigate_to_poi")}
        if not pois:
            return {"result": self._clarify(lang, MSG["unknown_place"], "navigate_to_poi")}
        replies = [{"label": p.name(lang)[:40], "selected_poi_id": p.poi_id} for p in pois[:3]]
        return {"result": {**self._clarify(lang, MSG["choose_place"], "navigate_to_poi"), "suggested_replies": replies}}

    # ── 도우미 ───────────────────────────────────────────────────────
    @staticmethod
    def _clarify(lang: str, pair: tuple[str, str], intent: str) -> dict:
        return {
            "status": "needs_clarification",
            "intent": intent,
            "assistant_message": say(pair, lang),
            "next_action": "reply",
        }

    @staticmethod
    def _buttons(lang: str, *keys: str) -> list[dict]:
        return [{"label": say(BUTTONS[k], lang), "selected_poi_id": None} for k in keys]

    @staticmethod
    def _arex_poi(ctx: AgentContext, train_type: str, category: str) -> Poi | None:
        """열차 종류 · 분류로 지도의 verified POI 를 찾는다 (POI id 를 코드에 박지 않음)."""
        return next(
            (p for p in ctx.map.pois if p.verified and p.train_type == train_type and p.category == category), None
        )

    @staticmethod
    def _exit_poi(ctx: AgentContext, exit_number: str | None) -> Poi | None:
        if not exit_number:
            return None
        needle = f"{exit_number}번 출구"
        return next(
            (
                p
                for p in ctx.map.pois
                if p.verified
                and p.category == "exit"
                and (needle in (p.name.ko, *p.aliases) or f"Exit {exit_number}" in (p.name.en, *p.aliases))
            ),
            None,
        )

    @staticmethod
    def _with_slots(query: str, slots: Slots, lang: str) -> str:
        """검색어에 슬롯 이름을 붙인다. 예: '승차권 구매 방법' + express → '직통열차 승차권 구매 방법'."""
        if slots.train_type in TRAIN_NAME:
            name = say(TRAIN_NAME[slots.train_type], lang)
            if name.lower() not in query.lower():
                query = f"{name} {query}"
        return query

    def _search(self, s: State, query: str) -> list[KnowledgeHit]:
        """R3 검색. 도구가 없거나 장애면 빈 결과 (근거 없음 → 보류)."""
        if self.search_knowledge is None:
            return []
        ctx = s["ctx"]
        try:
            hits = self.search_knowledge(
                query, ctx.language, {"map_id": ctx.map.map_id, "slots": s["slots"].model_dump()}
            )
        except ToolUnavailable as exc:
            log.warning("knowledge_unavailable %s", exc)
            return []
        hits = [h for h in hits if h.excerpt.strip()][:3]
        s["retrieved"].update(h.document_id for h in hits)
        return hits

    def _grounded(self, s: State, hits: list[KnowledgeHit]) -> tuple[str, list[dict]] | None:
        """자료로 답할 수 있으면 (근거 문장, 인용한 자료의 출처), 아니면 None (LLM 장애는 위로 올림).

        None 인 경우: 자료 없음 · compose 가 NO_ANSWER(자료에 질문의 답이 없음) · 인용 번호가 없거나 범위 밖.
        검색된 자료라도 인용하지 않았으면 출처로 붙이지 않는다 (#5).
        """
        if not hits:
            return None
        try:
            text = self.llm.compose(s["message"] or "-", hits, s["ctx"].language).strip()
            if text.startswith(NO_ANSWER):
                log.info("compose_no_answer hits=%s", [h.document_id for h in hits])
                return None
            cited = sorted({int(n) for n in re.findall(r"\[(\d+)\]", text)})
            if not cited or not set(cited) <= set(range(1, len(hits) + 1)):
                raise LLMError(f"인용 번호 위반: {cited}")
            return text[:600], self._sources([hits[n - 1] for n in cited])
        except LLMUnavailable:
            raise
        except LLMError as exc:
            log.warning("compose_rejected %s", exc)
            return None

    @staticmethod
    def _sources(hits: list[KnowledgeHit]) -> list[dict]:
        return [
            Source(
                document_id=h.document_id, title=h.title, source_url=h.source_url, verified_at=h.verified_at
            ).model_dump()
            for h in hits[:3]
        ]

    def _destination(self, s: State, poi: Poi, intent: str, answer: tuple[str, list] | None = None) -> dict:
        ctx, lang = s["ctx"], s["ctx"].language
        floor = ctx.map.node_by_id[poi.node_id].floor_id
        msg = say(MSG["dest"], lang, name=poi.name(lang), floor=floor)
        here = ctx.confirmed_node_id is not None
        msg += " " + say(MSG["go_now" if here else "locate_first"], lang)
        sources = []
        if answer:
            msg, sources = f"{answer[0]} {msg}", answer[1]
        return {
            "status": "destination_set",
            "intent": intent,
            "assistant_message": msg[:800],
            "destination": {"poi_id": poi.poi_id},
            "sources": sources,
            "next_action": "navigate" if here else "locate",
        }

    def _arrival_items(self, s: State) -> tuple[list[str], dict] | None:
        """[선택] 시각표 기준 가장 빨리 '도착' 하는 열차 (출발이 아니라 도착 시각으로 비교). 남은 좌석은 다루지 않음."""
        if self.get_departures is None:
            return None
        lang, now = s["ctx"].language, self.clock()
        try:
            deps = [
                d for d in self.get_departures(self.origin_station, DEFAULT_TERMINAL, now, 3) if d.departs_at >= now
            ]
        except (ToolUnavailable, ValidationError) as exc:
            log.warning("departures_unavailable %s", exc)
            return None
        if not deps:
            return None
        hm = lambda t: t.astimezone(KST).strftime("%H:%M")  # noqa: E731
        dest = DEFAULT_TERMINAL if lang == "ko" else DEST_EN[DEFAULT_TERMINAL]
        first = min(deps, key=lambda d: d.arrives_at)
        items = [
            say(
                MSG["fastest"],
                lang,
                dest=dest,
                dep=hm(first.departs_at),
                train=say(TRAIN_NAME[first.train_type], lang),
                arr=hm(first.arrives_at),
            )
        ]
        others = [d for d in deps if d.train_type != first.train_type]
        if others:
            o = min(others, key=lambda d: d.arrives_at)
            items.append(
                say(
                    MSG["next_other"],
                    lang,
                    train=say(TRAIN_NAME[o.train_type], lang),
                    dep=hm(o.departs_at),
                    arr=hm(o.arrives_at),
                )
            )
        src = Source(
            document_id="timetable",
            title=first.source_title,
            source_url=first.source_url,
            verified_at=first.verified_at,
        ).model_dump()
        return items, src
