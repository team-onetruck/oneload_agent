"""AI Agent — handle_message (명세 v0.3 10장 · 8.5).

흐름 (노드 이름 = docs/STEP05.md 그림의 단계 이름)

    START → understand → fill_slots ─┬─ deny ──────────────────────────→ END  (위험 · 범위 밖)
                                     ├─ lost ──────────────────────────→ END  (위치 재확인 요청)
                                     ├─ verify_selected ───────────────→ END  (화면에서 고른 POI 검증)
                                     ├─ ask_slot ──────────────────────→ END  (빠진 슬롯 되묻기)
                                     ├─ explain_options ───────────────→ END  ("잘 모르겠어요" → 차이 설명 후 다시 묻기)
                                     ├─ find_poi ──────────────────────→ END  (목적지 확정 / 장소 되묻기)
                                     └─ retrieve → compose ─┬──────────→ END  (이용 방법 답변 / 보류)
                                                            └─ find_poi → END  (답변 + 목적지, 예: 티켓 구매)

설계 원칙
- 목적지는 search_pois 가 돌려준 verified POI 에서만 고른다. LLM 이 poi_id 를 만들지 않는다.
- 이용 방법은 search_knowledge 근거가 있을 때만 답한다. 없으면 needs_review (추측 금지).
- 승차권 질문은 열차 종류(train_type) 슬롯이 채워져야 답한다. 비어 있으면 되묻고,
  "잘 모르겠어요" 면 R3 자료로 차이를 설명한 뒤 다시 묻는다.
- 대화 상태는 Agent 가 들고 있지 않다. 되묻는 중인 의도 · 채운 슬롯은 AgentResponse.slots 로 내보내고,
  Backend 가 다음 요청의 SessionContext.slots 로 돌려준다.
- Tool · LLM 장애는 삼키지 않고 올린다 → Backend 가 503 AI_SERVICE_UNAVAILABLE.
"""
from __future__ import annotations

import logging
import os
import re
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError

from .contracts import AgentResponse, KnowledgeHit, POI, SessionContext, Source
from .llm import LLM, LLMError, LLMUnavailable
from .tools import KnowledgeSearch, PoiSearch, ToolUnavailable

log = logging.getLogger("hangil_r2.agent")

MAX_CLARIFY = int(os.getenv("MAX_CLARIFY", "2"))  # 되묻기 상한. 넘으면 목록 직접 선택 유도
MAX_SUGGEST = 3  # 추천 답변 중 POI 버튼 수 (+ '잘 모르겠어요' 1개)
REQUIRED_SLOTS = {"ticket": ("train_type",)}  # 주제별 필수 슬롯. 시연 범위 확정 후 추가
PENDING_KEYS = ("pending_intent", "pending_topic")

