import os
import logging
import discord
import shlex
import asyncio
import json
import hmac
import copy
import secrets
import time
from collections import deque, defaultdict
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

# Configure logging with an in-memory buffer for the WebUI
log_buffer = deque(maxlen=500)
log_buffer.append(f"System Initialized. Log capture started.")

# --- Login Rate Limiter ---
class LoginRateLimiter:
    """Simple in-memory rate limiter: max_attempts per window_seconds per IP."""
    def __init__(self, max_attempts: int = 5, window_seconds: int = 60):
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self._attempts: dict = defaultdict(list)

    def is_allowed(self, key: str) -> bool:
        now = time.time()
        self._attempts[key] = [t for t in self._attempts[key] if now - t < self.window_seconds]
        if len(self._attempts[key]) >= self.max_attempts:
            return False
        self._attempts[key].append(now)
        return True

    def reset(self, key: str):
        self._attempts.pop(key, None)

login_limiter = LoginRateLimiter(max_attempts=5, window_seconds=60)
api_limiter = LoginRateLimiter(max_attempts=30, window_seconds=60)

# --- Audit Logger ---
AUDIT_LOG_FILE = "audit.log"
def audit_log(user_id: int, username: str, command: str, details: str):
    """Log privileged actions to a persistent file."""
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    log_entry = f"[{timestamp}] USER:{user_id} ({username}) | CMD:{command} | {details}\n"
    with open(AUDIT_LOG_FILE, "a", encoding="utf-8") as f:
        f.write(log_entry)
    logger.info(f"AUDIT: {log_entry.strip()}")

