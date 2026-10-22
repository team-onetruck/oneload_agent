"""R2 내부 계약 타입 — API 명세 v0.3 10장을 그대로 코드로 옮긴 것.

- 필드 이름은 명세와 1:1 로 맞춘다. 명세에 없던 값 · 필드는 `# [제안]` 으로 표시하고
  docs/decisions.md 에 결정 카드로 남긴다 (R4 와 합의 후 확정).
- 모든 모델은 알 수 없는 필드를 거부한다(extra="forbid") → 팀원 간 오타 · 계약 불일치를 바로 드러냄.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Language = Literal["ko", "en", "ja", "zh"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ── 입력: Backend(R4) → Agent ───────────────────────────────────────────
class ChatTurn(_Strict):
    role: Literal["user", "assistant"]
    text: str
    # [제안] assistant 턴의 status. 추가 질문 횟수를 세는 데 사용
    status: str | None = None


class SessionContext(_Strict):
    session_id: str
    language: Language = "ko"
    map_id: str
    map_version: str
    state: str  # 세션 state (명세 4장). Agent 는 읽기만 한다
    destination: dict | None = None  # {"poi_id": ...}
    confirmed_location: dict | None = None  # {"node_id": ..., "floor_id": ...}
    recent_messages: list[ChatTurn] = Field(default_factory=list)  # [제안] 최근 6턴


# ── Tool 결과: R3 · R4 → Agent ─────────────────────────────────────────
class KnowledgeHit(_Strict):
    document_id: str
    title: str
    excerpt: str
    source_url: str | None = None
    verified_at: str | None = None
    scope: str | None = None


class POI(_Strict):
    poi_id: str
    name_ko: str
    name_en: str | None = None
    category: str
    floor_id: str
    nearest_node_id: str
    aliases: list[str] = Field(default_factory=list)
    verified: bool


# ── 출력: Agent → Backend ──────────────────────────────────────────────
AgentStatus = Literal[
    "needs_clarification",  # 명세 8.5
    "destination_set",  # 명세 8.5
    "answered",  # [제안] 이용 방법 답변 (출처 필수)
    "needs_review",  # [제안] 근거 자료 없음 → 추측하지 않고 보류
    "needs_location",  # 명세 7장 시퀀스 B ("길 잘못 든 것 같아")
    "denied",  # [제안] 위험 · 범위 밖 요청
]
NextAction = Literal["reply", "locate", "navigate", "none"]  # navigate · none 은 [제안]


class Destination(_Strict):
    poi_id: str
    display_name: str | None = None


class SuggestedReply(_Strict):
    label: str = Field(min_length=1, max_length=40)
    selected_poi_id: str | None = None


class Source(_Strict):
    document_id: str
    title: str
    source_url: str | None = None
    verified_at: str | None = None


class AgentResponse(_Strict):
    status: AgentStatus
    intent: str
    assistant_message: str = Field(min_length=1, max_length=800)
    destination: Destination | None = None
    suggested_replies: list[SuggestedReply] = Field(default_factory=list, max_length=4)
    sources: list[Source] = Field(default_factory=list, max_length=4)
    next_action: NextAction

    @model_validator(mode="after")
    def _rules(self) -> "AgentResponse":
        # 규칙 1. 목적지 확정이면 destination 필수, 아니면 destination 금지
        if (self.status == "destination_set") != (self.destination is not None):
            raise ValueError("destination 은 destination_set 일 때만, 그리고 반드시 있어야 함")
        # 규칙 2. 이용 방법 답변은 출처 1개 이상 (근거 없는 안내 금지)
        if self.status == "answered" and not self.sources:
            raise ValueError("answered 응답에 sources 가 없음")
        # 규칙 3. 보류 · 거절 · 위치 재확인에는 출처 · 추천 답변을 싣지 않음
        if self.status in ("needs_review", "denied", "needs_location") and (self.sources or self.suggested_replies):
            raise ValueError(f"{self.status} 응답에는 sources · suggested_replies 를 넣지 않음")
        # 규칙 4. 상태별 next_action 고정 (화면 분기를 단순하게)
        allowed = {
            "needs_clarification": {"reply"},
            "destination_set": {"locate", "navigate"},
            "answered": {"none"},
            "needs_review": {"none"},
            "needs_location": {"locate"},
            "denied": {"none"},
        }[self.status]
        if self.next_action not in allowed:
            raise ValueError(f"{self.status} 의 next_action 은 {sorted(allowed)} 중 하나")
        return self


# ── Visual Localization: Backend → R2 → Backend ────────────────────────
class LocationCandidate(_Strict):
    node_id: str
    floor_id: str
    rank_score: float = Field(ge=0.0, le=1.0)  # 상대 점수. 확률 · % 로 표시 금지 (명세 3장)
    heading_deg: float | None = Field(default=None, ge=0.0, lt=360.0)
    landmark_id: str | None = None
    evidence: list[str] = Field(default_factory=list)


class LocalizationContext(_Strict):
    floor_hint: str | None = None
    last_node_id: str | None = None
    ar_session_id: str | None = None
    ar_pose_at_capture: dict | None = None
    # [제안] 직전 촬영의 후보. 여러 장을 합쳐 판단(다중 촬영 결합)할 때 Backend 가 넘겨줌
    previous_candidates: list[LocationCandidate] = Field(default_factory=list)
    user_text: str | None = None  # [제안] "에스컬레이터 앞이야" 같은 사용자 설명


class LocationEstimate(_Strict):
    status: Literal["needs_confirmation", "no_match"]
    candidates: list[LocationCandidate] = Field(default_factory=list, max_length=3)
    # [제안] 후보가 비슷할 때 다음 촬영 안내 문장 (예: "왼쪽 '출구 1' 방향으로 한 장 더")
    hint: str | None = None

    @model_validator(mode="after")
    def _rules(self) -> "LocationEstimate":
        if (self.status == "no_match") != (not self.candidates):
            raise ValueError("no_match 는 후보 0개, needs_confirmation 은 후보 1개 이상")
        scores = [c.rank_score for c in self.candidates]
        if scores != sorted(scores, reverse=True):
            raise ValueError("candidates 는 rank_score 내림차순")
        return self
