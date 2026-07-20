from __future__ import annotations

import json
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from .config import (
    default_gemini_key,
    default_gemini_model,
    default_ollama_model,
    default_ollama_url,
    get_cors_origins,
)
from .extractors import extract_document
from .models import ExtractOptions, ExtractResponse, HealthResponse

MAX_UPLOAD_BYTES = 1024 * 1024 * 1024  # 1GB

app = FastAPI(
    title="OmniParse Maintenance API",
    description="Python FastAPI backend for heavy maintenance/spare-parts extraction. UI remains in JS.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse()


@app.get("/api/health", response_model=HealthResponse)
async def api_health() -> HealthResponse:
    return HealthResponse()


@app.post("/api/extract", response_model=ExtractResponse)
async def api_extract(
    file: UploadFile = File(...),
    engine: str = Form("gemini"),
    parse_strategy: str = Form("ocr"),
    gemini_api_key: Optional[str] = Form(None),
    gemini_model: Optional[str] = Form(None),
    ollama_url: Optional[str] = Form(None),
    ollama_model: Optional[str] = Form(None),
    page_start: Optional[int] = Form(None),
    page_end: Optional[int] = Form(None),
    equipment_category: Optional[str] = Form("Default"),
    learned_patterns: Optional[str] = Form(None),
) -> ExtractResponse:
    if engine not in {"gemini", "ollama"}:
        raise HTTPException(status_code=400, detail="engine must be 'gemini' or 'ollama'")
    if parse_strategy not in {"native", "ocr"}:
        raise HTTPException(status_code=400, detail="parse_strategy must be 'native' or 'ocr'")

    filename = file.filename or "document"
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty file upload")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail="File exceeds 1GB limit")

    patterns = []
    if learned_patterns:
        try:
            parsed = json.loads(learned_patterns)
            if isinstance(parsed, list):
                patterns = [p for p in parsed if isinstance(p, dict)]
        except json.JSONDecodeError as err:
            raise HTTPException(status_code=400, detail=f"learned_patterns must be JSON array: {err}") from err

    options = ExtractOptions(
        engine=engine,  # type: ignore[arg-type]
        parse_strategy=parse_strategy,  # type: ignore[arg-type]
        gemini_api_key=(gemini_api_key or default_gemini_key() or None),
        gemini_model=(gemini_model or default_gemini_model()),
        ollama_url=(ollama_url or default_ollama_url()),
        ollama_model=(ollama_model or default_ollama_model()),
        page_start=page_start,
        page_end=page_end,
        equipment_category=(equipment_category or "Default").strip() or "Default",
        learned_patterns=patterns,
    )

    if options.engine == "gemini" and not options.gemini_api_key:
        raise HTTPException(status_code=400, detail="Gemini API key required (form field or GEMINI_API_KEY env)")
    if options.engine == "ollama" and not options.ollama_model:
        raise HTTPException(status_code=400, detail="Ollama model required")

    try:
        return await extract_document(data, filename, options)
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err
    except Exception as err:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Extraction failed: {err}") from err
