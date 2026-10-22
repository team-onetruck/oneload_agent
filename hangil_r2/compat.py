"""백엔드와 '같은 클래스' 여야 하는 것을 고르는 곳.

hangil_r2/backend/ 는 백엔드 규격의 사본이다. 모양(스키마)은 같아도 파이썬에서는 다른 클래스라서,
백엔드가 `except app.ai.contracts.VisionUnavailable` 로 잡는 예외를 사본 클래스로 던지면 잡히지 않는다
(→ 503 대신 500, 이슈 #1).

규칙
- 백엔드 안에서 실행되면(app.ai.contracts 를 import 할 수 있으면) 백엔드의 진짜 클래스를 쓴다.
- R2 단독 실행(테스트 · 평가)이면 사본을 쓴다.
- 고르는 시점은 '던지거나 잡는 순간' (import 순서에 따라 결과가 달라지지 않게).
"""

from __future__ import annotations

from .backend.contracts import VisionUnavailable as MirrorVisionUnavailable


def vision_unavailable_class() -> type[Exception]:
    """LLMVision 이 던질 예외 클래스. 백엔드가 있으면 백엔드 것, 없으면 사본."""
    try:
        from app.ai.contracts import VisionUnavailable as backend_cls  # 백엔드 환경에서만 존재
    except ImportError:
        return MirrorVisionUnavailable
    # 이름만 같은 다른 'app' 패키지일 수도 있으니 예외 클래스인지 확인
    if isinstance(backend_cls, type) and issubclass(backend_cls, Exception):
        return backend_cls
    return MirrorVisionUnavailable


def vision_unavailable_types() -> tuple[type[Exception], ...]:
    """잡을 때 쓰는 목록: 사본과 백엔드 클래스를 모두 잡는다 (Vision 구현이 어느 쪽을 던져도 처리)."""
    cls = vision_unavailable_class()
    return (MirrorVisionUnavailable,) if cls is MirrorVisionUnavailable else (MirrorVisionUnavailable, cls)
