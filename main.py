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

# --- Commands ---
@bot.tree.command(name="ping", description="Check bot latency")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message(f'Pong! {round(bot.latency * 1000)}ms')

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
