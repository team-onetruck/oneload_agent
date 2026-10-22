"""[제안] 백엔드 match_location 개선안 — 같은 입력 · 출력 (app/domain/localization.py).

백엔드는 지금 '정규화 후 완전 일치 개수' 로 점수를 매긴다. 이 모듈은 같은 시그니처로 세 가지를 더한다.
1. 드문 문구 가중 (IDF) : '공항철도' 처럼 여러 landmark 에 있는 문구보다 '4호선' 처럼 한 곳에만 있는 문구를 더 강한 증거로
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
    if features.image_quality != "usable":
        return []
    total = max(len(map_.landmarks), 1)
    neighbours = _neighbours(map_, hints.last_node_id)
    scores: dict[str, float] = {}
    evidence: dict[str, list[str]] = {}
    for seen in dict.fromkeys(t.text for t in features.texts):
        per_lm = {}
        for lm in map_.landmarks:
            best = max(((text_match(seen, s), s) for s in lm.sign_texts), default=(0.0, ""))
            if best[0] > 0:
                per_lm[lm.landmark_id] = best
        if not per_lm:
            continue
        idf = math.log(1 + total / len(per_lm))  # 걸리는 landmark 가 적을수록 큰 가중치
        for lm_id, (m, sign) in per_lm.items():
            scores[lm_id] = scores.get(lm_id, 0.0) + m * idf
            evidence.setdefault(lm_id, []).append(f"Sign reads “{sign}”" if lang == "en" else f"“{sign}” 안내판")

    ranked = []
    by_id = {lm.landmark_id: lm for lm in map_.landmarks}
    for lm_id, score in scores.items():
        node = map_.node_by_id[by_id[lm_id].node_id]
        score += HINT_BONUS if hints.floor_hint == node.floor_id else 0
        score += HINT_BONUS if node.id in neighbours else 0
        ranked.append(
            (
                score,
                Candidate(
                    node_id=node.id,
                    floor_id=node.floor_id,
                    rank_score=round(score / (score + 1), 2),
                    landmark_id=lm_id,
                    evidence=evidence[lm_id],
                ),
            )
        )
    ranked.sort(key=lambda x: x[0], reverse=True)
    return [c for _, c in ranked[:MAX_CANDIDATES]]


def ambiguity_hint(candidates: list[Candidate], map_: MapData, lang: Lang) -> str | None:
    """1 · 2위가 비슷하면, 1위 landmark 에만 있는 문구를 찍어 보라는 안내."""
    if len(candidates) < 2 or candidates[0].rank_score - candidates[1].rank_score >= AMBIGUITY_MARGIN:
        return None
    by_id = {lm.landmark_id: lm for lm in map_.landmarks}
    other = {normalise(s) for s in by_id[candidates[1].landmark_id].sign_texts}
    for s in by_id[candidates[0].landmark_id].sign_texts:
        if normalise(s) not in other:
            return (
                f"If you see a “{s}” sign nearby, take one more photo of it."
                if lang == "en"
                else f"주변에 “{s}” 안내판이 보이면 한 장 더 찍어 주세요."
            )
    return None


def _neighbours(map_: MapData, node_id: str | None) -> set[str]:
    if node_id is None:
        return set()
    return {e.to for e in map_.edges if e.from_ == node_id} | {e.from_ for e in map_.edges if e.to == node_id}
