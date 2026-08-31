"""SQL Server trigger installation and durable delivery queue operations."""

from __future__ import annotations

from contextlib import contextmanager
import re
from typing import Iterator, Sequence

import pyodbc

from .config import Settings
from .models import Punch


_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def sql_identifier(value: str) -> str:
    if not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"Unsafe SQL identifier: {value!r}")
    return f"[{value}]"


def odbc_value(value: str) -> str:
    return "{" + value.replace("}", "}}") + "}"


class SqlServerManager:
    def __init__(self, settings: Settings):
        self.settings = settings

    def connection_string(self) -> str:
        parts = [
            f"DRIVER={odbc_value(self.settings.db_driver)}",
            f"SERVER={odbc_value(self.settings.db_server)}",
            f"DATABASE={odbc_value(self.settings.db_name)}",
            f"Encrypt={'yes' if self.settings.db_encrypt else 'no'}",
            "TrustServerCertificate="
            + ("yes" if self.settings.db_trust_server_certificate else "no"),
            "Connection Timeout=15",
        ]
        if self.settings.db_trusted_connection:
            parts.append("Trusted_Connection=yes")
        else:
            parts.extend(
                [
                    f"UID={odbc_value(self.settings.db_user)}",
                    f"PWD={odbc_value(self.settings.db_password)}",
                ]
            )
        return ";".join(parts)

    @contextmanager
    def connect(self) -> Iterator[pyodbc.Connection]:
        connection = pyodbc.connect(self.connection_string(), autocommit=False)
        try:
            yield connection
        finally:
            connection.close()


