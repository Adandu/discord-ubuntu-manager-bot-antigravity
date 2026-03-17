import os
import discord
from discord import app_commands
from dotenv import load_dotenv
from ssh_manager import SSHManager

# Load environment variables
load_dotenv()

DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
GUILD_ID = os.getenv('GUILD_ID') # Optional: For faster command sync

# Initialize SSH Manager
ssh_manager = SSHManager()

class UbuntuManagerBot(discord.Client):
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

bot = UbuntuManagerBot()

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user} (ID: {bot.user.id})')
    print('------')

# --- Helper: Autocomplete for Servers ---
async def server_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
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
    # Get the "server" value already selected in the interaction
    server = interaction.namespace.server
    if not server:
        return []
    
    # This might be slow if many containers, but usually acceptable for homelab
    containers = ssh_manager.get_containers(server)
    return [
        app_commands.Choice(name=name, value=name)
        for name in containers if current.lower() in name.lower()
    ][:25] # Discord limit is 25 choices

# --- Commands ---
@bot.tree.command(name="ping", description="Check bot latency")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message(f'Pong! {round(bot.latency * 1000)}ms')

@bot.tree.command(name="stats", description="Show system statistics (CPU, RAM, Disk, etc.) for a server")
@app_commands.autocomplete(server=server_autocomplete)
async def stats(interaction: discord.Interaction, server: str):
    await interaction.response.defer()
    output = ssh_manager.get_system_stats(server)
    await interaction.followup.send(f"**System Stats for `{server}`:**\n```\n{output[:1900]}\n```")

# --- Docker Command Group ---
docker_group = app_commands.Group(name="docker", description="Manage Docker containers")

@docker_group.command(name="ps", description="List containers on a server")
@app_commands.autocomplete(server=server_autocomplete)
async def docker_ps(interaction: discord.Interaction, server: str, all: bool = True):
    await interaction.response.defer()
    cmd = "sudo docker ps -a" if all else "sudo docker ps"
    output = ssh_manager.execute_command(server, cmd)
    await interaction.followup.send(f"**Containers on `{server}`:**\n```\n{output[:1900]}\n```")

@docker_group.command(name="control", description="Start, stop, or restart a container")
@app_commands.autocomplete(server=server_autocomplete, container=container_autocomplete)
@app_commands.describe(action="The action to perform (start, stop, restart)")
@app_commands.choices(action=[
    app_commands.Choice(name="start", value="start"),
    app_commands.Choice(name="stop", value="stop"),
    app_commands.Choice(name="restart", value="restart"),
])
async def docker_control(interaction: discord.Interaction, server: str, action: str, container: str):
    await interaction.response.defer()
    output = ssh_manager.container_action(server, container, action)
    await interaction.followup.send(f"**Action `{action}` on container `{container}` (`{server}`):**\n```\n{output[:1900]}\n```")

@docker_group.command(name="logs", description="View container logs")
@app_commands.autocomplete(server=server_autocomplete, container=container_autocomplete)
@app_commands.describe(lines="Number of lines to display")
async def docker_logs(interaction: discord.Interaction, server: str, container: str, lines: int = 50):
    await interaction.response.defer()
    lines = min(max(1, lines), 100)
    output = ssh_manager.get_container_logs(server, container, lines)
    await interaction.followup.send(f"**Logs for `{container}` on `{server}` (Last {lines} lines):**\n```\n{output[:1900]}\n```")

@docker_group.command(name="details", description="View container image, IP, and ports")
@app_commands.autocomplete(server=server_autocomplete, container=container_autocomplete)
async def docker_details(interaction: discord.Interaction, server: str, container: str):
    await interaction.response.defer()
    output = ssh_manager.get_container_details(server, container)
    await interaction.followup.send(f"**Details for `{container}` on `{server}`:**\n```\n{output[:1900]}\n```")

bot.tree.add_command(docker_group)

@bot.tree.command(name="disk", description="Check disk space on a specific Ubuntu server")
@app_commands.autocomplete(server=server_autocomplete)
async def disk(interaction: discord.Interaction, server: str):
    await interaction.response.defer()
    output = ssh_manager.execute_command(server, "df -h")
    await interaction.followup.send(f"**Disk Space on `{server}`:**\n```\n{output[:1900]}\n```")

@bot.tree.command(name="update", description="Check and install updates on a specific Ubuntu server")
@app_commands.autocomplete(server=server_autocomplete)
async def update(interaction: discord.Interaction, server: str):
    await interaction.response.defer()
    # Using -y for non-interactive upgrade. Note: sudo might need NOPASSWD config.
    cmd = "sudo apt-get update && sudo apt-get upgrade -y"
    output = ssh_manager.execute_command(server, cmd)
    await interaction.followup.send(f"**Update Result for `{server}`:**\n```\n{output[:1900]}\n```")

@bot.tree.command(name="process", description="Search for running processes on a specific Ubuntu server")
@app_commands.autocomplete(server=server_autocomplete)
async def process(interaction: discord.Interaction, server: str, search: str):
    await interaction.response.defer()
    # Case-insensitive grep, excluding the grep process itself
    cmd = f"ps aux | grep -i '{search}' | grep -v grep"
    output = ssh_manager.execute_command(server, cmd)
    if not output.strip():
        output = f"No processes found matching '{search}'."
    await interaction.followup.send(f"**Processes on `{server}` (Search: '{search}'):**\n```\n{output[:1900]}\n```")

@bot.tree.command(name="service", description="Control a service on a specific Ubuntu server")
@app_commands.autocomplete(server=server_autocomplete)
@app_commands.describe(action="The action to perform (status, start, stop, restart)")
@app_commands.choices(action=[
    app_commands.Choice(name="status", value="status"),
    app_commands.Choice(name="start", value="start"),
    app_commands.Choice(name="stop", value="stop"),
    app_commands.Choice(name="restart", value="restart"),
])
async def service(interaction: discord.Interaction, server: str, action: str, name: str):
    await interaction.response.defer()
    cmd = f"sudo systemctl {action} {name}"
    # For status, we want to see the output. For others, just a confirmation if no error.
    output = ssh_manager.execute_command(server, cmd)
    
    response_msg = f"**Service `{name}` {action} on `{server}`**"
    if output.strip():
        response_msg += f":\n```\n{output[:1800]}\n```"
    else:
        response_msg += " successfully (no output)."
        
    await interaction.followup.send(response_msg)

@bot.tree.command(name="logs", description="View recent log entries on a specific Ubuntu server")
@app_commands.autocomplete(server=server_autocomplete)
@app_commands.describe(path="Path to the log file (e.g., /var/log/syslog)", lines="Number of lines to display")
async def logs(interaction: discord.Interaction, server: str, path: str, lines: int = 20):
    await interaction.response.defer()
    # Cap lines to prevent massive messages
    lines = min(max(1, lines), 100)
    cmd = f"sudo tail -n {lines} {path}"
    output = ssh_manager.execute_command(server, cmd)
    
    await interaction.followup.send(f"**Last {lines} lines of `{path}` on `{server}`:**\n```\n{output[:1900]}\n```")

if __name__ == "__main__":
    if not DISCORD_TOKEN:
        print("Error: DISCORD_TOKEN not found in environment variables.")
    else:
        bot.run(DISCORD_TOKEN)
