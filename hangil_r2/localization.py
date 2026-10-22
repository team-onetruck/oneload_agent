"""Visual Localization — estimate_location (명세 v0.3 10장 · 8.6). 방법 설명: docs/localization.md

한 줄 요약: "사진에서 읽은 글자 · 시설을, 지도에 측량해 둔 표지판 · 시설과 대조해 노드 후보에 순위를 매긴다."

처리 순서
1. 입력 검사      : 빈 이미지 · 크기 초과 · 형식 오류 거절
2. 장면 읽기 (VLM): 사진에서 보이는 글자(texts)와 시설 종류(objects)만 추출 — 위치 판단은 LLM 에 맡기지 않음
3. 대조 점수      : 노드별로 표지판 문구 일치(흔한 문구일수록 낮은 가중치) + 시설 일치
4. 보조 단서      : 직전 위치 인접 · 층 힌트 · 직전 촬영 후보 · 사용자 설명 — 증거가 있는 노드의 순위만 조정
5. 후보 · 방향    : 상위 3개, 방향 = 일치한 표지판이 바라보는 방향의 반대
6. 애매하면 안내  : 1 · 2위가 비슷하면 둘을 가르는 표지판을 찍도록 hint 제공. 증거 0 이면 no_match

원칙: 자동 확정하지 않는다. 항상 사용자 확인(needs_confirmation)을 거친다 (명세 3장 rank_score 규칙).
"""
from __future__ import annotations

import logging
import math
import os
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from .contracts import LocalizationContext, LocationCandidate, LocationEstimate
from .llm import LLM, LLMError, LLMUnavailable
from .tools import MapStore

log = logging.getLogger("hangil_r2.localization")

MAX_IMAGE_BYTES = int(os.getenv("MAX_IMAGE_BYTES", str(2 * 1024 * 1024)))  # 명세 3장 이미지 정책 2MB
MAX_CANDIDATES = 3
AMBIGUITY_MARGIN = float(os.getenv("AMBIGUITY_MARGIN", "0.1"))  # 1 · 2위 rank_score 차가 이보다 작으면 hint

# 가중치 (평가셋으로 조정할 값. 바꾸면 docs/experiments 에 기록)
W_EXACT, W_CONTAIN, W_FUZZY = 1.0, 0.7, 0.5
W_FEATURE = 0.5
W_USER_TEXT = 0.8  # 사용자 설명은 사진보다 약하게
P_SAME_NODE, P_NEIGHBOR, P_FLOOR = 0.3, 0.2, 0.2
F_OTHER_FLOOR = 0.7  # 층 힌트와 다른 층이면 곱함
W_PREVIOUS = 0.5

# 사용자 설명 속 시설 단어 → objects 값
USER_WORDS = {"계단": "stairs", "에스컬레이터": "escalator", "엘리베이터": "elevator", "발매기": "ticket_machine",
              "개찰구": "gate", "승강장": "platform", "환급기": "refund_machine", "안내센터": "information_desk",
              "stairs": "stairs", "escalator": "escalator", "elevator": "elevator", "gate": "gate"}
OBJECT_KO = {"stairs": "계단", "escalator": "에스컬레이터", "elevator": "엘리베이터", "ticket_machine": "발매기",
             "gate": "개찰구", "platform": "승강장", "refund_machine": "환급기", "information_desk": "안내센터"}

JPEG, PNG = b"\xff\xd8\xff", b"\x89PNG"


class InvalidImage(ValueError):
    """Backend 가 413 IMAGE_TOO_LARGE / 415 UNSUPPORTED_IMAGE 로 변환."""


def norm(s: str) -> str:
    """비교용 정규화: 전각/반각 통일(NFKC) · 소문자 · 공백 · 기호 제거."""
    s = unicodedata.normalize("NFKC", s).lower()
    return "".join(ch for ch in s if ch.isalnum())


def text_match(detected: str, sign: str) -> float:
    d, s = norm(detected), norm(sign)
    if not d or not s:
        return 0.0
    if d == s:
        return W_EXACT
    if min(len(d), len(s)) >= 2 and (s in d or d in s):
        return W_CONTAIN
    return W_FUZZY if SequenceMatcher(None, d, s).ratio() >= 0.8 else 0.0  # OCR 한두 글자 오류 허용


