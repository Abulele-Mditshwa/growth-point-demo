"""
RAG service for the Growthpoint Knowledge Assistant.

Chooses the mock client (default, no AWS) or the live Bedrock client based on
config.use_mock, and exposes a single chat method to the frontend.
"""

from typing import List

from models.chat_models import ChatMessage, MessageRole
from config import Config


class RAGService:
    """Orchestrates retrieval + grounded generation."""

    def __init__(self, config: Config):
        self.config = config
        if config.use_mock:
            from clients.mock_client import MockKnowledgeBaseClient
            self.client = MockKnowledgeBaseClient(config)
            self.mode = "mock"
        else:
            from clients.bedrock_client import BedrockKnowledgeBaseClient
            self.client = BedrockKnowledgeBaseClient(config)
            self.mode = "bedrock"

    def chat(
        self,
        message: str,
        user_level: str,
        chat_history: List[ChatMessage] = None,
    ) -> ChatMessage:
        result = self.client.retrieve_and_generate(message, user_level, chat_history)
        return ChatMessage(
            role=MessageRole.ASSISTANT,
            content=result["response"],
            sources=result.get("sources") or [],
            answered=result.get("answered", True),
        )

    def chat_stream(
        self,
        message: str,
        user_level: str,
        chat_history: List[ChatMessage] = None,
    ):
        """Yield streaming events from the active client.

        Events: {"type": "sources"|"delta"|"done"|"error", ...}. Both the mock and
        the live Bedrock clients implement retrieve_and_generate_stream with the
        same protocol, so the frontend streams identically in either mode.
        """
        yield from self.client.retrieve_and_generate_stream(
            message, user_level, chat_history
        )

    def status(self) -> dict:
        return self.client.test_connection()
