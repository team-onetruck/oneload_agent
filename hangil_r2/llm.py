"""LLM 어댑터. 흐름 코드(agent · localization)는 `LLM` 프로토콜만 알고, 모델 · 공급자는 여기서만 바꾼다.

- FakeLLM  : 키 없이 테스트 · 리허설. 규칙 기반이라 매번 같은 결과.
- OpenAILLM: 실제 호출. 프롬프트는 prompts/<이름>_<PROMPT_VERSION>.md 파일에서 읽는다.

오류는 두 종류로 나눈다.
- LLMError       : 응답 형식이 깨짐 → 흐름에서 보류(needs_review) · 폴백으로 처리
- LLMUnavailable : 네트워크 · 키 · 한도 · 시간 초과 → 다시 던져 Backend 가 503 AI_SERVICE_UNAVAILABLE
"""
from __future__ import annotations

import base64
import json
import os
import re
from pathlib import Path
from string import Template
from typing import Protocol

from .contracts import ChatTurn, KnowledgeHit

PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompts"

INTENTS = ("navigate_to_poi", "ask_procedure", "ticket_purchase_and_navigation", "report_lost", "out_of_scope")
TOPICS = ("ticket", "card", "transfer", "other")  # 이용 방법 주제. ticket 이면 열차 종류 슬롯이 필요
TRAIN_TYPES = ("express", "general")  # 공항철도 직통열차 · 일반열차
OBJECT_KINDS = ("stairs", "escalator", "elevator", "ticket_machine", "gate", "platform", "refund_machine", "information_desk")


class LLMError(RuntimeError):
    pass


class LLMUnavailable(LLMError):
    pass


class LLM(Protocol):
    def understand(self, message: str, history: list[ChatTurn], language: str) -> dict: ...
    def compose(self, question: str, hits: list[KnowledgeHit], language: str) -> str: ...
    def read_scene(self, image_bytes: bytes) -> dict: ...  # {"texts": [...], "objects": [...]}


def prompt_version() -> str:
    return os.getenv("PROMPT_VERSION", "v2")


