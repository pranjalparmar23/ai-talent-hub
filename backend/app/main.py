from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api import auth_routes, candidate_routes, recruiter_routes, admin_routes
from app.middleware.logging import LoggingMiddleware
from app.middleware.rate_limit import RateLimitMiddleware
from app.database.postgres import init_db
import os


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown hooks."""
    # Startup: create tables if they don't exist (dev convenience; production uses Alembic)
    await init_db()
    yield
    # Shutdown: nothing to clean up for now


app = FastAPI(
    title="AI Talent Hub",
    description="AI-powered recruitment and career development platform",
    version="1.0.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    lifespan=lifespan,
)

# ── Middleware stack (outermost runs first on requests) ──────
app.add_middleware(RateLimitMiddleware)
app.add_middleware(LoggingMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv(
        "ALLOWED_ORIGINS",
        "http://localhost:5173,http://localhost:3000",
    ).split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routes ───────────────────────────────────────────────────
app.include_router(auth_routes.router, prefix="/api/auth", tags=["auth"])
app.include_router(candidate_routes.router, prefix="/api/candidate", tags=["candidate"])
app.include_router(recruiter_routes.router, prefix="/api/recruiter", tags=["recruiter"])
app.include_router(admin_routes.router, prefix="/api/admin", tags=["admin"])


@app.get("/health", tags=["system"])
async def health_check():
    """Liveness probe endpoint."""
    return {"status": "healthy", "version": "1.0.0"}


@app.get("/", tags=["system"])
async def root():
    """Root endpoint — redirects to docs."""
    return {"message": "AI Talent Hub API", "docs": "/api/docs"}