def _get_client_ip(request: Request) -> str:
    """Return the client IP address, taking only the first (leftmost) value from
    X-Forwarded-For to avoid spoofing via attacker-controlled intermediate hops."""
    xff = request.headers.get("X-Forwarded-For", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"

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
    aliases = sorted(ssh_manager.get_server_aliases(), key=lambda x: x.lower())
    return [app_commands.Choice(name=alias, value=alias) for alias in aliases if current.lower() in alias.lower()]

async def container_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    if not check_permissions(interaction.user): return []
    server = interaction.namespace.server
    if not server: return []
    try:
        containers = await asyncio.to_thread(ssh_manager.get_containers, server)
        # Sort containers alphabetically by name
        sorted_containers = sorted(containers, key=lambda x: x.lower())
        return [app_commands.Choice(name=name, value=name) for name in sorted_containers if current.lower() in name.lower()][:25]
    except Exception: return []

async def log_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    if not check_permissions(interaction.user): return []
    server = interaction.namespace.server
    if not server: return []
    try:
        logs = await asyncio.to_thread(ssh_manager.get_log_files, server)
        sorted_logs = sorted(logs, key=lambda x: x.lower())
        return [app_commands.Choice(name=path, value=path) for path in sorted_logs if current.lower() in path.lower()][:25]
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
    audit_log(interaction.user.id, interaction.user.name, "server_power", f"Action: {action} | Server: {server}")
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
    audit_log(interaction.user.id, interaction.user.name, "service", f"Action: {action} | Service: {name} | Server: {server}")
    cmd = f"sudo systemctl {shlex.quote(action)} {shlex.quote(name)}"
    output = await asyncio.to_thread(ssh_manager.execute_command, server, cmd)
    await interaction.followup.send(f"**Service `{name}` {action} on `{server}`**:\n```\n{output[:MAX_MSG_LEN]}\n```")

@bot.tree.command(name="logs", description="View recent system log entries on a server")
@is_admin()
@app_commands.autocomplete(server=server_autocomplete, path=log_autocomplete)
async def system_logs(interaction: discord.Interaction, server: str, path: str, lines: int = 20, search: str = None):
    await interaction.response.defer()
    
    # 1. Resolve remote symlinks to prevent traversal via evil symlinks
    real_path = await asyncio.to_thread(ssh_manager.resolve_remote_path, server, path)
    
    # 2. Normalize the path locally to resolve any traversal sequences
    normalized_path = os.path.normpath(real_path).replace("\\", "/") # Ensure POSIX style for remote
    
    if not any(normalized_path.startswith(root) for root in ALLOWED_LOG_ROOTS):
        await interaction.followup.send(f"❌ Access denied to path: `{path}` (resolved to `{normalized_path}`)", ephemeral=True)
        return
        
    lines = min(max(1, lines), 100)
    cmd = f"sudo tail -n {lines} {shlex.quote(normalized_path)}"
    if search:
        cmd += f" | grep -i -e {shlex.quote(search)}"
    output = await asyncio.to_thread(ssh_manager.execute_command, server, cmd)
    await interaction.followup.send(f"**Last {lines} lines of `{normalized_path}` on `{server}`:**\n```\n{output[:MAX_MSG_LEN]}\n```")

# --- Docker Group ---
if config["features"].get("enable_docker") == "true":
    docker_group = app_commands.Group(name="docker", description="Manage Docker containers")
    
    @docker_group.command(name="ps", description="List containers on a server")
    @app_commands.check(lambda i: check_permissions(i.user))
    @app_commands.autocomplete(server=server_autocomplete)
    async def docker_ps(interaction: discord.Interaction, server: str, all: bool = True):
        await interaction.response.defer()
        cmd = "sudo docker ps -a" if all else "sudo docker ps"
        output = await asyncio.to_thread(ssh_manager.execute_command, server, cmd)
        await interaction.followup.send(f"**Containers on `{server}`:**\n```\n{output[:MAX_MSG_LEN]}\n```")

    @docker_group.command(name="control", description="Start, stop, or restart a container")
    @app_commands.check(lambda i: check_permissions(i.user))
    @app_commands.autocomplete(server=server_autocomplete, container=container_autocomplete)
    @app_commands.choices(action=[
        app_commands.Choice(name="start", value="start"),
        app_commands.Choice(name="stop", value="stop"),
        app_commands.Choice(name="restart", value="restart"),
    ])
    async def docker_control(interaction: discord.Interaction, server: str, action: str, container: str):
        await interaction.response.defer()
        audit_log(interaction.user.id, interaction.user.name, "docker_control", f"Action: {action} | Container: {container} | Server: {server}")
        output = await asyncio.to_thread(ssh_manager.container_action, server, container, action)
        await interaction.followup.send(f"**Action `{action}` on container `{container}` (`{server}`):**\n```\n{output[:MAX_MSG_LEN]}\n```")

    @docker_group.command(name="logs", description="View container logs")
    @app_commands.check(lambda i: check_permissions(i.user))
    @app_commands.autocomplete(server=server_autocomplete, container=container_autocomplete)
    async def docker_logs(interaction: discord.Interaction, server: str, container: str, lines: int = 50, search: str = None):
        await interaction.response.defer()
        lines = min(max(1, lines), 100)
        output = await asyncio.to_thread(ssh_manager.get_container_logs, server, container, lines, search)
        header = f"**Logs for `{container}` on `{server}` (Last {lines} lines):**"
        await interaction.followup.send(f"{header}\n```\n{output[:MAX_MSG_LEN]}\n```")

    @docker_group.command(name="details", description="View container image, IP, and ports")
    @app_commands.check(lambda i: check_permissions(i.user))
    @app_commands.autocomplete(server=server_autocomplete, container=container_autocomplete)
    async def docker_details(interaction: discord.Interaction, server: str, container: str):
        await interaction.response.defer()
        output = await asyncio.to_thread(ssh_manager.get_container_details, server, container)
        await interaction.followup.send(f"**Details for `{container}` on `{server}`:**\n```\n{output[:MAX_MSG_LEN]}\n```")
    
    bot.tree.add_command(docker_group)

# --- FastAPI WebUI Setup ---
app = FastAPI(title="DiscoBunty Dashboard")

# Ensure SECRET_KEY is set for session security
MASTER_KEY = os.getenv("SECRET_KEY")
if not MASTER_KEY:
    raise ValueError("SECRET_KEY environment variable is mandatory for WebUI session security.")

app.add_middleware(
    SessionMiddleware,
    secret_key=MASTER_KEY,
    session_cookie="session",
    same_site="strict",   # CSRF protection: browser won't send cookie on cross-origin requests
    https_only=False,     # Set to True if serving over HTTPS
    max_age=3600,         # Sessions expire after 1 hour of inactivity
)

@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    # CSP: allow trusted CDNs for styling and functionality
    response.headers["Content-Security-Policy"] = (
        "default-src 'self' https://fonts.googleapis.com https://fonts.gstatic.com; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "img-src 'self' data: *; "
        "font-src 'self' data: https://fonts.gstatic.com; "
        "connect-src 'self' https://fonts.googleapis.com; "
        "frame-ancestors 'none';"
    )
    return response

# Use absolute path for templates to ensure they are found in all environments (Docker vs Local)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

# Mount the static directory only (NOT BASE_DIR to avoid exposing config/env/keys)
# This directory is now included in the image via the repository.
STATIC_DIR = os.path.join(BASE_DIR, "static")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

def is_authenticated(request: Request):
    web_pass = config["webui"].get("password")
    if not web_pass: return False
    return request.session.get("authenticated") == True

def get_csrf_token(request: Request) -> str:
    """Get existing CSRF token from session, or generate and store a new one."""
    token = request.session.get("csrf_token")
    if not token:
        token = secrets.token_hex(32)
        request.session["csrf_token"] = token
    return token

def validate_csrf(request: Request) -> None:
    """Validate X-CSRF-Token header against the session token. Raises 403 on failure."""
    session_token = request.session.get("csrf_token")
    header_token = request.headers.get("X-CSRF-Token", "")
    if not session_token or not hmac.compare_digest(session_token, header_token):
        raise HTTPException(status_code=403, detail="CSRF token missing or invalid")

def validate_csrf_form(request: Request, form_token: str) -> None:
    """Validate a CSRF token submitted as a form field against the session token."""
    session_token = request.session.get("csrf_token")
    if not session_token or not form_token or not hmac.compare_digest(session_token, form_token):
        raise HTTPException(status_code=403, detail="CSRF token missing or invalid")

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    if not is_authenticated(request): return RedirectResponse(url="/login")
    
    # Mask sensitive values before sending to the UI
    display_config = copy.deepcopy(config)
    display_config["discord"]["token"] = "********" if config["discord"].get("token") else ""
    display_config["webui"]["password"] = "********" if config["webui"].get("password") else ""
    display_config["features"]["power_control_password"] = "********" if config["features"].get("power_control_password") else ""
    
    # Mask server secrets
    for s in display_config["servers"]:
        if s.get("password"): s["password"] = "********"
        if s.get("key") and not (s["key"].startswith('/') or os.path.isfile(s["key"])):
            s["key"] = "********"

    return templates.TemplateResponse("index.html", {
        "request": request,
        "config": display_config,
        "servers": display_config["servers"],
        "csrf_token": get_csrf_token(request),
    })

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    error = request.query_params.get("error")
    return templates.TemplateResponse("login.html", {
        "request": request,
        "csrf_token": get_csrf_token(request),
        "error": error,
    })

@app.post("/login")
async def login(request: Request, password: str = Form(...), csrf_token: str = Form(...)):
    validate_csrf_form(request, csrf_token)

    client_ip = _get_client_ip(request)
    if not login_limiter.is_allowed(client_ip):
        return RedirectResponse(url="/login?error=ratelimit", status_code=303)

    stored_pass = config["webui"].get("password", "")
    if not stored_pass:
        return RedirectResponse(url="/login?error=no_pass", status_code=303)

    # Use hmac.compare_digest to prevent timing attacks
    if hmac.compare_digest(password, stored_pass):
        request.session.clear() # Rotate session on login
        request.session["authenticated"] = True
        login_limiter.reset(client_ip)
        return RedirectResponse(url="/", status_code=303)
    return RedirectResponse(url="/login?error=1", status_code=303)

@app.post("/logout")
async def logout(request: Request, csrf_token: str = Form(...)):
    validate_csrf_form(request, csrf_token)
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)

