import unittest
from types import SimpleNamespace

from app_state import AppState
from bot_app import check_permissions
from models import AppConfig


class BotPermissionTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
