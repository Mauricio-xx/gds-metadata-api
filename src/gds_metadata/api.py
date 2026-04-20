"""FastAPI application for GDS metadata extraction."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .models import GdsMetadata
from .parser import parse_gds_metadata
from .sources import SourceError, resolve_source

app = FastAPI(
    title="GDS Metadata API",
    description="Extract metadata from GDSII layout files without loading geometry.",
    version="0.1.0",
)


class ExtractRequest(BaseModel):
    """Request body for metadata extraction."""

    source: str = Field(
        ...,
        description="Local file path or GitHub URL to a .gds file.",
        examples=[
            "/path/to/layout.gds",
            "https://github.com/owner/repo/blob/main/design.gds",
        ],
    )
    max_text_labels: int = Field(
        default=10_000,
        ge=0,
        description="Maximum number of text labels to collect.",
    )
    max_properties: int = Field(
        default=5_000,
        ge=0,
        description="Maximum number of properties to collect.",
    )
    max_cells: int = Field(
        default=500_000,
        ge=0,
        description="Maximum number of cells to collect.",
    )


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/extract", response_model=GdsMetadata)
def extract_metadata(req: ExtractRequest):
    """Extract metadata from a GDSII file.

    Accepts a local path or a GitHub URL. The file is parsed in streaming
    mode - geometry is skipped, only metadata records are read.
    """
    is_temp = False
    try:
        path, is_temp = resolve_source(req.source)
    except SourceError as e:
        raise HTTPException(status_code=400, detail=str(e))

    try:
        result = parse_gds_metadata(
            path,
            max_text_labels=req.max_text_labels,
            max_properties=req.max_properties,
            max_cells=req.max_cells,
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Parse error: {e}")
    finally:
        if is_temp:
            os.unlink(path)