@dataclass
class _NodeIndex:
    node_id: str
    floor_id: str
    signs: list[dict] = field(default_factory=list)  # landmark(reference_type=sign)
    features: set[str] = field(default_factory=set)


class VisualLocalizer:
    def __init__(self, llm: LLM, map_store: MapStore):
        self.llm, self.map_store = llm, map_store

    # ── 진입점 (Backend 가 호출) ────────────────────────────────────────
    def estimate_location(self, image_bytes: bytes, map_id: str, map_version: str,
                          context: LocalizationContext) -> LocationEstimate:
        self._check_image(image_bytes)
        graph = self.map_store.get_graph(map_id, map_version)  # MapVersionMismatch → Backend 409
        try:
            scene = self.llm.read_scene(image_bytes)
        except LLMUnavailable:
            raise  # Backend 503
        except LLMError as exc:  # 형식 깨짐 → 위치를 추측하지 않고 재촬영 요청
            log.warning("scene_parse_failed %s", exc)
            scene = {"texts": [], "objects": []}
        log.info("scene texts=%s objects=%s", scene["texts"], scene["objects"])
        return self.rank(graph, scene, context)

    # ── 점수 계산 (LLM 없이 시험 가능하도록 분리) ───────────────────────
    def rank(self, graph: dict, scene: dict, ctx: LocalizationContext) -> LocationEstimate:
        nodes = self._index(graph)
        if not nodes:
            return LocationEstimate(status="no_match", hint="지도 데이터에 표지판 정보가 없어요.")
        texts = scene.get("texts", [])
        objects = set(scene.get("objects", []))
        user_objects = {v for k, v in USER_WORDS.items() if ctx.user_text and k in ctx.user_text.lower()}
        text_hits = self._text_hits(nodes, texts)
        feat_idf = self._feature_idf(nodes)
        neighbors = self._neighbors(graph)
        prev = {c.node_id: c.rank_score for c in ctx.previous_candidates}

        scored = []
        for n in nodes.values():
            evidence: list[str] = []
            best: tuple[float, dict | None] = (0.0, None)  # (기여 점수, 표지판 landmark)
            raw = 0.0
            for contrib, lm, sign_text in text_hits.get(n.node_id, []):  # 표지판 문구 일치
                raw += contrib
                evidence.append(f"'{sign_text}' 안내판 식별")
                if contrib > best[0]:
                    best = (contrib, lm)
            for obj in n.features & objects:  # 사진 속 시설 일치
                raw += W_FEATURE * feat_idf[obj]
                evidence.append(f"{OBJECT_KO.get(obj, obj)} 확인")
            for obj in n.features & user_objects:  # 사용자 설명 속 시설
                raw += W_FEATURE * W_USER_TEXT * feat_idf[obj]
                evidence.append(f"사용자 설명: {OBJECT_KO.get(obj, obj)}")
            if raw == 0:
                continue  # 증거 없는 노드는 보조 단서만으로 후보가 되지 않음
            # 보조 단서: 순위 조정만
            if ctx.last_node_id == n.node_id:
                raw += P_SAME_NODE
                evidence.append("직전 확정 위치와 같음")
            elif ctx.last_node_id and n.node_id in neighbors.get(ctx.last_node_id, set()):
                raw += P_NEIGHBOR
                evidence.append(f"직전 위치 {ctx.last_node_id}와 인접")
            if ctx.floor_hint:
                raw = raw + P_FLOOR if ctx.floor_hint == n.floor_id else raw * F_OTHER_FLOOR
            if n.node_id in prev:
                raw += W_PREVIOUS * prev[n.node_id]
                evidence.append("이전 사진 후보와 일치")
            sign = best[1]
            heading = (sign["facing_deg"] + 180) % 360 if sign and "facing_deg" in sign else None
            scored.append((raw, n, sign, heading, list(dict.fromkeys(evidence))[:4]))

        if not scored:
            return LocationEstimate(status="no_match",
                                    hint="위치를 알아보기 어려워요. 출구 번호나 안내판이 보이게 다시 찍어 주세요.")
        scored.sort(key=lambda x: x[0], reverse=True)
        cands = [LocationCandidate(node_id=n.node_id, floor_id=n.floor_id, rank_score=round(raw / (raw + 1), 2),
                                   heading_deg=heading, landmark_id=sign["id"] if sign else None, evidence=ev)
                 for raw, n, sign, heading, ev in scored[:MAX_CANDIDATES]]
        return LocationEstimate(status="needs_confirmation", candidates=cands, hint=self._hint(cands, nodes))

    # ── 도우미 ───────────────────────────────────────────────────────
    @staticmethod
    def _check_image(image_bytes: bytes) -> None:
        if not image_bytes:
            raise InvalidImage("빈 이미지")
        if len(image_bytes) > MAX_IMAGE_BYTES:
            raise InvalidImage(f"이미지 크기 초과: {len(image_bytes)} bytes")
        if not (image_bytes.startswith(JPEG) or image_bytes.startswith(PNG)):
            raise InvalidImage("JPEG · PNG 만 허용")

    @staticmethod
    def _index(graph: dict) -> dict[str, _NodeIndex]:
        nodes = {n["id"]: _NodeIndex(n["id"], n["floor_id"]) for n in graph.get("nodes", [])}
        for lm in graph.get("landmarks", []):
            node = nodes.get(lm.get("node_id"))
            if node is None:
                continue
            if lm.get("reference_type") == "sign" and lm.get("text"):
                node.signs.append(lm)
            elif lm.get("reference_type") == "feature" and lm.get("kind"):
                node.features.add(lm["kind"])
        return nodes

    @staticmethod
    def _text_hits(nodes: dict[str, _NodeIndex], texts: list[str]) -> dict[str, list[tuple[float, dict, str]]]:
        """읽힌 문구마다 일치하는 노드를 찾고, '그 문구가 얼마나 드문가'(IDF)로 가중한다.

        예: 'Tickets' 는 여러 노드 표지판에 걸리므로 낮은 가중치, '출구 1' 은 한 노드에만 있어 높은 가중치.
        반환: {node_id: [(기여 점수, 표지판 landmark, 일치한 표지판 문구), ...]}
        """
        total = len(nodes)
        out: dict[str, list[tuple[float, dict, str]]] = {}
        for d in dict.fromkeys(texts):  # 같은 문구 중복 제거
            per_node = {}
            for n in nodes.values():
                best = max(((text_match(d, s), lm, s) for lm in n.signs for s in lm["text"]),
                           key=lambda x: x[0], default=(0.0, None, ""))
                if best[0] > 0:
                    per_node[n.node_id] = best
            if not per_node:
                continue
            idf = math.log(1 + total / len(per_node))
            for nid, (m, lm, s) in per_node.items():
                out.setdefault(nid, []).append((m * idf, lm, s))
        return out

    @staticmethod
    def _feature_idf(nodes: dict[str, _NodeIndex]) -> dict[str, float]:
        """흔한 시설(예: 계단이 여러 곳)은 낮은 가중치."""
        df: dict[str, int] = {}
        for n in nodes.values():
            for f in n.features:
                df[f] = df.get(f, 0) + 1
        return {k: math.log(1 + len(nodes) / v) for k, v in df.items()}

    @staticmethod
    def _neighbors(graph: dict) -> dict[str, set[str]]:
        adj: dict[str, set[str]] = {}
        for e in graph.get("edges", []):
            adj.setdefault(e["from"], set()).add(e["to"])
            if e.get("bidirectional", True):
                adj.setdefault(e["to"], set()).add(e["from"])
        return adj

    @staticmethod
    def _hint(cands: list[LocationCandidate], nodes: dict[str, _NodeIndex]) -> str | None:
        """1 · 2위가 비슷하면, 1위에만 있는 표지판을 찍어 보라고 안내 (다음 사진으로 둘을 가름)."""
        if len(cands) < 2 or cands[0].rank_score - cands[1].rank_score >= AMBIGUITY_MARGIN:
            return None
        a, b = nodes[cands[0].node_id], nodes[cands[1].node_id]
        b_texts = {norm(t) for lm in b.signs for t in lm["text"]}
        for lm in a.signs:
            for text in lm["text"]:
                if norm(text) not in b_texts:
                    return f"주변에 '{text}' 표지판이 보이면 그쪽을 한 장 더 찍어 주세요."
        return "다른 방향의 표지판도 한 장 더 찍어 주세요."
