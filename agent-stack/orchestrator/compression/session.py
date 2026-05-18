"""Session model for the compression manager."""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


class SessionStatus(str, enum.Enum):
    ACTIVE = "active"
    CLOSED = "closed"
    EXPIRED = "expired"


@dataclass
class Session:
    """Represents an isolated compression session."""

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    status: SessionStatus = SessionStatus.ACTIVE
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    last_accessed: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    metadata: dict = field(default_factory=dict)

    def touch(self) -> None:
        """Update last_accessed to now."""
        self.last_accessed = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "status": self.status.value,
            "created_at": self.created_at,
            "last_accessed": self.last_accessed,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Session:
        data["status"] = SessionStatus(data["status"])
        return cls(**data)
