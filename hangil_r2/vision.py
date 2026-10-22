"""사진 → 보이는 글자 · 시설 (백엔드 VisionExtractor 규격 구현).

백엔드 분담 (oneload-backend README · app/domain/localization.py):
- R2  : 사진에서 '보이는 것' 만 읽어 VisionFeatures 로 돌려준다 (이 파일)
- 백엔드: VisionFeatures 를 지도 landmark 와 대조해 위치 후보를 정한다 (match_location)
→ 모델이 위치를 지어낼 수 없다.

백엔드 연결: create_app(vision=LLMVision(OpenAILLM()))
"""

from __future__ import annotations

import logging

from .backend.contracts import VisionUnavailable
from .backend.localization_types import VisionFeatures
from .llm import LLM, LLMError, LLMUnavailable

log = logging.getLogger("hangil_r2.vision")


class LLMVision:
    def __init__(self, llm: LLM):
        self.llm = llm

    def extract(self, image: bytes) -> VisionFeatures:
        """이미지 형식 · 크기 검사는 백엔드(read_jpeg)가 이미 했다."""
        try:
            return VisionFeatures.model_validate(self.llm.read_scene(image))
        except LLMUnavailable as exc:
            # 백엔드가 503 AI_SERVICE_UNAVAILABLE → 화면이 수동 위치 선택으로 넘어감
            raise VisionUnavailable("사진 인식 서버에 연결하지 못했어요.") from exc
        except (LLMError, ValueError) as exc:
            # 형식이 깨진 응답: 위치를 추측하지 않고 '읽은 글자 없음' 으로 → 백엔드가 재촬영 안내
            log.warning("vision_parse_failed %s", exc)
            return VisionFeatures(image_quality="usable", texts=[])
