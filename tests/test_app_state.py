import unittest
import logging
import tempfile
from pathlib import Path
from unittest.mock import MagicMock
from app_state import AppState
from config_manager import ConfigManager
from models import AppConfig, DiscordSettings, WebUISettings, FeatureSettings, ServerSettings

class TestAppState(unittest.TestCase):
    def test_masked_config_dict(self):
        config_manager_mock = MagicMock(spec=ConfigManager)

        discord_settings = DiscordSettings(token="supersecrettoken")
        webui_settings = WebUISettings(password="webuisecret")
        feature_settings = FeatureSettings(power_control_password="powersecret")
        server1 = ServerSettings(alias="alpha", host="example-alpha.invalid", password="server1secret")
        server2 = ServerSettings(alias="beta", host="example-beta.invalid", key="/path/to/key")
        server3 = ServerSettings(alias="gamma", host="example-gamma.invalid", key="nonexistent_key")

        config = AppConfig(
            discord=discord_settings,
            webui=webui_settings,
            features=feature_settings,
            servers=[server1, server2, server3]
        )

        config_manager_mock.config = config

        logger_mock = MagicMock(spec=logging.Logger)

        with tempfile.TemporaryDirectory() as temp_dir:
            app_state = AppState(
                config_manager=config_manager_mock,
                logger=logger_mock,
                data_dir=Path(temp_dir)
            )

            masked_dict = app_state.masked_config_dict()

            self.assertEqual(masked_dict["discord"]["token"], "********")
            self.assertEqual(masked_dict["webui"]["password"], "********")
            self.assertEqual(masked_dict["features"]["power_control_password"], "********")
            self.assertEqual(masked_dict["servers"][0]["password"], "********")
            self.assertEqual(masked_dict["servers"][1]["key"], "/path/to/key")
            self.assertEqual(masked_dict["servers"][2]["key"], "********")


    def test_audit_log_creation_and_append(self):
        config_manager_mock = MagicMock(spec=ConfigManager)

        discord_settings = DiscordSettings(token="supersecrettoken")
        webui_settings = WebUISettings(password="webuisecret")
        feature_settings = FeatureSettings(power_control_password="powersecret")
        config = AppConfig(
            discord=discord_settings,
            webui=webui_settings,
            features=feature_settings,
            servers=[]
        )
        config_manager_mock.config = config

        logger_mock = MagicMock(spec=logging.Logger)

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            app_state = AppState(
                config_manager=config_manager_mock,
                logger=logger_mock,
                data_dir=temp_path
            )

            # First log entry (should create file)
            app_state.audit_log(123, "testuser", "testcmd", "test details")
            self.assertTrue((temp_path / "audit.log").exists())

            with open(temp_path / "audit.log", "r") as f:
                content = f.read()
            self.assertIn("USER:123 (testuser) | CMD:testcmd | test details", content)

            # Second log entry (should append)
            app_state.audit_log(456, "otheruser", "othercmd", "other details")
            with open(temp_path / "audit.log", "r") as f:
                content = f.read()
            self.assertIn("USER:123 (testuser) | CMD:testcmd | test details", content)
            self.assertIn("USER:456 (otheruser) | CMD:othercmd | other details", content)

            self.assertEqual(logger_mock.info.call_count, 2)

    def test_audit_log_sanitization(self):
        config_manager_mock = MagicMock(spec=ConfigManager)

        discord_settings = DiscordSettings(token="supersecrettoken")
        webui_settings = WebUISettings(password="webuisecret")
        feature_settings = FeatureSettings(power_control_password="powersecret")
        config = AppConfig(
            discord=discord_settings,
            webui=webui_settings,
            features=feature_settings,
            servers=[]
        )
        config_manager_mock.config = config

        logger_mock = MagicMock(spec=logging.Logger)

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            app_state = AppState(
                config_manager=config_manager_mock,
                logger=logger_mock,
                data_dir=temp_path
            )

            app_state.audit_log(123, "test\nuser", "test\rcmd", "details\nwith\rnewlines")

            with open(temp_path / "audit.log", "r") as f:
                content = f.read()

            self.assertIn("USER:123 (test user) | CMD:test cmd | details with newlines", content)
            self.assertNotIn("\nuser", content)
            self.assertNotIn("\rcmd", content)

if __name__ == '__main__':
    unittest.main()