def load_prompt(name: str) -> Template:
    path = PROMPT_DIR / f"{name}_{prompt_version()}.md"
    try:
        return Template(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise LLMError(f"프롬프트 파일 없음: {path.name}") from exc


def parse_json(text: str) -> dict | list:
    """```json 코드 블록으로 감싼 응답도 허용."""
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    raw = m.group(1) if m else text
    try:
        return json.loads(raw.strip())
    except json.JSONDecodeError as exc:
        raise LLMError(f"JSON 해석 실패: {text[:80]!r}") from exc


def validate_understanding(out: object) -> dict:
    """LLM 해석 결과를 규격에 맞게 정리. 허용 값 밖이면 버린다(None) — 슬롯을 지어내지 않게."""
    if not isinstance(out, dict) or out.get("intent") not in INTENTS:
        raise LLMError(f"의도 분류 규격 위반: {out!r}")
    raw_slots = out.get("slots") if isinstance(out.get("slots"), dict) else {}
    slots = {
        "train_type": raw_slots.get("train_type") if raw_slots.get("train_type") in TRAIN_TYPES else None,
        "terminal": str(raw_slots["terminal"])[:30] if raw_slots.get("terminal") else None,
        "payment": str(raw_slots["payment"])[:30] if raw_slots.get("payment") else None,
    }
    return {"intent": out["intent"], "blocked": bool(out.get("blocked", False)),
            "topic": out.get("topic") if out.get("topic") in TOPICS else None,
            "slots": slots, "unsure": bool(out.get("unsure", False)),
            "poi_query": out.get("poi_query") or None, "knowledge_query": out.get("knowledge_query") or None}


def validate_scene(out: object) -> dict:
    if not isinstance(out, dict):
        raise LLMError("장면 읽기 규격 위반")
    texts = [t for t in out.get("texts", []) if isinstance(t, str) and t.strip()][:20]
    objects = [o for o in out.get("objects", []) if o in OBJECT_KINDS]
    return {"texts": texts, "objects": objects}


# ── 테스트 · 리허설용 ──────────────────────────────────────────────────────
class FakeLLM:
    """키워드 규칙으로 동작. scene 으로 '사진에서 읽혔다고 칠 내용' 을 주입한다."""

    UNSAFE = ("선로", "무단", "track", "폭탄")
    LOST = ("잘못", "길을 잃", "wrong way", "lost")
    PROCEDURE = ("어떻게", "환급", "보증금", "사야", "how", "refund", "which card")
    NAVIGATE = ("어디", "가는 길", "where", "가야", "찾아")

    def __init__(self, scene: dict | None = None, broken: bool = False):
        self.scene = scene or {"texts": [], "objects": []}
        self.broken = broken  # True 면 형식 위반 응답을 흉내

    TICKET = re.compile(r"티켓|승차권|ticket|표(\s|$|를|는|사|살)")
    UNSURE = ("모르", "not sure", "don't know")

    def understand(self, message, history, language):
        if self.broken:
            raise LLMError("fake broken output")
        m = message.lower()
        base = {"blocked": False, "topic": None, "unsure": any(w in m for w in self.UNSURE),
                "slots": {"train_type": "express" if ("직통" in m or "express" in m)
                          else "general" if ("일반" in m or "regular" in m) else None,
                          "terminal": None, "payment": None},
                "poi_query": None, "knowledge_query": None}
        if any(w in m for w in self.UNSAFE):
            return {**base, "intent": "out_of_scope", "blocked": True}
        if any(w in m for w in self.LOST):
            return {**base, "intent": "report_lost"}
        ticket = bool(self.TICKET.search(m))
        topic = "ticket" if ticket else "card" if any(w in m for w in ("보증금", "환급", "refund", "deposit")) else None
        if ticket and ("어디" in m or "where" in m):
            intent = "ticket_purchase_and_navigation"
        elif any(w in m for w in self.PROCEDURE):
            intent = "ask_procedure"
        elif any(w in m for w in self.NAVIGATE) or history:  # 추가 질문에 대한 짧은 대답은 길 안내로 이어 봄
            intent = "navigate_to_poi"
        else:
            intent = "out_of_scope"  # 되묻는 중이면 agent 가 이전 의도로 이어 붙인다
        return {**base, "intent": intent, "topic": topic,
                "poi_query": message if intent in ("navigate_to_poi", "ticket_purchase_and_navigation") else None,
                "knowledge_query": message if intent in ("ask_procedure", "ticket_purchase_and_navigation") else None}

    def compose(self, question, hits, language):
        if self.broken:
            raise LLMError("fake broken output")
        return " ".join(f"{h.excerpt} [{i}]" for i, h in enumerate(hits, start=1))

    def read_scene(self, image_bytes):
        return validate_scene(self.scene)


# ── 실제 호출 ──────────────────────────────────────────────────────────────
class OpenAILLM:
    """langchain-openai 기반. 모델은 OPENAI_MODEL, 시간 상한은 LLM_TIMEOUT_S (.env)."""

    def __init__(self, model: str | None = None, timeout_s: float | None = None):
        try:
            from langchain_openai import ChatOpenAI  # 지연 import: 테스트에는 패키지 불필요
        except ImportError as exc:
            raise LLMError("langchain-openai 미설치: pip install -r requirements.txt") from exc
        self._chat = ChatOpenAI(model=model or os.getenv("OPENAI_MODEL", "gpt-4o-mini"), temperature=0,
                                timeout=timeout_s or float(os.getenv("LLM_TIMEOUT_S", "6")), max_retries=1)

    def _ask(self, content) -> str:
        try:
            return self._chat.invoke([{"role": "user", "content": content}]).content
        except Exception as exc:  # 원인 종류만 남기고 메시지(키 일부가 섞일 수 있음)는 버린다
            raise LLMUnavailable(f"LLM 호출 실패: {type(exc).__name__}") from exc

    def understand(self, message, history, language):
        hist = "\n".join(f"{t.role}: {t.text}" for t in history[-6:]) or "(없음)"
        prompt = load_prompt("understand").substitute(message=message, history=hist, language=language)
        return validate_understanding(parse_json(self._ask(prompt)))  # v1 프롬프트면 슬롯은 모두 None

    def compose(self, question, hits, language):
        docs = "\n".join(f"[{i}] {h.title}: {h.excerpt}" for i, h in enumerate(hits, start=1))
        text = self._ask(load_prompt("compose").substitute(question=question, documents=docs, language=language))
        return text.strip()

    def read_scene(self, image_bytes):
        b64 = base64.b64encode(image_bytes).decode()
        content = [{"type": "text", "text": load_prompt("scene").substitute()},
                   {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}]
        return validate_scene(parse_json(self._ask(content)))
