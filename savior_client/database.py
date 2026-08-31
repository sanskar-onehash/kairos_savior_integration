"""MySQL trigger installation and durable delivery queue operations."""

from __future__ import annotations

from contextlib import contextmanager
import re
from typing import Iterator, Sequence

import mysql.connector
from mysql.connector.abstracts import MySQLConnectionAbstract

from .config import Settings
from .models import Punch


_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def sql_identifier(value: str) -> str:
    if not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"Unsafe SQL identifier: {value!r}")
    return f"`{value}`"


class MySqlManager:
    def __init__(self, settings: Settings):
        self.settings = settings

    @contextmanager
    def connect(self) -> Iterator[MySQLConnectionAbstract]:
        connection = mysql.connector.connect(
            host=self.settings.db_host,
            port=self.settings.db_port,
            user=self.settings.db_user,
            password=self.settings.db_password,
            database=self.settings.db_name,
            autocommit=False,
            connection_timeout=15,
        )
        try:
            yield connection
        finally:
            connection.close()


class PunchQueue:
    def __init__(self, manager: MySqlManager, settings: Settings):
        self.manager = manager
        self.settings = settings
        self.queue = sql_identifier(settings.queue_table)
        self.source = sql_identifier(settings.source_table)
        self.trigger = sql_identifier(settings.trigger_name)

    def prepare(self) -> None:
        create_queue = f"""
            CREATE TABLE IF NOT EXISTS {self.queue} (
                id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
                cardno VARCHAR(64) NOT NULL,
                officepunch DATETIME(6) NOT NULL,
                inout VARCHAR(8) NULL,
                attempts INT UNSIGNED NOT NULL DEFAULT 0,
                next_attempt_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
                processed_at DATETIME(6) NULL,
                dead_letter_at DATETIME(6) NULL,
                last_error VARCHAR(1000) NULL,
                created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
                PRIMARY KEY (id),
                KEY ix_pending (processed_at, next_attempt_at, id)
            ) ENGINE=InnoDB
        """
        create_trigger = f"""
            CREATE TRIGGER {self.trigger}
            AFTER INSERT ON {self.source}
            FOR EACH ROW
            INSERT INTO {self.queue}
                (cardno, officepunch, inout)
            VALUES
                (NEW.cardno, NEW.officepunch, NEW.inout)
        """
        with self.manager.connect() as connection:
            cursor = connection.cursor()
            try:
                self._validate_source_schema(cursor)
                cursor.execute(create_queue)
                cursor.execute(
                    """
                        SELECT EVENT_OBJECT_TABLE, ACTION_STATEMENT
                        FROM information_schema.TRIGGERS
                        WHERE TRIGGER_SCHEMA = DATABASE() AND TRIGGER_NAME = %s
                    """,
                    (self.settings.trigger_name,),
                )
                existing_trigger = cursor.fetchone()
                if existing_trigger is None:
                    cursor.execute(create_trigger)
                elif (
                    existing_trigger[0].lower() != self.settings.source_table.lower()
                    or self.settings.queue_table.lower() not in existing_trigger[1].lower()
                ):
                    raise RuntimeError(
                        f"Existing trigger {self.settings.trigger_name!r} does not match the "
                        "configured source and queue; review it before installation"
                    )
                if self.settings.sync_from:
                    cursor.execute(
                        f"""
                            INSERT INTO {self.queue} (cardno, officepunch, inout)
                            SELECT source.cardno, source.officepunch, source.inout
                            FROM {self.source} AS source
                            WHERE source.officepunch >= %s
                              AND NOT EXISTS (
                                  SELECT 1 FROM {self.queue} AS queued
                                  WHERE queued.cardno = source.cardno
                                    AND queued.officepunch = source.officepunch
                                    AND (queued.inout <=> source.inout)
                              )
                        """,
                        (self.settings.sync_from,),
                    )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                cursor.close()

    def _validate_source_schema(self, cursor) -> None:
        cursor.execute(
            """
                SELECT COLUMN_NAME
                FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s
            """,
            (self.settings.source_table,),
        )
        columns = {row[0].lower() for row in cursor.fetchall()}
        missing = {"cardno", "officepunch", "inout"} - columns
        if missing:
            missing_text = ", ".join(sorted(missing))
            raise RuntimeError(
                f"Savior source table {self.settings.source_table!r} is missing: {missing_text}"
            )

    def fetch_pending(self, limit: int) -> list[Punch]:
        query = f"""
            SELECT id, cardno, officepunch, inout, attempts
            FROM {self.queue}
            WHERE processed_at IS NULL
              AND dead_letter_at IS NULL
              AND next_attempt_at <= CURRENT_TIMESTAMP(6)
            ORDER BY id ASC
            LIMIT %s
        """
        with self.manager.connect() as connection:
            cursor = connection.cursor(dictionary=True)
            try:
                cursor.execute(query, (limit,))
                rows = cursor.fetchall()
                return [
                    Punch(
                        queue_id=row["id"],
                        card_number=str(row["cardno"]).strip(),
                        timestamp=row["officepunch"],
                        raw_direction=row["inout"],
                        attempts=row["attempts"],
                    )
                    for row in rows
                ]
            finally:
                cursor.close()

    def mark_delivered(self, queue_ids: Sequence[int]) -> None:
        if not queue_ids:
            return
        placeholders = ", ".join(["%s"] * len(queue_ids))
        query = f"""
            UPDATE {self.queue}
            SET processed_at = CURRENT_TIMESTAMP(6), last_error = NULL
            WHERE id IN ({placeholders}) AND processed_at IS NULL
        """
        self._execute_update(query, tuple(queue_ids))

    def mark_failed(
        self, queue_id: int, error: str, retry_seconds: int, *, dead_letter: bool = False
    ) -> None:
        dead_letter_sql = "CURRENT_TIMESTAMP(6)" if dead_letter else "dead_letter_at"
        query = f"""
            UPDATE {self.queue}
            SET attempts = attempts + 1,
                last_error = %s,
                next_attempt_at = TIMESTAMPADD(SECOND, %s, CURRENT_TIMESTAMP(6)),
                dead_letter_at = {dead_letter_sql}
            WHERE id = %s AND processed_at IS NULL
        """
        self._execute_update(query, (error[:1000], retry_seconds, queue_id))

    def _execute_update(self, query: str, params: tuple[object, ...]) -> None:
        with self.manager.connect() as connection:
            cursor = connection.cursor()
            try:
                cursor.execute(query, params)
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                cursor.close()
