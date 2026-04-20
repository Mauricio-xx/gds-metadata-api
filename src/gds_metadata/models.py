"""Pydantic models for GDS metadata output."""

from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel, Field


class CellInfo(BaseModel):
    """Metadata for a single GDSII structure (cell)."""

    name: str
    modified: datetime | None = None
    accessed: datetime | None = None


class TextLabel(BaseModel):
    """A TEXT element extracted from the layout."""

    text: str
    layer: int | None = None
    texttype: int | None = None
    cell: str | None = None


class Property(BaseModel):
    """An element or structure property (PROPATTR/PROPVALUE pair)."""

    attr: int | None = None
    value: str
    cell: str | None = None


class ElementCounts(BaseModel):
    """Count of each geometry element type in the file."""

    boundary: int = 0
    path: int = 0
    sref: int = 0
    aref: int = 0
    text: int = 0
    box: int = 0
    node: int = 0

    @property
    def total(self) -> int:
        return self.boundary + self.path + self.sref + self.aref + self.text + self.box + self.node


class ToolInference(BaseModel):
    """Best-effort inference of the EDA tool that generated the file."""

    tool: str | None = None
    confidence: str = "low"
    clues: list[str] = Field(default_factory=list)


class GdsMetadata(BaseModel):
    """Complete metadata extracted from a GDSII file."""

    file_path: str
    file_size_bytes: int
    gds_version: int | None = None
    library_name: str | None = None
    library_modified: datetime | None = None
    library_accessed: datetime | None = None
    units_dbu_in_user: float | None = None
    units_dbu_in_meters: float | None = None
    format_type: int | None = None
    masks: list[str] = Field(default_factory=list)
    reflibs: str | None = None
    fonts: str | None = None
    generations: int | None = None
    attrtable: str | None = None
    layers_used: list[int] = Field(default_factory=list)
    cells: list[CellInfo] = Field(default_factory=list)
    element_counts: ElementCounts = Field(default_factory=ElementCounts)
    text_labels: list[TextLabel] = Field(default_factory=list)
    properties: list[Property] = Field(default_factory=list)
    tool_inference: ToolInference = Field(default_factory=ToolInference)
    parse_time_seconds: float | None = None

    @property
    def cell_count(self) -> int:
        return len(self.cells)

    @property
    def text_count(self) -> int:
        return len(self.text_labels)
