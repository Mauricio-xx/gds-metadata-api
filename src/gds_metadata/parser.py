"""Streaming GDSII metadata parser.

Designed for multi-GB files. For local files, uses mmap for zero-copy
scanning with OS-managed paging. For streams (e.g. network downloads),
falls back to buffered reads with seek-based skipping.

When the Cython scanner (_scanner.pyx) is compiled, the mmap hot loop
runs as native C code (~10x faster than pure Python). Falls back to
pure Python transparently if Cython is not available.

Memory usage is O(metadata), not O(file_size). The mmap doesn't load
the file into RAM - it lets the OS page in only what we touch, and
since we skip geometry payloads, most pages are never faulted in.
"""

from __future__ import annotations

import mmap
import os
import struct
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import BinaryIO

from .models import (
    CellInfo,
    ElementCounts,
    GdsMetadata,
    Property,
    TextLabel,
    ToolInference,
)
from .records import ELEMENT_NAMES, METADATA_RECORDS, RecordType

try:
    from ._scanner import scan_mmap as _cython_scan
    _HAS_CYTHON = True
except ImportError:
    _HAS_CYTHON = False

_DATE = struct.Struct(">12h")

# Pre-compute a 256-byte lookup: is this record type metadata?
_IS_METADATA = bytearray(256)
for _rt in METADATA_RECORDS:
    _IS_METADATA[_rt] = 1

_ELEM_COUNTER_KEY: dict[int, str] = {
    RecordType.BOUNDARY: "boundary",
    RecordType.PATH: "path",
    RecordType.SREF: "sref",
    RecordType.AREF: "aref",
    RecordType.BOX: "box",
    RecordType.NODE: "node",
}


def _gds_real8(data, offset: int) -> float:
    """Convert 8-byte GDSII real at offset to float."""
    b0 = data[offset]
    sign = -1.0 if (b0 & 0x80) else 1.0
    exp = (b0 & 0x7F) - 64
    mantissa = int.from_bytes(data[offset + 1 : offset + 8], "big")
    return sign * mantissa * (16.0 ** (exp - 14))


def _parse_dates(data, offset: int) -> tuple[datetime | None, datetime | None]:
    """Parse two GDSII date stamps (12 int16 = 24 bytes) at offset."""
    vals = _DATE.unpack_from(data, offset)
    out: list[datetime | None] = []
    for i in (0, 6):
        try:
            out.append(datetime(vals[i], vals[i+1], vals[i+2],
                                vals[i+3], vals[i+4], vals[i+5]))
        except (ValueError, OverflowError):
            out.append(None)
    return out[0], out[1]


def _decode_ascii(data, start: int, end: int) -> str:
    """Decode ASCII from data[start:end], stripping null padding."""
    raw = bytes(data[start:end])
    return raw.rstrip(b"\x00").decode("ascii", errors="replace").strip()


def _signed16(hi: int, lo: int) -> int:
    """Big-endian signed 16-bit from two bytes."""
    v = (hi << 8) | lo
    return v - 0x10000 if v >= 0x8000 else v


def _infer_tool(meta: GdsMetadata) -> ToolInference:
    """Best-effort EDA tool detection from available metadata clues."""
    clues: list[str] = []

    lib = (meta.library_name or "").lower()
    if "klayout" in lib:
        clues.append("KLayout (library name)")
    elif lib in ("library", "lib"):
        clues.append("Generic library name (common in KLayout)")
    elif "cadence" in lib or "virtuoso" in lib:
        clues.append("Cadence Virtuoso (library name)")

    for label in meta.text_labels:
        t = label.text.lower()
        if "klayout" in t:
            clues.append(f"KLayout (text: {label.text[:60]})")
        elif "cadence" in t or "virtuoso" in t:
            clues.append(f"Cadence (text: {label.text[:60]})")
        elif "calibre" in t:
            clues.append(f"Calibre (text: {label.text[:60]})")
        elif "pdk version" in t:
            clues.append(f"PDK version embedded (text: {label.text[:80]})")

    for prop in meta.properties:
        v = prop.value.lower()
        if "klayout" in v:
            clues.append(f"KLayout (property: {prop.value[:60]})")
        elif "oa" in v or "cadence" in v:
            clues.append(f"Cadence/OA (property: {prop.value[:60]})")

    if meta.gds_version is not None:
        clues.append(f"GDSII version {meta.gds_version}")

    cell_names = {c.name.lower() for c in meta.cells}
    if any("fill_cell" in n for n in cell_names):
        clues.append("Fill cells present (post-fill GDS)")

    tool = None
    confidence = "low"
    kl = sum(1 for c in clues if "klayout" in c.lower())
    ca = sum(1 for c in clues if "cadence" in c.lower() or "oa" in c.lower())

    if kl >= 2:
        tool, confidence = "KLayout", "high"
    elif kl == 1:
        tool, confidence = "KLayout", "medium"
    elif ca >= 2:
        tool, confidence = "Cadence Virtuoso", "high"
    elif ca == 1:
        tool, confidence = "Cadence Virtuoso", "medium"

    return ToolInference(tool=tool, confidence=confidence, clues=clues)


