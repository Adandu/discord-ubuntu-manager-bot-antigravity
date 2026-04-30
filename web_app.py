from __future__ import annotations

import asyncio
import hmac
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from app_state import AppState
from auth_utils import hash_password
from auth_utils import verify_password
from models import (
    AppConfig,
    RestoreConfigResponse,
    SaveConfigRequest,
    TestServerRequest,
)

router = APIRouter()


def get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip.strip()
    return request.client.host if request.client else "unknown"


def is_authenticated(request: Request) -> bool:
    state: AppState = request.app.state.app_state
    return (
        bool(state.config.webui.password)
        and request.session.get("authenticated") is True
    )


def setup_required(request: Request) -> bool:
    state: AppState = request.app.state.app_state
    return not bool(state.config.webui.password)


def get_csrf_token(request: Request) -> str:
    token = request.session.get("csrf_token")
    if not token:
        token = secrets.token_hex(32)
        request.session["csrf_token"] = token
    return token


def validate_csrf(request: Request) -> None:
    session_token = request.session.get("csrf_token")
    header_token = request.headers.get("X-CSRF-Token", "")
    if not session_token or not hmac.compare_digest(session_token, header_token):
        raise HTTPException(status_code=403, detail="CSRF token missing or invalid")


def validate_csrf_form(request: Request, form_token: str) -> None:
    session_token = request.session.get("csrf_token")
    if (
        not session_token
        or not form_token
        or not hmac.compare_digest(session_token, form_token)
    ):
        raise HTTPException(status_code=403, detail="CSRF token missing or invalid")


async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self' https://fonts.googleapis.com https://fonts.gstatic.com; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "img-src 'self' data: *; "
        "font-src 'self' data: https://fonts.gstatic.com; "
        "connect-src 'self'; "
        "frame-ancestors 'none';"
    )
    return response


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    if setup_required(request):
        return RedirectResponse(url="/setup")
    if not is_authenticated(request):
        return RedirectResponse(url="/login")

    state: AppState = request.app.state.app_state
    templates: Jinja2Templates = request.app.state.templates

    display_config = state.masked_config_dict()
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "config": display_config,
            "servers": display_config["servers"],
            "csrf_token": get_csrf_token(request),
            "observability_refresh_ms": request.app.state.observability_refresh_ms,
        },
    )


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if setup_required(request):
        return RedirectResponse(url="/setup")

    templates: Jinja2Templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "login.html",
        {
            "csrf_token": get_csrf_token(request),
            "error": request.query_params.get("error"),
        },
    )


@router.post("/login")
async def login(
    request: Request, password: str = Form(...), csrf_token: str = Form(...)
):
    if setup_required(request):
        return RedirectResponse(url="/setup", status_code=303)
    validate_csrf_form(request, csrf_token)
    client_ip = get_client_ip(request)
    state: AppState = request.app.state.app_state

    if not state.login_limiter.is_allowed(client_ip):
        return RedirectResponse(url="/login?error=ratelimit", status_code=303)

    stored_pass = state.config.webui.password
    if not stored_pass:
        return RedirectResponse(url="/login?error=no_pass", status_code=303)

    if verify_password(password, stored_pass):
        request.session.clear()
        request.session["authenticated"] = True
        request.session["csrf_token"] = secrets.token_hex(32)
        state.login_limiter.reset(client_ip)
        return RedirectResponse(url="/", status_code=303)
    return RedirectResponse(url="/login?error=1", status_code=303)


@router.post("/logout")
async def logout(request: Request, csrf_token: str = Form(...)):
    validate_csrf_form(request, csrf_token)
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)


@router.get("/setup", response_class=HTMLResponse)
async def setup_page(request: Request):
    if not setup_required(request):
        return RedirectResponse(url="/" if is_authenticated(request) else "/login")

    templates: Jinja2Templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "setup.html",
        {
            "csrf_token": get_csrf_token(request),
            "error": request.query_params.get("error"),
        },
    )


@router.post("/setup")
async def setup_submit(
    request: Request,
    password: str = Form(...),
    confirm_password: str = Form(...),
    csrf_token: str = Form(...),
):
    if not setup_required(request):
        return RedirectResponse(
            url="/" if is_authenticated(request) else "/login", status_code=303
        )
    validate_csrf_form(request, csrf_token)
    if len(password) < 8:
        return RedirectResponse(url="/setup?error=short", status_code=303)
    if password != confirm_password:
        return RedirectResponse(url="/setup?error=mismatch", status_code=303)

    state: AppState = request.app.state.app_state
    config = state.config.model_copy(deep=True)
    config.webui.password = hash_password(password)
    state.save_config(config)
    request.session.clear()
    request.session["authenticated"] = True
    request.session["csrf_token"] = secrets.token_hex(32)
    return RedirectResponse(url="/", status_code=303)


@router.post("/api/test-server")
async def test_server(request: Request, server_data: TestServerRequest):
    if not is_authenticated(request):
        raise HTTPException(status_code=401)
    validate_csrf(request)

    client_ip = get_client_ip(request)
    state: AppState = request.app.state.app_state
    if not state.api_limiter.is_allowed(client_ip):
        raise HTTPException(
            status_code=429,
            detail="Too many requests. Please wait before testing again.",
        )

    server_payload = server_data.model_dump(by_alias=True)
    original = next(
        (
            server
            for server in state.config.servers
            if server.alias == server_data.alias
        ),
        None,
    )
    if original:
        server_payload["host"] = original.host
        server_payload["port"] = original.port
        if server_payload.get("password") == "********":
            server_payload["password"] = original.password
        if server_payload.get("key") == "********":
            server_payload["key"] = original.key

    success, message, fingerprint = await asyncio.to_thread(
        state.ssh_manager.test_server_connection,
        server_payload,
        server_data.trust_host,
    )
    return {"success": success, "message": message, "fingerprint": fingerprint}


