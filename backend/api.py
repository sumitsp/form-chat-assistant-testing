from __future__ import annotations

import asyncio
import time
import uuid

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, Response

from backend.batch_excel_report import (
    batch_frontend_html,
    build_batch_contract_json_bytes,
    build_batch_outputs,
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

_ARTIFACT_TTL_SECONDS = 15 * 60
_BATCH_ARTIFACTS: dict[str, dict[str, object]] = {}


def _purge_old_artifacts() -> None:
    now = time.time()
    stale_ids = [
        run_id
        for run_id, item in _BATCH_ARTIFACTS.items()
        if (now - float(item.get("created_at", now))) > _ARTIFACT_TTL_SECONDS
    ]
    for run_id in stale_ids:
        _BATCH_ARTIFACTS.pop(run_id, None)


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


@app.post("/api/batch/report-json")
async def batch_report_json(file: UploadFile = File(...)):
    filename = (file.filename or "").lower()
    if not filename.endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="Please upload a .xlsx file.")
    payload = await file.read()
    if not payload:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")
    try:
        json_bytes = await asyncio.wait_for(
            asyncio.to_thread(build_batch_contract_json_bytes, payload),
            timeout=120,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except asyncio.TimeoutError as exc:
        raise HTTPException(status_code=504, detail="Batch JSON timed out after 120 seconds.") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Batch JSON failed: {exc}") from exc

    return Response(
        content=json_bytes,
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="eligibility_batch_test_api_contract.json"'},
    )


@app.post("/api/batch/run")
async def batch_run(file: UploadFile = File(...)):
    filename = (file.filename or "").lower()
    if not filename.endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="Please upload a .xlsx file.")
    payload = await file.read()
    if not payload:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")
    try:
        pdf_bytes, json_bytes = await asyncio.wait_for(
            asyncio.to_thread(build_batch_outputs, payload),
            timeout=120,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except asyncio.TimeoutError as exc:
        raise HTTPException(status_code=504, detail="Batch run timed out after 120 seconds.") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Batch run failed: {exc}") from exc

    _purge_old_artifacts()
    run_id = uuid.uuid4().hex
    _BATCH_ARTIFACTS[run_id] = {
        "created_at": time.time(),
        "pdf": pdf_bytes,
        "json": json_bytes,
    }
    return {
        "run_id": run_id,
        "pdf_download_url": f"/api/batch/download/{run_id}/pdf",
        "json_download_url": f"/api/batch/download/{run_id}/json",
    }


@app.get("/api/batch/download/{run_id}/pdf")
def batch_download_pdf(run_id: str):
    _purge_old_artifacts()
    item = _BATCH_ARTIFACTS.get(run_id)
    if not item:
        raise HTTPException(status_code=404, detail="Run artifact not found or expired.")
    return Response(
        content=bytes(item["pdf"]),
        media_type=pdf_media_type(),
        headers={"Content-Disposition": 'attachment; filename="mortgage-batch-report.pdf"'},
    )


@app.get("/api/batch/download/{run_id}/json")
def batch_download_json(run_id: str):
    _purge_old_artifacts()
    item = _BATCH_ARTIFACTS.get(run_id)
    if not item:
        raise HTTPException(status_code=404, detail="Run artifact not found or expired.")
    return Response(
        content=bytes(item["json"]),
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="eligibility_batch_test_api_contract.json"'},
    )
