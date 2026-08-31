from __future__ import annotations

from typing import Any, Literal, Optional
from pydantic import BaseModel, Field

EngineMode = Literal["gemini", "ollama"]
ParseStrategy = Literal["native", "ocr"]
ReviewStatus = Literal[
    "Draft",
    "Pending Review",
    "In Review",
    "Pending Sign-Off",
    "Approved",
    "Rejected",
    "Needs Revision",
]


class DocumentMetadata(BaseModel):
    title: str = "NA"
    oem_manufacturer: str = "NA"
    equipment_model: str = "NA"
    equipment_type: str = "NA"
    document_version: str = "NA"
    publication_date: str = "NA"


class RowQuality(BaseModel):
    grounding_score: float = Field(default=1.0, description="Grounding match score (0.0 - 1.0)")
    completeness_score: float = Field(default=1.0, description="Completeness score (0.0 - 1.0)")
    grounding_available: bool = Field(default=False)
    reasons: list[str] = Field(default_factory=list)


from pydantic import BaseModel, Field, field_validator


class MaintenanceRow(BaseModel):
    id: int = 0
    equipment_title: str = "NA"
    subsystem_component: str = "NA"
    maintenance_routine: str = "NA"
    checks_instructions: str = "NA"
    date: str = "NA"
    maintenance_work_description: str = "NA"
    parts_renewed: str = "NA"
    attended_by: str = "NA"
    remarks: str = "NA"
    page: Any = "NA"
    pdf_order: int = 0
    confidence: Any = 1.0
    quality: Optional[Any] = None
    status: str = "Pending Review"
    reviewed_by: Optional[str] = None
    reviewed_at: Optional[str] = None
    rejection_reason: Optional[str] = None

    @field_validator("confidence", mode="before")
    @classmethod
    def _parse_confidence(cls, v: Any) -> float:
        try:
            return float(v)
        except Exception:
            return 1.0

    @field_validator("id", "pdf_order", mode="before")
    @classmethod
    def _parse_ints(cls, v: Any) -> int:
        try:
            return int(v)
        except Exception:
            return 0

    @field_validator("status", mode="before")
    @classmethod
    def _parse_status(cls, v: Any) -> str:
        s = str(v or "").strip()
        if s in {"Draft", "Pending Review", "In Review", "Pending Sign-Off", "Approved", "Rejected", "Needs Revision"}:
            return s
        if s.lower() in {"approved", "signed off", "signed-off"}:
            return "Approved"
        if s.lower() in {"rejected", "reject"}:
            return "Rejected"
        if s.lower() in {"in review", "in-review", "reviewing"}:
            return "In Review"
        if s.lower() in {"pending sign-off", "pending sign off", "pending-sign-off"}:
            return "Pending Sign-Off"
        if s.lower() in {"needs revision", "revision"}:
            return "Needs Revision"
        return "Pending Review"


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
    pdf_order: int = 0
    confidence: Any = 1.0
    quality: Optional[Any] = None
    status: str = "Pending Review"
    reviewed_by: Optional[str] = None
    reviewed_at: Optional[str] = None
    rejection_reason: Optional[str] = None

    @field_validator("confidence", mode="before")
    @classmethod
    def _parse_confidence(cls, v: Any) -> float:
        try:
            return float(v)
        except Exception:
            return 1.0

    @field_validator("id", "pdf_order", mode="before")
    @classmethod
    def _parse_ints(cls, v: Any) -> int:
        try:
            return int(v)
        except Exception:
            return 0

    @field_validator("status", mode="before")
    @classmethod
    def _parse_status(cls, v: Any) -> str:
        s = str(v or "").strip()
        if s in {"Draft", "Pending Review", "In Review", "Pending Sign-Off", "Approved", "Rejected", "Needs Revision"}:
            return s
        if s.lower() in {"approved", "signed off", "signed-off"}:
            return "Approved"
        if s.lower() in {"rejected", "reject"}:
            return "Rejected"
        if s.lower() in {"in review", "in-review", "reviewing"}:
            return "In Review"
        if s.lower() in {"pending sign-off", "pending sign off", "pending-sign-off"}:
            return "Pending Sign-Off"
        if s.lower() in {"needs revision", "revision"}:
            return "Needs Revision"
        return "Pending Review"


