"""
Retail - Sales and Inventory Copilot
TRACK_ID: PS03

Primary application entrypoint.
Starts FastAPI application and serves static frontend and REST endpoints.
Start command: python app.py
Default URL: http://localhost:8000
"""
import os
import sys
import socket
from pathlib import Path
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

# Load environment variables (e.g. GEMINI_API_KEY)
load_dotenv()

# Initialize core modules
from src.database.schema import init_db

app = FastAPI(
    title="Retail - Sales and Inventory Copilot",
    description="Deterministic retail business logic combined with Gemini-powered reasoning.",
    version="0.1.0"
)

# Base directories
BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

# Ensure static directory exists and mount it
os.makedirs(STATIC_DIR, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

@app.on_event("startup")
def on_startup():
    """Ensure database schema is initialized on boot."""
    init_db()

@app.get("/api/health")
def health_check():
    """
    Health check endpoint required by hackathon specification.
    """
    return {
        "status": "healthy",
        "track_id": "PS03",
        "project": "Retail - Sales and Inventory Copilot",
        "version": "0.1.0"
    }

@app.get("/")
def read_root():
    """
    Serves the simple landing page for the Retail Sales and Inventory Copilot.
    No frontend build step required.
    """
    index_file = STATIC_DIR / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return {
        "project": "Retail - Sales and Inventory Copilot",
        "track_id": "PS03",
        "message": "Welcome to Retail Sales and Inventory Copilot"
    }

def get_target_port() -> int:
    """
    Determines the port to bind to.
    Defaults strictly to 8000 as mandated by hackathon requirements.
    In environments where 8000 is occupied (or DEFAULT_APP_PORT is defined for container ingress),
    it adapts seamlessly.
    """
    # 1. Direct command-line override: python app.py --port 8000
    for idx, arg in enumerate(sys.argv):
        if arg in ("--port", "-p") and idx + 1 < len(sys.argv):
            try:
                return int(sys.argv[idx + 1])
            except ValueError:
                pass

    # 2. Check if DEFAULT_APP_PORT is explicitly requested by container routing
    if os.environ.get("DEFAULT_APP_PORT"):
        return int(os.environ["DEFAULT_APP_PORT"])

    # 3. Standard clean evaluation machine: Default to 8000
    preferred_port = 8000
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("0.0.0.0", preferred_port))
            return preferred_port
    except OSError:
        # Fallback if 8000 is held by an environment daemon
        return 3000

if __name__ == "__main__":
    port = get_target_port()
    print(f"Starting Retail - Sales and Inventory Copilot on 0.0.0.0:{port}")
    uvicorn.run("app:app", host="0.0.0.0", port=port, log_level="info")
