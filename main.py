import os
import logging
import discord
import shlex
import asyncio
from discord import app_commands
from dotenv import load_dotenv
from ssh_manager import SSHManager

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('discobunty')

# Load environment variables
load_dotenv()

DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
GUILD_ID = os.getenv('GUILD_ID') # Optional: For faster command sync
ENABLE_DOCKER = os.getenv('ENABLE_DOCKER', 'false').lower() == 'true'
ALLOWED_ROLES = [r.strip() for r in os.getenv('ALLOWED_ROLES', '').split(',') if r.strip()]

# Constants
MAX_MSG_LEN = 1900
ALLOWED_LOG_ROOTS = ["/var/log/", "/home/"] # Paths allowed for the /logs command

# Initialize SSH Manager
ssh_manager = SSHManager()

class DiscoBunty(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        if GUILD_ID:
            guild = discord.Object(id=int(GUILD_ID))
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()

bot = DiscoBunty()

@bot.event
async def on_ready():
    logger.info(f'Logged in as {bot.user} (ID: {bot.user.id})')
    logger.info(f'Docker integration: {"Enabled" if ENABLE_DOCKER else "Disabled"}')
    if not ALLOWED_ROLES:
        logger.warning('ALLOWED_ROLES is not configured. All administrative commands will be blocked.')
    else:
        logger.info(f'Allowed Roles: {ALLOWED_ROLES}')
    logger.info('------')

# --- RBAC Helper ---
def check_permissions(user) -> bool:
    """Helper to check if a user has required roles (fails closed)."""
    if not ALLOWED_ROLES:
        return False
    if not hasattr(user, 'roles'):
        return False
    user_roles = [role.name for role in user.roles]
    return any(role in ALLOWED_ROLES for role in user_roles)

# --- RBAC Check Decorator ---
def is_admin():
    def predicate(interaction: discord.Interaction) -> bool:
        return check_permissions(interaction.user)
    return app_commands.check(predicate)

@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.CheckFailure):
        await interaction.response.send_message("❌ You do not have permission to use this command.", ephemeral=True)
    else:
        logger.error(f"Command error: {error}")
        msg = "❌ An unexpected error occurred while executing the command."
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)

# --- Helper: Autocomplete for Servers ---
async def server_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    # 🛡️ RBAC: Don't leak server names to non-admins
    if not check_permissions(interaction.user):
        return []
    
    aliases = ssh_manager.get_server_aliases()
    return [
        app_commands.Choice(name=alias, value=alias)
        for alias in aliases if current.lower() in alias.lower()
    ]

# --- Helper: Autocomplete for Containers ---
async def container_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    # 🛡️ RBAC: Don't leak container names to non-admins
    if not check_permissions(interaction.user):
        return []

    server = interaction.namespace.server
    if not server:
        return []
    
    try:
        # 🚀 Async: Offload SSH call to a thread to avoid blocking loop
        containers = await asyncio.to_thread(ssh_manager.get_containers, server)
        return [
            app_commands.Choice(name=name, value=name)
            for name in containers if current.lower() in name.lower()
        ][:25] # Discord limit is 25 choices
    except Exception as e:
        logger.error(f"Error fetching containers for {server}: {e}")
        return []

# --- Helper: Autocomplete for Logs ---
async def log_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    # 🛡️ RBAC: Don't leak log paths to non-admins
    if not check_permissions(interaction.user):
        return []

    server = interaction.namespace.server
    if not server:
        return []
    
    try:
        # 🚀 Async: Offload SSH call to a thread to avoid blocking loop
        logs = await asyncio.to_thread(ssh_manager.get_log_files, server)
        return [
            app_commands.Choice(name=path, value=path)
            for path in logs if current.lower() in path.lower()
        ][:25]
    except Exception as e:
        logger.error(f"Error fetching logs for {server}: {e}")
        return []

