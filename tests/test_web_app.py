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

            export = client.get("/api/backup/export", headers={"X-CSRF-Token": authed_csrf})
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

    def test_backup_restore_rejects_invalid_and_oversized_uploads(self):
        temp_dir, patcher, client, _state = self._build_client()
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

            invalid = client.post(
                "/api/backup/restore",
                headers={"X-CSRF-Token": authed_csrf},
                files={"backup_file": ("backup.json", b"{not-json", "application/json")},
            )
            self.assertEqual(invalid.status_code, 400)
            self.assertEqual(invalid.json()["detail"], "Invalid backup file.")

            client.app.state.max_backup_upload_bytes = 8
            oversized = client.post(
                "/api/backup/restore",
                headers={"X-CSRF-Token": authed_csrf},
                files={"backup_file": ("backup.json", b"0123456789", "application/json")},
            )
            self.assertEqual(oversized.status_code, 413)
        finally:
            client.close()
            patcher.stop()
            temp_dir.cleanup()


    def test_save_config_ui_authentication(self):
        temp_dir, patcher, client, _state = self._build_client()
        try:
            # Unauthenticated should return 401
            response = client.post("/save", json={"discord": {"token": "test"}})
            self.assertEqual(response.status_code, 401)
        finally:
            client.close()
            patcher.stop()
            temp_dir.cleanup()

    def test_save_config_ui_secret_key_rejection(self):
        temp_dir, patcher, client, _state = self._build_client()
        try:
            # Login first
            login_page = client.get("/login")
            csrf_token = login_page.text.split('name="csrf_token" value="', 1)[1].split('"', 1)[0]
            client.post(
                "/login",
                data={"password": "admin-pass", "csrf_token": csrf_token},
                follow_redirects=False,
            )
            home = client.get("/")
            authed_csrf = home.text.split('meta name="csrf-token" content="', 1)[1].split('"', 1)[0]

            # Setup original state config with test values after login to avoid messing up auth
            _state.config.discord.token = "original_token"
            _state.config.webui.password = "original_password"
            _state.config.features.power_control_password = "original_power_password"

            from models import ServerSettings
            _state.config.servers = [ServerSettings(alias="example_server", host="192.0.2.50", user="test", password="original_server_password", key="original_server_key")]
            _state.save_config(_state.config)
            original_webui_hash = _state.config.webui.password
            original_power_hash = _state.config.features.power_control_password


            payload = _state.config.model_dump()
            payload["SECRET_KEY"] = "new_secret"

            response = client.post(
                "/save",
                headers={"X-CSRF-Token": authed_csrf},
                json=payload
            )
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.json()["detail"], "SECRET_KEY rotation is not supported from the WebUI.")
        finally:
            client.close()
            patcher.stop()
            temp_dir.cleanup()

    def test_save_config_ui_preserves_secrets(self):
        temp_dir, patcher, client, _state = self._build_client()
        try:
            # Login
            login_page = client.get("/login")
            csrf_token = login_page.text.split('name="csrf_token" value="', 1)[1].split('"', 1)[0]
            client.post(
                "/login",
                data={"password": "admin-pass", "csrf_token": csrf_token},
                follow_redirects=False,
            )
            home = client.get("/")
            authed_csrf = home.text.split('meta name="csrf-token" content="', 1)[1].split('"', 1)[0]

            # Setup original state config with test values after login to avoid messing up auth
            _state.config.discord.token = "original_token"
            _state.config.webui.password = "original_password"
            _state.config.features.power_control_password = "original_power_password"

            from models import ServerSettings
            _state.config.servers = [ServerSettings(alias="example_server", host="192.0.2.50", user="test", password="original_server_password", key="original_server_key")]
            _state.save_config(_state.config)
            original_webui_hash = _state.config.webui.password
            original_power_hash = _state.config.features.power_control_password


            payload = _state.config.model_dump()
            payload["discord"]["token"] = "********"
            payload["webui"]["password"] = "********"
            payload["features"]["power_control_password"] = "********"
            payload["servers"][0]["password"] = "********"
            payload["servers"][0]["key"] = "********"

            response = client.post(
                "/save",
                headers={"X-CSRF-Token": authed_csrf},
                json=payload
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["status"], "success")

            # Verify secrets are preserved
            self.assertEqual(_state.config.discord.token, "original_token")
            self.assertEqual(_state.config.webui.password, original_webui_hash)
            self.assertEqual(_state.config.features.power_control_password, original_power_hash)
            self.assertEqual(_state.config.servers[0].password, "original_server_password")
            self.assertEqual(_state.config.servers[0].key, "original_server_key")

        finally:
            client.close()
            patcher.stop()
            temp_dir.cleanup()

    def test_save_config_ui_rate_limiting(self):
        temp_dir, patcher, client, _state = self._build_client()
        try:
            # Login
            login_page = client.get("/login")
            csrf_token = login_page.text.split('name="csrf_token" value="', 1)[1].split('"', 1)[0]
            client.post(
                "/login",
                data={"password": "admin-pass", "csrf_token": csrf_token},
                follow_redirects=False,
            )
            home = client.get("/")
            authed_csrf = home.text.split('meta name="csrf-token" content="', 1)[1].split('"', 1)[0]

            # Setup original state config with test values after login to avoid messing up auth
            _state.config.discord.token = "original_token"
            _state.config.webui.password = "original_password"
            _state.config.features.power_control_password = "original_power_password"

            from models import ServerSettings
            _state.config.servers = [ServerSettings(alias="example_server", host="192.0.2.50", user="test", password="original_server_password", key="original_server_key")]
            _state.save_config(_state.config)
            original_webui_hash = _state.config.webui.password
            original_power_hash = _state.config.features.power_control_password


            payload = _state.config.model_dump()

            # Make enough requests to trigger rate limit
            # According to `api_limiter` setup, we might need 10 requests within a second (depends on how state is configured)
            # Default rate limit is usually 5 req/min. Let's just make 10 fast requests.
            for i in range(40):
                response = client.post(
                    "/save",
                    headers={"X-CSRF-Token": authed_csrf},
                    json=payload
                )
                if response.status_code == 429:


                    self.assertEqual(response.json()["detail"], "Too many requests. Please wait before saving again.")
                    break
            else:
                self.fail("Rate limiting did not trigger")

        finally:
            client.close()
            patcher.stop()
            temp_dir.cleanup()

    def test_save_config_ui_server_renaming(self):
        temp_dir, patcher, client, _state = self._build_client()
        try:
            from models import ServerSettings
            _state.config.servers = [ServerSettings(alias="example_server", host="192.0.2.50", user="test", password="original_server_password", key="original_server_key")]
            _state.save_config(_state.config)
            original_webui_hash = _state.config.webui.password
            original_power_hash = _state.config.features.power_control_password

            # Login
            login_page = client.get("/login")
            csrf_token = login_page.text.split('name="csrf_token" value="', 1)[1].split('"', 1)[0]
            client.post(
                "/login",
                data={"password": "admin-pass", "csrf_token": csrf_token},
                follow_redirects=False,
            )
            home = client.get("/")
            authed_csrf = home.text.split('meta name="csrf-token" content="', 1)[1].split('"', 1)[0]

            # Setup original state config with test values after login to avoid messing up auth
            _state.config.discord.token = "original_token"
            _state.config.webui.password = "original_password"
            _state.config.features.power_control_password = "original_power_password"

            from models import ServerSettings
            _state.config.servers = [ServerSettings(alias="example_server", host="192.0.2.50", user="test", password="original_server_password", key="original_server_key")]
            _state.save_config(_state.config)
            original_webui_hash = _state.config.webui.password
            original_power_hash = _state.config.features.power_control_password


            payload = _state.config.model_dump()
            payload["servers"][0]["alias"] = "new_example_server"
            payload["servers"][0]["_original_alias"] = "example_server"
            payload["servers"][0]["password"] = "********"
            payload["servers"][0]["key"] = "********"

            response = client.post(
                "/save",
                headers={"X-CSRF-Token": authed_csrf},
                json=payload
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["status"], "success")

            # Verify secrets are preserved and server renamed
            self.assertEqual(_state.config.servers[0].alias, "new_example_server")
            self.assertEqual(_state.config.servers[0].password, "original_server_password")
            self.assertEqual(_state.config.servers[0].key, "original_server_key")

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



    def test_example_server_unauthenticated(self):
        temp_dir, patcher, client, _state = self._build_client()
        try:
            response = client.post(
                "/api/test-server",
                json={"alias": "example_server", "host": "192.0.2.31", "user": "root", "port": 22, "auth_method": "password", "password": "pass"}
            )
            self.assertEqual(response.status_code, 401)
        finally:
            client.close()
            patcher.stop()
            temp_dir.cleanup()

    @patch("ssh_manager.SSHManager.test_server_connection")
    def test_example_server_authenticated_success(self, mock_test_conn):
        temp_dir, patcher, client, state = self._build_client()
        try:
            # Login first
            login_page = client.get("/login")
            csrf_token = login_page.text.split('name="csrf_token" value="', 1)[1].split('"', 1)[0]
            client.post(
                "/login",
                data={"password": "admin-pass", "csrf_token": csrf_token},
                follow_redirects=False,
            )
            home = client.get("/")
            authed_csrf = home.text.split('meta name="csrf-token" content="', 1)[1].split('"', 1)[0]

            mock_test_conn.return_value = (True, "Connection successful", "fingerprint123")

            payload = {
                "alias": "example_new_server",
                "host": "192.0.2.30",
                "user": "admin",
                "port": 2222,
                "auth_method": "password",
                "password": "secret_password",
                "trust_host": True
            }

            response = client.post(
                "/api/test-server",
                headers={"X-CSRF-Token": authed_csrf},
                json=payload
            )

            self.assertEqual(response.status_code, 200)
            data = response.json()
            self.assertEqual(data["success"], True)
            self.assertEqual(data["message"], "Connection successful")
            self.assertEqual(data["fingerprint"], "fingerprint123")

            mock_test_conn.assert_called_once()
            call_args = mock_test_conn.call_args[0]
            self.assertEqual(call_args[0]["alias"], "example_new_server")
            self.assertEqual(call_args[0]["host"], "192.0.2.30")
            self.assertEqual(call_args[0]["user"], "admin")
            self.assertEqual(call_args[0]["port"], 2222)
            self.assertEqual(call_args[0]["auth_method"], "password")
            self.assertEqual(call_args[0]["password"], "secret_password")
            self.assertEqual(call_args[1], True) # trust_host

        finally:
            client.close()
            patcher.stop()
            temp_dir.cleanup()

    @patch("ssh_manager.SSHManager.test_server_connection")
    def test_example_server_obfuscated_credentials(self, mock_test_conn):
        temp_dir, patcher, client, state = self._build_client()
        try:
            # Add an existing server to config
            from models import AppConfig, ServerSettings
            config = AppConfig.model_validate(state.config.model_dump())
            config.servers.append(ServerSettings(
                alias="example_existing_server",
                host="192.0.2.21",
                user="root",
                port=22,
                auth_method="password",
                password="real_password"
            ))
            config.servers.append(ServerSettings(
                alias="example_key_server",
                host="192.0.2.22",
                user="root",
                port=22,
                auth_method="key",
                key="real_key"
            ))
            state.save_config(config)

            # Login
            login_page = client.get("/login")
            csrf_token = login_page.text.split('name="csrf_token" value="', 1)[1].split('"', 1)[0]
            client.post(
                "/login",
                data={"password": "admin-pass", "csrf_token": csrf_token},
                follow_redirects=False,
            )
            home = client.get("/")
            authed_csrf = home.text.split('meta name="csrf-token" content="', 1)[1].split('"', 1)[0]

            mock_test_conn.return_value = (True, "OK", "fp")

            # Test password obfuscation
            payload_pwd = {
                "alias": "example_existing_server",
                "host": "192.0.2.21",
                "user": "root",
                "port": 22,
                "auth_method": "password",
                "password": "********",
                "trust_host": False
            }

            response_pwd = client.post(
                "/api/test-server",
                headers={"X-CSRF-Token": authed_csrf},
                json=payload_pwd
            )

            self.assertEqual(response_pwd.status_code, 200)
            mock_test_conn.assert_called_once()
            call_args_pwd = mock_test_conn.call_args[0]
            self.assertEqual(call_args_pwd[0]["password"], "real_password")
            self.assertEqual(call_args_pwd[0]["host"], "192.0.2.21")

            mock_test_conn.reset_mock()

            # Test key obfuscation
            payload_key = {
                "alias": "example_key_server",
                "host": "192.0.2.22",
                "user": "root",
                "port": 22,
                "auth_method": "key",
                "key": "********",
                "trust_host": False
            }

            response_key = client.post(
                "/api/test-server",
                headers={"X-CSRF-Token": authed_csrf},
                json=payload_key
            )

            self.assertEqual(response_key.status_code, 200)
            mock_test_conn.assert_called_once()
            call_args_key = mock_test_conn.call_args[0]
            self.assertEqual(call_args_key[0]["key"], "real_key")

        finally:
            client.close()
            patcher.stop()
            temp_dir.cleanup()

    @patch("ssh_manager.SSHManager.test_server_connection")
    def test_example_server_rate_limit(self, mock_test_conn):
        temp_dir, patcher, client, state = self._build_client()
        try:
            # Login
            login_page = client.get("/login")
            csrf_token = login_page.text.split('name="csrf_token" value="', 1)[1].split('"', 1)[0]
            client.post(
                "/login",
                data={"password": "admin-pass", "csrf_token": csrf_token},
                follow_redirects=False,
            )
            home = client.get("/")
            authed_csrf = home.text.split('meta name="csrf-token" content="', 1)[1].split('"', 1)[0]

            mock_test_conn.return_value = (True, "OK", "fp")

            payload = {
                "alias": "test_limit",
                "host": "192.0.2.31",
                "user": "root",
                "port": 22,
                "auth_method": "password",
                "password": "pass",
                "trust_host": False
            }

            state.api_limiter.max_attempts = 3

            # Fire off limit requests
            limit = state.api_limiter.max_attempts
            for _ in range(limit):
                resp = client.post(
                    "/api/test-server",
                    headers={"X-CSRF-Token": authed_csrf},
                    json=payload
                )
                self.assertEqual(resp.status_code, 200)

            # The next one should fail with 429
            resp = client.post(
                "/api/test-server",
                headers={"X-CSRF-Token": authed_csrf},
                json=payload
            )
            self.assertEqual(resp.status_code, 429)
            self.assertEqual(resp.json()["detail"], "Too many requests. Please wait before testing again.")

        finally:
            client.close()
            patcher.stop()
            temp_dir.cleanup()

