"""Custom agents extending CAMEL-AI base classes."""

from .custom_agent import CustomChatAgent, create_custom_model
from .specialized_agent import SpecializedAgent

__all__ = ["CustomChatAgent", "SpecializedAgent", "create_custom_model"]
