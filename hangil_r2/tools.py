"""Agent Tool · 지도 조회 인터페이스와 Mock 구현.

- Protocol = 팀과의 계약. 실제 구현(R3 RAG, R4 Places · Map store)은 같은 시그니처로 갈아 끼운다.
- Mock* = 키 · 서버 없이 R2 흐름을 시험하기 위한 대역. mocks/*.json 의 가상 데이터를 읽는다.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Protocol

from .contracts import POI, Departure, KnowledgeHit

MOCK_DIR = Path(__file__).resolve().parent.parent / "mocks"


class ToolUnavailable(RuntimeError):
    """Tool(검색 서버 등) 장애. Agent 는 삼키지 않고 Backend 로 올린다 → 503."""


class MapVersionMismatch(ValueError):
    """요청의 map_version 이 저장된 지도와 다름 → Backend 가 409 MAP_VERSION_MISMATCH."""


class KnowledgeSearch(Protocol):  # R2 Agent -> R3 RAG (명세 10장)
    def __call__(self, query: str, language: str, context: dict) -> list[KnowledgeHit]: ...


class PoiSearch(Protocol):  # R2 Agent -> R4 Places (명세 10장)
    def __call__(self, map_id: str, query: str, filters: dict) -> list[POI]: ...


class MapStore(Protocol):  # R2 Visual Localization -> R4 공간 데이터 (8.3 graph 형식)
    def get_graph(self, map_id: str, map_version: str) -> dict: ...


class DepartureSearch(Protocol):  # [제안] R2 Agent -> R4 시각표 도구 (R3 공식 시각표 데이터)
    def __call__(self, origin: str, destination: str, now: datetime, limit: int) -> list[Departure]: ...


def _load(name: str) -> dict:
    with open(MOCK_DIR / name, encoding="utf-8") as f:
        return json.load(f)


def _norm(s: str) -> str:
    return "".join(s.lower().split())


class MockKnowledgeSearch:
    """키워드 겹침으로 찾는 가짜 RAG. 실제 R3 구현은 임베딩 검색 + 출처 메타데이터."""

    def __init__(self, path: str = "knowledge.json"):
        self._docs = _load(path)["hits"]

    def __call__(self, query: str, language: str, context: dict) -> list[KnowledgeHit]:
        q = _norm(query)
        scored = []
        for d in self._docs:
            hit = sum(1 for k in d["keywords"] if _norm(k) in q)
            if hit:
                scored.append((hit, d))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [KnowledgeHit(**{k: v for k, v in d.items() if k != "keywords"}) for _, d in scored[:3]]


class MockPoiSearch:
    """이름 · 별칭 포함 여부로 찾는 가짜 POI 검색.

    filters: {"verified": bool, "poi_id": str, "category": str}. query 가 비면 필터만으로 고른다.
    """

    def __init__(self, path: str = "pois.json"):
        self._pois = [POI(**p) for p in _load(path)["pois"]]

    def __call__(self, map_id: str, query: str, filters: dict) -> list[POI]:
        out = []
        q = _norm(query)
        for p in self._pois:
            if "poi_id" in filters and p.poi_id != filters["poi_id"]:
                continue
            if "verified" in filters and p.verified != filters["verified"]:
                continue
            if "category" in filters and p.category != filters["category"]:
                continue
            names = [p.name_ko, p.name_en or "", *p.aliases]
            if not q or any(_norm(n) and (_norm(n) in q or q in _norm(n)) for n in names):
                out.append(p)
        return out


class MockDepartures:
    """첫차 · 막차 · 배차 간격으로 출발 시각을 만들어 내는 가짜 시각표 도구 (mocks/timetable.json, 가상 값)."""

    def __init__(self, path: str = "timetable.json"):
        self._tt = _load(path)

    def __call__(self, origin: str, destination: str, now: datetime, limit: int = 3) -> list[Departure]:
        if origin != self._tt["origin"]:
            return []
        out: list[Departure] = []
        for svc in self._tt["services"]:
            minutes = svc["duration_min"].get(destination)
            if minutes is None:
                continue
            day = now.replace(hour=0, minute=0, second=0, microsecond=0)
            h, m = map(int, svc["first"].split(":"))
            lh, lm = map(int, svc["last"].split(":"))
            t, last = day + timedelta(hours=h, minutes=m), day + timedelta(hours=lh, minutes=lm)
            count = 0
            while t <= last and count < limit:
                if t >= now:
                    out.append(Departure(train_type=svc["train_type"], departs_at=t,
                                         arrives_at=t + timedelta(minutes=minutes), origin=origin,
                                         destination=destination, source_title=self._tt["source_title"],
                                         source_url=self._tt.get("source_url"), verified_at=self._tt.get("verified_at")))
                    count += 1
                t += timedelta(minutes=svc["every_min"])
        return sorted(out, key=lambda d: d.departs_at)


class MockMapStore:
    def __init__(self, path: str = "demo_map.json"):
        self._graph = _load(path)

    def get_graph(self, map_id: str, map_version: str) -> dict:
        if map_id != self._graph["map_id"]:
            raise KeyError(f"등록되지 않은 지도: {map_id}")
        if map_version != self._graph["map_version"]:
            raise MapVersionMismatch(f"{map_version} != {self._graph['map_version']}")
        return self._graph
