from pathlib import Path

import pytest

from hangil_r2.backend.contracts import AgentContext, Slots
from hangil_r2.backend.mapdata import MapData, MapStore

MAPS = Path(__file__).resolve().parent.parent / "mocks" / "maps"


@pytest.fixture(scope="session")
def seoul() -> MapData:
    """oneload-data maps/seoul-station.json 사본 (노드 위치 · 문구 등은 답사 전 임시값)."""
    return MapStore.load(MAPS).get("seoul-station")


@pytest.fixture
def ctx(seoul):
    def make(lang="ko", slots=None, here=None, history=None) -> AgentContext:
        return AgentContext(
            language=lang,
            map=seoul,
            state="idle",
            destination_poi_id=None,
            confirmed_node_id=here,
            slots=slots or Slots(),
            recent_messages=history or [],
        )

    return make
