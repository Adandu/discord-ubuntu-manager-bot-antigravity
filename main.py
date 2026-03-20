import os
import logging
import discord
import shlex
import asyncio
import json
from discord import app_commands
from dotenv import load_dotenv
from ssh_manager import SSHManager
from config_manager import ConfigManager

# FastAPI Imports
from fastapi import FastAPI, Request, Form, HTTPException, Depends
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
import uvicorn

from collections import deque

# Configure logging with an in-memory buffer for the WebUI
log_buffer = deque(maxlen=500)

class WebUIHandler(logging.Handler):
    def emit(self, record):
        log_entry = self.format(record)
        log_buffer.append(log_entry)

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        WebUIHandler()
    ]
)
logger = logging.getLogger('discobunty')

# Load environment variables (mostly for SECRET_KEY)
load_dotenv()

# Initialize ConfigManager
config_manager = ConfigManager()
config = config_manager.config
ssh_manager = SSHManager(config_manager.get_server_config())

# Constants
MAX_MSG_LEN = 1900
ALLOWED_LOG_ROOTS = ["/var/log/", "/home/"]

# --- Discord Bot Setup ---
class DiscoBunty(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        guild_id = config["discord"].get("guild_id")
        if guild_id:
            try:
                guild = discord.Object(id=int(guild_id))
                self.tree.copy_global_to(guild=guild)
                await self.tree.sync(guild=guild)
            except ValueError:
                logger.error(f"Invalid Guild ID: {guild_id}")
        else:
            await self.tree.sync()

bot = DiscoBunty()

@bot.event
async def on_ready():
    logger.info(f'Logged in as {bot.user} (ID: {bot.user.id})')
    logger.info(f'Docker integration: {"Enabled" if config["features"].get("enable_docker") == "true" else "Disabled"}')
    logger.info('------')

# --- RBAC Helpers ---
def check_permissions(user) -> bool:
    allowed_roles_str = config["discord"].get("allowed_roles", "")
    allowed_roles = [r.strip() for r in allowed_roles_str.split(',') if r.strip()]
    if not allowed_roles: return False
    if not hasattr(user, 'roles'): return False
    user_roles = [role.name for role in user.roles]
    return any(role in allowed_roles for role in user_roles)

def is_admin():
    def predicate(interaction: discord.Interaction) -> bool:
        return check_permissions(interaction.user)
    return app_commands.check(predicate)

# --- Autocomplete Helpers ---
async def server_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    if not check_permissions(interaction.user): return []
    aliases = ssh_manager.get_server_aliases()
    return [app_commands.Choice(name=alias, value=alias) for alias in aliases if current.lower() in alias.lower()]

async def container_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    if not check_permissions(interaction.user): return []
    server = interaction.namespace.server
    if not server: return []
    try:
        containers = await asyncio.to_thread(ssh_manager.get_containers, server)
        return [app_commands.Choice(name=name, value=name) for name in containers if current.lower() in name.lower()][:25]
    except Exception: return []

async def log_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    if not check_permissions(interaction.user): return []
    server = interaction.namespace.server
    if not server: return []
    try:
        logs = await asyncio.to_thread(ssh_manager.get_log_files, server)
        return [app_commands.Choice(name=path, value=path) for path in logs if current.lower() in path.lower()][:25]
    except Exception: return []

# --- Power Control Modal ---
class PowerControlModal(discord.ui.Modal, title='Verify Power Action'):
    password = discord.ui.TextInput(label='Security Password', placeholder='Enter safety password', style=discord.TextStyle.short, required=True)
    def __init__(self, server: str, action: str):
        super().__init__()
        self.server = server
        self.action = action
    async def on_submit(self, interaction: discord.Interaction):
        if self.password.value != config["features"].get("power_control_password"):
            await interaction.response.send_message("❌ Incorrect password. Action aborted.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        output = await asyncio.to_thread(ssh_manager.server_power_action, self.server, self.action)
        await interaction.followup.send(output, ephemeral=True)

class PowerConfirmationView(discord.ui.View):
    def __init__(self, server: str, action: str):
        super().__init__(timeout=60)
        self.server = server
        self.action = action
    @discord.ui.button(label='Confirm Action', style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(PowerControlModal(self.server, self.action))
        self.stop()

# --- Discord Bot Commands ---
@bot.tree.command(name="ping", description="Check bot latency")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message(f'Pong! {round(bot.latency * 1000)}ms')

@bot.tree.command(name="server", description="Server management commands")
@is_admin()
@app_commands.autocomplete(server=server_autocomplete)
@app_commands.choices(action=[app_commands.Choice(name="reboot", value="reboot"), app_commands.Choice(name="shutdown", value="shutdown")])
async def server_power(interaction: discord.Interaction, server: str, action: str):
    if config["features"].get("power_control_enabled") != "true":
        await interaction.response.send_message("❌ Power control is currently disabled.", ephemeral=True)
        return
    view = PowerConfirmationView(server, action)
    await interaction.response.send_message(content=f"⚠️ **Warning:** You are about to **{action}** server `{server}`. Are you sure?", view=view, ephemeral=True)

@bot.tree.command(name="stats", description="Show system statistics for a server")
@is_admin()
@app_commands.autocomplete(server=server_autocomplete)
async def stats(interaction: discord.Interaction, server: str):
    await interaction.response.defer()
    output = await asyncio.to_thread(ssh_manager.get_system_stats, server)
    await interaction.followup.send(f"**System Stats for `{server}`:**\n```\n{output[:MAX_MSG_LEN]}\n```")

@bot.tree.command(name="disk", description="Check disk space on a server")
@is_admin()
@app_commands.autocomplete(server=server_autocomplete)
async def disk(interaction: discord.Interaction, server: str):
    await interaction.response.defer()
    output = await asyncio.to_thread(ssh_manager.execute_command, server, "df -h")
    await interaction.followup.send(f"**Disk Space on `{server}`:**\n```\n{output[:MAX_MSG_LEN]}\n```")

@bot.tree.command(name="update", description="Check and install updates on a server")
@is_admin()
@app_commands.autocomplete(server=server_autocomplete)
async def update(interaction: discord.Interaction, server: str):
    await interaction.response.defer()
    output = await asyncio.to_thread(ssh_manager.execute_command, server, "sudo apt-get update && sudo apt-get upgrade -y")
    await interaction.followup.send(f"**Update Result for `{server}`:**\n```\n{output[:MAX_MSG_LEN]}\n```")

@bot.tree.command(name="process", description="Search for running processes on a server")
@is_admin()
@app_commands.autocomplete(server=server_autocomplete)
async def process(interaction: discord.Interaction, server: str, search: str):
    await interaction.response.defer()
    safe_search = shlex.quote(search)
    cmd = f"ps aux | grep -i -e {safe_search} | grep -v grep"
    output = await asyncio.to_thread(ssh_manager.execute_command, server, cmd)
    await interaction.followup.send(f"**Processes on `{server}` (Search: '{search}'):**\n```\n{output[:MAX_MSG_LEN]}\n```")

@bot.tree.command(name="service", description="Control a systemd service on a server")
@is_admin()
@app_commands.autocomplete(server=server_autocomplete)
@app_commands.choices(action=[
    app_commands.Choice(name="status", value="status"),
    app_commands.Choice(name="start", value="start"),
    app_commands.Choice(name="stop", value="stop"),
    app_commands.Choice(name="restart", value="restart"),
])
async def service(interaction: discord.Interaction, server: str, action: str, name: str):
    await interaction.response.defer()
    cmd = f"sudo systemctl {shlex.quote(action)} {shlex.quote(name)}"
    output = await asyncio.to_thread(ssh_manager.execute_command, server, cmd)
    await interaction.followup.send(f"**Service `{name}` {action} on `{server}`**:\n```\n{output[:MAX_MSG_LEN]}\n```")

@bot.tree.command(name="logs", description="View recent system log entries on a server")
@is_admin()
@app_commands.autocomplete(server=server_autocomplete, path=log_autocomplete)
async def system_logs(interaction: discord.Interaction, server: str, path: str, lines: int = 20, search: str = None):
    await interaction.response.defer()
    if not any(path.startswith(root) for root in ALLOWED_LOG_ROOTS) or ".." in path:
        await interaction.followup.send(f"❌ Access denied to path: `{path}`", ephemeral=True)
        return
    lines = min(max(1, lines), 100)
    cmd = f"sudo tail -n {lines} {shlex.quote(path)}"
    if search:
        cmd += f" | grep -i -e {shlex.quote(search)}"
    output = await asyncio.to_thread(ssh_manager.execute_command, server, cmd)
    await interaction.followup.send(f"**Last {lines} lines of `{path}` on `{server}`:**\n```\n{output[:MAX_MSG_LEN]}\n```")

# --- Docker Group ---
if config["features"].get("enable_docker") == "true":
    docker_group = app_commands.Group(name="docker", description="Manage Docker containers")
    
    @docker_group.command(name="ps", description="List containers on a server")
    @app_commands.autocomplete(server=server_autocomplete)
    async def docker_ps(interaction: discord.Interaction, server: str, all: bool = True):
        await interaction.response.defer()
        cmd = "sudo docker ps -a" if all else "sudo docker ps"
        output = await asyncio.to_thread(ssh_manager.execute_command, server, cmd)
        await interaction.followup.send(f"**Containers on `{server}`:**\n```\n{output[:MAX_MSG_LEN]}\n```")

    @docker_group.command(name="control", description="Start, stop, or restart a container")
    @app_commands.autocomplete(server=server_autocomplete, container=container_autocomplete)
    @app_commands.choices(action=[
        app_commands.Choice(name="start", value="start"),
        app_commands.Choice(name="stop", value="stop"),
        app_commands.Choice(name="restart", value="restart"),
    ])
    async def docker_control(interaction: discord.Interaction, server: str, action: str, container: str):
        await interaction.response.defer()
        output = await asyncio.to_thread(ssh_manager.container_action, server, container, action)
        await interaction.followup.send(f"**Action `{action}` on container `{container}` (`{server}`):**\n```\n{output[:MAX_MSG_LEN]}\n```")

    @docker_group.command(name="logs", description="View container logs")
    @app_commands.autocomplete(server=server_autocomplete, container=container_autocomplete)
    async def docker_logs(interaction: discord.Interaction, server: str, container: str, lines: int = 50, search: str = None):
        await interaction.response.defer()
        lines = min(max(1, lines), 100)
        output = await asyncio.to_thread(ssh_manager.get_container_logs, server, container, lines, search)
        header = f"**Logs for `{container}` on `{server}` (Last {lines} lines):**"
        await interaction.followup.send(f"{header}\n```\n{output[:MAX_MSG_LEN]}\n```")

    @docker_group.command(name="details", description="View container image, IP, and ports")
    @app_commands.autocomplete(server=server_autocomplete, container=container_autocomplete)
    async def docker_details(interaction: discord.Interaction, server: str, container: str):
        await interaction.response.defer()
        output = await asyncio.to_thread(ssh_manager.get_container_details, server, container)
        await interaction.followup.send(f"**Details for `{container}` on `{server}`:**\n```\n{output[:MAX_MSG_LEN]}\n```")
    
    bot.tree.add_command(docker_group)

# --- FastAPI WebUI Setup ---
app = FastAPI(title="DiscoBunty Dashboard")
app.add_middleware(SessionMiddleware, secret_key=os.getenv("SECRET_KEY", "default-insecure-key"))

# Use absolute path for templates to ensure they are found in all environments (Docker vs Local)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

def is_authenticated(request: Request):
    web_pass = config["webui"].get("password")
    if not web_pass: return True
    return request.session.get("authenticated") == True

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    if not is_authenticated(request): return RedirectResponse(url="/login")
    return templates.TemplateResponse("index.html", {
        "request": request, "config": config, "servers": config["servers"]
    })

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return HTMLResponse("<html><body style='background:#300a24;color:white;display:flex;justify-content:center;align-items:center;height:100vh;'><form method='post' style='background:#2b2d31;padding:40px;border-radius:8px;'><h2>DiscoBunty Login</h2><input type='password' name='password' style='width:100%;margin-bottom:20px;'><button type='submit' style='width:100%;background:#5865F2;color:white;border:none;padding:10px;'>Login</button></form></body></html>")

@app.post("/login")
async def login(request: Request, password: str = Form(...)):
    if password == config["webui"].get("password"):
        request.session["authenticated"] = True
        return RedirectResponse(url="/", status_code=303)
    return RedirectResponse(url="/login?error=1", status_code=303)

@app.post("/api/test-server")
async def test_server(request: Request, server_data: dict):
    if not is_authenticated(request): raise HTTPException(status_code=401)
    success, message = await asyncio.to_thread(ssh_manager.test_server_connection, server_data)
    return {"success": success, "message": message}

@app.post("/save")
async def save_config_ui(request: Request, data: dict):
    if not is_authenticated(request): raise HTTPException(status_code=401)
    
    # Update current config dictionary from flat WebUI data or nested JSON
    if "servers" in data:
        # Full sync mode
        config["discord"] = data.get("discord", config["discord"])
        config["features"] = data.get("features", config["features"])
        config["webui"] = data.get("webui", config["webui"])
        config["servers"] = data.get("servers", config["servers"])
    else:
        # Flat data mode (from original form)
        config["discord"]["token"] = data.get("DISCORD_TOKEN", config["discord"]["token"])
        config["discord"]["guild_id"] = data.get("GUILD_ID", config["discord"]["guild_id"])
        config["features"]["enable_docker"] = data.get("ENABLE_DOCKER", config["features"]["enable_docker"])
        config["features"]["power_control_enabled"] = data.get("POWER_CONTROL_ENABLED", config["features"]["power_control_enabled"])
        config["features"]["power_control_password"] = data.get("POWER_CONTROL_PASSWORD", config["features"]["power_control_password"])
        config["webui"]["password"] = data.get("WEB_PASSWORD", config["webui"]["password"])
        
    # Update SECRET_KEY in .env if changed
    if "SECRET_KEY" in data:
        from dotenv import set_key
        set_key(".env", "SECRET_KEY", data["SECRET_KEY"])
        
    config_manager.save_config(config)
    # Refresh SSHManager servers list
    ssh_manager.servers = config["servers"]
    return {"status": "success"}

@app.get("/api/logs")
async def get_app_logs(request: Request):
    if not is_authenticated(request): raise HTTPException(status_code=401)
    return {"logs": "\n".join(list(log_buffer))}

# --- Main Logic ---
async def main():
    tasks = []
    if config["discord"].get("token"):
        tasks.append(bot.start(config["discord"]["token"]))
    if config["webui"].get("enabled") == "true":
        uv_config = uvicorn.Config(app, host="0.0.0.0", port=8000, log_level="info")
        tasks.append(uvicorn.Server(uv_config).serve())
    if tasks: await asyncio.gather(*tasks)

if __name__ == "__main__":
    try: asyncio.run(main())
    except KeyboardInterrupt: pass
