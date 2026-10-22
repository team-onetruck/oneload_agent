"""LLM 어댑터. agent · vision 은 `LLM` 프로토콜만 알고, 모델 · 공급자는 여기서만 바꾼다.

LLM 이 하는 일은 세 가지뿐이다.
1. understand : 사용자 말 → 의도 · 주제 · 슬롯 (백엔드 contracts 의 값으로만)
2. compose    : 검색된 자료로만 답변 문장 (인용 번호 필수)
3. read_scene : 사진 → 보이는 글자 · 시설 (백엔드 VisionFeatures 모양). 위치 판단은 하지 않는다

오류는 두 종류로 나눈다.
- LLMError       : 응답 형식이 깨짐 → 흐름에서 보류하거나 대체 응답
- LLMUnavailable : 네트워크 · 키 · 한도 · 시간 초과 → 대체 agent(RuleAgent) 또는 503
"""

from __future__ import annotations

import base64
import json
import os
import re
from pathlib import Path
from string import Template
from typing import Protocol

from .knowledge import KnowledgeHit

PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompts"

# 백엔드 app/ai/contracts.py · app/domain/localization.py 와 같은 값 (scripts/check_backend_compat.py 가 확인)
INTENTS = (
    "navigate_to_poi",
    "ticket_purchase_and_navigation",
    "payment_question",
    "usage_question",
    "report_off_route",
    "out_of_scope",
)
TOPICS = ("tickets", "platform", "fare", "payment", "card", "deposit", "recharge", "other")
TRAIN_TYPES = ("express", "allstop")
CARD_VALUES = ("yes", "no")
IMAGE_QUALITY = ("usable", "blurry", "too_dark", "no_signage", "too_close")
TEXT_KINDS = ("line_name", "line_number", "exit_number", "facility_name", "direction", "other")


class LLMError(RuntimeError):
    pass


class LLMUnavailable(LLMError):
    pass


class LLM(Protocol):
    def understand(self, message: str, history: list[tuple[str, str]], language: str, photo: str | None) -> dict: ...
    def compose(self, question: str, hits: list[KnowledgeHit], language: str) -> str: ...
    def read_scene(self, image: bytes) -> dict: ...


def prompt_version() -> str:
    return os.getenv("PROMPT_VERSION", "v3")


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
    """허용 값 밖은 버린다(None) — 의도 · 슬롯을 지어내지 못하게."""
    if not isinstance(out, dict) or out.get("intent") not in INTENTS:
        raise LLMError(f"의도 분류 규격 위반: {out!r}")
    slots = out.get("slots") if isinstance(out.get("slots"), dict) else {}
    return {
        "intent": out["intent"],
        "topic": out.get("topic") if out.get("topic") in TOPICS else None,
        "train_type": slots.get("train_type") if slots.get("train_type") in TRAIN_TYPES else None,
        "has_transit_card": slots.get("has_transit_card") if slots.get("has_transit_card") in CARD_VALUES else None,
        "unsure": bool(out.get("unsure", False)),
        "blocked": bool(out.get("blocked", False)),
        "poi_query": str(out["poi_query"])[:100] if out.get("poi_query") else None,
        "knowledge_query": str(out["knowledge_query"])[:200] if out.get("knowledge_query") else None,
    }


def validate_scene(out: object) -> dict:
    """백엔드 VisionFeatures 에 그대로 넣을 수 있는 dict 로 정리."""
    if not isinstance(out, dict) or out.get("image_quality") not in IMAGE_QUALITY:
        raise LLMError(f"장면 읽기 규격 위반: {out!r}")
    texts = []
    for t in out.get("texts", [])[:20]:
        if isinstance(t, dict) and isinstance(t.get("text"), str) and t["text"].strip():
            texts.append(
                {
                    "text": t["text"].strip()[:60],
                    "lang": t.get("lang") if isinstance(t.get("lang"), str) else None,
                    "kind": t.get("kind") if t.get("kind") in TEXT_KINDS else "other",
                }
            )
    facilities = [f for f in out.get("facilities", []) if isinstance(f, str)][:10]
    scene = out.get("scene_type") if isinstance(out.get("scene_type"), str) else None
    return {
        "image_quality": out["image_quality"],
        "scene_type": scene,
        "texts": texts,
        "facilities": facilities,
        "person_visible": bool(out.get("person_visible", False)),
    }


