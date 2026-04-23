import unittest
from types import SimpleNamespace

from app_state import AppState
from bot_app import _matches_roles, build_user_facing_error_message, check_permissions, is_allowed_log_path
from discord import app_commands
from models import AppConfig


class BotPermissionTests(unittest.TestCase):
    def test_matches_roles(self):
        self.assertFalse(_matches_roles(["Admin"], ""))
        self.assertFalse(_matches_roles(["Admin"], "   "))
        self.assertFalse(_matches_roles(["Admin"], ","))
        self.assertFalse(_matches_roles([], "Admin"))
        self.assertTrue(_matches_roles(["Admin"], "Admin"))
        self.assertTrue(_matches_roles(["Admin", "User"], "Admin"))
        self.assertFalse(_matches_roles(["admin"], "Admin"))
        self.assertTrue(_matches_roles(["User"], "Admin,User,Guest"))
        self.assertTrue(_matches_roles(["Guest"], "Admin, User, Guest"))
        self.assertFalse(_matches_roles(["Guest"], "Admin,User"))

    def test_server_role_scope_is_more_restrictive_than_global_roles(self):
        state = AppState.__new__(AppState)
        state.config = AppConfig.model_validate(
            {
                "discord": {"allowed_roles": "Admin,DevOps"},
                "servers": [
                    {"alias": "alpha", "allowed_roles": ""},
                    {"alias": "beta", "allowed_roles": "SRE"},
                ],
            }
        )
        user = SimpleNamespace(roles=[SimpleNamespace(name="Admin")])
        sre_user = SimpleNamespace(roles=[SimpleNamespace(name="Admin"), SimpleNamespace(name="SRE")])

        self.assertTrue(check_permissions(state, user))
        self.assertTrue(check_permissions(state, user, "alpha"))
        self.assertFalse(check_permissions(state, user, "beta"))
        self.assertTrue(check_permissions(state, sre_user, "beta"))

    def test_log_path_validation_requires_absolute_allowed_root(self):
        self.assertTrue(is_allowed_log_path("/var/log/syslog"))
        self.assertTrue(is_allowed_log_path("/home/app/logs/service.log"))
        self.assertFalse(is_allowed_log_path("var/log/syslog"))
        self.assertFalse(is_allowed_log_path("/etc/shadow"))

    def test_user_facing_error_message_redacts_internal_exception_details(self):
        error = app_commands.AppCommandError("secret backend details")
        message = build_user_facing_error_message(error, "deadbeef")
        self.assertIn("deadbeef", message)
        self.assertNotIn("secret backend details", message)

    def test_user_facing_error_message_check_failure(self):
        error = app_commands.CheckFailure("check failed")
        message = build_user_facing_error_message(error, "deadbeef")
        self.assertEqual("❌ You do not have permission to use that command or access that server.", message)


if __name__ == "__main__":
    unittest.main()
