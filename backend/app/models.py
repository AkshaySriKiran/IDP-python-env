from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


EngineMode = Literal["gemini", "ollama"]
ParseStrategy = Literal["native", "ocr"]


class RowQuality(BaseModel):
    grounding_score: float = Field(
        default=1.0, description="Token/phrase source grounding match ratio (0.0 - 1.0)"
    )
    completeness_score: float = Field(
        default=1.0, description="Ratio of non-empty essential fields (0.0 - 1.0)"
    )


class MaintenanceRow(BaseModel):
    id: int = 0
    equipment_title: str = "NA"
    subsystem_component: str = "NA"
    maintenance_routine: str = "NA"
    checks_instructions: str = "NA"
    # Logbook alternate fields (optional; "NA" when unused)
    date: str = "NA"
    maintenance_work_description: str = "NA"
    parts_renewed: str = "NA"
    attended_by: str = "NA"
    remarks: str = "NA"
    page: Any = "NA"
    # Top-to-bottom order on the source PDF page (1-based).
    pdf_order: int = 0
    confidence: float = Field(default=1.0, description="Row confidence score (0.0 - 1.0)")
    quality: Optional[RowQuality] = None


class SparePartRow(BaseModel):
    id: int = 0
    equipment_title: str = "NA"
    subsystem_location: str = "NA"
    item_no: str = "NA"
    part_name: str = "NA"
    part_number_code: str = "NA"
    drawing_model_no: str = "NA"
    oem_standard_body: str = "NA"
    part_categorization: str = "NA"
    quantity: str = "NA"
    recommended_stock_qty: str = "NA"
    warranty_period: str = "NA"
    frequency_of_use: str = "NA"
    page: Any = "NA"
    # Top-to-bottom order on the source PDF page (1-based). Used to avoid name-sorting jumble.
    pdf_order: int = 0
    confidence: float = Field(default=1.0, description="Row confidence score (0.0 - 1.0)")
    quality: Optional[RowQuality] = None


class TroubleshootingRow(BaseModel):
    id: int = 0
    equipment_title: str = "NA"
    subsystem_component: str = "NA"
    problem: str = "NA"
    root_cause_solution: str = "NA"
    page: Any = "NA"
    # Top-to-bottom order on the source PDF page (1-based).
    pdf_order: int = 0
    confidence: float = Field(default=1.0, description="Row confidence score (0.0 - 1.0)")
    quality: Optional[RowQuality] = None


class PageText(BaseModel):
    pageNum: int
    text: str


class ExtractMeta(BaseModel):
    filename: str
    engine: str
    parse_strategy: str
    pages_total: int = 0
    pages_processed: int = 0
    maintenance_count: int = 0
    spare_parts_count: int = 0
    troubleshooting_count: int = 0
    warnings: list[str] = Field(default_factory=list)
    overall_score: float = Field(
        default=100.0, description="Overall run quality score percentage (0 - 100)"
    )
    grounding_pass_rate: float = Field(
        default=1.0, description="Share of grounded rows with grounding_score >= 0.70"
    )
    filter_drop_rate: float = Field(
        default=0.0, description="Ratio of incomplete/noisy rows filtered out"
    )
    low_confidence_count: int = Field(
        default=0, description="Total rows with confidence below 0.70"
    )


class ExtractResponse(BaseModel):
    maintenance: list[MaintenanceRow] = Field(default_factory=list)
    spare_parts: list[SparePartRow] = Field(default_factory=list)
    troubleshooting: list[TroubleshootingRow] = Field(default_factory=list)
    pages: list[PageText] = Field(default_factory=list)
    meta: ExtractMeta


class HealthResponse(BaseModel):
    status: str = "ok"
    service: str = "omniparse-maintenance-api"
    version: str = "0.3.0"
    busy: bool = False


class ExtractJobCreateResponse(BaseModel):
    job_id: str
    status: str = "queued"
    message: str = "Extraction job queued"


class ExtractJobStatusResponse(BaseModel):
    job_id: str
    status: str  # queued | running | done | error
    message: str = ""
    progress: float = 0.0
    filename: str = ""
    error: Optional[str] = None
    result: Optional[ExtractResponse] = None


class ExtractOptions(BaseModel):
    engine: EngineMode = "gemini"
    parse_strategy: ParseStrategy = "ocr"
    gemini_api_key: Optional[str] = None
    gemini_model: str = "gemini-3.5-flash"
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = ""
    page_start: Optional[int] = None
    page_end: Optional[int] = None
    equipment_category: str = "Default"
    learned_patterns: list[dict[str, Any]] = Field(default_factory=list)


class ExtractAuditRecord(BaseModel):
    """Admin-visible summary of one AI extraction run (no secrets, no full registries)."""

    id: str
    created_at: str
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    duration_ms: int = 0
    job_id: Optional[str] = None
    user_id: Optional[str] = None
    user_email: Optional[str] = None
    status: str
    error: Optional[str] = None
    filename: str = ""
    engine: str = ""
    parse_strategy: str = ""
    gemini_model: Optional[str] = None
    ollama_model: Optional[str] = None
    equipment_category: str = "Default"
    page_start: Optional[int] = None
    page_end: Optional[int] = None
    pages_total: int = 0
    pages_processed: int = 0
    maintenance_count: int = 0
    spare_parts_count: int = 0
    troubleshooting_count: int = 0
    overall_score: Optional[float] = None
    grounding_pass_rate: Optional[float] = None
    filter_drop_rate: Optional[float] = None
    low_confidence_count: Optional[int] = None
    warnings: list[str] = Field(default_factory=list)
    s3_key: Optional[str] = None


class ExtractAuditListResponse(BaseModel):
    items: list[ExtractAuditRecord] = Field(default_factory=list)
    count: int = 0

