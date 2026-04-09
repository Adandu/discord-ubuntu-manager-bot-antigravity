from __future__ import annotations

import asyncio
import os
import shlex

import discord
from discord import app_commands

from app_state import ALLOWED_LOG_ROOTS, MAX_MSG_LEN, AppState
from auth_utils import verify_password


def _role_names(user) -> list[str]:
    return [role.name for role in getattr(user, "roles", [])]


def _matches_roles(user_roles: list[str], allowed_roles_str: str) -> bool:
    allowed_roles = [role.strip() for role in allowed_roles_str.split(",") if role.strip()]
    if not allowed_roles:
        return False
    return any(role in allowed_roles for role in user_roles)


def check_permissions(state: AppState, user, server_alias: str | None = None) -> bool:
    if not hasattr(user, "roles"):
        return False
    user_roles = _role_names(user)
    if not _matches_roles(user_roles, state.config.discord.allowed_roles):
        return False

    if not server_alias:
        return True

    server = next((candidate for candidate in state.config.servers if candidate.alias == server_alias), None)
    if not server or not server.allowed_roles.strip():
        return True
    return _matches_roles(user_roles, server.allowed_roles)


def is_admin(state: AppState):
    def predicate(interaction: discord.Interaction) -> bool:
        return check_permissions(state, interaction.user)

    return app_commands.check(predicate)


class DiscoBunty(discord.Client):
    def __init__(self, state: AppState):
        intents = discord.Intents.default()
        super().__init__(intents=intents)
        self.state = state
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self) -> None:
        guild_id = self.state.config.discord.guild_id
        if guild_id:
            try:
                guild = discord.Object(id=int(guild_id))
                self.tree.copy_global_to(guild=guild)
                await self.tree.sync(guild=guild)
            except ValueError:
                self.state.logger.error("Invalid Guild ID: %s", guild_id)
        else:
            await self.tree.sync()


