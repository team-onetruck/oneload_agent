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
