"""Domain values shared by the database and API layers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


class InvalidPunch(ValueError):
    """A Savior row cannot be represented as an HRMS Employee Checkin."""


@dataclass(frozen=True)
class Punch:
    queue_id: int
    card_number: str
    timestamp: datetime
    raw_direction: str | None
    attempts: int = 0

    @property
    def log_type(self) -> str | None:
        value = (self.raw_direction or "").strip().upper()
        if value in {"I", "IN"}:
            return "IN"
        if value in {"O", "OUT"}:
            return "OUT"
        if value in {"", "N"}:
            return None
        raise InvalidPunch(f"Unsupported Savior inout value: {self.raw_direction!r}")

    def validate(self) -> None:
        if not self.card_number.strip():
            raise InvalidPunch("Punch has no card number")
        if not isinstance(self.timestamp, datetime):
            raise InvalidPunch("Punch timestamp is not a datetime")
        self.log_type