def parse_gds_metadata(
    source: str | Path | BinaryIO,
    *,
    max_text_labels: int = 10_000,
    max_properties: int = 5_000,
    max_cells: int = 500_000,
) -> GdsMetadata:
    """Extract metadata from a GDSII file.

    For local files, uses mmap. If the Cython scanner is compiled,
    the scan loop runs as native C (~10x faster). Falls back to pure
    Python transparently.

    For file-like objects (streams), falls back to buffered read/seek.
    """
    t0 = time.monotonic()

    if isinstance(source, (str, Path)):
        path = Path(source)
        file_size = path.stat().st_size
        file_path_str = str(path)

        if file_size == 0:
            return GdsMetadata(file_path=file_path_str, file_size_bytes=0,
                               parse_time_seconds=0.0)

        fd = os.open(str(path), os.O_RDONLY)
        try:
            mm = mmap.mmap(fd, 0, access=mmap.ACCESS_READ)
            try:
                if _HAS_CYTHON:
                    result = _parse_with_cython(
                        mm, file_path=file_path_str, file_size=file_size, t0=t0,
                        max_text_labels=max_text_labels,
                        max_properties=max_properties,
                        max_cells=max_cells,
                    )
                else:
                    result = _parse_mmap(
                        mm, file_path=file_path_str, file_size=file_size, t0=t0,
                        max_text_labels=max_text_labels,
                        max_properties=max_properties,
                        max_cells=max_cells,
                    )
                return result
            finally:
                mm.close()
        finally:
            os.close(fd)
    else:
        file_path_str = getattr(source, "name", "<stream>")
        pos = source.tell()
        source.seek(0, 2)
        file_size = source.tell()
        source.seek(pos)
        return _parse_stream(
            source, file_path=file_path_str, file_size=file_size, t0=t0,
            max_text_labels=max_text_labels,
            max_properties=max_properties,
            max_cells=max_cells,
        )


def _parse_with_cython(
    mm: mmap.mmap,
    *,
    file_path: str,
    file_size: int,
    t0: float,
    max_text_labels: int,
    max_properties: int,
    max_cells: int,
) -> GdsMetadata:
    """Use the Cython scanner for the hot loop, then wrap results."""
    raw = _cython_scan(mm)

    meta = GdsMetadata(
        file_path=file_path,
        file_size_bytes=file_size,
        gds_version=raw["gds_version"],
        library_name=raw["library_name"],
        library_modified=raw["library_modified"],
        library_accessed=raw["library_accessed"],
        units_dbu_in_user=raw["units_dbu_in_user"],
        units_dbu_in_meters=raw["units_dbu_in_meters"],
        format_type=raw["format_type"],
        masks=raw["masks"],
        reflibs=raw["reflibs"],
        fonts=raw["fonts"],
        generations=raw["generations"],
        attrtable=raw["attrtable"],
        layers_used=raw["layers_used"],
    )

    for name, mod, acc in raw["cells"][:max_cells]:
        meta.cells.append(CellInfo(name=name, modified=mod, accessed=acc))

    for text, layer, ttype, cname in raw["text_labels"][:max_text_labels]:
        meta.text_labels.append(TextLabel(
            text=text, layer=layer, texttype=ttype, cell=cname))

    for attr, val, cname in raw["properties"][:max_properties]:
        meta.properties.append(Property(attr=attr, value=val, cell=cname))

    meta.element_counts = ElementCounts(**raw["element_counts"])
    meta.tool_inference = _infer_tool(meta)
    meta.parse_time_seconds = round(time.monotonic() - t0, 4)
    return meta


