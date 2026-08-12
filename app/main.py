from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from .auth import COOKIE_NAME, Auth
from .config import Settings
from .db import Database
from .web import WEB_DIR, load_page


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    db = Database(settings.data_dir / "allfilethingy.sqlite3")
    auth = Auth(settings.app_password, settings.app_secret, settings.cookie_max_age)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        (settings.data_dir / "jobs").mkdir(exist_ok=True)
        db.initialize()
        yield

    application = FastAPI(title="AllFileThingy", docs_url=None, redoc_url=None, lifespan=lifespan)
    application.state.settings = settings
    application.state.db = db
    application.state.auth = auth
    application.mount("/static", StaticFiles(directory=WEB_DIR), name="static")

    @application.get("/healthz")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @application.get("/login", response_class=HTMLResponse)
    async def login_page(request: Request) -> Response:
        if auth.authenticated(request):
            return RedirectResponse("/", status_code=303)
        return HTMLResponse(load_page("login.html"))

    @application.post("/api/login")
    async def login(request: Request) -> Response:
        try:
            payload = await request.json()
        except Exception:
            return JSONResponse({"detail": "Invalid request"}, status_code=400)
        if not isinstance(payload, dict) or not isinstance(payload.get("password"), str):
            return JSONResponse({"detail": "Password is required"}, status_code=422)
        if not auth.password_matches(payload["password"]):
            return JSONResponse({"detail": "Incorrect password"}, status_code=401)
        response = JSONResponse({"ok": True})
        response.set_cookie(
            COOKIE_NAME,
            auth.issue(),
            max_age=settings.cookie_max_age,
            httponly=True,
            secure=request.url.scheme == "https",
            samesite="strict",
            path="/",
        )
        return response

    @application.post("/api/logout")
    async def logout(request: Request) -> Response:
        auth.require(request)
        response = JSONResponse({"ok": True})
        response.delete_cookie(COOKIE_NAME, path="/", samesite="strict")
        return response

    @application.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> Response:
        if not auth.authenticated(request):
            return RedirectResponse("/login", status_code=303)
        return HTMLResponse(load_page("index.html"))

    @application.get("/api/session")
    async def session(request: Request) -> dict[str, bool]:
        auth.require(request)
        return {"authenticated": True}

    return application


app = create_app()

