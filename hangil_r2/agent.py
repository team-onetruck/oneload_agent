"""AI Agent — handle_message (명세 v0.3 10장 · 8.5).

흐름 (노드 이름 = docs/STEP05.md 그림의 단계 이름)

    START → understand ─┬─ deny ─────────────────────────────→ END  (위험 · 범위 밖)
                        ├─ lost ─────────────────────────────→ END  (위치 재확인 요청)
                        ├─ verify_selected ──────────────────→ END  (화면에서 고른 POI 검증)
                        ├─ find_poi ─────────────────────────→ END  (목적지 확정 / 추가 질문)
                        └─ retrieve → compose ─┬─────────────→ END  (이용 방법 답변 / 보류)
                                               └─ find_poi ──→ END  (답변 + 목적지, 예: 티켓 구매)

설계 원칙
- 목적지는 search_pois 가 돌려준 verified POI 에서만 고른다. LLM 이 poi_id 를 만들지 않는다.
- 이용 방법은 search_knowledge 근거가 있을 때만 답한다. 없으면 needs_review (추측 금지).
- 대화 상태(추가 질문 맥락)는 Backend 가 SessionContext.recent_messages 로 넘긴다 → Agent 는 상태를 들고 있지 않다.
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

MAX_CLARIFY = int(os.getenv("MAX_CLARIFY", "2"))  # 추가 질문 상한. 넘으면 목록 직접 선택 유도
MAX_SUGGEST = 3  # 추천 답변 중 POI 버튼 수 (+ '잘 모르겠어요' 1개)

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
    "fallback": {"ko": "답변을 만들지 못했어요. 잠시 후 다시 시도해 주세요.",
                 "en": "We couldn't create an answer. Please try again shortly."},
}


def t(key: str, lang: str, **kw) -> str:
    table = MSG[key]
    return table.get(lang, table["en"]).format(**kw)


def poi_name(p: POI, lang: str) -> str:
    return p.name_ko if lang == "ko" or not p.name_en else p.name_en


class State(TypedDict, total=False):
    ctx: SessionContext
    message: str
    selected_poi_id: str | None
    intent: str
    blocked: bool
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
            return AgentResponse.model_validate(out["result"])
        except (LLMUnavailable, ToolUnavailable):
            raise  # 외부 장애 → Backend 503
        except (LLMError, ValidationError) as exc:  # 형식 위반 → 보류 (추측해서 답하지 않음)
            log.warning("agent_fallback session=%s err=%s", session_context.session_id, exc)
            return AgentResponse(status="needs_review", intent="unknown",
                                 assistant_message=t("fallback", lang), next_action="none")

    # ── 그래프 ───────────────────────────────────────────────────────
    def _build(self):
        g = StateGraph(State)
        for name in ("understand", "deny", "lost", "verify_selected", "find_poi", "retrieve", "compose"):
            g.add_node(name, getattr(self, f"_{name}"))
        g.add_edge(START, "understand")
        g.add_conditional_edges("understand", self._after_understand,
                                ["deny", "lost", "verify_selected", "find_poi", "retrieve"])
        g.add_edge("retrieve", "compose")
        g.add_conditional_edges("compose", self._after_compose, ["find_poi", END])
        for n in ("deny", "lost", "verify_selected", "find_poi"):
            g.add_edge(n, END)
        return g.compile()

    # ── 노드 ─────────────────────────────────────────────────────────
    def _understand(self, s: State):
        if s.get("selected_poi_id"):  # 화면에서 고른 목적지 → LLM 해석 불필요
            return {"intent": "navigate_to_poi", "blocked": False}
        u = self.llm.understand(s["message"], s["ctx"].recent_messages, s["ctx"].language)
        return {"intent": u["intent"], "blocked": u["blocked"],
                "poi_query": u["poi_query"] or s["message"], "knowledge_query": u["knowledge_query"] or s["message"]}

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

    def _retrieve(self, s: State):
        ctx = s["ctx"]
        hits = self.search_knowledge(s["knowledge_query"], ctx.language,
                                     {"map_id": ctx.map_id, "intent": s["intent"]})
        return {"hits": [h for h in hits if h.excerpt.strip()][:4]}

    def _compose(self, s: State):
        hits, lang = s.get("hits") or [], s["ctx"].language
        if not hits:
            if s["intent"] == "ask_procedure":  # 근거 없음 → 보류
                return {"answer_text": None, "result": {"status": "needs_review", "intent": s["intent"],
                                                        "assistant_message": t("no_evidence", lang),
                                                        "next_action": "none"}}
            return {"answer_text": None}  # 티켓+길 안내: 목적지 찾기는 계속
        try:
            text = self.llm.compose(s["message"], hits, lang)
            cited = {int(n) for n in re.findall(r"\[(\d+)\]", text)}
            if not cited or not cited <= set(range(1, len(hits) + 1)):
                raise LLMError(f"인용 번호 위반: {cited}")
        except LLMUnavailable:
            raise
        except LLMError as exc:  # 문장 생성 실패 → 자료 제목만 보여 주는 안전한 폴백
            log.warning("compose_fallback %s", exc)
            text = t("sources_only", lang)
        text = text[:600]
        if s["intent"] == "ask_procedure":
            return {"answer_text": text, "result": {"status": "answered", "intent": s["intent"],
                                                    "assistant_message": text, "sources": self._sources(hits),
                                                    "next_action": "none"}}
        return {"answer_text": text}

    def _find_poi(self, s: State):
        ctx, lang = s["ctx"], s["ctx"].language
        pois = [p for p in self.search_pois(ctx.map_id, s["poi_query"], {"verified": True}) if p.verified]
        sources = self._sources(s.get("hits") or [])
        if len(pois) == 1:
            return {"result": self._destination(s, pois[0], sources)}
        if not pois:
            if s.get("answer_text"):  # 장소는 못 찾았지만 이용 방법 답은 있음
                return {"result": {"status": "answered", "intent": s["intent"], "assistant_message": s["answer_text"],
                                   "sources": sources, "next_action": "none"}}
            return {"result": {"status": "needs_clarification", "intent": s["intent"],
                               "assistant_message": t("ask_where", lang), "next_action": "reply"}}
        # 후보 여러 개 → 추가 질문. 상한을 넘으면 목록 직접 선택으로 유도
        asked = sum(1 for m in ctx.recent_messages if m.role == "assistant" and m.status == "needs_clarification")
        key = "choose_list" if asked >= MAX_CLARIFY else "choose"
        replies = [{"label": poi_name(p, lang)[:40], "selected_poi_id": p.poi_id} for p in pois[:MAX_SUGGEST]]
        replies.append({"label": t("dont_know", lang), "selected_poi_id": None})
        msg = t(key, lang) if not s.get("answer_text") else f"{s['answer_text']} {t(key, lang)}"
        return {"result": {"status": "needs_clarification", "intent": s["intent"], "assistant_message": msg[:800],
                           "suggested_replies": replies, "next_action": "reply"}}

    # ── 분기 ─────────────────────────────────────────────────────────
    @staticmethod
    def _after_understand(s: State) -> str:
        if s.get("blocked") or s["intent"] == "out_of_scope":
            return "deny"
        if s["intent"] == "report_lost":
            return "lost"
        if s.get("selected_poi_id"):
            return "verify_selected"
        if s["intent"] == "navigate_to_poi":
            return "find_poi"
        return "retrieve"  # ask_procedure · ticket_purchase_and_navigation

    @staticmethod
    def _after_compose(s: State) -> str:
        return END if s.get("result") else "find_poi"

    # ── 도우미 ───────────────────────────────────────────────────────
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
