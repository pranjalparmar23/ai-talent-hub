from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api import candidate_routes, recruiter_routes, auth_routes, admin_routes
from app.database.postgres import init_db
import uvicorn

app = FastAPI(
    title="AI Talent Hub",
    description="AI-powered recruitment and career development platform",
    version="1.0.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_routes.router, prefix="/api/auth", tags=["auth"])
app.include_router(candidate_routes.router, prefix="/api/candidate", tags=["candidate"])
app.include_router(recruiter_routes.router, prefix="/api/recruiter", tags=["recruiter"])
app.include_router(admin_routes.router, prefix="/api/admin", tags=["admin"])


@app.on_event("startup")
async def startup_event():
    await init_db()


@app.get("/health")
async def health_check():
    return {"status": "healthy", "version": "1.0.0"}


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
