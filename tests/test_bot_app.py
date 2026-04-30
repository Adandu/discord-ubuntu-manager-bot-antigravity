import discord
from unittest.mock import AsyncMock, patch, MagicMock
import unittest
from types import SimpleNamespace

from app_state import AppState
from bot_app import _matches_roles, build_user_facing_error_message, check_permissions, is_allowed_log_path, is_admin
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

    def test_is_admin_check(self):
        state = AppState.__new__(AppState)
        state.config = AppConfig.model_validate({"discord": {"allowed_roles": "Admin"}})

        @is_admin(state)
        async def dummy_command(interaction):
            pass

        checks = getattr(dummy_command, "__discord_app_commands_checks__", [])
        self.assertEqual(len(checks), 1)
        predicate = checks[0]

        allowed_interaction = SimpleNamespace(user=SimpleNamespace(roles=[SimpleNamespace(name="Admin")]))
        denied_interaction = SimpleNamespace(user=SimpleNamespace(roles=[SimpleNamespace(name="User")]))

        self.assertTrue(predicate(allowed_interaction))
        self.assertFalse(predicate(denied_interaction))

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
        state.servers_by_alias = {s.alias: s for s in state.config.servers}
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

class TestBotApp_Allowlist(unittest.IsolatedAsyncioTestCase):

    @patch("bot_app.asyncio.to_thread", new_callable=AsyncMock)
    async def test_docker_control_allowlist(self, mock_to_thread):
        state = MagicMock()
        state.config.servers = [
            MagicMock(alias="alpha", allowed_containers="nginx, db"),
            MagicMock(alias="beta", allowed_containers=""),
        ]
        state.config.features.allowed_containers = "global_redis, global_api"

        from bot_app import DiscoBunty
        bot = DiscoBunty(state)

        # We need to test the docker_control command
        from bot_app import DockerGroup
        docker_group = DockerGroup(bot)


        # Test 1: Allowed container on alpha
        interaction = AsyncMock(spec=discord.Interaction)
        interaction.user.id = 123
        interaction.user.name = "testuser"
        interaction.response = AsyncMock()
        interaction.followup = AsyncMock()

        # Mock ensure_server_access to pass
        docker_group.ensure_server_access = MagicMock()

        mock_to_thread.return_value = "started"

        await docker_group.docker_control.callback(docker_group, interaction, "alpha", "start", "nginx")
        interaction.response.send_message.assert_not_called()
        interaction.response.defer.assert_awaited_once()

        # Test 2: Denied container on alpha
        interaction.reset_mock()
        await docker_group.docker_control.callback(docker_group, interaction, "alpha", "start", "hacked_container")
        interaction.response.send_message.assert_awaited_once()
        self.assertIn("Execution denied", interaction.response.send_message.call_args[0][0])

        # Test 3: Fallback to global config on beta, allowed
        interaction.reset_mock()
        await docker_group.docker_control.callback(docker_group, interaction, "beta", "start", "global_redis")
        interaction.response.send_message.assert_not_called()
        interaction.response.defer.assert_awaited_once()

        # Test 4: Fallback to global config on beta, denied
        interaction.reset_mock()
        await docker_group.docker_control.callback(docker_group, interaction, "beta", "start", "nginx")
        interaction.response.send_message.assert_awaited_once()
        self.assertIn("Execution denied", interaction.response.send_message.call_args[0][0])