class TestGetClientIp(unittest.TestCase):
    def _request(self, headers=None, client=("127.0.0.1", 8080), trusted_proxies="127.0.0.1"):
        from fastapi import FastAPI, Request
        from web_app import _parse_ip_networks

        app = FastAPI()
        app.state.trusted_proxy_networks = _parse_ip_networks(trusted_proxies)
        scope = {
            "type": "http",
            "headers": headers or [],
            "client": client,
            "app": app,
        }
        return Request(scope)

    def test_x_forwarded_for(self):
        from web_app import get_client_ip
        request = self._request(
            headers=[
                (b"x-forwarded-for", b"198.51.100.10, 192.0.2.21"),
                (b"x-real-ip", b"192.0.2.21"),
            ],
        )
        self.assertEqual(get_client_ip(request), "198.51.100.10")

    def test_x_real_ip(self):
        from web_app import get_client_ip
        request = self._request(
            headers=[
                (b"x-real-ip", b"192.0.2.21"),
            ],
        )
        self.assertEqual(get_client_ip(request), "192.0.2.21")

    def test_untrusted_proxy_headers_are_ignored(self):
        from web_app import get_client_ip
        request = self._request(
            headers=[(b"x-forwarded-for", b"198.51.100.10")],
            client=("203.0.113.9", 8080),
        )
        self.assertEqual(get_client_ip(request), "203.0.113.9")

    def test_client_host_fallback(self):
        from fastapi import Request
        from web_app import get_client_ip
        scope = {
            "type": "http",
            "headers": [],
            "client": ("198.51.100.20", 8080)
        }
        request = Request(scope)
        self.assertEqual(get_client_ip(request), "198.51.100.20")

    def test_missing_client(self):
        from fastapi import Request
        from web_app import get_client_ip
        scope = {
            "type": "http",
            "headers": [],
        }
        request = Request(scope)
        self.assertEqual(get_client_ip(request), "unknown")

if __name__ == "__main__":
    unittest.main()