# --- Commands ---
@bot.tree.command(name="ping", description="Check bot latency")
async def ping(interaction: discord.Interaction):
    logger.info(f"Command '/ping' used by {interaction.user}")
    await interaction.response.send_message(f'Pong! {round(bot.latency * 1000)}ms')

@bot.tree.command(name="stats", description="Show system statistics (CPU, RAM, Disk, etc.) for a server")
@is_admin()
@app_commands.autocomplete(server=server_autocomplete)
async def stats(interaction: discord.Interaction, server: str):
    logger.info(f"Command '/stats' for server '{server}' used by {interaction.user}")
    await interaction.response.defer()
    try:
        output = await asyncio.to_thread(ssh_manager.get_system_stats, server)
        await interaction.followup.send(f"**System Stats for `{server}`:**\n```\n{output[:MAX_MSG_LEN]}\n```")
    except Exception as e:
        logger.error(f"Error executing '/stats' for {server}: {e}")
        await interaction.followup.send("❌ Error executing stats command. Check bot logs.")

# --- Docker Command Group ---
if ENABLE_DOCKER:
    docker_group = app_commands.Group(name="docker", description="Manage Docker containers")

    @docker_group.command(name="ps", description="List containers on a server")
    @is_admin()
    @app_commands.autocomplete(server=server_autocomplete)
    async def docker_ps(interaction: discord.Interaction, server: str, all: bool = True):
        logger.info(f"Command '/docker ps' (all={all}) for server '{server}' used by {interaction.user}")
        await interaction.response.defer()
        try:
            cmd = "sudo docker ps -a" if all else "sudo docker ps"
            output = await asyncio.to_thread(ssh_manager.execute_command, server, cmd)
            await interaction.followup.send(f"**Containers on `{server}`:**\n```\n{output[:MAX_MSG_LEN]}\n```")
        except Exception as e:
            logger.error(f"Error executing '/docker ps' for {server}: {e}")
            await interaction.followup.send("❌ Error listing containers.")

    @docker_group.command(name="control", description="Start, stop, or restart a container")
    @is_admin()
    @app_commands.autocomplete(server=server_autocomplete, container=container_autocomplete)
    @app_commands.describe(action="The action to perform (start, stop, restart)")
    @app_commands.choices(action=[
        app_commands.Choice(name="start", value="start"),
        app_commands.Choice(name="stop", value="stop"),
        app_commands.Choice(name="restart", value="restart"),
    ])
    async def docker_control(interaction: discord.Interaction, server: str, action: str, container: str):
        logger.info(f"Command '/docker control {action}' for container '{container}' on server '{server}' used by {interaction.user}")
        await interaction.response.defer()
        try:
            output = await asyncio.to_thread(ssh_manager.container_action, server, container, action)
            await interaction.followup.send(f"**Action `{action}` on container `{container}` (`{server}`):**\n```\n{output[:MAX_MSG_LEN]}\n```")
        except Exception as e:
            logger.error(f"Error executing '/docker control {action}' for {container} on {server}: {e}")
            await interaction.followup.send("❌ Error performing docker action.")

    @docker_group.command(name="logs", description="View container logs")
    @is_admin()
    @app_commands.autocomplete(server=server_autocomplete, container=container_autocomplete)
    @app_commands.describe(lines="Number of lines to display", search="Optional term to search for in logs")
    async def docker_logs(interaction: discord.Interaction, server: str, container: str, lines: int = 50, search: str = None):
        logger.info(f"Command '/docker logs' for container '{container}' on server '{server}' (lines={lines}, search={search}) used by {interaction.user}")
        await interaction.response.defer()
        try:
            lines = min(max(1, lines), 100)
            output = await asyncio.to_thread(ssh_manager.get_container_logs, server, container, lines, search)
            
            if not output.strip() and "Error" not in output:
                output = "No logs found or search term not found."

            header = f"**Logs for `{container}` on `{server}` (Last {lines} lines"
            if search:
                header += f", Search: `{search}`"
            header += "):**"
            await interaction.followup.send(f"{header}\n```\n{output[:MAX_MSG_LEN]}\n```")
        except Exception as e:
            logger.error(f"Error executing '/docker logs' for {container} on {server}: {e}")
            await interaction.followup.send("❌ Error fetching logs.")

    @docker_group.command(name="details", description="View container image, IP, and ports")
    @is_admin()
    @app_commands.autocomplete(server=server_autocomplete, container=container_autocomplete)
    async def docker_details(interaction: discord.Interaction, server: str, container: str):
        logger.info(f"Command '/docker details' for container '{container}' on server '{server}' used by {interaction.user}")
        await interaction.response.defer()
        try:
            output = await asyncio.to_thread(ssh_manager.get_container_details, server, container)
            await interaction.followup.send(f"**Details for `{container}` on `{server}`:**\n```\n{output[:MAX_MSG_LEN]}\n```")
        except Exception as e:
            logger.error(f"Error executing '/docker details' for {container} on {server}: {e}")
            await interaction.followup.send("❌ Error fetching container details.")

    bot.tree.add_command(docker_group)

