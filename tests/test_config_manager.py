import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from pydantic import ValidationError

from config_manager import ConfigManager
from models import AppConfig


class ConfigManagerTests(unittest.TestCase):
    def test_env_migration_hashes_passwords_and_normalizes_booleans(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            env = {
                "SECRET_KEY": "x" * 32,
                "DATA_DIR": temp_dir,
                "ENABLE_DOCKER": "true",
                "POWER_CONTROL_ENABLED": "false",
                "WEBUI_ENABLED": "true",
                "WEB_PASSWORD": "web-pass",
                "POWER_CONTROL_PASSWORD": "power-pass",
            }
            with patch.dict(os.environ, env, clear=False):
                manager = ConfigManager()

            self.assertTrue(manager.config.features.enable_docker)
            self.assertTrue(manager.config.webui.enabled)
            self.assertTrue(manager.config.webui.password.startswith("PBKDF2_SHA256$"))
            self.assertTrue(manager.config.features.power_control_password.startswith("PBKDF2_SHA256$"))

            saved = json.loads(Path(temp_dir, "config.json").read_text(encoding="utf-8"))
            self.assertTrue(saved["webui"]["password"].startswith("PBKDF2_SHA256$"))
            self.assertIsInstance(saved["features"]["enable_docker"], bool)

    def test_save_config_persists_typed_booleans(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            env = {"SECRET_KEY": "y" * 32, "DATA_DIR": temp_dir}
            with patch.dict(os.environ, env, clear=False):
                manager = ConfigManager()
                config = AppConfig.model_validate(manager.config.model_dump())
                config.features.enable_docker = True
                config.webui.enabled = False
                manager.save_config(config)

            saved = json.loads(Path(temp_dir, "config.json").read_text(encoding="utf-8"))
            self.assertTrue(saved["features"]["enable_docker"])
            self.assertFalse(saved["webui"]["enabled"])

    def test_legacy_config_path_is_migrated_into_data_dir(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            data_dir = workspace / "data"
            data_dir.mkdir()
            legacy_config = workspace / "config.json"
            legacy_config.write_text(
                json.dumps(
                    {
                        "discord": {"token": "", "guild_id": "", "allowed_roles": "Admin"},
                        "features": {"enable_docker": True, "power_control_enabled": False, "power_control_password": ""},
                        "webui": {"enabled": True, "password": "legacy-password"},
                        "servers": [{"alias": "srv1", "host": "1.2.3.4", "user": "root", "port": 22, "auth_method": "key", "password": "", "key": ""}],
                    }
                ),
                encoding="utf-8",
            )

            env = {"SECRET_KEY": "z" * 32, "DATA_DIR": str(data_dir)}
            with patch.dict(os.environ, env, clear=False):
                current_dir = os.getcwd()
                os.chdir(workspace)
                try:
                    manager = ConfigManager()
                finally:
                    os.chdir(current_dir)

            self.assertEqual(manager.config.servers[0].alias, "srv1")
            self.assertTrue((data_dir / "config.json").exists())
            migrated = json.loads((data_dir / "config.json").read_text(encoding="utf-8"))
            self.assertTrue(migrated["webui"]["password"].startswith("PBKDF2_SHA256$"))

    def test_import_raw_config_malformed_json(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            env = {"SECRET_KEY": "z" * 32, "DATA_DIR": temp_dir}
            with patch.dict(os.environ, env, clear=False):
                manager = ConfigManager()
                with self.assertRaises(json.JSONDecodeError):
                    manager.import_raw_config(b"invalid json")

    def test_import_raw_config_invalid_schema(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            env = {"SECRET_KEY": "z" * 32, "DATA_DIR": temp_dir}
            with patch.dict(os.environ, env, clear=False):
                manager = ConfigManager()
                # AppConfig doesn't throw ValidationError on just {"invalid": "schema"}
                # because the fields have default_factory or defaults.
                # Let's provide a dict that explicitly fails type validation
                # such as an invalid auth_method
                invalid_config = json.dumps({
                    "servers": [
                        {"alias": "srv1", "auth_method": "invalid_method"}
                    ]
                }).encode("utf-8")
                with self.assertRaises(ValidationError):
                    manager.import_raw_config(invalid_config)

    @patch("config_manager.logger.error")
    @patch("builtins.open", side_effect=PermissionError("Permission denied"))
    def test_save_config_handles_exception(self, mock_open, mock_logger_error):
        with tempfile.TemporaryDirectory() as temp_dir:
            env = {"SECRET_KEY": "z" * 32, "DATA_DIR": temp_dir}
            with patch.dict(os.environ, env, clear=False):
                manager = ConfigManager()
                config = AppConfig.model_validate(manager.config.model_dump())

                # Should not raise an exception
                manager.save_config(config)

                # Verify logger.error was called with the correct message
                mock_logger_error.assert_called()
                args, _ = mock_logger_error.call_args_list[-1]
                self.assertIn("Failed to save", args[0])
                self.assertIn("Permission denied", args[0])
if __name__ == "__main__":
    unittest.main()
