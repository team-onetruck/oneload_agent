"""[제안] 백엔드 match_location 개선안 — 같은 입력 · 출력 (app/domain/localization.py).

백엔드는 지금 '정규화 후 완전 일치 개수' 로 점수를 매긴다. 이 모듈은 같은 시그니처로 세 가지를 더한다.
0. 지점 단위 후보      : 한 지점에 표지판(landmark)이 여러 개여도 후보는 하나 (백엔드 match_location 과 같음)
1. 드문 문구 가중 (IDF) : '공항철도' 처럼 여러 지점에 있는 문구보다 '4호선' 처럼 한 곳에만 있는 문구를 더 강한 증거로
2. 부분 · 오타 일치     : OCR 이 한두 글자 틀리거나("Airport Raiload") 문구 일부만 읽혀도 낮은 점수로 인정
3. 동점일 때 안내       : 1 · 2위가 비슷하면 1위에만 있는 문구를 찍어 보라는 hint (백엔드 retake_hint 와 별개)

백엔드 코드는 R4 소관이라 직접 바꾸지 않는다. tests/test_ranker.py 로 백엔드 기존 기대 동작을 그대로 지키는지 확인하고,
R4 가 원하면 rank_location 을 match_location 자리에 넣으면 된다 (docs/decisions.md D-26).
"""

from __future__ import annotations

import math
import re
from difflib import SequenceMatcher

from .backend.localization_types import Candidate, Hints, VisionFeatures
from .backend.mapdata import Lang, MapData

MAX_CANDIDATES = 3
HINT_BONUS = 0.5  # 백엔드 _HINT_BONUS 와 같은 값
W_EXACT, W_CONTAIN, W_FUZZY = 1.0, 0.7, 0.5
AMBIGUITY_MARGIN = 0.1


def normalise(text: str) -> str:
    """백엔드 _normalise 와 같은 규칙 (공백 · 하이픈 · 가운뎃점 · 마침표 · 쉼표 · 따옴표 제거, 소문자)."""
    return re.sub(r"[\s\-·.,'’]", "", text).lower()


def text_match(seen: str, sign: str) -> float:
    a, b = normalise(seen), normalise(sign)
    if not a or not b:
        return 0.0
    if a == b:
        return W_EXACT
    if min(len(a), len(b)) >= 3 and (a in b or b in a):
        return W_CONTAIN
    return W_FUZZY if SequenceMatcher(None, a, b).ratio() >= 0.85 else 0.0


def rank_location(features: VisionFeatures, map_: MapData, hints: Hints, lang: Lang) -> list[Candidate]:
    """한 지점 = 후보 하나. 지점에 표지판(landmark)이 여러 개면 문구를 모아 한 번에 점수를 매긴다 (백엔드 match_location 과 같음)."""
    if features.image_quality != "usable":
        return []
    signs = _signs_by_node(map_)
    total = max(len(signs), 1)
    neighbours = _neighbours(map_, hints.last_node_id)
    scores: dict[str, float] = {}
    evidence: dict[str, dict[str, str]] = {}  # node id -> 정규화 문구 -> 표지판에 적힌 문구
    hits: dict[str, dict[str, int]] = {}  # node id -> landmark id -> 걸린 문구 수
    for seen in dict.fromkeys(t.text for t in features.texts):
        per_node = {}
        for node_id, node_signs in signs.items():
            # 문구가 빈 표지판만 있는 지점도 멈추지 않게 default (지도 규격이 빈 sign_texts 를 허용)
            best = max(
                ((text_match(seen, s), s, lm_id) for s, lm_id in node_signs), key=lambda x: x[0], default=(0.0, "", "")
            )
            if best[0] > 0:
                per_node[node_id] = best
        if not per_node:
            continue
        idf = math.log(1 + total / len(per_node))  # 걸리는 지점이 적을수록 큰 가중치
        for node_id, (m, sign, lm_id) in per_node.items():
            scores[node_id] = scores.get(node_id, 0.0) + m * idf
            evidence.setdefault(node_id, {}).setdefault(normalise(sign), sign)
            node_hits = hits.setdefault(node_id, {})
            node_hits[lm_id] = node_hits.get(lm_id, 0) + 1

    ranked = []
    for node_id, score in scores.items():
        node = map_.node_by_id[node_id]
        score += HINT_BONUS if hints.floor_hint == node.floor_id else 0
        score += HINT_BONUS if node.id in neighbours else 0
        ranked.append(
            (
                score,
                Candidate(
                    node_id=node.id,
                    floor_id=node.floor_id,
                    rank_score=round(score / (score + 1), 2),
                    landmark_id=max(hits[node_id], key=hits[node_id].__getitem__),  # 가장 많이 걸린 표지판
                    evidence=[
                        f"Sign reads “{s}”" if lang == "en" else f"“{s}” 안내판" for s in evidence[node_id].values()
                    ],
                ),
            )
        )
    ranked.sort(key=lambda x: x[0], reverse=True)
    return [c for _, c in ranked[:MAX_CANDIDATES]]


def ambiguity_hint(candidates: list[Candidate], map_: MapData, lang: Lang) -> str | None:
    """1 · 2위가 비슷하면, 1위 지점에만 있는 문구를 찍어 보라는 안내."""
    if len(candidates) < 2 or candidates[0].rank_score - candidates[1].rank_score >= AMBIGUITY_MARGIN:
        return None
    signs = _signs_by_node(map_)
    other = {normalise(s) for s, _ in signs.get(candidates[1].node_id, [])}
    for s, _ in signs.get(candidates[0].node_id, []):
        if normalise(s) not in other:
            return (
                f"If you see a “{s}” sign nearby, take one more photo of it."
                if lang == "en"
                else f"주변에 “{s}” 안내판이 보이면 한 장 더 찍어 주세요."
            )
    return None


def _signs_by_node(map_: MapData) -> dict[str, list[tuple[str, str]]]:
    """node id -> [(표지판 문구, landmark id)], 지도 순서대로."""
    signs: dict[str, list[tuple[str, str]]] = {}
    for lm in map_.landmarks:
        signs.setdefault(lm.node_id, []).extend((s, lm.landmark_id) for s in lm.sign_texts)
    return signs


def _neighbours(map_: MapData, node_id: str | None) -> set[str]:
    if node_id is None:
        return set()
    return {e.to for e in map_.edges if e.from_ == node_id} | {e.from_ for e in map_.edges if e.to == node_id}
