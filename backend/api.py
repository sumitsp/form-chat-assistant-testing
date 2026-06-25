from __future__ import annotations

import asyncio
import json
import math
import time
import uuid
from typing import Any

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, Response

from backend.batch_excel_report import (
    batch_frontend_html,
    build_batch_contract_json_bytes,
    build_batch_outputs,
    build_batch_outputs_from_parsed,
    build_batch_report_pdf_bytes,
    build_template_xlsx_bytes,
    excel_media_type,
    parse_batch_scenarios,
    pdf_media_type,
)
from backend.eligibility import router as eligibility_router
from backend.loanpass_routes import router as loanpass_router
from backend.testing_history import (
    delete_testing_history_row,
    ensure_testing_history_table,
    get_testing_history_artifact,
    insert_testing_history_row,
    list_testing_history,
)

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

_CHUNK_SIZE = 10
_JOB_TTL_SECONDS = 2 * 60 * 60
_BATCH_JOBS: dict[str, dict[str, Any]] = {}


def _purge_old_jobs() -> None:
    now = time.time()
    stale = [
        job_id
        for job_id, item in _BATCH_JOBS.items()
        if (now - float(item.get("created_at", now))) > _JOB_TTL_SECONDS
    ]
    for job_id in stale:
        _BATCH_JOBS.pop(job_id, None)


def _build_chunk_label(file_name: str, chunk_index: int, chunk_total: int, start_no: int, end_no: int) -> str:
    if chunk_total <= 1:
        return file_name
    return f"{file_name} - Scenarios {start_no}-{end_no} (part {chunk_index}/{chunk_total})"


def _details_json_for_chunk(
    source_file_name: str,
    parsed_chunk: list[tuple[int, str, dict[str, str], list[tuple[str, str]]]],
    json_bytes: bytes,
) -> str:
    contract = json.loads(json_bytes.decode("utf-8"))
    results = contract.get("results") or []
    scenarios: list[dict[str, Any]] = []
    for idx, parsed in enumerate(parsed_chunk):
        scenario_no, scenario_name, form_payload, input_rows = parsed
        result = results[idx] if idx < len(results) else {}
        matched = result.get("matched_programs") or []
        rejected = result.get("rejected_programs") or []
        loanpass_passed = [
            {
                "program_id": m.get("program_id"),
                "program_name_np": m.get("program_name_np"),
                "lender": ((m.get("lender") or {}).get("brand_name") or ""),
            }
            for m in matched
            if bool(m.get("loanpass_pass"))
        ]
        scenarios.append(
            {
                "scenario_no": scenario_no,
                "scenario_name": scenario_name,
                "input_fields": [{"question": q, "value": v} for q, v in input_rows],
                "payload_fields": form_payload,
                "loanpass_passed_programs": loanpass_passed,
                "passed_programs": matched,
                "failed_programs": rejected,
            }
        )
    payload = {
        "source_file_name": source_file_name,
        "scenario_count": len(parsed_chunk),
        "scenarios": scenarios,
    }
    return json.dumps(payload, ensure_ascii=False)


def _process_one_chunk(
    *,
    job_id: str,
    source_file_name: str,
    parsed_chunk: list[tuple[int, str, dict[str, str], list[tuple[str, str]]]],
    chunk_index: int,
    chunk_total: int,
) -> dict[str, Any]:
    pdf_bytes, json_bytes = build_batch_outputs_from_parsed(parsed_chunk)
    start_no = parsed_chunk[0][0]
    end_no = parsed_chunk[-1][0]
    chunk_label = _build_chunk_label(source_file_name, chunk_index, chunk_total, start_no, end_no)
    details_json = _details_json_for_chunk(source_file_name, parsed_chunk, json_bytes)
    history_id = insert_testing_history_row(
        job_id=job_id,
        source_file_name=source_file_name,
        chunk_label=chunk_label,
        chunk_index=chunk_index,
        chunk_total=chunk_total,
        scenario_count=len(parsed_chunk),
        details_json=details_json,
        pdf_blob=pdf_bytes,
        json_blob=json_bytes,
    )
    return {
        "history_id": history_id,
        "chunk_label": chunk_label,
        "scenario_count": len(parsed_chunk),
        "chunk_index": chunk_index,
        "chunk_total": chunk_total,
        "pdf_download_url": f"/api/batch/history/{history_id}/pdf",
        "json_download_url": f"/api/batch/history/{history_id}/json",
    }


