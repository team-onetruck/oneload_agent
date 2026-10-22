# MIRROR of team-onetruck/oneload-backend@0019f55 app/domain/localization.py (타입만) — 직접 수정 금지.
# 백엔드가 바뀌면 python scripts/check_backend_compat.py <backend 경로> 로 차이를 확인한 뒤 다시 복사한다.
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel

from .mapdata import Lang, MapData  # noqa: F401 (원본과 같은 import 유지)

ImageQuality = Literal["usable", "blurry", "too_dark", "no_signage", "too_close"]
TextKind = Literal["line_name", "line_number", "exit_number", "facility_name", "direction", "other"]

MAX_CANDIDATES = 3
_HINT_BONUS = 0.5


class VisionText(BaseModel):
    text: str
    lang: str | None = None
    kind: TextKind = "other"


class VisionFeatures(BaseModel):
    image_quality: ImageQuality
    scene_type: str | None = None
    texts: list[VisionText] = []
    facilities: list[str] = []
    person_visible: bool = False


class Candidate(BaseModel):
    node_id: str
    floor_id: str
    rank_score: float  # relative ordering only; never shown to users as a probability
    heading_deg: float | None = None
    landmark_id: str
    evidence: list[str]


@dataclass(frozen=True)
class Hints:
    floor_hint: str | None = None
    last_node_id: str | None = None