class PunchQueue:
    def __init__(self, manager: SqlServerManager, settings: Settings):
        self.manager = manager
        self.settings = settings
        self.schema = sql_identifier(settings.db_schema)
        self.queue_name = sql_identifier(settings.queue_table)
        self.source_name = sql_identifier(settings.source_table)
        self.trigger_name = sql_identifier(settings.trigger_name)
        self.queue = f"{self.schema}.{self.queue_name}"
        self.source = f"{self.schema}.{self.source_name}"

    def prepare(self) -> None:
        create_queue = f"""
            CREATE TABLE {self.queue} (
                [id] BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
                [cardno] NVARCHAR(64) NOT NULL,
                [officepunch] DATETIME2(6) NOT NULL,
                [inout] NVARCHAR(8) NULL,
                [attempts] INT NOT NULL DEFAULT 0,
                [next_attempt_at] DATETIME2(6) NOT NULL DEFAULT SYSDATETIME(),
                [processed_at] DATETIME2(6) NULL,
                [dead_letter_at] DATETIME2(6) NULL,
                [last_error] NVARCHAR(1000) NULL,
                [created_at] DATETIME2(6) NOT NULL DEFAULT SYSDATETIME()
            );
            CREATE INDEX [ix_onehash_changes_pending]
                ON {self.queue} ([processed_at], [dead_letter_at], [next_attempt_at], [id]);
        """
        create_trigger = f"""
            CREATE TRIGGER {self.schema}.{self.trigger_name}
            ON {self.source}
            AFTER INSERT
            AS
            BEGIN
                SET NOCOUNT ON;
                INSERT INTO {self.queue} ([cardno], [officepunch], [inout])
                SELECT [cardno], [officepunch], [inout]
                FROM inserted;
            END
        """
        with self.manager.connect() as connection:
            cursor = connection.cursor()
            try:
                self._validate_source_schema(cursor)
                cursor.execute(
                    """
                        SELECT COUNT(*)
                        FROM sys.tables AS tables
                        INNER JOIN sys.schemas AS schemas
                            ON schemas.schema_id = tables.schema_id
                        WHERE schemas.name = ? AND tables.name = ?
                    """,
                    self.settings.db_schema,
                    self.settings.queue_table,
                )
                if cursor.fetchone()[0] == 0:
                    cursor.execute(create_queue)

                cursor.execute(
                    """
                        SELECT source_table.name, source_schema.name, OBJECT_DEFINITION(triggers.object_id)
                        FROM sys.triggers AS triggers
                        INNER JOIN sys.tables AS source_table
                            ON source_table.object_id = triggers.parent_id
                        INNER JOIN sys.schemas AS source_schema
                            ON source_schema.schema_id = source_table.schema_id
                        WHERE triggers.name = ? AND source_schema.name = ?
                    """,
                    self.settings.trigger_name,
                    self.settings.db_schema,
                )
                existing_trigger = cursor.fetchone()
                if existing_trigger is None:
                    cursor.execute(create_trigger)
                elif (
                    existing_trigger[0].lower() != self.settings.source_table.lower()
                    or existing_trigger[1].lower() != self.settings.db_schema.lower()
                    or self.settings.queue_table.lower() not in existing_trigger[2].lower()
                ):
                    raise RuntimeError(
                        f"Existing trigger {self.settings.trigger_name!r} does not match the "
                        "configured source and queue; review it before installation"
                    )

                if self.settings.sync_from:
                    cursor.execute(
                        f"""
                            INSERT INTO {self.queue} ([cardno], [officepunch], [inout])
                            SELECT source.[cardno], source.[officepunch], source.[inout]
                            FROM {self.source} AS source
                            WHERE source.[officepunch] >= ?
                              AND NOT EXISTS (
                                  SELECT 1
                                  FROM {self.queue} AS queued
                                  WHERE queued.[cardno] = source.[cardno]
                                    AND queued.[officepunch] = source.[officepunch]
                                    AND (
                                        queued.[inout] = source.[inout]
                                        OR (queued.[inout] IS NULL AND source.[inout] IS NULL)
                                    )
                              )
                        """,
                        self.settings.sync_from,
                    )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                cursor.close()

    def _validate_source_schema(self, cursor: pyodbc.Cursor) -> None:
        cursor.execute(
            """
                SELECT COLUMN_NAME
                FROM INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ?
            """,
            self.settings.db_schema,
            self.settings.source_table,
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
            SELECT TOP ({int(limit)}) [id], [cardno], [officepunch], [inout], [attempts]
            FROM {self.queue}
            WHERE [processed_at] IS NULL
              AND [dead_letter_at] IS NULL
              AND [next_attempt_at] <= SYSDATETIME()
            ORDER BY [id] ASC
        """
        with self.manager.connect() as connection:
            cursor = connection.cursor()
            try:
                cursor.execute(query)
                return [
                    Punch(
                        queue_id=row[0],
                        card_number=str(row[1]).strip(),
                        timestamp=row[2],
                        raw_direction=row[3],
                        attempts=row[4],
                    )
                    for row in cursor.fetchall()
                ]
            finally:
                cursor.close()

    def mark_delivered(self, queue_ids: Sequence[int]) -> None:
        if not queue_ids:
            return
        placeholders = ", ".join(["?"] * len(queue_ids))
        query = f"""
            UPDATE {self.queue}
            SET [processed_at] = SYSDATETIME(), [last_error] = NULL
            WHERE [id] IN ({placeholders}) AND [processed_at] IS NULL
        """
        self._execute_update(query, tuple(queue_ids))

    def mark_failed(
        self, queue_id: int, error: str, retry_seconds: int, *, dead_letter: bool = False
    ) -> None:
        dead_letter_sql = "SYSDATETIME()" if dead_letter else "[dead_letter_at]"
        query = f"""
            UPDATE {self.queue}
            SET [attempts] = [attempts] + 1,
                [last_error] = ?,
                [next_attempt_at] = DATEADD(SECOND, ?, SYSDATETIME()),
                [dead_letter_at] = {dead_letter_sql}
            WHERE [id] = ? AND [processed_at] IS NULL
        """
        self._execute_update(query, (error[:1000], retry_seconds, queue_id))

    def _execute_update(self, query: str, params: tuple[object, ...]) -> None:
        with self.manager.connect() as connection:
            cursor = connection.cursor()
            try:
                cursor.execute(query, *params)
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                cursor.close()
