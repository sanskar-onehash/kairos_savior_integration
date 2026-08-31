"""Synchronization orchestration independent of Windows Service plumbing."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from threading import Event

from .config import Settings
from .database import MySqlManager, PunchQueue
from .models import InvalidPunch
from .onehash import DeliveryError, OneHashClient, PermanentDeliveryError


@dataclass(frozen=True)
class CycleResult:
    selected: int
    delivered: int
    failed: int


class SaviorRunner:
    def __init__(self, settings: Settings, queue: PunchQueue, client: OneHashClient):
        self.settings = settings
        self.queue = queue
        self.client = client
        self.stop_event = Event()
        self.logger = logging.getLogger(__name__)

    @classmethod
    def build(cls, settings: Settings) -> "SaviorRunner":
        manager = MySqlManager(settings)
        return cls(settings, PunchQueue(manager, settings), OneHashClient(settings))

    def prepare(self) -> None:
        self.queue.prepare()

    def run_once(self) -> CycleResult:
        punches = self.queue.fetch_pending(self.settings.batch_size)
        delivered = 0
        failed = 0
        for punch in punches:
            if self.stop_event.is_set():
                break
            try:
                self.client.send(punch)
                self.queue.mark_delivered([punch.queue_id])
                delivered += 1
            except (PermanentDeliveryError, InvalidPunch) as exc:
                self.queue.mark_failed(punch.queue_id, str(exc), 0, dead_letter=True)
                self.logger.error(
                    "Punch queue_id=%d moved to the dead-letter queue: %s", punch.queue_id, exc
                )
                failed += 1
            except DeliveryError as exc:
                retry_seconds = self.retry_delay(punch.attempts + 1)
                dead_letter = punch.attempts + 1 >= self.settings.max_attempts
                self.queue.mark_failed(
                    punch.queue_id, str(exc), retry_seconds, dead_letter=dead_letter
                )
                self.logger.error(
                    "Punch queue_id=%d failed%s; retry delay=%d seconds: %s",
                    punch.queue_id,
                    " and reached max attempts" if dead_letter else "",
                    retry_seconds,
                    exc,
                )
                failed += 1
            except Exception:
                self.logger.exception("Unexpected failure processing punch queue_id=%d", punch.queue_id)
                raise
        return CycleResult(len(punches), delivered, failed)

    def retry_delay(self, attempt: int) -> int:
        exponent = min(max(attempt - 1, 0), 20)
        return min(self.settings.retry_base_seconds * (2**exponent), self.settings.retry_max_seconds)

    def run_forever(self) -> None:
        self.logger.info("Savior integration started")
        while not self.stop_event.is_set():
            try:
                result = self.run_once()
                if result.selected:
                    self.logger.info(
                        "Polling cycle complete: selected=%d delivered=%d failed=%d",
                        result.selected,
                        result.delivered,
                        result.failed,
                    )
            except Exception:
                self.logger.exception("Polling cycle failed")
            self.stop_event.wait(self.settings.poll_interval_seconds)
        self.logger.info("Savior integration stopped")

    def stop(self) -> None:
        self.stop_event.set()
