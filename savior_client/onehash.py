"""Authenticated client for the Savior mapping API on OneHash."""

from __future__ import annotations

import logging
from typing import Any

import requests

from .config import Settings
from .models import Punch


class DeliveryError(RuntimeError):
    """The checkin could not be confirmed in OneHash."""


class PermanentDeliveryError(DeliveryError):
    """OneHash rejected the punch and retrying unchanged data will not help."""


class OneHashClient:
    def __init__(self, settings: Settings, session: requests.Session | None = None):
        self.settings = settings
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"token {settings.erp_api_key}:{settings.erp_api_secret}",
                "Accept": "application/json",
                "User-Agent": "onehash-savior-client/0.1.0",
            }
        )
        self.endpoint = f"{settings.erp_url}/api/method/{settings.checkin_method}"
        self.logger = logging.getLogger(__name__)

    def send(self, punch: Punch) -> dict[str, Any]:
        punch.validate()
        payload: dict[str, str | int] = {
            "employee_code": punch.card_number,
            "timestamp": punch.timestamp.strftime("%Y-%m-%d %H:%M:%S.%f"),
            "device_id": self.settings.device_id,
        }
        if punch.log_type:
            payload["log_type"] = punch.log_type

        try:
            response = self.session.post(
                self.endpoint,
                data=payload,
                timeout=self.settings.request_timeout_seconds,
                verify=self.settings.verify_tls,
            )
        except requests.RequestException as exc:
            raise DeliveryError(f"Network error: {exc}") from exc

        body = self._json_body(response)
        if response.ok:
            return body
        if self._is_duplicate(response.status_code, body):
            self.logger.info(
                "Punch queue_id=%d already exists in OneHash; treating it as delivered",
                punch.queue_id,
            )
            return {"duplicate": True}
        message = self._error_message(body) or response.text[:500] or response.reason
        error = f"OneHash returned HTTP {response.status_code}: {message}"
        if 400 <= response.status_code < 500 and response.status_code not in {408, 429}:
            raise PermanentDeliveryError(error)
        raise DeliveryError(error)

    @staticmethod
    def _json_body(response: requests.Response) -> dict[str, Any]:
        try:
            value = response.json()
        except ValueError:
            return {}
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _error_message(body: dict[str, Any]) -> str:
        for key in ("message", "_server_messages", "exception", "exc"):
            value = body.get(key)
            if value:
                return str(value)
        return ""

    @classmethod
    def _is_duplicate(cls, status_code: int, body: dict[str, Any]) -> bool:
        if status_code < 400:
            return False
        message = cls._error_message(body).lower()
        return "already has a log with the same timestamp" in message
