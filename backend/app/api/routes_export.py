"""Excel manifest export endpoints."""

import io

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse, StreamingResponse

from ..core.logging import logger
from ..core.auth import require_authenticated_user
from ..export.excel_exporter import export_roster_workbook

router = APIRouter()


@router.post("/api/export_excel")
def export_excel(body: dict, user: dict = Depends(require_authenticated_user)):
    try:
        output_stream = io.BytesIO()
        export_roster_workbook(
            body.get("routes", []),
            body.get("employees", []),
            body.get("unverified_employees", []),
            output_stream,
        )
        output_stream.seek(0)
        return StreamingResponse(
            output_stream,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": 'attachment; filename="AUTOROSTER_DISPATCH_MANIFEST.xlsx"'},
        )
    except Exception as e:
        logger.error(f"Error in /api/export_excel: {e}", exc_info=True)
        return JSONResponse({"status": "ERROR", "error": str(e)}, status_code=500)