def _parse_mmap(
    mm: mmap.mmap,
    *,
    file_path: str,
    file_size: int,
    t0: float,
    max_text_labels: int,
    max_properties: int,
    max_cells: int,
) -> GdsMetadata:
    """Parse using mmap. The hot loop uses only integer indexing into
    the mmap object - no allocations, no method calls in the skip path."""

    meta = GdsMetadata(file_path=file_path, file_size_bytes=file_size)
    layers: set[int] = set()
    ecounts: dict[str, int] = defaultdict(int)

    cell_name: str | None = None
    cell_mod: datetime | None = None
    cell_acc: datetime | None = None
    in_text = False
    text_layer: int | None = None
    text_type: int | None = None
    prop_attr: int | None = None
    n_text = 0
    n_prop = 0
    n_cell = 0

    # Local refs for hot path
    is_meta = _IS_METADATA
    elem_key = _ELEM_COUNTER_KEY
    RT = RecordType
    data = mm
    end = file_size
    pos = 0

    while pos + 4 <= end:
        # Read record header: 2 bytes length, 1 byte type, 1 byte datatype
        rec_len = (data[pos] << 8) | data[pos + 1]
        rec_type = data[pos + 2]
        payload_len = rec_len - 4
        p_start = pos + 4
        p_end = pos + rec_len

        if p_end > end:
            break

        # Skip non-metadata records (just advance pointer - no I/O)
        if not is_meta[rec_type]:
            pos = p_end
            continue

        pos = p_end  # advance past this record

        # --- Metadata dispatch ---

        if rec_type == RT.HEADER:
            if payload_len >= 2:
                meta.gds_version = _signed16(data[p_start], data[p_start + 1])

        elif rec_type == RT.BGNLIB:
            if payload_len >= 24:
                meta.library_modified, meta.library_accessed = _parse_dates(data, p_start)

        elif rec_type == RT.LIBNAME:
            meta.library_name = _decode_ascii(data, p_start, p_end)

        elif rec_type == RT.UNITS:
            if payload_len >= 16:
                meta.units_dbu_in_user = _gds_real8(data, p_start)
                meta.units_dbu_in_meters = _gds_real8(data, p_start + 8)

        elif rec_type == RT.BGNSTR:
            if payload_len >= 24:
                cell_mod, cell_acc = _parse_dates(data, p_start)

        elif rec_type == RT.STRNAME:
            cell_name = _decode_ascii(data, p_start, p_end)
            if n_cell < max_cells:
                meta.cells.append(CellInfo(
                    name=cell_name, modified=cell_mod, accessed=cell_acc))
            n_cell += 1

        elif rec_type == RT.ENDSTR:
            cell_name = None

        elif rec_type == RT.TEXT:
            ecounts["text"] += 1
            in_text = True
            text_layer = None
            text_type = None

        elif rec_type == RT.LAYER:
            if payload_len >= 2:
                layer = _signed16(data[p_start], data[p_start + 1])
                layers.add(layer)
                if in_text:
                    text_layer = layer

        elif rec_type == RT.TEXTTYPE:
            if payload_len >= 2:
                text_type = _signed16(data[p_start], data[p_start + 1])

        elif rec_type == RT.STRING:
            if n_text < max_text_labels:
                meta.text_labels.append(TextLabel(
                    text=_decode_ascii(data, p_start, p_end),
                    layer=text_layer, texttype=text_type, cell=cell_name))
            n_text += 1

        elif rec_type == RT.PROPATTR:
            if payload_len >= 2:
                prop_attr = _signed16(data[p_start], data[p_start + 1])

        elif rec_type == RT.PROPVALUE:
            if n_prop < max_properties:
                meta.properties.append(Property(
                    attr=prop_attr,
                    value=_decode_ascii(data, p_start, p_end),
                    cell=cell_name))
            n_prop += 1

        elif rec_type == RT.REFLIBS:
            meta.reflibs = _decode_ascii(data, p_start, p_end)

        elif rec_type == RT.FONTS:
            meta.fonts = _decode_ascii(data, p_start, p_end)

        elif rec_type == RT.GENERATIONS:
            if payload_len >= 2:
                meta.generations = _signed16(data[p_start], data[p_start + 1])

        elif rec_type == RT.ATTRTABLE:
            meta.attrtable = _decode_ascii(data, p_start, p_end)

        elif rec_type == RT.FORMAT:
            if payload_len >= 2:
                meta.format_type = (data[p_start] << 8) | data[p_start + 1]

        elif rec_type == RT.MASK:
            meta.masks.append(_decode_ascii(data, p_start, p_end))

        elif rec_type == RT.ENDEL:
            in_text = False

        elif rec_type == RT.ENDLIB:
            break

        else:
            key = elem_key.get(rec_type)
            if key:
                ecounts[key] += 1
                in_text = False

    meta.layers_used = sorted(layers)
    meta.element_counts = ElementCounts(**ecounts)
    meta.tool_inference = _infer_tool(meta)
    meta.parse_time_seconds = round(time.monotonic() - t0, 4)
    return meta


