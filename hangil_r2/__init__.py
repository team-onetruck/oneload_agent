"""한길 R2 모듈: AI Agent(handle_message) · Visual Localization(estimate_location).

Backend(R4) 연결 예:
    from hangil_r2 import Agent, VisualLocalizer, OpenAILLM
    agent = Agent(OpenAILLM(), search_knowledge=r3_search, search_pois=r4_search_pois)
    resp = agent.handle_message(ctx, "공항 가는 티켓 어디서 사?", None)
"""
from .agent import Agent
from .contracts import (AgentResponse, KnowledgeHit, LocalizationContext, LocationEstimate, POI,
                        SessionContext)
from .llm import FakeLLM, LLMError, LLMUnavailable, OpenAILLM
from .localization import InvalidImage, VisualLocalizer
from .tools import MapVersionMismatch, ToolUnavailable

__all__ = ["Agent", "VisualLocalizer", "OpenAILLM", "FakeLLM", "AgentResponse", "LocationEstimate",
           "SessionContext", "LocalizationContext", "KnowledgeHit", "POI", "LLMError", "LLMUnavailable",
           "ToolUnavailable", "MapVersionMismatch", "InvalidImage"]
