import os
import json
import logging
import copy
from pathlib import Path
from auth_utils import hash_password, is_password_hash
from crypto_utils import CryptoManager
from models import AppConfig

logger = logging.getLogger('discobunty.config')

DEFAULT_CONFIG = {
    "discord": {
        "token": "",
        "guild_id": "",
        "allowed_roles": "Admin,DevOps"
    },
    "features": {
        "enable_docker": False,
        "power_control_enabled": False,
        "power_control_password": ""
    },
    "webui": {
        "enabled": True,
        "password": ""
    },
    "servers": []
}

class ConfigManager:
    def __init__(self, config_path: str | None = None):
        data_dir = Path(os.getenv("DATA_DIR", "data"))
        data_dir.mkdir(parents=True, exist_ok=True)
        self.config_path = Path(config_path) if config_path else data_dir / "config.json"
        self.legacy_config_paths = [Path("config.json"), Path("/app/config.json")]
        # SECRET_KEY must still come from environment for initial decryption
        secret_key = os.getenv('SECRET_KEY')
        if not secret_key:
            raise ValueError("SECRET_KEY environment variable is mandatory.")
            
        self.crypto = CryptoManager(secret_key)
        self.config = self._load_config()

    def _load_config(self) -> AppConfig:
        config_source = self._resolve_existing_config_path()
        if config_source:
            try:
                with open(config_source, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                    logger.info(f"Loaded configuration from {config_source}")
                    config = self._process_config(config, decrypt=True)
                    if self._migrate_password_hashes(config) or config_source != self.config_path:
                        self.save_config(AppConfig.model_validate(config))
                    return AppConfig.model_validate(config)
            except Exception as e:
                logger.error(f"Failed to load config.json: {e}")
        
        logger.warning("Config file not found or invalid. Using defaults/env migration.")
        return self._migrate_from_env()

    def _resolve_existing_config_path(self) -> Path | None:
        if self.config_path.exists():
            return self.config_path

        for candidate in self.legacy_config_paths:
            try:
                if candidate.resolve() == self.config_path.resolve():
                    continue
            except FileNotFoundError:
                pass
            if candidate.exists() and candidate.is_file():
                logger.warning("Using legacy config path %s and migrating it to %s", candidate, self.config_path)
                return candidate

        return None

    def _migrate_from_env(self) -> AppConfig:
        """Helper to migrate existing .env settings to the new JSON format."""
        # Use deepcopy to avoid mutating the global DEFAULT_CONFIG
        config = copy.deepcopy(DEFAULT_CONFIG)
        
        # Mapping from .env keys to JSON structure
        config["discord"]["token"] = os.getenv('DISCORD_TOKEN', '')
        config["discord"]["guild_id"] = os.getenv('GUILD_ID', '')
        config["discord"]["allowed_roles"] = os.getenv('ALLOWED_ROLES', '')
        
        config["features"]["enable_docker"] = os.getenv('ENABLE_DOCKER', 'false').lower() == 'true'
        config["features"]["power_control_enabled"] = os.getenv('POWER_CONTROL_ENABLED', 'false').lower() == 'true'
        config["features"]["power_control_password"] = os.getenv('POWER_CONTROL_PASSWORD', '')
        
        config["webui"]["enabled"] = os.getenv('WEBUI_ENABLED', 'true').lower() == 'true'
        config["webui"]["password"] = os.getenv('WEB_PASSWORD', '')
        
        # Servers migration
        i = 1
        while True:
            alias = os.getenv(f'DISCORD_UBUNTU_SERVER_ALIAS_{i}')
            if not alias: break
            
            server = {
                "alias": alias,
                "host": os.getenv(f'DISCORD_UBUNTU_SERVER_IP_{i}'),
                "user": os.getenv(f'DISCORD_UBUNTU_SERVER_USER_{i}', 'root'),
                "port": int(os.getenv(f'DISCORD_UBUNTU_SERVER_PORT_{i}', '22')),
                "auth_method": os.getenv(f'DISCORD_UBUNTU_SERVER_AUTH_METHOD_{i}', 'key').lower(),
                "password": os.getenv(f'DISCORD_UBUNTU_SERVER_PASSWORD_{i}', ''),
                "key": os.getenv(f'DISCORD_UBUNTU_SERVER_KEY_{i}', '')
            }
            config["servers"].append(server)
            i += 1
            
        # Save the migrated config (will encrypt automatically in save_config)
        self._migrate_password_hashes(config)
        typed_config = AppConfig.model_validate(config)
        self.config = typed_config
        self.save_config(typed_config)
        return typed_config

    def _process_config(self, config: dict, decrypt: bool = True) -> dict:
        """Recursively encrypt or decrypt passwords in the config."""
        # Process Discord Token (Critical fix: Discord token is now encrypted)
        if "discord" in config and config["discord"].get("token"):
            t = config["discord"]["token"]
            config["discord"]["token"] = self.crypto.decrypt(t) if decrypt else self.crypto.encrypt(t)

        # Process top-level passwords
        if "features" in config and config["features"].get("power_control_password"):
            p = config["features"]["power_control_password"]
            if decrypt:
                config["features"]["power_control_password"] = self.crypto.decrypt(p)
            else:
                config["features"]["power_control_password"] = hash_password(self.crypto.decrypt(p) if p.startswith("ENC:") else p)
            
        if "webui" in config and config["webui"].get("password"):
            p = config["webui"]["password"]
            if decrypt:
                config["webui"]["password"] = self.crypto.decrypt(p)
            else:
                config["webui"]["password"] = hash_password(self.crypto.decrypt(p) if p.startswith("ENC:") else p)

        # Process server passwords/keys
        if "servers" in config:
            for s in config["servers"]:
                if s.get("password"):
                    s["password"] = self.crypto.decrypt(s["password"]) if decrypt else self.crypto.encrypt(s["password"])
                if s.get("key"):
                    # Only encrypt/decrypt if it's not a path
                    if s["key"] and not (s["key"].startswith('/') or os.path.isfile(s["key"])):
                        s["key"] = self.crypto.decrypt(s["key"]) if decrypt else self.crypto.encrypt(s["key"])
        
        return config

    def _migrate_password_hashes(self, config: dict) -> bool:
        """Upgrade legacy plaintext runtime values to password hashes before saving."""
        changed = False

        web_password = config.get("webui", {}).get("password", "")
        if web_password and not is_password_hash(web_password):
            config["webui"]["password"] = hash_password(web_password)
            changed = True

        power_password = config.get("features", {}).get("power_control_password", "")
        if power_password and not is_password_hash(power_password):
            config["features"]["power_control_password"] = hash_password(power_password)
            changed = True

        return changed

    def save_config(self, new_config: AppConfig):
        """Save configuration to JSON file with encryption."""
        runtime_config = copy.deepcopy(new_config.model_dump(by_alias=True))
        self._migrate_password_hashes(runtime_config)
        typed_runtime_config = AppConfig.model_validate(runtime_config)

        # Deep copy to avoid encrypting the in-memory config
        to_save = copy.deepcopy(typed_runtime_config.model_dump(by_alias=True))
        to_save = self._process_config(to_save, decrypt=False)
        
        try:
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(to_save, f, indent=4)
            self.config = typed_runtime_config # Update in-memory config
            logger.info(f"Saved configuration to {self.config_path}")
        except Exception as e:
            logger.error(f"Failed to save {self.config_path}: {e}")

    def get_server_config(self) -> list[dict]:
        return [server.model_dump(by_alias=True) for server in self.config.servers]

    def export_raw_config(self) -> bytes:
        if not self.config_path.exists():
            self.save_config(self.config)
        return self.config_path.read_bytes()

    def import_raw_config(self, raw_content: bytes) -> AppConfig:
        payload = json.loads(raw_content.decode("utf-8"))
        runtime_config = self._process_config(copy.deepcopy(payload), decrypt=True)
        typed_config = AppConfig.model_validate(runtime_config)
        self.save_config(typed_config)
        return self.config