class TroubleshootingRow(BaseModel):
    id: int = 0
    equipment_title: str = "NA"
    subsystem_component: str = "NA"
    problem: str = "NA"
    root_cause_solution: str = "NA"
    page: Any = "NA"
    pdf_order: int = 0
    confidence: Any = 1.0
    quality: Optional[Any] = None
    status: str = "Pending Review"
    reviewed_by: Optional[str] = None
    reviewed_at: Optional[str] = None
    rejection_reason: Optional[str] = None

    @field_validator("confidence", mode="before")
    @classmethod
    def _parse_confidence(cls, v: Any) -> float:
        try:
            return float(v)
        except Exception:
            return 1.0

    @field_validator("id", "pdf_order", mode="before")
    @classmethod
    def _parse_ints(cls, v: Any) -> int:
        try:
            return int(v)
        except Exception:
            return 0

    @field_validator("status", mode="before")
    @classmethod
    def _parse_status(cls, v: Any) -> str:
        s = str(v or "").strip()
        if s in {"Draft", "Pending Review", "In Review", "Pending Sign-Off", "Approved", "Rejected", "Needs Revision"}:
            return s
        if s.lower() in {"approved", "signed off", "signed-off"}:
            return "Approved"
        if s.lower() in {"rejected", "reject"}:
            return "Rejected"
        if s.lower() in {"in review", "in-review", "reviewing"}:
            return "In Review"
        if s.lower() in {"pending sign-off", "pending sign off", "pending-sign-off"}:
            return "Pending Sign-Off"
        if s.lower() in {"needs revision", "revision"}:
            return "Needs Revision"
        return "Pending Review"


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
    overall_score: float = 100.0
    grounding_pass_rate: float = 1.0
    filter_drop_rate: float = 0.0
    low_confidence_count: int = 0
    run_id: Optional[str] = None
    doc_metadata: Optional[DocumentMetadata] = None
    document_status: ReviewStatus = "Pending Review"
    approved_by: Optional[str] = None
    approved_at: Optional[str] = None
    assigned_approver: Optional[str] = None
    submitted_by: Optional[str] = None
    has_diff: Optional[bool] = False
    already_approved: bool = False
    prior_approved_by: Optional[str] = None
    prior_approved_at: Optional[str] = None


class BaselineExtraction(BaseModel):
    maintenance: list[MaintenanceRow] = Field(default_factory=list)
    spare_parts: list[SparePartRow] = Field(default_factory=list)
    troubleshooting: list[TroubleshootingRow] = Field(default_factory=list)
    doc_metadata: Optional[DocumentMetadata] = None
    extracted_at: Optional[str] = None


class ExtractResponse(BaseModel):
    maintenance: list[MaintenanceRow] = Field(default_factory=list)
    spare_parts: list[SparePartRow] = Field(default_factory=list)
    troubleshooting: list[TroubleshootingRow] = Field(default_factory=list)
    pages: list[PageText] = Field(default_factory=list)
    meta: ExtractMeta
    baseline: Optional[BaselineExtraction] = None
    raw_payload: Optional[dict[str, Any]] = None
    edited_payload: Optional[dict[str, Any]] = None


class HealthResponse(BaseModel):
    status: str = "ok"
    service: str = "omniparse-maintenance-api"
    version: str = "1.0.0"
    busy: bool = False
    queue_depth: int = 0


class ExtractJobCreateResponse(BaseModel):
    job_id: str
    status: str = "queued"
    message: str = "Extraction job queued"
    position: int = 0


class SharePointFolderItem(BaseModel):
    id: str
    name: str
    parent_id: Optional[str] = None
    item_count: Optional[int] = None


class SharePointFileItem(BaseModel):
    id: str
    name: str
    size: int = 0
    etag: Optional[str] = None
    last_modified: Optional[str] = None
    web_url: Optional[str] = None
    folder_id: Optional[str] = None


class SharePointFileListResponse(BaseModel):
    files: list[SharePointFileItem] = Field(default_factory=list)
    folders: list[SharePointFolderItem] = Field(default_factory=list)
    current_folder: Optional[SharePointFolderItem] = None
    parent_folder_id: Optional[str] = None
    configured: bool = True


