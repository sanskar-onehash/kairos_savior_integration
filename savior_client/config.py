"""Environment-backed configuration with no secrets in source control."""

from __future__ import annotations

from dataclasses import dataclass, replace as dataclass_replace
from datetime import datetime
import os
from pathlib import Path


class ConfigurationError(ValueError):
    """Raised when required runtime configuration is absent or invalid."""


def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ConfigurationError(f"Invalid line {line_number} in {path}")
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("\"'")
    return values


def _value(values: dict[str, str], name: str, *legacy_names: str, default: str = "") -> str:
    for key in (name, *legacy_names):
        if key in os.environ:
            return os.environ[key]
        if key in values:
            return values[key]
    return default


def _positive_int(raw: str, name: str) -> int:
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be an integer") from exc
    if value <= 0:
        raise ConfigurationError(f"{name} must be greater than zero")
    return value


def _boolean(raw: str, name: str) -> bool:
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigurationError(f"{name} must be true or false")


def _optional_datetime(raw: str, name: str) -> datetime | None:
    if not raw.strip():
        return None
    try:
        return datetime.fromisoformat(raw.strip())
    except ValueError as exc:
        raise ConfigurationError(f"{name} must use YYYY-MM-DD or YYYY-MM-DD HH:MM:SS") from exc


@dataclass(frozen=True)
class Settings:
    db_server: str
    db_driver: str
    db_trusted_connection: bool
    db_encrypt: bool
    db_trust_server_certificate: bool
    db_user: str
    db_password: str
    db_name: str
    db_schema: str
    source_table: str
    queue_table: str
    trigger_name: str
    sync_from: datetime | None
    erp_url: str
    erp_api_key: str
    erp_api_secret: str
    checkin_method: str
    device_id: str
    poll_interval_seconds: int
    batch_size: int
    request_timeout_seconds: int
    retry_base_seconds: int
    retry_max_seconds: int
    max_attempts: int
    verify_tls: bool
    log_file: Path

    @classmethod
    def load(cls, env_file: Path | None = None) -> "Settings":
        path = env_file or Path(__file__).resolve().parents[1] / ".env"
        values = _read_env_file(path)
        settings = cls(
            db_server=_value(values, "SAVIOR_DB_SERVER"),
            db_driver=_value(
                values,
                "SAVIOR_DB_DRIVER",
                default="ODBC Driver 18 for SQL Server",
            ),
            db_trusted_connection=_boolean(
                _value(values, "SAVIOR_DB_TRUSTED_CONNECTION", default="true"),
                "SAVIOR_DB_TRUSTED_CONNECTION",
            ),
            db_encrypt=_boolean(
                _value(values, "SAVIOR_DB_ENCRYPT", default="true"),
                "SAVIOR_DB_ENCRYPT",
            ),
            db_trust_server_certificate=_boolean(
                _value(values, "SAVIOR_DB_TRUST_SERVER_CERTIFICATE", default="true"),
                "SAVIOR_DB_TRUST_SERVER_CERTIFICATE",
            ),
            db_user=_value(values, "SAVIOR_DB_USER"),
            db_password=_value(values, "SAVIOR_DB_PASSWORD"),
            db_name=_value(values, "SAVIOR_DB_NAME"),
            db_schema=_value(values, "SAVIOR_DB_SCHEMA", default="dbo"),
            source_table=_value(values, "SAVIOR_SOURCE_TABLE", default="machinerawpunch"),
            queue_table=_value(values, "SAVIOR_QUEUE_TABLE", default="onehash_db_changes"),
            trigger_name=_value(values, "SAVIOR_TRIGGER_NAME", default="log_onehash_task"),
            sync_from=_optional_datetime(_value(values, "SAVIOR_SYNC_FROM"), "SAVIOR_SYNC_FROM"),
            erp_url=_value(values, "ONEHASH_URL").rstrip("/"),
            erp_api_key=_value(values, "ONEHASH_API_KEY"),
            erp_api_secret=_value(values, "ONEHASH_API_SECRET"),
            checkin_method=_value(
                values,
                "ONEHASH_CHECKIN_METHOD",
                default="savior_add_employee_checkin",
            ),
            device_id=_value(values, "ONEHASH_DEVICE_ID", default="Savior"),
            poll_interval_seconds=_positive_int(
                _value(values, "POLL_INTERVAL_SECONDS", default="120"), "POLL_INTERVAL_SECONDS"
            ),
            batch_size=_positive_int(_value(values, "BATCH_SIZE", default="50"), "BATCH_SIZE"),
            request_timeout_seconds=_positive_int(
                _value(values, "REQUEST_TIMEOUT_SECONDS", default="30"), "REQUEST_TIMEOUT_SECONDS"
            ),
            retry_base_seconds=_positive_int(
                _value(values, "RETRY_BASE_SECONDS", default="60"), "RETRY_BASE_SECONDS"
            ),
            retry_max_seconds=_positive_int(
                _value(values, "RETRY_MAX_SECONDS", default="3600"), "RETRY_MAX_SECONDS"
            ),
            max_attempts=_positive_int(_value(values, "MAX_ATTEMPTS", default="20"), "MAX_ATTEMPTS"),
            verify_tls=_boolean(_value(values, "VERIFY_TLS", default="true"), "VERIFY_TLS"),
            log_file=Path(_value(values, "LOG_FILE", default="logs/savior-integration.log")),
        )
        if not settings.log_file.is_absolute():
            settings = dataclass_replace(settings, log_file=path.parent / settings.log_file)
        settings.validate()
        return settings

    def validate(self) -> None:
        required = {
            "SAVIOR_DB_SERVER": self.db_server,
            "SAVIOR_DB_NAME": self.db_name,
            "ONEHASH_URL": self.erp_url,
            "ONEHASH_API_KEY": self.erp_api_key,
            "ONEHASH_API_SECRET": self.erp_api_secret,
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise ConfigurationError("Missing required settings: " + ", ".join(missing))
        if not self.db_trusted_connection and not self.db_user:
            raise ConfigurationError(
                "SAVIOR_DB_USER is required when SAVIOR_DB_TRUSTED_CONNECTION is false"
            )
        if not self.db_trusted_connection and not self.db_password:
            raise ConfigurationError(
                "SAVIOR_DB_PASSWORD is required when SAVIOR_DB_TRUSTED_CONNECTION is false"
            )
        if not self.erp_url.startswith("https://"):
            raise ConfigurationError("ONEHASH_URL must use HTTPS")
        if not all(part.isidentifier() for part in self.checkin_method.split(".")):
            raise ConfigurationError("ONEHASH_CHECKIN_METHOD must be a valid method name")
        if self.retry_max_seconds < self.retry_base_seconds:
            raise ConfigurationError("RETRY_MAX_SECONDS cannot be below RETRY_BASE_SECONDS")