@app.post("/api/test-server")
async def test_server(request: Request, server_data: dict):
    if not is_authenticated(request): raise HTTPException(status_code=401)
    validate_csrf(request)
    
    client_ip = _get_client_ip(request)
    if not api_limiter.is_allowed(client_ip):
        raise HTTPException(status_code=429, detail="Too many requests. Please wait before testing again.")

    trust_host = server_data.get("trust_host", False)
    alias = server_data.get("alias")
    host = server_data.get("host")
    
    # Port validation to prevent 500 error
    try:
        port = int(server_data.get("port", 22))
        if not (1 <= port <= 65535):
            return JSONResponse(status_code=400, content={"success": False, "message": f"Invalid port {port}. Must be 1-65535."})
    except (ValueError, TypeError):
        return JSONResponse(status_code=400, content={"success": False, "message": "Invalid port number."})

    # Configuration Guard: prevent SSRF by ensuring existing aliases use their configured host/port
    configured_servers = config_manager.get_server_config()
    orig = next((s for s in configured_servers if s["alias"] == alias), None)
    
    if orig:
        # SSRF Protection: For existing aliases, always use the authoritative host/port
        server_data["host"] = orig.get("host")
        server_data["port"] = orig.get("port", 22)
        
        # Restore masked credentials if necessary
        if server_data.get("password") == "********": server_data["password"] = orig.get("password")
        if server_data.get("key") == "********": server_data["key"] = orig.get("key")
    else:
        # This is a NEW server (or an unsaved edit). 
        # We allow the test so the user can verify credentials before saving.
        pass

    success, message, fingerprint = await asyncio.to_thread(ssh_manager.test_server_connection, server_data, trust_host=trust_host)
    return {"success": success, "message": message, "fingerprint": fingerprint}

