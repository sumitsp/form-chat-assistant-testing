from __future__ import annotations

import asyncio

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, Response

from backend.batch_excel_report import (
    batch_frontend_html,
    build_batch_report_pdf_bytes,
    build_template_xlsx_bytes,
    excel_media_type,
    pdf_media_type,
)
from backend.eligibility import router as eligibility_router
from backend.loanpass_routes import router as loanpass_router

app = FastAPI(title="Mortgage Batch Runner API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(eligibility_router)
app.include_router(loanpass_router)


@app.get("/", include_in_schema=False)
def home() -> HTMLResponse:
    return HTMLResponse(content=batch_frontend_html())


@app.get("/api/health")
def health() -> dict[str, object]:
    return {"ok": True, "service": "mortgage-batch-runner"}


@app.get("/api/batch/template")
def batch_template_download():
    data = build_template_xlsx_bytes()
    return Response(
        content=data,
        media_type=excel_media_type(),
        headers={"Content-Disposition": 'attachment; filename="mortgage-batch-template.xlsx"'},
    )


@app.post("/api/batch/report")
async def batch_report(file: UploadFile = File(...)):
    filename = (file.filename or "").lower()
    if not filename.endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="Please upload a .xlsx file.")

    payload = await file.read()
    if not payload:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    try:
        # Hard timeout so requests never hang indefinitely.
        pdf_bytes = await asyncio.wait_for(
            asyncio.to_thread(build_batch_report_pdf_bytes, payload),
            timeout=120,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except asyncio.TimeoutError as exc:
        raise HTTPException(
            status_code=504,
            detail="Batch report timed out after 120 seconds.",
        ) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Batch report failed: {exc}") from exc

    return Response(
        content=pdf_bytes,
        media_type=pdf_media_type(),
        headers={"Content-Disposition": 'attachment; filename="mortgage-batch-report.pdf"'},
    )
