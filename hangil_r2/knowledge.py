"""Agent 가 쓰는 외부 도구: 이용 안내 검색(R3 RAG) · 시각표(선택).

백엔드에는 아직 RAG 가 없다 (README "Knowledge (RAG): Fixed citations inside RuleAgent").
그래서 R2 는 도구 모양(Protocol)만 정해 두고, 지금은 Mock 으로 시험한다. R3 가 같은 시그니처로 구현하면 교체 끝.

- KnowledgeHit : 명세 v0.3 10장 필드 그대로
- Departure    : [제안] 시각표 한 줄. 백엔드 RuleAgent 는 "확인된 요금 · 소요시간이 없으면 비교하지 않음" 이라
                 실제 공식 시각표가 생기기 전까지는 도구를 넘기지 않는다 (기본 꺼짐, D-21 · D-25)
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, model_validator

MOCK_DIR = Path(__file__).resolve().parent.parent / "mocks"


class ToolUnavailable(RuntimeError):
    """검색 · 시각표 서버 장애. Agent 는 보조 정보면 생략하고, 본 답변 근거면 cannot_answer 로 보류한다."""


class KnowledgeHit(BaseModel):
    document_id: str
    title: str
    excerpt: str
    source_url: str | None = None
    verified_at: str
    scope: str | None = None


class KnowledgeSearch(Protocol):  # R2 Agent -> R3 RAG
    def __call__(self, query: str, language: str, context: dict) -> list[KnowledgeHit]: ...


class Departure(BaseModel):
    train_type: Literal["express", "allstop"]  # 백엔드 Slots.train_type 과 같은 값
    departs_at: datetime
    arrives_at: datetime
    origin: str
    destination: str
    source_title: str
    source_url: str | None = None
    verified_at: str

    @model_validator(mode="after")
    def _order(self) -> Departure:
        if self.arrives_at <= self.departs_at:
            raise ValueError("도착 시각이 출발 시각보다 빠름")
        return self


class DepartureSearch(Protocol):  # [제안] R2 Agent -> R4 시각표 도구
    def __call__(self, origin: str, destination: str, now: datetime, limit: int) -> list[Departure]: ...


def _load(name: str) -> dict:
    with open(MOCK_DIR / name, encoding="utf-8") as f:
        return json.load(f)


def _norm(s: str) -> str:
    return "".join(s.lower().split())


class MockKnowledgeSearch:
    """키워드 겹침으로 찾는 가짜 RAG (mocks/knowledge.json, 데모 문구). 실제 R3 는 pgvector 검색 예정."""

    def __init__(self, path: str = "knowledge.json"):
        self._docs = _load(path)["docs"]

    def __call__(self, query: str, language: str, context: dict) -> list[KnowledgeHit]:
        """context["slots"]["train_type"] 이 정해져 있으면 다른 열차 전용 문서는 뺀다 (R3 메타데이터 필터 대역)."""
        q = _norm(query)
        train = (context.get("slots") or {}).get("train_type")
        scored = []
        for d in self._docs:
            if train in ("express", "allstop") and d.get("train_type") not in (None, train):
                continue
            hit = sum(1 for k in d["keywords"] if _norm(k) in q)
            if hit:
                scored.append((hit, d))
        scored.sort(key=lambda x: x[0], reverse=True)
        lang = "en" if language == "en" else "ko"
        return [
            KnowledgeHit(
                document_id=d["document_id"],
                title=d["title"][lang],
                excerpt=d["excerpt"][lang],
                source_url=d.get("source_url"),
                verified_at=d["verified_at"],
                scope=d.get("scope"),
            )
            for _, d in scored[:3]
        ]


class MockDepartures:
    """첫차 · 막차 · 배차 간격으로 출발 시각을 만드는 가짜 시각표 (mocks/timetable.json, 가상 값)."""

    def __init__(self, path: str = "timetable.json"):
        self._tt = _load(path)

    def __call__(self, origin: str, destination: str, now: datetime, limit: int = 3) -> list[Departure]:
        if origin != self._tt["origin"]:
            return []
        out: list[Departure] = []
        day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        for svc in self._tt["services"]:
            minutes = svc["duration_min"].get(destination)
            if minutes is None:
                continue
            h, m = map(int, svc["first"].split(":"))
            lh, lm = map(int, svc["last"].split(":"))
            t, last, count = day + timedelta(hours=h, minutes=m), day + timedelta(hours=lh, minutes=lm), 0
            while t <= last and count < limit:
                if t >= now:
                    out.append(
                        Departure(
                            train_type=svc["train_type"],
                            departs_at=t,
                            arrives_at=t + timedelta(minutes=minutes),
                            origin=origin,
                            destination=destination,
                            source_title=self._tt["source_title"],
                            source_url=self._tt.get("source_url"),
                            verified_at=self._tt["verified_at"],
                        )
                    )
                    count += 1
                t += timedelta(minutes=svc["every_min"])
        return sorted(out, key=lambda d: d.departs_at)