@bot.tree.command(name="disk", description="Check disk space on a specific Ubuntu server")
@is_admin()
@app_commands.autocomplete(server=server_autocomplete)
async def disk(interaction: discord.Interaction, server: str):
    logger.info(f"Command '/disk' for server '{server}' used by {interaction.user}")
    await interaction.response.defer()
    try:
        output = await asyncio.to_thread(ssh_manager.execute_command, server, "df -h")
        await interaction.followup.send(f"**Disk Space on `{server}`:**\n```\n{output[:MAX_MSG_LEN]}\n```")
    except Exception as e:
        logger.error(f"Error executing '/disk' for {server}: {e}")
        await interaction.followup.send("❌ Error checking disk space.")

@bot.tree.command(name="update", description="Check and install updates on a specific Ubuntu server")
@is_admin()
@app_commands.autocomplete(server=server_autocomplete)
async def update(interaction: discord.Interaction, server: str):
    logger.info(f"Command '/update' for server '{server}' used by {interaction.user}")
    await interaction.response.defer()
    try:
        # Using -y for non-interactive upgrade. Note: sudo might need NOPASSWD config.
        cmd = "sudo apt-get update && sudo apt-get upgrade -y"
        output = await asyncio.to_thread(ssh_manager.execute_command, server, cmd)
        await interaction.followup.send(f"**Update Result for `{server}`:**\n```\n{output[:MAX_MSG_LEN]}\n```")
    except Exception as e:
        logger.error(f"Error executing '/update' for {server}: {e}")
        await interaction.followup.send("❌ Error performing update.")

@bot.tree.command(name="process", description="Search for running processes on a specific Ubuntu server")
@is_admin()
@app_commands.autocomplete(server=server_autocomplete)
async def process(interaction: discord.Interaction, server: str, search: str):
    logger.info(f"Command '/process' (search='{search}') for server '{server}' used by {interaction.user}")
    await interaction.response.defer()
    try:
        # Sanitize search input
        safe_search = shlex.quote(search)
        # Case-insensitive grep, excluding the grep process itself. Use -e to prevent flag injection.
        cmd = f"ps aux | grep -i -e {safe_search} | grep -v grep"
        output = await asyncio.to_thread(ssh_manager.execute_command, server, cmd)
        if not output.strip():
            output = f"No processes found matching '{search}'."
        await interaction.followup.send(f"**Processes on `{server}` (Search: '{search}'):**\n```\n{output[:MAX_MSG_LEN]}\n```")
    except Exception as e:
        logger.error(f"Error executing '/process' for {server}: {e}")
        await interaction.followup.send("❌ Error searching processes.")

