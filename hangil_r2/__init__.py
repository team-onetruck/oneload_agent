"""한길 R2 모듈 — oneload-backend 의 AI 규격(app/ai/contracts.py)에 맞춘 구현.

백엔드 연결 예 (app/main.py 의 create_app 기본값만 바꾸면 됨, 엔드포인트 변경 없음):
    from hangil_r2 import LLMAgent, LLMVision, OpenAILLM, MockKnowledgeSearch
    llm = OpenAILLM()
    app = create_app(agent=LLMAgent(llm, search_knowledge=MockKnowledgeSearch(), fallback=RuleAgent()),
                     vision=LLMVision(llm))
"""

from .agent import LLMAgent
from .knowledge import KnowledgeHit, MockDepartures, MockKnowledgeSearch, ToolUnavailable
from .llm import FakeLLM, LLMError, LLMUnavailable, OpenAILLM
from .ranker import ambiguity_hint, rank_location
from .vision import LLMVision

__all__ = [
    "LLMAgent",
    "LLMVision",
    "OpenAILLM",
    "FakeLLM",
    "LLMError",
    "LLMUnavailable",
    "KnowledgeHit",
    "MockKnowledgeSearch",
    "MockDepartures",
    "ToolUnavailable",
    "rank_location",
    "ambiguity_hint",
]
