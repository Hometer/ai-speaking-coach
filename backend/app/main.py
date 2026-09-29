"""FastAPI 入口：注册路由、静态页与统一错误处理。"""
import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.core.errors import AppError, error_body
from app.models.database import init_db
from app.services.cleanup import cleanup_expired_data

STATIC_DIR = Path(__file__).resolve().parent / "static"
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(_app: FastAPI):
    async def clean_periodically():
        while True:
            await asyncio.sleep(60)
            try:
                await asyncio.to_thread(cleanup_expired_data)
            except Exception:
                logger.error("periodic_cleanup_failed")

    await asyncio.to_thread(cleanup_expired_data)
    task = asyncio.create_task(clean_periodically())
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="AI 口语练习 · 后端 MVP", version="0.2.0", lifespan=lifespan)


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError):
    return JSONResponse(status_code=exc.status_code, content=error_body(exc.code, exc.message))


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=422, content=error_body("VALIDATION_ERROR", "请求参数不合法"))


@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, exc: Exception):
    # 不向前端泄露堆栈；具体原因写入服务端日志
    return JSONResponse(status_code=500, content=error_body("INTERNAL_ERROR", "服务内部错误，请稍后重试"))


app.include_router(router)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


# 启动时建表（幂等）；schema 稳定后引入 Alembic 迁移
init_db()
from app.core.auth import seed_invites  # noqa: E402

seed_invites()