# 고정 문구. ja · zh 는 en 으로 대체 (다국어 범위 확정 후 추가 · 확인 필요)
MSG = {
    "deny": {"ko": "안전을 위해 이 요청은 안내할 수 없어요. 역 직원에게 문의해 주세요.",
             "en": "For your safety we can't help with this request. Please ask station staff."},
    "scope": {"ko": "한길은 역 안 길 안내와 교통 이용 방법만 도와드려요.",
              "en": "Hangil helps only with directions and transit use inside the station."},
    "lost": {"ko": "괜찮아요, 현재 위치부터 다시 확인할게요. 주변 표지판이 보이게 사진을 찍어 주세요.",
             "en": "No problem, let's find where you are. Please take a photo showing nearby signs."},
    "ask_where": {"ko": "어디로 가실지 조금 더 알려 주세요. (예: 1번 출구, 직통열차 승차권 판매처)",
                  "en": "Where would you like to go? (e.g., Exit 1, express train tickets)"},
    "choose": {"ko": "다음 중 어디로 가실 건가요?", "en": "Which one are you looking for?"},
    "choose_list": {"ko": "정확히 찾지 못했어요. 목적지 목록에서 직접 골라 주세요.",
                    "en": "We couldn't pin it down. Please pick your destination from the list."},
    "unknown_poi": {"ko": "확인되지 않은 목적지예요. 다시 골라 주세요.",
                    "en": "That destination isn't verified. Please choose again."},
    "dest_locate": {"ko": "{name}(으)로 안내할게요. 주변 표지판이 보이게 사진을 찍어 주세요.",
                    "en": "I'll guide you to {name}. Please take a photo showing nearby signs."},
    "dest_route": {"ko": "{name}(으)로 가는 경로를 찾을게요.", "en": "Finding a route to {name}."},
    "no_evidence": {"ko": "확인된 안내 자료를 찾지 못했어요. 역 안내센터에 문의해 주세요.",
                    "en": "We couldn't find verified information. Please ask the information center."},
    "sources_only": {"ko": "아래 안내 자료를 확인해 주세요.", "en": "Please check the guide below."},
    "dont_know": {"ko": "잘 모르겠어요", "en": "Not sure"},
    "ask_train_type": {"ko": "직통열차와 일반열차 중 어떤 승차권인가요?",
                       "en": "Is it an express train or a regular train ticket?"},
    "pick_train_type": {"ko": "어떤 승차권으로 안내할까요?", "en": "Which ticket would you like?"},
    "no_compare": {"ko": "차이를 설명할 확인된 자료가 없어요. 원하시는 승차권을 골라 주세요.",
                   "en": "We have no verified comparison. Please choose a ticket type."},
    "fallback": {"ko": "답변을 만들지 못했어요. 잠시 후 다시 시도해 주세요.",
                 "en": "We couldn't create an answer. Please try again shortly."},
}

# 슬롯 값 → 버튼 · 검색어에 쓰는 이름
SLOT_LABELS = {"train_type": {"express": {"ko": "직통열차", "en": "Express train"},
                              "general": {"ko": "일반열차", "en": "Regular train"}}}
SLOT_QUESTIONS = {"train_type": "ask_train_type"}


def t(key: str, lang: str, **kw) -> str:
    table = MSG[key]
    return table.get(lang, table["en"]).format(**kw)


def label(slot: str, value: str, lang: str) -> str:
    names = SLOT_LABELS[slot][value]
    return names.get(lang, names["en"])


def poi_name(p: POI, lang: str) -> str:
    return p.name_ko if lang == "ko" or not p.name_en else p.name_en


class State(TypedDict, total=False):
    ctx: SessionContext
    message: str
    selected_poi_id: str | None
    intent: str
    topic: str | None
    blocked: bool
    unsure: bool  # "잘 모르겠어요"
    new_slots: dict  # 이번 질문에서 뽑은 슬롯
    slots: dict  # 이전 슬롯 + 이번 슬롯 (pending 제외)
    missing: list[str]  # 아직 비어 있는 필수 슬롯
    poi_query: str | None
    knowledge_query: str | None
    hits: list[KnowledgeHit]
    answer_text: str | None  # compose 결과 (이용 방법 답변)
    result: dict  # 최종 AgentResponse