class PowerControlModal(discord.ui.Modal, title="Verify Power Action"):
    password = discord.ui.TextInput(
        label="Security Password",
        placeholder="Enter safety password",
        style=discord.TextStyle.short,
        required=True,
    )

    def __init__(self, state: AppState, server: str, action: str):
        super().__init__()
        self.state = state
        self.server = server
        self.action = action

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not verify_password(self.password.value, self.state.config.features.power_control_password):
            await interaction.response.send_message("❌ Incorrect password. Action aborted.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        output = await asyncio.to_thread(self.state.ssh_manager.server_power_action, self.server, self.action)
        await interaction.followup.send(output, ephemeral=True)


class PowerConfirmationView(discord.ui.View):
    def __init__(self, state: AppState, server: str, action: str):
        super().__init__(timeout=60)
        self.state = state
        self.server = server
        self.action = action

    @discord.ui.button(label="Confirm Action", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(PowerControlModal(self.state, self.server, self.action))
        self.stop()


def create_bot(state: AppState) -> DiscoBunty:
    bot = DiscoBunty(state)

    @bot.event
    async def on_ready() -> None:
        state.logger.info("Logged in as %s (ID: %s)", bot.user, bot.user.id)
        state.logger.info("Docker integration: %s", "Enabled" if state.config.features.enable_docker else "Disabled")
        state.logger.info("------")

    @bot.tree.error
    async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        state.logger.exception("Discord app command error: %s", error, exc_info=error)
        if isinstance(error, app_commands.CheckFailure):
            message = "❌ You do not have permission to use that command or access that server."
        elif isinstance(error, app_commands.CommandInvokeError) and error.original:
            message = f"❌ Command failed: {error.original}"
        else:
            message = "❌ Command failed. Check DiscoBunty logs for details."

        if interaction.response.is_done():
            await interaction.followup.send(message[:1900], ephemeral=True)
        else:
            await interaction.response.send_message(message[:1900], ephemeral=True)

    def ensure_server_access(interaction: discord.Interaction, server: str) -> None:
        if not check_permissions(state, interaction.user, server):
            raise app_commands.CheckFailure(f"User lacks access to server {server}")

    async def server_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        if not check_permissions(state, interaction.user):
            return []
        aliases = sorted(
            [alias for alias in state.ssh_manager.get_server_aliases() if check_permissions(state, interaction.user, alias)],
            key=lambda value: value.lower(),
        )
        return [app_commands.Choice(name=alias, value=alias) for alias in aliases if current.lower() in alias.lower()]

    async def container_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        if not check_permissions(state, interaction.user):
            return []
        server = interaction.namespace.server
        if not server or not check_permissions(state, interaction.user, server):
            return []
        try:
            containers = await asyncio.to_thread(state.ssh_manager.get_containers, server)
            return [
                app_commands.Choice(name=name, value=name)
                for name in sorted(containers, key=lambda value: value.lower())
                if current.lower() in name.lower()
            ][:25]
        except Exception:
            return []

    async def log_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        if not check_permissions(state, interaction.user):
            return []
        server = interaction.namespace.server
        if not server or not check_permissions(state, interaction.user, server):
            return []
        try:
            logs = await asyncio.to_thread(state.ssh_manager.get_log_files, server)
            return [
                app_commands.Choice(name=path, value=path)
                for path in sorted(logs, key=lambda value: value.lower())
                if current.lower() in path.lower()
            ][:25]
        except Exception:
            return []

    @bot.tree.command(name="ping", description="Check bot latency")
    async def ping(interaction: discord.Interaction) -> None:
        await interaction.response.send_message(f"Pong! {round(bot.latency * 1000)}ms")

    @bot.tree.command(name="server", description="Server management commands")
    @is_admin(state)
    @app_commands.autocomplete(server=server_autocomplete)
    @app_commands.choices(action=[app_commands.Choice(name="reboot", value="reboot"), app_commands.Choice(name="shutdown", value="shutdown")])
    async def server_power(interaction: discord.Interaction, server: str, action: str) -> None:
        ensure_server_access(interaction, server)
        if not state.config.features.power_control_enabled:
            await interaction.response.send_message("❌ Power control is currently disabled.", ephemeral=True)
            return
        state.audit_log(interaction.user.id, interaction.user.name, "server_power", f"Action: {action} | Server: {server}")
        view = PowerConfirmationView(state, server, action)
        await interaction.response.send_message(
            content=f"⚠️ **Warning:** You are about to **{action}** server `{server}`. Are you sure?",
            view=view,
            ephemeral=True,
        )

    @bot.tree.command(name="stats", description="Show system statistics for a server")
    @is_admin(state)
    @app_commands.autocomplete(server=server_autocomplete)
    async def stats(interaction: discord.Interaction, server: str) -> None:
        ensure_server_access(interaction, server)
        await interaction.response.defer()
        output = await asyncio.to_thread(state.ssh_manager.get_system_stats, server)
        await interaction.followup.send(f"**System Stats for `{server}`:**\n```\n{output[:MAX_MSG_LEN]}\n```")

    @bot.tree.command(name="disk", description="Check disk space on a server")
    @is_admin(state)
    @app_commands.autocomplete(server=server_autocomplete)
    async def disk(interaction: discord.Interaction, server: str) -> None:
        ensure_server_access(interaction, server)
        await interaction.response.defer()
        output = await asyncio.to_thread(state.ssh_manager.execute_command, server, "df -h")
        await interaction.followup.send(f"**Disk Space on `{server}`:**\n```\n{output[:MAX_MSG_LEN]}\n```")

    @bot.tree.command(name="update", description="Check and install updates on a server")
    @is_admin(state)
    @app_commands.autocomplete(server=server_autocomplete)
    async def update(interaction: discord.Interaction, server: str) -> None:
        ensure_server_access(interaction, server)
        await interaction.response.defer()
        output = await asyncio.to_thread(state.ssh_manager.execute_command, server, "sudo apt-get update && sudo apt-get upgrade -y")
        await interaction.followup.send(f"**Update Result for `{server}`:**\n```\n{output[:MAX_MSG_LEN]}\n```")

    @bot.tree.command(name="process", description="Search for running processes on a server")
    @is_admin(state)
    @app_commands.autocomplete(server=server_autocomplete)
    async def process(interaction: discord.Interaction, server: str, search: str) -> None:
        ensure_server_access(interaction, server)
        await interaction.response.defer()
        cmd = f"ps aux | grep -i -e {shlex.quote(search)} | grep -v grep"
        output = await asyncio.to_thread(state.ssh_manager.execute_command, server, cmd)
        await interaction.followup.send(f"**Processes on `{server}` (Search: '{search}'):**\n```\n{output[:MAX_MSG_LEN]}\n```")

    @bot.tree.command(name="service", description="Control a systemd service on a server")
    @is_admin(state)
    @app_commands.autocomplete(server=server_autocomplete)
    @app_commands.choices(action=[
        app_commands.Choice(name="status", value="status"),
        app_commands.Choice(name="start", value="start"),
        app_commands.Choice(name="stop", value="stop"),
        app_commands.Choice(name="restart", value="restart"),
    ])
    async def service(interaction: discord.Interaction, server: str, action: str, name: str) -> None:
        ensure_server_access(interaction, server)
        await interaction.response.defer()
        state.audit_log(interaction.user.id, interaction.user.name, "service", f"Action: {action} | Service: {name} | Server: {server}")
        cmd = f"sudo systemctl {shlex.quote(action)} {shlex.quote(name)}"
        output = await asyncio.to_thread(state.ssh_manager.execute_command, server, cmd)
        await interaction.followup.send(f"**Service `{name}` {action} on `{server}`**:\n```\n{output[:MAX_MSG_LEN]}\n```")

    @bot.tree.command(name="logs", description="View recent system log entries on a server")
    @is_admin(state)
    @app_commands.autocomplete(server=server_autocomplete, path=log_autocomplete)
    async def system_logs(interaction: discord.Interaction, server: str, path: str, lines: int = 20, search: str | None = None) -> None:
        ensure_server_access(interaction, server)
        await interaction.response.defer()
        real_path = await asyncio.to_thread(state.ssh_manager.resolve_remote_path, server, path)
        normalized_path = os.path.normpath(real_path).replace("\\", "/")
        if not any(normalized_path.startswith(root) for root in ALLOWED_LOG_ROOTS):
            await interaction.followup.send(
                f"❌ Access denied to path: `{path}` (resolved to `{normalized_path}`)",
                ephemeral=True,
            )
            return

        lines = min(max(1, lines), 100)
        cmd = f"sudo tail -n {lines} {shlex.quote(normalized_path)}"
        if search:
            cmd += f" | grep -i -e {shlex.quote(search)}"
        output = await asyncio.to_thread(state.ssh_manager.execute_command, server, cmd)
        await interaction.followup.send(f"**Last {lines} lines of `{normalized_path}` on `{server}`:**\n```\n{output[:MAX_MSG_LEN]}\n```")

    if state.config.features.enable_docker:
        docker_group = app_commands.Group(name="docker", description="Manage Docker containers")

        @docker_group.command(name="ps", description="List containers on a server")
        @app_commands.check(lambda interaction: check_permissions(state, interaction.user))
        @app_commands.autocomplete(server=server_autocomplete)
        async def docker_ps(interaction: discord.Interaction, server: str, all: bool = True) -> None:
            ensure_server_access(interaction, server)
            await interaction.response.defer()
            cmd = "sudo docker ps -a" if all else "sudo docker ps"
            output = await asyncio.to_thread(state.ssh_manager.execute_command, server, cmd)
            await interaction.followup.send(f"**Containers on `{server}`:**\n```\n{output[:MAX_MSG_LEN]}\n```")

        @docker_group.command(name="control", description="Start, stop, or restart a container")
        @app_commands.check(lambda interaction: check_permissions(state, interaction.user))
        @app_commands.autocomplete(server=server_autocomplete, container=container_autocomplete)
        @app_commands.choices(action=[
            app_commands.Choice(name="start", value="start"),
            app_commands.Choice(name="stop", value="stop"),
            app_commands.Choice(name="restart", value="restart"),
        ])
        async def docker_control(interaction: discord.Interaction, server: str, action: str, container: str) -> None:
            ensure_server_access(interaction, server)
            await interaction.response.defer()
            state.audit_log(
                interaction.user.id,
                interaction.user.name,
                "docker_control",
                f"Action: {action} | Container: {container} | Server: {server}",
            )
            output = await asyncio.to_thread(state.ssh_manager.container_action, server, container, action)
            await interaction.followup.send(f"**Action `{action}` on container `{container}` (`{server}`):**\n```\n{output[:MAX_MSG_LEN]}\n```")

        @docker_group.command(name="logs", description="View container logs")
        @app_commands.check(lambda interaction: check_permissions(state, interaction.user))
        @app_commands.autocomplete(server=server_autocomplete, container=container_autocomplete)
        async def docker_logs(interaction: discord.Interaction, server: str, container: str, lines: int = 50, search: str | None = None) -> None:
            ensure_server_access(interaction, server)
            await interaction.response.defer()
            lines = min(max(1, lines), 100)
            output = await asyncio.to_thread(state.ssh_manager.get_container_logs, server, container, lines, search)
            await interaction.followup.send(f"**Logs for `{container}` on `{server}` (Last {lines} lines):**\n```\n{output[:MAX_MSG_LEN]}\n```")

        @docker_group.command(name="details", description="View container image, IP, and ports")
        @app_commands.check(lambda interaction: check_permissions(state, interaction.user))
        @app_commands.autocomplete(server=server_autocomplete, container=container_autocomplete)
        async def docker_details(interaction: discord.Interaction, server: str, container: str) -> None:
            ensure_server_access(interaction, server)
            await interaction.response.defer()
            output = await asyncio.to_thread(state.ssh_manager.get_container_details, server, container)
            await interaction.followup.send(f"**Details for `{container}` on `{server}`:**\n```\n{output[:MAX_MSG_LEN]}\n```")

        bot.tree.add_command(docker_group)

    return bot