@app.post("/save")
async def save_config_ui(request: Request, data: dict):
    if not is_authenticated(request): raise HTTPException(status_code=401)
    validate_csrf(request)

    client_ip = _get_client_ip(request)
    if not api_limiter.is_allowed(client_ip):
        raise HTTPException(status_code=429, detail="Too many requests. Please wait before saving again.")

    # Validate server config inputs
    for s in data.get("servers", []):
        try:
            port = int(s.get("port", 22))
            if not (1 <= port <= 65535):
                raise HTTPException(status_code=422, detail=f"Invalid port {port} for server '{s.get('alias')}'")
        except (ValueError, TypeError):
            raise HTTPException(status_code=422, detail=f"Port must be a number for server '{s.get('alias')}'")
        if s.get("auth_method") not in ("key", "password"):
            raise HTTPException(status_code=422, detail=f"auth_method must be 'key' or 'password'")
    
    # Restore masked values and sync config sections
    if data.get("discord", {}).get("token") == "********":
        data["discord"]["token"] = config["discord"].get("token", "")
    if data.get("webui", {}).get("password") == "********":
        data["webui"]["password"] = config["webui"].get("password", "")
    if data.get("features", {}).get("power_control_password") == "********":
        data["features"]["power_control_password"] = config["features"].get("power_control_password", "")

    if "servers" in data:
        for i, s in enumerate(data["servers"]):
            if i < len(config["servers"]):
                orig = config["servers"][i]
                if s.get("password") == "********": s["password"] = orig.get("password")
                if s.get("key") == "********": s["key"] = orig.get("key")

    config["discord"] = data.get("discord", config["discord"])
    config["features"] = data.get("features", config["features"])
    config["webui"] = data.get("webui", config["webui"])
    config["servers"] = data.get("servers", config["servers"])
    
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

@app.get("/health")
async def health():
    return {"status": "ok"}

# --- Main Logic ---
async def main():
    tasks = []
    if config["discord"].get("token"):
        tasks.append(bot.start(config["discord"]["token"]))
    if config["webui"].get("enabled") == "true":
        uv_config = uvicorn.Config(app, host="0.0.0.0", port=8000, log_level="info", proxy_headers=True, forwarded_allow_ips="*")
        tasks.append(uvicorn.Server(uv_config).serve())
    if tasks: await asyncio.gather(*tasks, return_exceptions=True)

if __name__ == "__main__":
    try: asyncio.run(main())
    except KeyboardInterrupt: pass
