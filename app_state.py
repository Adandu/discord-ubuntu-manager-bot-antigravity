
import asyncio
import logging
import os
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path

from config_manager import ConfigManager
from models import AppConfig
from ssh_manager import SSHManager

MAX_MSG_LEN = 1900
ALLOWED_LOG_ROOTS = ["/var/log/", "/home/"]


class LoginRateLimiter:
    """Simple in-memory rate limiter: max_attempts per window_seconds per key."""

    def __init__(self, max_attempts: int = 5, window_seconds: int = 60):
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self._attempts: dict[str, deque[float]] = defaultdict(deque)

    def is_allowed(self, key: str) -> bool:
        now = time.time()
        attempts = self._attempts[key]
        while attempts and now - attempts[0] >= self.window_seconds:
            attempts.popleft()

        if len(attempts) >= self.max_attempts:
            return False
        attempts.append(now)
        return True

    def reset(self, key: str) -> None:
        self._attempts.pop(key, None)


class WebUIHandler(logging.Handler):
    def __init__(self, log_buffer: deque[str]):
        super().__init__()
        self.log_buffer = log_buffer

    def emit(self, record: logging.LogRecord) -> None:
        self.log_buffer.append(self.format(record))


def configure_logging(log_buffer: deque[str]) -> logging.Logger:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[
            logging.StreamHandler(),
            WebUIHandler(log_buffer),
        ],
        force=True,
    )
    return logging.getLogger("discobunty")


@dataclass
class AppState:
    config_manager: ConfigManager
    logger: logging.Logger
    data_dir: Path
    log_buffer: deque[str] = field(default_factory=lambda: deque(maxlen=500))
    login_limiter: LoginRateLimiter = field(default_factory=lambda: LoginRateLimiter(max_attempts=5, window_seconds=60))
    api_limiter: LoginRateLimiter = field(default_factory=lambda: LoginRateLimiter(max_attempts=30, window_seconds=60))
    ssh_fanout_limit: int = 5
    observability_cache_ttl: int = 15
    _server_check_cache: dict = field(default_factory=dict)
    _server_overview_cache: dict = field(default_factory=dict)
    _server_check_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _server_overview_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def __post_init__(self) -> None:
        self.log_buffer.append("System Initialized. Log capture started.")
        self.refresh_runtime()

    def refresh_runtime(self) -> None:
        self.config: AppConfig = self.config_manager.config
        self.ssh_manager = SSHManager([server.model_dump(by_alias=True) for server in self.config.servers])
        self.servers_by_alias = {server.alias: server for server in self.config.servers}
        self.clear_observability_cache()

    def clear_observability_cache(self) -> None:
        self._server_check_cache.clear()
        self._server_overview_cache.clear()

    @property
    def audit_log_path(self) -> Path:
        return self.data_dir / "audit.log"

    def audit_log(self, user_id: int, username: str, command: str, details: str) -> None:
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        log_entry = f"[{timestamp}] USER:{user_id} ({username}) | CMD:{command} | {details}\n"
        self.audit_log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.audit_log_path.open("a", encoding="utf-8") as handle:
            handle.write(log_entry)
        self.logger.info("AUDIT: %s", log_entry.strip())

    def masked_config_dict(self) -> dict:
        display_config = self.config.model_dump(by_alias=True)
        if display_config["discord"].get("token"):
            display_config["discord"]["token"] = "********"
        if display_config["webui"].get("password"):
            display_config["webui"]["password"] = "********"
        if display_config["features"].get("power_control_password"):
            display_config["features"]["power_control_password"] = "********"

        for server in display_config["servers"]:
            if server.get("password"):
                server["password"] = "********"
            key_value = server.get("key", "")
            if key_value and not key_value.startswith("/"):
                if not os.path.isfile(key_value):
                    server["key"] = "********"

        return display_config

    def save_config(self, config: AppConfig) -> None:
        self.config_manager.save_config(config)
        self.refresh_runtime()

    async def read_audit_entries(self, limit: int = 200) -> list[str]:
        if not self.audit_log_path.exists():
            return []
        def _read():
            with self.audit_log_path.open("r", encoding="utf-8") as handle:
                lines = handle.readlines()
            return [line.rstrip("\n") for line in lines[-limit:]]
        return await asyncio.to_thread(_read)
