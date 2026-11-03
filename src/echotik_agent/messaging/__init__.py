"""Módulo de Mass Messaging HITL (Wechaty + Batch Review + Queue)."""
from __future__ import annotations

from .models import (
    Contact,
    ContactSource,
    Template,
    MessageQueueItem,
    BatchReview,
    BatchStatus,
    MessageStatus,
    ContactSourceConfig,
    WechatyConfig,
    QueueConfig,
)
from .wechaty_client import WechatyClient, WechatyConfig, WechatySendError
from .contact_sources import ContactSourceResolver, ContactSourceError, ContactSourceResolverFactory
from .template_engine import TemplateEngine, TemplateError
from .queue import MessageQueueScheduler, QueueConfig, QueueError

__all__ = [
    "Contact",
    "ContactSource",
    "Template",
    "MessageQueueItem",
    "BatchReview",
    "BatchStatus",
    "MessageStatus",
    "ContactSourceConfig",
    "WechatyClient",
    "WechatyConfig",
    "WechatySendError",
    "ContactSourceResolver",
    "ContactSourceError",
    "ContactSourceResolverFactory",
    "TemplateEngine",
    "TemplateError",
    "MessageQueueScheduler",
    "QueueConfig",
    "QueueError",
]