@router.post("/save")
async def save_config_ui(request: Request, payload: SaveConfigRequest):
    if not is_authenticated(request):
        raise HTTPException(status_code=401)
    validate_csrf(request)

    client_ip = get_client_ip(request)
    state: AppState = request.app.state.app_state
    if not state.api_limiter.is_allowed(client_ip):
        raise HTTPException(
            status_code=429,
            detail="Too many requests. Please wait before saving again.",
        )

    body = payload.model_dump(by_alias=True)
    if "SECRET_KEY" in body:
        raise HTTPException(
            status_code=400,
            detail="SECRET_KEY rotation is not supported from the WebUI.",
        )

    if body["discord"].get("token") == "********":
        body["discord"]["token"] = state.config.discord.token
    if body["webui"].get("password") == "********":
        body["webui"]["password"] = state.config.webui.password
    if body["features"].get("power_control_password") == "********":
        body["features"]["power_control_password"] = (
            state.config.features.power_control_password
        )

    original_by_alias = {server.alias: server for server in state.config.servers}
    original_by_index = list(state.config.servers)
    for index, server in enumerate(body["servers"]):
        original_alias = server.get("_original_alias") or server.get("alias")
        original = original_by_alias.get(original_alias)
        if not original and index < len(original_by_index):
            original = original_by_index[index]
        if original and server.get("password") == "********":
            server["password"] = original.password
        if original and server.get("key") == "********":
            server["key"] = original.key
        server.pop("_original_alias", None)

    new_config = AppConfig.model_validate(body)
    state.save_config(new_config)
    return {"status": "success"}


@router.get("/api/logs")
async def get_app_logs(request: Request):
    if not is_authenticated(request):
        raise HTTPException(status_code=401)
    state: AppState = request.app.state.app_state
    return {"logs": "\n".join(list(state.log_buffer))}


@router.get("/api/audit")
async def get_audit_logs(request: Request):
    if not is_authenticated(request):
        raise HTTPException(status_code=401)
    state: AppState = request.app.state.app_state
    return {"entries": await state.read_audit_entries()}


@router.get("/api/backup/export")
async def export_backup(request: Request):
    if not is_authenticated(request):
        raise HTTPException(status_code=401)
    validate_csrf(request)

    client_ip = get_client_ip(request)
    state: AppState = request.app.state.app_state
    if not state.api_limiter.is_allowed(client_ip):
        raise HTTPException(
            status_code=429, detail="Too many requests. Please try again later."
        )

    raw = state.config_manager.export_raw_config()
    filename = (
        f"discobunty-backup-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}.json"
    )
    return Response(
        content=raw,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/api/backup/restore", response_model=RestoreConfigResponse)
async def restore_backup(request: Request, backup_file: UploadFile = File(...)):
    if not is_authenticated(request):
        raise HTTPException(status_code=401)
    validate_csrf(request)
    state: AppState = request.app.state.app_state
    raw = await backup_file.read()
    restored = state.config_manager.import_raw_config(raw)
    state.refresh_runtime()
    return RestoreConfigResponse(
        status="success", restored_servers=len(restored.servers)
    )


@router.get("/api/servers/check")
async def bulk_server_check(request: Request):
    if not is_authenticated(request):
        raise HTTPException(status_code=401)

    state: AppState = request.app.state.app_state

    async def check_one(server):
        return await asyncio.to_thread(
            state.ssh_manager.check_server_capabilities,
            server.alias,
            server.backup_path,
            state.config.features.enable_docker,
        )

    results = await asyncio.gather(
        *(check_one(server) for server in state.config.servers)
    )
    return {"results": results}


@router.get("/api/servers/overview")
async def server_overview(request: Request):
    if not is_authenticated(request):
        raise HTTPException(status_code=401)

    state: AppState = request.app.state.app_state

    async def overview_one(server):
        return await asyncio.to_thread(
            state.ssh_manager.get_observability,
            server.alias,
            server.backup_path,
            state.config.features.enable_docker,
        )

    results = await asyncio.gather(
        *(overview_one(server) for server in state.config.servers)
    )
    return {"results": results}


@router.get("/health")
async def health(request: Request):
    state: AppState = request.app.state.app_state
    return {
        "status": "ok",
        "config_loaded": True,
        "webui_enabled": state.config.webui.enabled,
        "discord_enabled": bool(state.config.discord.token),
        "servers_configured": len(state.config.servers),
    }


def create_web_app(state: AppState) -> FastAPI:
    app = FastAPI(title="DiscoBunty Dashboard")
    secret_key = os.getenv("SECRET_KEY")
    observability_refresh_ms = max(
        5000, int(os.getenv("OBSERVABILITY_REFRESH_MS", "30000"))
    )
    if not secret_key:
        raise ValueError(
            "SECRET_KEY environment variable is mandatory for WebUI session security."
        )

    app.add_middleware(
        SessionMiddleware,
        secret_key=secret_key,
        session_cookie="session",
        same_site="strict",
        https_only=os.getenv("WEBUI_SECURE_COOKIES", "false").lower() == "true",
        max_age=3600,
    )

    app.middleware("http")(add_security_headers)

    base_dir = Path(__file__).resolve().parent
    templates = Jinja2Templates(directory=str(base_dir / "templates"))
    app.mount("/static", StaticFiles(directory=str(base_dir / "static")), name="static")

    app.state.app_state = state
    app.state.templates = templates
    app.state.observability_refresh_ms = observability_refresh_ms

    app.include_router(router)

    return app
