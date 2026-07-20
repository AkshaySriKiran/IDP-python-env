from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


EngineMode = Literal["gemini", "ollama"]
ParseStrategy = Literal["native", "ocr"]


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


class TroubleshootingRow(BaseModel):
    id: int = 0
    equipment_title: str = "NA"
    subsystem_component: str = "NA"
    problem: str = "NA"
    root_cause_solution: str = "NA"
    page: Any = "NA"


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


class ExtractResponse(BaseModel):
    maintenance: list[MaintenanceRow] = Field(default_factory=list)
    spare_parts: list[SparePartRow] = Field(default_factory=list)
    troubleshooting: list[TroubleshootingRow] = Field(default_factory=list)
    pages: list[PageText] = Field(default_factory=list)
    meta: ExtractMeta


class HealthResponse(BaseModel):
    status: str = "ok"
    service: str = "omniparse-maintenance-api"
    version: str = "0.1.0"


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
