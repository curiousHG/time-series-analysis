"""Closed value sets persisted in DB rows or shared across layers.

Members are `str`, so they compare equal to stored VARCHAR values; columns stay plain strings.
"""

from __future__ import annotations

from enum import StrEnum


class SourceStatus(StrEnum):
    AVAILABLE = "available"
    PENDING = "pending"
    UNAVAILABLE = "unavailable"


class RunStatus(StrEnum):
    DRAFT = "draft"
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class TaskStatus(StrEnum):
    IDLE = "idle"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
