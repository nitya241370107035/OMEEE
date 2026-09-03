import os
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from backend.api.routers.coverage import router as coverage_router
from backend.api.routers.ingest import router as ingest_router
from backend.api.routers.archive import router as archive_router

app = FastAPI(
    title="Satellite Imagery Semantic Retrieval & Change Detection API",
    description="Backend service for semantic retrieval and multi-temporal change analysis (PS SIH-26227 for Indian Army DGIS).",
    version="1.0.0"
)

# CORS middleware for frontend communication
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include Routers
app.include_router(coverage_router)
app.include_router(ingest_router)
app.include_router(archive_router)

# Mount Data & Static directories if they exist
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
data_dir = REPO_ROOT / "data"
frontend_dir = REPO_ROOT / "frontend"

data_dir.mkdir(parents=True, exist_ok=True)
frontend_dir.mkdir(parents=True, exist_ok=True)

app.mount("/data", StaticFiles(directory=str(data_dir)), name="data")
app.mount("/frontend", StaticFiles(directory=str(frontend_dir)), name="frontend")


@app.get("/")
def read_root():
    index_file = frontend_dir / "index.html"
    if index_file.exists():
        return FileResponse(str(index_file))
    return {
        "status": "online",
        "service": "Satellite Imagery Semantic Retrieval & Change Detection API",
        "docs": "/docs"
    }


@app.get("/health")
def health():
    return {
        "status": "ok"
    }


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("backend.api.main:app", host="0.0.0.0", port=port, reload=True)