@bot.tree.command(name="service", description="Control a service on a specific Ubuntu server")
@is_admin()
@app_commands.autocomplete(server=server_autocomplete)
@app_commands.describe(action="The action to perform (status, start, stop, restart)")
@app_commands.choices(action=[
    app_commands.Choice(name="status", value="status"),
    app_commands.Choice(name="start", value="start"),
    app_commands.Choice(name="stop", value="stop"),
    app_commands.Choice(name="restart", value="restart"),
])
async def service(interaction: discord.Interaction, server: str, action: str, name: str):
    logger.info(f"Command '/service {action}' for service '{name}' on server '{server}' used by {interaction.user}")
    await interaction.response.defer()
    try:
        # Sanitize inputs
        safe_action = shlex.quote(action)
        safe_name = shlex.quote(name)
        cmd = f"sudo systemctl {safe_action} {safe_name}"
        # For status, we want to see the output. For others, just a confirmation if no error.
        output = await asyncio.to_thread(ssh_manager.execute_command, server, cmd)
        
        response_msg = f"**Service `{name}` {action} on `{server}`**"
        if output.strip():
            response_msg += f":\n```\n{output[:1800]}\n```"
        else:
            response_msg += " successfully (no output)."
            
        await interaction.followup.send(response_msg)
    except Exception as e:
        logger.error(f"Error executing '/service {action}' for {name} on {server}: {e}")
        await interaction.followup.send("❌ Error performing service action.")

@bot.tree.command(name="logs", description="View recent log entries on a specific Ubuntu server")
@is_admin()
@app_commands.autocomplete(server=server_autocomplete, path=log_autocomplete)
@app_commands.describe(path="Path to the log file (e.g., /var/log/syslog)", lines="Number of lines to display", search="Optional term to search for in logs")
async def logs(interaction: discord.Interaction, server: str, path: str, lines: int = 20, search: str = None):
    logger.info(f"Command '/logs' for path '{path}' on server '{server}' (lines={lines}, search={search}) used by {interaction.user}")
    await interaction.response.defer()
    try:
        # Path validation: must start with an allowed root
        if not any(path.startswith(root) for root in ALLOWED_LOG_ROOTS) or ".." in path:
            await interaction.followup.send(f"❌ Access denied to path: `{path}`. Only paths starting with {ALLOWED_LOG_ROOTS} are allowed.", ephemeral=True)
            return

        # Cap lines to prevent massive messages
        lines = min(max(1, lines), 100)
        # Sanitize path
        safe_path = shlex.quote(path)
        # Remote-side symlink check for defense-in-depth: resolve symlinks and check again
        allowed_pattern = "^(" + "|".join(ALLOWED_LOG_ROOTS) + ")"
        
        if search:
            safe_search = shlex.quote(search)
            # Chain grep after tail, but keep the symlink/realpath check. Use -e to prevent flag injection.
            cmd = f"realpath {safe_path} | grep -qE {shlex.quote(allowed_pattern)} && sudo tail -n {lines} {safe_path} | grep -i -e {safe_search} | tail -n {lines}"
        else:
            cmd = f"realpath {safe_path} | grep -qE {shlex.quote(allowed_pattern)} && sudo tail -n {lines} {safe_path}"
            
        output = await asyncio.to_thread(ssh_manager.execute_command, server, cmd)
        
        if not output.strip() and "Error" not in output:
             output = "Access denied, file empty, or search term not found."
        
        header = f"**Last {lines} lines of `{path}` on `{server}`"
        if search:
            header += f" (Search: `{search}`)"
        header += ":**"
        
        await interaction.followup.send(f"{header}\n```\n{output[:MAX_MSG_LEN]}\n```")
    except Exception as e:
        logger.error(f"Error executing '/logs' for {path} on {server}: {e}")
        await interaction.followup.send("❌ Error fetching system logs.")

if __name__ == "__main__":
    if not DISCORD_TOKEN:
        print("Error: DISCORD_TOKEN not found in environment variables.")
    else:
        bot.run(DISCORD_TOKEN)
