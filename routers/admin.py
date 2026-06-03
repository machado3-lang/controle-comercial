import os
import logging
from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import text

from database import engine

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["Admin"])

# ---------------------------------------------------------------------------
# Simple token-based protection.
# Set ADMIN_TOKEN in your Railway environment variables.
# If the variable is not set the endpoint is disabled entirely.
# ---------------------------------------------------------------------------
_ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")

_SQL_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "restore.sql")


def _check_token(x_admin_token: str | None) -> None:
    """Raise 401/403 if the token is missing or wrong."""
    if not _ADMIN_TOKEN:
        raise HTTPException(
            status_code=503,
            detail="Admin endpoint is disabled: ADMIN_TOKEN environment variable is not set.",
        )
    if x_admin_token != _ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid or missing X-Admin-Token header.")


@router.post("/restore-db")
def restore_db(x_admin_token: str | None = Header(default=None)):
    """
    Restore the database from restore.sql.

    Requires the X-Admin-Token header to match the ADMIN_TOKEN environment variable.

    Example:
        curl -X POST https://<your-app>.railway.app/admin/restore-db \\
             -H "X-Admin-Token: <your-token>"
    """
    _check_token(x_admin_token)

    # Read the SQL file
    if not os.path.exists(_SQL_FILE):
        raise HTTPException(
            status_code=404,
            detail=f"restore.sql not found at expected path: {_SQL_FILE}",
        )

    try:
        with open(_SQL_FILE, "r", encoding="utf-8") as f:
            sql_content = f.read()
    except Exception as exc:
        logger.error("Failed to read restore.sql: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Could not read restore.sql: {exc}")

    # Split into individual statements (skip blank lines and comment-only lines)
    raw_statements = sql_content.split(";")
    statements = [s.strip() for s in raw_statements if s.strip()]
    statements = [s for s in statements if not all(line.startswith("--") for line in s.splitlines() if line.strip())]

    if not statements:
        return JSONResponse(
            status_code=200,
            content={"status": "ok", "message": "restore.sql is empty — nothing to execute.", "statements_executed": 0},
        )

    executed = 0
    errors = []

    try:
        with engine.begin() as conn:
            for stmt in statements:
                try:
                    conn.execute(text(stmt))
                    executed += 1
                except Exception as exc:
                    # Collect errors but continue so we restore as much as possible
                    errors.append({"statement_preview": stmt[:120], "error": str(exc)})
                    logger.warning("Statement failed during restore: %s | Error: %s", stmt[:120], exc)
    except Exception as exc:
        logger.error("Database connection failed during restore: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Database connection error: {exc}")

    if errors:
        return JSONResponse(
            status_code=207,
            content={
                "status": "partial",
                "message": f"Restore completed with {len(errors)} error(s).",
                "statements_executed": executed,
                "errors": errors,
            },
        )

    return JSONResponse(
        status_code=200,
        content={
            "status": "ok",
            "message": "Database restored successfully.",
            "statements_executed": executed,
        },
    )
