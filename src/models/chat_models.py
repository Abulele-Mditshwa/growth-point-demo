"""
Data models for the Growthpoint Knowledge Assistant.
"""

from dataclasses import dataclass
from typing import List, Dict, Any, Optional
from enum import Enum


class MessageRole(Enum):
    """Enumeration for message roles."""
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


@dataclass
class Source:
    """A cited source document with metadata."""
    content: str
    title: str
    category: str
    doc_ref: str
    section: str
    access_level: str
    score: float

    def get_display_content(self, max_length: int = 240) -> str:
        """Get truncated content for display."""
        if len(self.content) <= max_length:
            return self.content
        return self.content[:max_length].rstrip() + "..."


@dataclass
class ChatMessage:
    """A chat message with optional cited sources."""
    role: MessageRole
    content: str
    sources: Optional[List[Source]] = None
    answered: bool = True  # False when the assistant stayed silent (no source)

    @property
    def role_str(self) -> str:
        return self.role.value