class Agent:
    def __init__(self, llm: LLM, search_knowledge: KnowledgeSearch, search_pois: PoiSearch):
        self.llm, self.search_knowledge, self.search_pois = llm, search_knowledge, search_pois
        self.graph = self._build()

    # ── 진입점 (Backend 가 호출) ────────────────────────────────────────
    def handle_message(self, session_context: SessionContext, message: str,
                       selected_poi_id: str | None) -> AgentResponse:
        lang = session_context.language
        try:
            out = self.graph.invoke({"ctx": session_context, "message": message.strip(),
                                     "selected_poi_id": selected_poi_id})
            result = dict(out["result"])
            result.setdefault("slots", out.get("slots", {}))  # 되묻기 노드는 pending 을 직접 넣음
            return AgentResponse.model_validate(result)
        except (LLMUnavailable, ToolUnavailable):
            raise  # 외부 장애 → Backend 503
        except (LLMError, ValidationError) as exc:  # 형식 위반 → 보류 (추측해서 답하지 않음)
            log.warning("agent_fallback session=%s err=%s", session_context.session_id, exc)
            return AgentResponse(status="needs_review", intent="unknown",
                                 assistant_message=t("fallback", lang), next_action="none")

    # ── 그래프 ───────────────────────────────────────────────────────
    def _build(self):
        g = StateGraph(State)
        for name in ("understand", "fill_slots", "deny", "lost", "verify_selected", "ask_slot",
                     "explain_options", "find_poi", "retrieve", "compose"):
            g.add_node(name, getattr(self, f"_{name}"))
        g.add_edge(START, "understand")
        g.add_edge("understand", "fill_slots")
        g.add_conditional_edges("fill_slots", self._after_slots,
                                ["deny", "lost", "verify_selected", "ask_slot", "explain_options",
                                 "find_poi", "retrieve"])
        g.add_edge("retrieve", "compose")
        g.add_conditional_edges("compose", self._after_compose, ["find_poi", END])
        for n in ("deny", "lost", "verify_selected", "ask_slot", "explain_options", "find_poi"):
            g.add_edge(n, END)
        return g.compile()

    # ── 노드 ─────────────────────────────────────────────────────────
    def _understand(self, s: State):
        if s.get("selected_poi_id"):  # 화면에서 고른 목적지 → LLM 해석 불필요
            return {"intent": "navigate_to_poi", "blocked": False, "topic": None, "unsure": False, "new_slots": {}}
        u = self.llm.understand(s["message"], s["ctx"].recent_messages, s["ctx"].language)
        return {"intent": u["intent"], "blocked": u["blocked"], "topic": u.get("topic"),
                "unsure": u.get("unsure", False), "new_slots": u.get("slots") or {},
                "poi_query": u["poi_query"] or s["message"], "knowledge_query": u["knowledge_query"] or s["message"]}

    def _fill_slots(self, s: State):
        """이전 슬롯과 합치고, 되묻던 질문의 짧은 대답("직통열차", "잘 모르겠어요")을 원래 의도로 이어 붙인다."""
        prev = dict(s["ctx"].slots)
        pending_intent, pending_topic = prev.pop("pending_intent", None), prev.pop("pending_topic", None)
        new = {k: v for k, v in (s.get("new_slots") or {}).items() if v}
        slots = {**prev, **new}
        intent, topic = s["intent"], s.get("topic")
        answered_pending = bool(new) or s.get("unsure")
        if pending_intent and not s.get("blocked") and intent in ("out_of_scope", "navigate_to_poi") and answered_pending:
            intent, topic = pending_intent, pending_topic
            # 원래 질문의 검색어는 남아 있지 않으므로 슬롯으로 다시 만든다
            s_query = "승차권" if topic == "ticket" else s["message"]
            update = {"poi_query": s_query, "knowledge_query": f"{s_query} 구매 방법"}
        else:
            update = {}
        if intent == "ticket_purchase_and_navigation":
            topic = "ticket"
        needs = REQUIRED_SLOTS.get(topic, ()) if intent in ("ask_procedure", "ticket_purchase_and_navigation") else ()
        missing = [k for k in needs if not slots.get(k)]
        return {"intent": intent, "topic": topic, "slots": slots, "missing": missing, **update}

    def _deny(self, s: State):
        key = "deny" if s.get("blocked") else "scope"
        return {"result": {"status": "denied", "intent": s["intent"],
                           "assistant_message": t(key, s["ctx"].language), "next_action": "none"}}

    def _lost(self, s: State):
        return {"result": {"status": "needs_location", "intent": "report_lost",
                           "assistant_message": t("lost", s["ctx"].language), "next_action": "locate"}}

    def _verify_selected(self, s: State):
        ctx = s["ctx"]
        found = self.search_pois(ctx.map_id, "", {"poi_id": s["selected_poi_id"], "verified": True})
        if not found:
            return {"result": {"status": "needs_clarification", "intent": s["intent"],
                               "assistant_message": t("unknown_poi", ctx.language), "next_action": "reply"}}
        return {"result": self._destination(s, found[0], sources=[])}

    def _ask_slot(self, s: State):
        """빠진 필수 슬롯 하나를 버튼과 함께 묻는다. 버튼은 텍스트로 답이 돌아오도록 poi_id 없이 만든다."""
        lang, slot = s["ctx"].language, s["missing"][0]
        return {"result": {"status": "needs_clarification", "intent": s["intent"],
                           "assistant_message": t(SLOT_QUESTIONS[slot], lang),
                           "suggested_replies": self._slot_buttons(slot, lang, with_unsure=True),
                           "next_action": "reply", "slots": self._pending(s)}}

    def _explain_options(self, s: State):
        """'잘 모르겠어요' → R3 자료로 선택지 차이를 설명하고 다시 묻는다 (자료 없으면 설명 없이 다시 묻기)."""
        ctx, lang, slot = s["ctx"], s["ctx"].language, s["missing"][0]
        names = " ".join(label(slot, v, "ko") for v in SLOT_LABELS[slot])
        hits = [h for h in self.search_knowledge(f"{names} 차이 비교", lang,
                                                 {"map_id": ctx.map_id, "intent": "compare"}) if h.excerpt.strip()][:4]
        if hits:
            msg = f"{self._compose_text(f'{names} 차이', hits, lang)} {t('pick_train_type', lang)}"
        else:
            msg = t("no_compare", lang)
        return {"result": {"status": "needs_clarification", "intent": s["intent"], "assistant_message": msg[:800],
                           "suggested_replies": self._slot_buttons(slot, lang, with_unsure=False),
                           "sources": self._sources(hits), "next_action": "reply", "slots": self._pending(s)}}

    def _retrieve(self, s: State):
        ctx = s["ctx"]
        hits = self.search_knowledge(self._with_slots(s["knowledge_query"], s), ctx.language,
                                     {"map_id": ctx.map_id, "intent": s["intent"], "slots": s.get("slots", {})})
        return {"hits": [h for h in hits if h.excerpt.strip()][:4]}

    def _compose(self, s: State):
        hits, lang = s.get("hits") or [], s["ctx"].language
        if not hits:
            if s["intent"] == "ask_procedure":  # 근거 없음 → 보류
                return {"answer_text": None, "result": {"status": "needs_review", "intent": s["intent"],
                                                        "assistant_message": t("no_evidence", lang),
                                                        "next_action": "none"}}
            return {"answer_text": None}  # 티켓+길 안내: 목적지 찾기는 계속
        text = self._compose_text(s["message"], hits, lang)[:600]
        if s["intent"] == "ask_procedure":
            return {"answer_text": text, "result": {"status": "answered", "intent": s["intent"],
                                                    "assistant_message": text, "sources": self._sources(hits),
                                                    "next_action": "none"}}
        return {"answer_text": text}

    def _find_poi(self, s: State):
        ctx, lang = s["ctx"], s["ctx"].language
        if s.get("missing"):  # 되묻기 상한 초과 → 해당 종류의 장소를 목록으로 보여 줌
            pois = self.search_pois(ctx.map_id, "", {"category": "ticket_office", "verified": True})
        else:
            pois = self.search_pois(ctx.map_id, self._with_slots(s["poi_query"], s), {"verified": True})
        pois = [p for p in pois if p.verified]
        sources = self._sources(s.get("hits") or [])
        if len(pois) == 1 and not s.get("missing"):
            return {"result": self._destination(s, pois[0], sources)}
        if not pois:
            if s.get("answer_text"):  # 장소는 못 찾았지만 이용 방법 답은 있음
                return {"result": {"status": "answered", "intent": s["intent"], "assistant_message": s["answer_text"],
                                   "sources": sources, "next_action": "none"}}
            return {"result": {"status": "needs_clarification", "intent": s["intent"],
                               "assistant_message": t("ask_where", lang), "next_action": "reply"}}
        # 후보 여러 개 → 장소 되묻기. 상한을 넘으면 목록 직접 선택으로 유도
        key = "choose_list" if self._asked(ctx) >= MAX_CLARIFY or s.get("missing") else "choose"
        replies = [{"label": poi_name(p, lang)[:40], "selected_poi_id": p.poi_id} for p in pois[:MAX_SUGGEST]]
        replies.append({"label": t("dont_know", lang), "selected_poi_id": None})
        msg = t(key, lang) if not s.get("answer_text") else f"{s['answer_text']} {t(key, lang)}"
        return {"result": {"status": "needs_clarification", "intent": s["intent"], "assistant_message": msg[:800],
                           "suggested_replies": replies, "next_action": "reply"}}

    # ── 분기 ─────────────────────────────────────────────────────────
    def _after_slots(self, s: State) -> str:
        if s.get("blocked") or s["intent"] == "out_of_scope":
            return "deny"
        if s["intent"] == "report_lost":
            return "lost"
        if s.get("selected_poi_id"):
            return "verify_selected"
        if s.get("missing"):
            if self._asked(s["ctx"]) >= MAX_CLARIFY:
                return "find_poi"  # 계속 못 고르면 목록에서 직접 고르게
            return "explain_options" if s.get("unsure") else "ask_slot"
        if s["intent"] == "navigate_to_poi":
            return "find_poi"
        return "retrieve"  # ask_procedure · ticket_purchase_and_navigation

    @staticmethod
    def _after_compose(s: State) -> str:
        return END if s.get("result") else "find_poi"

    # ── 도우미 ───────────────────────────────────────────────────────
    @staticmethod
    def _asked(ctx: SessionContext) -> int:
        return sum(1 for m in ctx.recent_messages if m.role == "assistant" and m.status == "needs_clarification")

    @staticmethod
    def _pending(s: State) -> dict:
        return {**s.get("slots", {}), "pending_intent": s["intent"], "pending_topic": s.get("topic") or ""}

    @staticmethod
    def _slot_buttons(slot: str, lang: str, with_unsure: bool) -> list[dict]:
        buttons = [{"label": label(slot, v, lang), "selected_poi_id": None} for v in SLOT_LABELS[slot]]
        if with_unsure:
            buttons.append({"label": t("dont_know", lang), "selected_poi_id": None})
        return buttons

    @staticmethod
    def _with_slots(query: str | None, s: State) -> str:
        """검색어에 채운 슬롯 이름을 붙인다. 예: '승차권' + express → '승차권 직통열차'."""
        q = query or s["message"]
        for slot, value in (s.get("slots") or {}).items():
            if slot in SLOT_LABELS and value in SLOT_LABELS[slot]:
                name = label(slot, value, "ko")
                if name not in q:
                    q = f"{name} {q}"
        return q

    def _compose_text(self, question: str, hits: list[KnowledgeHit], lang: str) -> str:
        """자료 근거 답변. 인용 번호가 없거나 범위 밖이면 자료 제목만 안내하는 폴백."""
        try:
            text = self.llm.compose(question, hits, lang)
            cited = {int(n) for n in re.findall(r"\[(\d+)\]", text)}
            if not cited or not cited <= set(range(1, len(hits) + 1)):
                raise LLMError(f"인용 번호 위반: {cited}")
            return text
        except LLMUnavailable:
            raise
        except LLMError as exc:
            log.warning("compose_fallback %s", exc)
            return t("sources_only", lang)

    @staticmethod
    def _sources(hits: list[KnowledgeHit]) -> list[dict]:
        return [Source(document_id=h.document_id, title=h.title, source_url=h.source_url,
                       verified_at=h.verified_at).model_dump() for h in hits[:4]]

    def _destination(self, s: State, poi: POI, sources: list[dict]) -> dict:
        ctx, lang = s["ctx"], s["ctx"].language
        name = poi_name(poi, lang)
        has_loc = ctx.confirmed_location is not None
        msg = t("dest_route" if has_loc else "dest_locate", lang, name=name)
        if s.get("answer_text"):
            msg = f"{s['answer_text']} {msg}"
        return {"status": "destination_set", "intent": s["intent"], "assistant_message": msg[:800],
                "destination": {"poi_id": poi.poi_id, "display_name": name}, "sources": sources,
                "next_action": "navigate" if has_loc else "locate"}
