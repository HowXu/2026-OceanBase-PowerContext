"""Start the pinned real PowerContext HTTP server with project-local storage."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("POWERCONTEXT_HOME", str(ROOT / "data"))
os.environ.setdefault("POWERCONTEXT_SERVER_DATABASE_KIND", "sqlite")
from powercontext.paths import sqlite_url
os.environ.setdefault("POWERCONTEXT_SERVER_DATABASE_URL", sqlite_url(ROOT / "data" / "powercontext.sqlite3"))
os.environ.setdefault("POWERCONTEXT_SERVER_HTTP_HOST", "127.0.0.1")
os.environ.setdefault("POWERCONTEXT_SERVER_MCP_ENABLED", "false")
os.environ.setdefault("POWERCONTEXT_SERVER_DASHBOARD_ENABLED", "false")

if __name__ == "__main__":
    import uvicorn
    from powercontext.server.factory import create_server_app
    uvicorn.run(create_server_app(), host=os.environ["POWERCONTEXT_SERVER_HTTP_HOST"], port=8000)