def _parse_stream(
    fh: BinaryIO,
    *,
    file_path: str,
    file_size: int,
    t0: float,
    max_text_labels: int,
    max_properties: int,
    max_cells: int,
) -> GdsMetadata:
    """Fallback parser for non-seekable streams (e.g. network downloads).
    Uses buffered read + seek to skip geometry payloads."""

    meta = GdsMetadata(file_path=file_path, file_size_bytes=file_size)
    layers: set[int] = set()
    ecounts: dict[str, int] = defaultdict(int)

    cell_name: str | None = None
    cell_mod: datetime | None = None
    cell_acc: datetime | None = None
    in_text = False
    text_layer: int | None = None
    text_type: int | None = None
    prop_attr: int | None = None
    n_text = 0
    n_prop = 0
    n_cell = 0

    is_meta = _IS_METADATA
    elem_key = _ELEM_COUNTER_KEY
    RT = RecordType
    header_buf = bytearray(4)

    while True:
        n = fh.readinto(header_buf)
        if n is None or n < 4:
            break

        rec_len = (header_buf[0] << 8) | header_buf[1]
        rec_type = header_buf[2]
        payload_len = rec_len - 4

        if not is_meta[rec_type]:
            if payload_len > 0:
                fh.seek(payload_len, 1)
            continue

        if payload_len > 0:
            data = fh.read(payload_len)
            if len(data) < payload_len:
                break
        else:
            data = b""

        if rec_type == RT.HEADER:
            if payload_len >= 2:
                meta.gds_version = _signed16(data[0], data[1])
        elif rec_type == RT.BGNLIB:
            if payload_len >= 24:
                meta.library_modified, meta.library_accessed = _parse_dates(data, 0)
        elif rec_type == RT.LIBNAME:
            meta.library_name = _decode_ascii(data, 0, len(data))
        elif rec_type == RT.UNITS:
            if payload_len >= 16:
                meta.units_dbu_in_user = _gds_real8(data, 0)
                meta.units_dbu_in_meters = _gds_real8(data, 8)
        elif rec_type == RT.BGNSTR:
            if payload_len >= 24:
                cell_mod, cell_acc = _parse_dates(data, 0)
        elif rec_type == RT.STRNAME:
            cell_name = _decode_ascii(data, 0, len(data))
            if n_cell < max_cells:
                meta.cells.append(CellInfo(name=cell_name, modified=cell_mod, accessed=cell_acc))
            n_cell += 1
        elif rec_type == RT.ENDSTR:
            cell_name = None
        elif rec_type == RT.TEXT:
            ecounts["text"] += 1
            in_text = True
            text_layer = None
            text_type = None
        elif rec_type == RT.LAYER:
            if payload_len >= 2:
                layer = _signed16(data[0], data[1])
                layers.add(layer)
                if in_text:
                    text_layer = layer
        elif rec_type == RT.TEXTTYPE:
            if payload_len >= 2:
                text_type = _signed16(data[0], data[1])
        elif rec_type == RT.STRING:
            if n_text < max_text_labels:
                meta.text_labels.append(TextLabel(
                    text=_decode_ascii(data, 0, len(data)),
                    layer=text_layer, texttype=text_type, cell=cell_name))
            n_text += 1
        elif rec_type == RT.PROPATTR:
            if payload_len >= 2:
                prop_attr = _signed16(data[0], data[1])
        elif rec_type == RT.PROPVALUE:
            if n_prop < max_properties:
                meta.properties.append(Property(
                    attr=prop_attr, value=_decode_ascii(data, 0, len(data)),
                    cell=cell_name))
            n_prop += 1
        elif rec_type == RT.REFLIBS:
            meta.reflibs = _decode_ascii(data, 0, len(data))
        elif rec_type == RT.FONTS:
            meta.fonts = _decode_ascii(data, 0, len(data))
        elif rec_type == RT.GENERATIONS:
            if payload_len >= 2:
                meta.generations = _signed16(data[0], data[1])
        elif rec_type == RT.ATTRTABLE:
            meta.attrtable = _decode_ascii(data, 0, len(data))
        elif rec_type == RT.FORMAT:
            if payload_len >= 2:
                meta.format_type = (data[0] << 8) | data[1]
        elif rec_type == RT.MASK:
            meta.masks.append(_decode_ascii(data, 0, len(data)))
        elif rec_type == RT.ENDEL:
            in_text = False
        elif rec_type == RT.ENDLIB:
            break
        else:
            key = elem_key.get(rec_type)
            if key:
                ecounts[key] += 1
                in_text = False

    meta.layers_used = sorted(layers)
    meta.element_counts = ElementCounts(**ecounts)
    meta.tool_inference = _infer_tool(meta)
    meta.parse_time_seconds = round(time.monotonic() - t0, 4)
    return meta