class FabricExtractSummary(BaseModel):
    run_id: str
    filename: str = ""
    content_hash: Optional[str] = None
    status: str = "done"
    overall_score: Optional[float] = None
    maintenance_count: int = 0
    spare_parts_count: int = 0
    troubleshooting_count: int = 0
    engine: Optional[str] = None
    parse_strategy: Optional[str] = None
    extracted_at: Optional[str] = None
    drive_item_id: Optional[str] = None
    doc_title: Optional[str] = None
    oem_manufacturer: Optional[str] = None
    document_status: Optional[str] = None
    approved_by: Optional[str] = None
    approved_at: Optional[str] = None
    submitted_by: Optional[str] = None
    assigned_approver: Optional[str] = None
    rejection_notes: Optional[str] = None


class FabricExtractListResponse(BaseModel):
    items: list[FabricExtractSummary] = Field(default_factory=list)
    count: int = 0
    configured: bool = True


class FabricReviewSyncRequest(BaseModel):
    document_status: str = "Pending Review"
    approved_by: Optional[str] = None
    approved_at: Optional[str] = None
    rejection_notes: Optional[str] = None
    doc_metadata: Optional[DocumentMetadata] = None
    spare_parts: Optional[list[Any]] = None
    maintenance: Optional[list[Any]] = None
    troubleshooting: Optional[list[Any]] = None

    @field_validator("document_status", mode="before")
    @classmethod
    def _parse_doc_status(cls, v: Any) -> str:
        s = str(v or "").strip()
        if s in {"Draft", "Pending Review", "In Review", "Pending Sign-Off", "Approved", "Rejected", "Needs Revision"}:
            return s
        if s.lower() in {"approved", "signed off", "signed-off"}:
            return "Approved"
        if s.lower() in {"rejected", "reject"}:
            return "Rejected"
        if s.lower() in {"in review", "in-review", "reviewing"}:
            return "In Review"
        if s.lower() in {"pending sign-off", "pending sign off", "pending-sign-off"}:
            return "Pending Sign-Off"
        if s.lower() in {"needs revision", "revision"}:
            return "Needs Revision"
        return "Pending Review"


class ShareLinkResponse(BaseModel):
    run_id: str
    share_token: str
    share_url: str
    expires_at: str
    expires_in_hours: int = 24


class SharedExtractResponse(BaseModel):
    run_id: str
    filename: str
    maintenance: list[MaintenanceRow] = Field(default_factory=list)
    spare_parts: list[SparePartRow] = Field(default_factory=list)
    troubleshooting: list[TroubleshootingRow] = Field(default_factory=list)
    meta: ExtractMeta
    expires_at: str
    is_shared_view: bool = True




class ExtractJobStatusResponse(BaseModel):
    job_id: str
    status: str
    message: str = ""
    progress: float = 0.0
    filename: str = ""
    error: Optional[str] = None
    result: Optional[ExtractResponse] = None


class ExtractOptions(BaseModel):
    engine: EngineMode = "gemini"
    parse_strategy: ParseStrategy = "ocr"
    gemini_api_key: Optional[str] = None
    gemini_model: str = "gemini-3.6-flash"
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = ""
    page_start: Optional[int] = None
    page_end: Optional[int] = None
    equipment_category: str = "Default"
    learned_patterns: list[dict[str, Any]] = Field(default_factory=list)


class ExtractAuditRecord(BaseModel):
    id: str
    created_at: str
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    duration_ms: int = 0
    job_id: Optional[str] = None
    user_id: Optional[str] = None
    user_email: Optional[str] = None
    user_name: Optional[str] = None
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
    run_id: Optional[str] = None
    document_status: Optional[str] = None
    approved_by: Optional[str] = None
    approved_at: Optional[str] = None
    rejection_notes: Optional[str] = None


class ExtractAuditListResponse(BaseModel):
    items: list[ExtractAuditRecord] = Field(default_factory=list)
    count: int = 0


class OpsStatusTile(BaseModel):
    value: str = "—"
    meta: str = ""
    tone: str = "neutral"
    ok: bool = True
    detail: str = ""


class OpsStatusResponse(BaseModel):
    region: str = ""
    ecs: OpsStatusTile = Field(default_factory=OpsStatusTile)
    cpu: OpsStatusTile = Field(default_factory=OpsStatusTile)
    memory: OpsStatusTile = Field(default_factory=OpsStatusTile)
    alb: OpsStatusTile = Field(default_factory=OpsStatusTile)
    audit_s3: OpsStatusTile = Field(default_factory=OpsStatusTile)
