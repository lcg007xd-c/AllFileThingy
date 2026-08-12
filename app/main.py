from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from .auth import COOKIE_NAME, Auth
from .config import Settings
from .db import Database
from .web import WEB_DIR, load_page
from .uploads import UploadService


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    db = Database(settings.data_dir / "allfilethingy.sqlite3")
    auth = Auth(settings.app_password, settings.app_secret, settings.cookie_max_age)
    uploads = UploadService(db, settings)

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
    application.state.uploads = uploads
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

    @application.post("/api/jobs", status_code=201)
    async def create_job(request: Request) -> dict:
        auth.require(request)
        try:
            payload = await request.json()
        except Exception:
            return JSONResponse({"detail": "Invalid request"}, status_code=400)
        return uploads.create_job(payload.get("files") if isinstance(payload, dict) else None)

    @application.get("/api/jobs/{job_id}")
    async def get_job(job_id: str, request: Request) -> dict:
        auth.require(request)
        return uploads.get_job(job_id)

    @application.get("/api/jobs/{job_id}/files/{file_id}/offset")
    async def get_offset(job_id: str, file_id: str, request: Request) -> dict:
        auth.require(request)
        return uploads.offset(job_id, file_id)

    @application.put("/api/jobs/{job_id}/files/{file_id}/chunks")
    async def upload_chunk(
        job_id: str,
        file_id: str,
        request: Request,
        upload_offset: int = Header(alias="Upload-Offset"),
    ) -> dict:
        auth.require(request)
        if request.headers.get("content-type", "").split(";", 1)[0].lower() != "application/octet-stream":
            return JSONResponse({"detail": "Chunks must use application/octet-stream"}, status_code=415)
        return await uploads.append_chunk(job_id, file_id, upload_offset, request.stream())

    @application.put("/api/jobs/{job_id}/order")
    async def reorder(job_id: str, request: Request) -> dict:
        auth.require(request)
        try:
            payload = await request.json()
        except Exception:
            return JSONResponse({"detail": "Invalid request"}, status_code=400)
        return uploads.reorder(job_id, payload.get("file_ids") if isinstance(payload, dict) else None)

    @application.delete("/api/jobs/{job_id}", status_code=204)
    async def delete_job(job_id: str, request: Request) -> Response:
        auth.require(request)
        uploads.delete(job_id)
        return Response(status_code=204)

    return application


app = create_app()
