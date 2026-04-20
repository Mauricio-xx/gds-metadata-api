# gds-metadata-api

Fast GDSII layout metadata extraction. Reads binary GDS records directly --
skips all geometry, extracts only metadata. Handles multi-GB files in seconds.

## What it extracts

- Library name, creation/modification dates
- GDS version, database units (DBU)
- Cell hierarchy with per-cell timestamps
- TEXT labels (PDK version, device parameters, annotations)
- Element properties (PCell parameters)
- Layer list, element counts (boundary, path, sref, aref, text, box)
- EDA tool inference (which layout tool was used)

## Install

Requirements: Python >= 3.10, a C compiler (gcc/clang) for the fast scanner.

```bash
git clone https://github.com/Mauricio-xx/gds-metadata-api.git
cd gds-metadata-api
python3 -m venv .venv
source .venv/bin/activate
pip install .
```

The install compiles a Cython extension for the scanning hot-loop
(~2 GB/s throughput). If compilation fails (no C compiler, no Cython),
it falls back to pure Python (~25 MB/s) automatically.

### Verify the install

```bash
gds-metadata-api extract --pretty /path/to/some/layout.gds
```

## Usage

### CLI -- extract metadata from a local file

```bash
gds-metadata-api extract /path/to/layout.gds
gds-metadata-api extract --pretty /path/to/layout.gds
```

### CLI -- extract from a GitHub URL

```bash
gds-metadata-api extract "https://github.com/owner/repo/blob/main/design.gds"
```

### API server

```bash
gds-metadata-api serve --port 8042
```

Then query it:

```bash
# Local file
curl -X POST http://localhost:8042/extract \
  -H "Content-Type: application/json" \
  -d '{"source": "/path/to/layout.gds"}'

# GitHub URL
curl -X POST http://localhost:8042/extract \
  -H "Content-Type: application/json" \
  -d '{"source": "https://github.com/owner/repo/blob/main/design.gds"}'
```

Interactive docs at `http://localhost:8042/docs`.

### Python API

```python
from gds_metadata.parser import parse_gds_metadata

meta = parse_gds_metadata("/path/to/layout.gds")

print(meta.library_name)
print(meta.library_modified)
print(meta.units_dbu_in_user)
print(meta.gds_version)

for cell in meta.cells:
    print(f"{cell.name} -- modified {cell.modified}")

for label in meta.text_labels:
    print(f"L{label.layer}: {label.text}")

print(meta.model_dump_json(indent=2))
```

## Output example

```json
{
  "file_path": "/tmp/layout.gds",
  "file_size_bytes": 14166200,
  "gds_version": 600,
  "library_name": "LIB",
  "library_modified": "2025-09-01T15:12:47",
  "units_dbu_in_user": 0.001,
  "units_dbu_in_meters": 1e-9,
  "layers_used": [1, 5, 6, 7, 8, 10, 14, 19, 30, 40, 63, 134],
  "cells": [
    {"name": "npn13G2", "modified": "2025-09-01T15:12:47"}
  ],
  "text_labels": [
    {"text": "PDK version: 6bda9f1cd9ae...", "layer": 63, "cell": "top"},
    {"text": "Device registration size: x=700.0 um ; y=750.0 um", "layer": 63}
  ],
  "element_counts": {
    "boundary": 185432, "path": 0, "sref": 1200,
    "aref": 500, "text": 20, "box": 0, "node": 0
  },
  "tool_inference": {
    "tool": "KLayout", "confidence": "medium",
    "clues": ["Generic library name (common in KLayout)", "GDSII version 600"]
  },
  "parse_time_seconds": 0.006
}
```

## Performance

The Cython scanner uses `mmap` and compiled C for the record-scanning loop.
Geometry payloads (polygon coordinates, paths) are skipped at the pointer
level -- the OS never even pages those regions into RAM.

| File size | Cython (mmap) | Pure Python (mmap) |
|-----------|---------------|--------------------|
| 15 MB     | 6 ms          | 600 ms             |
| 250 MB    | 0.1 s         | 10 s               |
| 10 GB     | ~5 s          | ~6 min             |

Memory usage stays constant (~30 MB RSS) regardless of file size.

## Architecture

```
src/gds_metadata/
    _scanner.pyx    Cython hot-loop (mmap scan, compiled to C)
    parser.py       Orchestration, pure-Python fallback, tool inference
    models.py       Pydantic models (JSON serialization)
    sources.py      Input resolution (local paths, GitHub URLs)
    api.py          FastAPI endpoint
    cli.py          CLI (extract / serve)
    records.py      GDSII record type constants
```

## License

Apache 2.0 -- see [LICENSE](LICENSE).