async def _process_job_in_background(
    *,
    job_id: str,
    source_file_name: str,
    parsed: list[tuple[int, str, dict[str, str], list[tuple[str, str]]]],
) -> None:
    try:
        chunk_total = max(1, math.ceil(len(parsed) / _CHUNK_SIZE))
        for i in range(chunk_total):
            chunk = parsed[i * _CHUNK_SIZE : (i + 1) * _CHUNK_SIZE]
            item = await asyncio.to_thread(
                _process_one_chunk,
                job_id=job_id,
                source_file_name=source_file_name,
                parsed_chunk=chunk,
                chunk_index=i + 1,
                chunk_total=chunk_total,
            )
            job = _BATCH_JOBS.get(job_id)
            if not job:
                return
            job["chunks"].append(item)
            job["completed_chunks"] = int(job.get("completed_chunks", 0)) + 1
        job = _BATCH_JOBS.get(job_id)
        if job is not None:
            job["status"] = "completed"
    except Exception as exc:  # pragma: no cover
        job = _BATCH_JOBS.get(job_id)
        if job is not None:
            job["status"] = "failed"
            job["error"] = str(exc)


@app.on_event("startup")
def _startup() -> None:
    ensure_testing_history_table()


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
        pdf_bytes = await asyncio.wait_for(
            asyncio.to_thread(build_batch_report_pdf_bytes, payload),
            timeout=120,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except asyncio.TimeoutError as exc:
        raise HTTPException(status_code=504, detail="Batch report timed out after 120 seconds.") from exc
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
    filename = (file.filename or "").strip()
    lower = filename.lower()
    if not lower.endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="Please upload a .xlsx file.")
    payload = await file.read()
    if not payload:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")
    try:
        parsed = await asyncio.to_thread(parse_batch_scenarios, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to parse Excel: {exc}") from exc

    _purge_old_jobs()
    job_id = uuid.uuid4().hex
    total_chunks = max(1, math.ceil(len(parsed) / _CHUNK_SIZE))
    _BATCH_JOBS[job_id] = {
        "job_id": job_id,
        "status": "running",
        "error": "",
        "created_at": time.time(),
        "source_file_name": filename or "uploaded.xlsx",
        "total_scenarios": len(parsed),
        "total_chunks": total_chunks,
        "completed_chunks": 0,
        "chunks": [],
    }
    asyncio.create_task(
        _process_job_in_background(
            job_id=job_id,
            source_file_name=filename or "uploaded.xlsx",
            parsed=parsed,
        )
    )
    return {
        "job_id": job_id,
        "status": "running",
        "total_scenarios": len(parsed),
        "total_chunks": total_chunks,
        "status_url": f"/api/batch/run/{job_id}",
    }


@app.get("/api/batch/run/{job_id}")
def batch_run_status(job_id: str):
    _purge_old_jobs()
    job = _BATCH_JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found or expired.")
    processed_scenarios = sum(int(c.get("scenario_count", 0)) for c in (job.get("chunks") or []))
    return {
        "job_id": job["job_id"],
        "status": job["status"],
        "error": job["error"],
        "source_file_name": job["source_file_name"],
        "total_scenarios": job["total_scenarios"],
        "processed_scenarios": processed_scenarios,
        "total_chunks": job["total_chunks"],
        "completed_chunks": job["completed_chunks"],
        "chunks": job["chunks"],
    }


@app.get("/api/batch/history")
def batch_history(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=50),
):
    return list_testing_history(page=page, page_size=page_size)


@app.get("/api/batch/history/{history_id}/pdf")
def batch_history_pdf(history_id: int):
    try:
        blob, name = get_testing_history_artifact(history_id, "pdf")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="History row not found.") from exc
    return Response(
        content=blob,
        media_type=pdf_media_type(),
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@app.get("/api/batch/history/{history_id}/json")
def batch_history_json(history_id: int):
    try:
        blob, name = get_testing_history_artifact(history_id, "json")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="History row not found.") from exc
    return Response(
        content=blob,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@app.delete("/api/batch/history/{history_id}")
def batch_history_delete(history_id: int):
    deleted = delete_testing_history_row(history_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="History row not found.")
    return {"ok": True, "deleted_id": history_id}


@app.post("/api/batch/run-sync")
async def batch_run_sync(file: UploadFile = File(...)):
    """Compatibility endpoint: immediate single-output run."""
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
    return {
        "pdf_size": len(pdf_bytes),
        "json_size": len(json_bytes),
    }
