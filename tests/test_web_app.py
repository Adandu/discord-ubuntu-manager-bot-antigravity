import os
import tempfile
import unittest
from collections import deque
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app_state import AppState, configure_logging
from config_manager import ConfigManager
from models import AppConfig
from web_app import create_web_app


class WebAppTests(unittest.TestCase):
    def _build_client(self, with_password=True):
        temp_dir = tempfile.TemporaryDirectory()
        env = {"SECRET_KEY": "z" * 32, "DATA_DIR": temp_dir.name}
        patcher = patch.dict(os.environ, env, clear=False)
        patcher.start()

        log_buffer = deque(maxlen=500)
        state = AppState(
            config_manager=ConfigManager(),
            logger=configure_logging(log_buffer),
            data_dir=Path(temp_dir.name),
            log_buffer=log_buffer,
        )
        if with_password:
            config = AppConfig.model_validate(state.config.model_dump())
            config.webui.password = "admin-pass"
            state.save_config(config)
        client = TestClient(create_web_app(state))
        return temp_dir, patcher, client, state

    def test_login_and_health_flow(self):
        temp_dir, patcher, client, _state = self._build_client()
        try:
            login_page = client.get("/login")
            self.assertEqual(login_page.status_code, 200)
            csrf_token = login_page.text.split('name="csrf_token" value="', 1)[1].split(
                '"', 1
            )[0]

            response = client.post(
                "/login",
                data={"password": "admin-pass", "csrf_token": csrf_token},
                follow_redirects=False,
            )
            self.assertEqual(response.status_code, 303)

            home = client.get("/")
            self.assertEqual(home.status_code, 200)

            health = client.get("/health")
            self.assertEqual(health.status_code, 200)
            self.assertEqual(health.json()["status"], "ok")
        finally:
            client.close()
            patcher.stop()
            temp_dir.cleanup()

    def test_first_run_setup_flow(self):
        temp_dir, patcher, client, state = self._build_client(with_password=False)
        try:
            response = client.get("/login", follow_redirects=False)
            self.assertEqual(response.status_code, 307)
            self.assertEqual(response.headers["location"], "/setup")

            setup_page = client.get("/setup")
            self.assertEqual(setup_page.status_code, 200)
            csrf_token = setup_page.text.split('name="csrf_token" value="', 1)[1].split(
                '"', 1
            )[0]

            submit = client.post(
                "/setup",
                data={
                    "password": "new-admin-pass",
                    "confirm_password": "new-admin-pass",
                    "csrf_token": csrf_token,
                },
                follow_redirects=False,
            )
            self.assertEqual(submit.status_code, 303)
            self.assertEqual(submit.headers["location"], "/")
            self.assertTrue(
                state.config_manager.config.webui.password.startswith("PBKDF2_SHA256$")
            )
        finally:
            client.close()
            patcher.stop()
            temp_dir.cleanup()

    def test_backup_export_and_restore(self):
        temp_dir, patcher, client, state = self._build_client()
        try:
            login_page = client.get("/login")
            csrf_token = login_page.text.split('name="csrf_token" value="', 1)[1].split(
                '"', 1
            )[0]
            client.post(
                "/login",
                data={"password": "admin-pass", "csrf_token": csrf_token},
                follow_redirects=False,
            )
            home = client.get("/")
            authed_csrf = home.text.split('meta name="csrf-token" content="', 1)[
                1
            ].split('"', 1)[0]

            export = client.get("/api/backup/export")
            self.assertEqual(export.status_code, 200)
            self.assertIn("attachment;", export.headers["content-disposition"])

            backup = export.content
            restored = client.post(
                "/api/backup/restore",
                headers={"X-CSRF-Token": authed_csrf},
                files={"backup_file": ("backup.json", backup, "application/json")},
            )
            self.assertEqual(restored.status_code, 200)
            self.assertEqual(restored.json()["status"], "success")
        finally:
            client.close()
            patcher.stop()
            temp_dir.cleanup()

    def test_csrf_validation_edge_cases(self):
        temp_dir, patcher, client, _state = self._build_client()
        try:
            # Get the login page to initialize a session with a valid csrf_token
            login_page = client.get("/login")
            valid_csrf_token = login_page.text.split('name="csrf_token" value="', 1)[
                1
            ].split('"', 1)[0]

            # --- validate_csrf_form edge cases ---

            # 1. Invalid form token
            response = client.post(
                "/login", data={"password": "admin-pass", "csrf_token": "invalid_token"}
            )
            self.assertEqual(response.status_code, 403)
            self.assertEqual(
                response.json(), {"detail": "CSRF token missing or invalid"}
            )

            # 2. Missing form token (handled by FastAPI dependencies, returns 422 Unprocessable Entity)
            response = client.post("/login", data={"password": "admin-pass"})
            self.assertEqual(response.status_code, 422)

            # 3. Missing session token (by clearing cookies on the client to simulate a new session without a token)
            client.cookies.clear()
            response = client.post(
                "/login",
                data={"password": "admin-pass", "csrf_token": valid_csrf_token},
            )
            self.assertEqual(response.status_code, 403)
            self.assertEqual(
                response.json(), {"detail": "CSRF token missing or invalid"}
            )

            # Re-authenticate to get a valid session for API calls
            login_page = client.get("/login")
            valid_csrf_token = login_page.text.split('name="csrf_token" value="', 1)[
                1
            ].split('"', 1)[0]
            client.post(
                "/login",
                data={"password": "admin-pass", "csrf_token": valid_csrf_token},
                follow_redirects=False,
            )

            # --- validate_csrf edge cases ---

            # 1. Invalid header token
            response = client.post(
                "/api/backup/restore",
                headers={"X-CSRF-Token": "invalid_token"},
                files={"backup_file": ("backup.json", b"{}", "application/json")},
            )
            self.assertEqual(response.status_code, 403)
            self.assertEqual(
                response.json(), {"detail": "CSRF token missing or invalid"}
            )

            # 2. Missing header token
            response = client.post(
                "/api/backup/restore",
                files={"backup_file": ("backup.json", b"{}", "application/json")},
            )
            self.assertEqual(response.status_code, 403)
            self.assertEqual(
                response.json(), {"detail": "CSRF token missing or invalid"}
            )

        finally:
            client.close()
            patcher.stop()
            temp_dir.cleanup()


if __name__ == "__main__":
    unittest.main()
