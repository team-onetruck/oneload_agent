"""LLMVision — 백엔드 VisionExtractor 규격 · VisionFeatures 모양 확인."""

import pytest

from hangil_r2 import FakeLLM, LLMVision
from hangil_r2.backend.contracts import VisionUnavailable
from hangil_r2.llm import LLMError, validate_scene


def test_extracts_backend_features():  # V-01
    scene = {
        "image_quality": "usable",
        "scene_type": "concourse",
        "person_visible": True,
        "texts": [{"text": "공항철도", "lang": "ko", "kind": "line_name"}, {"text": "4호선", "kind": "line_number"}],
        "facilities": ["escalator"],
    }
    f = LLMVision(FakeLLM(scene=scene)).extract(b"img")
    assert f.image_quality == "usable" and [t.text for t in f.texts] == ["공항철도", "4호선"]
    assert f.texts[1].kind == "line_number" and f.person_visible


def test_unknown_kind_becomes_other_and_bad_quality_rejected():  # V-02
    out = validate_scene({"image_quality": "usable", "texts": [{"text": "Exit 1", "kind": "exit"}]})
    assert out["texts"][0]["kind"] == "other"
    with pytest.raises(LLMError):
        validate_scene({"image_quality": "great"})


def test_down_raises_vision_unavailable():  # V-03 백엔드가 503 → 수동 위치 선택
    with pytest.raises(VisionUnavailable):
        LLMVision(FakeLLM(down=True)).extract(b"img")


def test_broken_output_reads_nothing():  # V-04 형식 위반 → 글자 없음 → 백엔드가 재촬영 안내 (위치 추측 안 함)
    f = LLMVision(FakeLLM(broken=True)).extract(b"img")
    assert f.image_quality == "usable" and f.texts == []


# ── 백엔드 안에서 실행될 때: 백엔드가 잡는 '같은 클래스' 를 던져야 503 이 된다 (이슈 #1) ──────────
@pytest.fixture
def backend_contracts(monkeypatch):
    """백엔드의 app.ai.contracts 대역. 사본과 이름 · 내용이 같지만 다른 클래스 (실제 상황과 같음)."""
    import sys
    import types

    class BackendVisionUnavailable(Exception):
        pass

    mod = types.ModuleType("app.ai.contracts")
    mod.VisionUnavailable = BackendVisionUnavailable
    for name, m in (("app", types.ModuleType("app")), ("app.ai", types.ModuleType("app.ai")), ("app.ai.contracts", mod)):
        monkeypatch.setitem(sys.modules, name, m)
    return BackendVisionUnavailable


def test_down_raises_backend_class_inside_backend(backend_contracts):  # V-05
    with pytest.raises(backend_contracts) as info:
        LLMVision(FakeLLM(down=True)).extract(b"img")
    assert type(info.value) is backend_contracts  # 사본 클래스가 아님 → 백엔드 except 에 잡힘


def test_agent_survives_backend_class(backend_contracts, ctx):  # V-06 대화 사진 장애 → 사진 없이 말로만 답함
    from hangil_r2 import LLMAgent, MockKnowledgeSearch

    class DownVision:
        def extract(self, image):
            raise backend_contracts("down")

    r = LLMAgent(FakeLLM(), search_knowledge=MockKnowledgeSearch(), vision=DownVision()).handle_message(
        ctx(), "공항 가는 티켓 어디서 사?", b"\xff\xd8\xff", None
    )
    assert r.response.status == "needs_clarification"


def test_standalone_uses_mirror_class():  # V-07 R2 단독 실행이면 사본 클래스
    from hangil_r2.compat import vision_unavailable_class

    assert vision_unavailable_class() is VisionUnavailable
