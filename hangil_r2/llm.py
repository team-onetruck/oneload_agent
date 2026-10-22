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
    return os.getenv("PROMPT_VERSION", "v1")


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
    if not isinstance(out, dict) or out.get("intent") not in INTENTS:
        raise LLMError(f"의도 분류 규격 위반: {out!r}")
    return {"intent": out["intent"], "blocked": bool(out.get("blocked", False)),
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

    def understand(self, message, history, language):
        if self.broken:
            raise LLMError("fake broken output")
        m = message.lower()
        if any(w in m for w in self.UNSAFE):
            return {"intent": "out_of_scope", "blocked": True, "poi_query": None, "knowledge_query": None}
        if any(w in m for w in self.LOST):
            return {"intent": "report_lost", "blocked": False, "poi_query": None, "knowledge_query": None}
        proc = any(w in m for w in self.PROCEDURE)
        nav = any(w in m for w in self.NAVIGATE)
        if ("티켓" in m or "ticket" in m) and ("어디" in m or "where" in m):
            intent = "ticket_purchase_and_navigation"
        elif proc:
            intent = "ask_procedure"
        elif nav or history:  # 추가 질문에 대한 짧은 대답("직통열차")은 길 안내로 이어 봄
            intent = "navigate_to_poi"
        else:
            intent = "out_of_scope"
        return {"intent": intent, "blocked": False,
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
        return validate_understanding(parse_json(self._ask(prompt)))

    def compose(self, question, hits, language):
        docs = "\n".join(f"[{i}] {h.title}: {h.excerpt}" for i, h in enumerate(hits, start=1))
        text = self._ask(load_prompt("compose").substitute(question=question, documents=docs, language=language))
        return text.strip()

    def read_scene(self, image_bytes):
        b64 = base64.b64encode(image_bytes).decode()
        content = [{"type": "text", "text": load_prompt("scene").substitute()},
                   {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}]
        return validate_scene(parse_json(self._ask(content)))