# ── 테스트 · 리허설용 ──────────────────────────────────────────────────────
class FakeLLM:
    """키워드 규칙으로 동작하는 가짜 LLM. scene 으로 '사진에서 읽혔다고 칠 내용' 을 주입한다."""

    UNSAFE = ("선로", "무단", "무임", "track", "fare evasion")
    LOST = ("잘못", "길을 잃", "헤매", "lost", "wrong way")
    UNSURE = ("모르", "not sure", "don't know")

    def __init__(self, scene: dict | None = None, broken: bool = False, down: bool = False):
        self.scene = scene or {"image_quality": "no_signage"}
        self.broken, self.down = broken, down  # broken: 형식 위반 흉내, down: 장애 흉내

    @staticmethod
    def _has(text: str, *words: str) -> bool:
        # 영어는 단어 경계로 ("press" 가 "express" 에 걸리지 않게), 한국어는 부분 문자열로
        return any(re.search(rf"\b{re.escape(w)}\b", text) if w.isascii() else w in text for w in words)

    def understand(self, message, history, language, photo):
        if self.down:
            raise LLMUnavailable("fake down")
        if self.broken:
            raise LLMError("fake broken output")
        m, has = message.lower(), lambda *w: self._has(message.lower(), *w)
        out = {
            "intent": "out_of_scope",
            "topic": None,
            "slots": {},
            "unsure": has(*self.UNSURE),
            "blocked": False,
            "poi_query": message,
            "knowledge_query": message,
        }
        if has(*self.UNSAFE):
            return validate_understanding({**out, "blocked": True})
        if has(*self.LOST):
            return validate_understanding({**out, "intent": "report_off_route"})
        slots = {}
        if has("직통", "express"):
            slots["train_type"] = "express"
        elif has("일반", "all-stop", "allstop", "regular"):
            slots["train_type"] = "allstop"
        if has("없어", "없음", "don't have", "do not have") or re.search(r"^\s*(no|아니)", m):
            slots["has_transit_card"] = "no"
        elif has("있어", "있음", "i have", "have one") or re.search(r"^\s*(yes|네)\b", m):
            slots["has_transit_card"] = "yes"
        topic = (
            "fare"
            if has("요금", "얼마", "가격", "fare", "price", "how much")
            else "deposit"
            if has("보증금", "환급", "deposit", "refund")
            else "recharge"
            if has("충전", "top up", "top-up")
            else "card"
            if has("교통카드가 뭐", "what's a transit card", "티머니", "t-money")
            else "payment"
            if has("결제", "현금", "신용카드", "pay", "cash", "credit")
            else "platform"
            if has("승강장", "타는 곳", "platform")
            else "tickets"
            if has("티켓", "승차권", "표 ", "표를", "표는", "ticket", "buy", "구매", "눌러", "press")
            else None
        )
        if topic is None and photo and "ticket_machine" in photo:
            topic = "tickets"
        where = has("어디", "where", "가는 길", "가려면")
        if topic in ("payment", "card", "recharge"):
            intent = "payment_question"
        elif topic in ("fare", "deposit") or (topic == "tickets" and not where):
            intent = "usage_question"
        elif topic == "platform":
            intent = "navigate_to_poi"
        elif topic == "tickets" or has("공항", "인천", "airport", "incheon", "arex"):
            intent = "ticket_purchase_and_navigation"
        elif where:
            intent = "navigate_to_poi"
        else:
            intent = "out_of_scope"  # 짧은 대답("직통열차", "없어요")은 agent 가 슬롯으로 이어 간다
        return validate_understanding({**out, "intent": intent, "topic": topic, "slots": slots})

    def compose(self, question, hits, language):
        if self.down:
            raise LLMUnavailable("fake down")
        if self.broken:
            raise LLMError("fake broken output")
        return " ".join(f"{h.excerpt} [{i}]" for i, h in enumerate(hits, start=1))

    def read_scene(self, image):
        if self.down:
            raise LLMUnavailable("fake down")
        if self.broken:
            raise LLMError("fake broken output")
        return validate_scene(self.scene)


# ── 실제 호출 ──────────────────────────────────────────────────────────────
class OpenAILLM:
    """langchain-openai 기반. 모델은 OPENAI_MODEL, 시간 상한은 LLM_TIMEOUT_S (.env)."""

    def __init__(self, model: str | None = None, timeout_s: float | None = None):
        try:
            from langchain_openai import ChatOpenAI  # 지연 import: 테스트에는 패키지 불필요
        except ImportError as exc:
            raise LLMError("langchain-openai 미설치: pip install -r requirements.txt") from exc
        self._chat = ChatOpenAI(
            model=model or os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            temperature=0,
            timeout=timeout_s or float(os.getenv("LLM_TIMEOUT_S", "6")),
            max_retries=1,
        )

    def _ask(self, content) -> str:
        try:
            return self._chat.invoke([{"role": "user", "content": content}]).content
        except Exception as exc:  # 원인 종류만 남기고 메시지(키 일부가 섞일 수 있음)는 버린다
            raise LLMUnavailable(f"LLM 호출 실패: {type(exc).__name__}") from exc

    def understand(self, message, history, language, photo):
        hist = "\n".join(f"{role}: {text}" for role, text in history[-6:]) or "(없음)"
        prompt = load_prompt("understand").substitute(
            message=message, history=hist, language=language, photo=photo or "(사진 없음)"
        )
        return validate_understanding(parse_json(self._ask(prompt)))

    def compose(self, question, hits, language):
        docs = "\n".join(f"[{i}] {h.title}: {h.excerpt}" for i, h in enumerate(hits, start=1))
        return self._ask(
            load_prompt("compose").substitute(question=question, documents=docs, language=language)
        ).strip()

    def read_scene(self, image):
        b64 = base64.b64encode(image).decode()
        content = [
            {"type": "text", "text": load_prompt("scene").substitute()},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
        ]
        return validate_scene(parse_json(self._ask(content)))
