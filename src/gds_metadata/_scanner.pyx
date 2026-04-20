# cython: language_level=3, boundscheck=False, wraparound=False, cdivision=True
"""
Cython-accelerated GDSII record scanner.

Scans through a memory-mapped GDS file extracting only metadata records.
The hot loop runs as compiled C with typed variables, eliminating Python
interpreter overhead. Geometry payloads are skipped with a pointer advance.
"""

from libc.string cimport memcpy
from cpython.bytes cimport PyBytes_FromStringAndSize

import struct
from datetime import datetime


# Record types we need to process
cdef enum RecType:
    RT_HEADER   = 0x00
    RT_BGNLIB   = 0x01
    RT_LIBNAME  = 0x02
    RT_UNITS    = 0x03
    RT_ENDLIB   = 0x04
    RT_BGNSTR   = 0x05
    RT_STRNAME  = 0x06
    RT_ENDSTR   = 0x07
    RT_BOUNDARY = 0x08
    RT_PATH     = 0x09
    RT_SREF     = 0x0A
    RT_AREF     = 0x0B
    RT_TEXT     = 0x0C
    RT_LAYER    = 0x0D
    RT_TEXTTYPE = 0x16
    RT_STRING   = 0x19
    RT_REFLIBS  = 0x1F
    RT_FONTS    = 0x20
    RT_GENERATIONS = 0x22
    RT_ATTRTABLE = 0x23
    RT_PROPATTR = 0x2B
    RT_PROPVALUE = 0x2C
    RT_BOX      = 0x2D
    RT_NODE     = 0x15
    RT_ENDEL    = 0x11
    RT_FORMAT   = 0x36
    RT_MASK     = 0x37


cdef inline double gds_real8(const unsigned char *d) noexcept nogil:
    """Convert GDSII 8-byte real to double."""
    cdef double sign = -1.0 if (d[0] & 0x80) else 1.0
    cdef int exp = (d[0] & 0x7F) - 64
    cdef unsigned long long mantissa = 0
    cdef int i
    for i in range(1, 8):
        mantissa = mantissa * 256 + d[i]
    return sign * <double>mantissa * (16.0 ** (exp - 14))


cdef inline int signed16(unsigned char hi, unsigned char lo) noexcept nogil:
    """Big-endian signed int16 from two bytes."""
    cdef int v = (hi << 8) | lo
    if v >= 0x8000:
        v -= 0x10000
    return v


# Metadata record lookup table
cdef unsigned char _is_metadata[256]

cdef void _init_metadata_table():
    cdef int i
    for i in range(256):
        _is_metadata[i] = 0
    # All records we need to read payload for
    cdef int meta_types[28]
    meta_types = [
        0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07,
        0x08, 0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x11, 0x15,
        0x16, 0x19, 0x1F, 0x20, 0x22, 0x23, 0x2B, 0x2C,
        0x2D, 0x36, 0x37, 0x38
    ]
    for i in range(28):
        _is_metadata[meta_types[i]] = 1

_init_metadata_table()


def scan_mmap(const unsigned char[::1] data not None):
    """Scan a memory-mapped GDSII file and return raw metadata events.

    Args:
        data: A contiguous buffer (mmap or bytes) of the GDSII file.

    Returns:
        dict with extracted metadata fields. Text labels, cells, etc.
        are returned as lists of tuples for the Python layer to wrap
        in Pydantic models.
    """
    cdef Py_ssize_t size = data.shape[0]
    cdef Py_ssize_t pos = 0
    cdef unsigned int rec_len
    cdef unsigned char rec_type
    cdef int payload_len
    cdef Py_ssize_t p_start, p_end

    # Results
    cdef int gds_version = -1
    cdef double dbu_user = 0.0, dbu_meters = 0.0
    cdef int has_units = 0

    # State
    cdef int in_text = 0
    cdef int text_layer = -1
    cdef int text_type = -1
    cdef int prop_attr = -1
    cdef int layer
    cdef int n_text = 0, n_prop = 0, n_cell = 0

    # Element counters
    cdef long cnt_boundary = 0, cnt_path = 0, cnt_sref = 0
    cdef long cnt_aref = 0, cnt_text = 0, cnt_box = 0, cnt_node = 0

    # Python objects for variable-size data
    lib_name = None
    lib_mod = None
    lib_acc = None
    reflibs = None
    fonts = None
    generations = None
    attrtable = None
    format_type = None
    masks_list = []

    cells = []  # list of (name, mod_date, acc_date)
    text_labels = []  # list of (text, layer, texttype, cell_name)
    properties = []  # list of (attr, value, cell_name)
    layers_set = set()

    cell_name = None
    cell_mod = None
    cell_acc = None

    cdef const unsigned char *ptr

    while pos + 4 <= size:
        ptr = &data[pos]
        rec_len = (ptr[0] << 8) | ptr[1]
        rec_type = ptr[2]
        payload_len = <int>rec_len - 4
        p_start = pos + 4
        p_end = pos + rec_len

        if p_end > size:
            break

        # Fast skip for non-metadata records
        if _is_metadata[rec_type] == 0:
            pos = p_end
            continue

        pos = p_end

        # --- Dispatch (most hot-path ops stay in C) ---

        if rec_type == RT_HEADER:
            if payload_len >= 2:
                gds_version = signed16(data[p_start], data[p_start + 1])

        elif rec_type == RT_BGNLIB:
            if payload_len >= 24:
                lib_mod, lib_acc = _extract_dates(data, p_start)

        elif rec_type == RT_LIBNAME:
            lib_name = _extract_ascii(data, p_start, p_end)

        elif rec_type == RT_UNITS:
            if payload_len >= 16:
                dbu_user = gds_real8(&data[p_start])
                dbu_meters = gds_real8(&data[p_start + 8])
                has_units = 1

        elif rec_type == RT_BGNSTR:
            if payload_len >= 24:
                cell_mod, cell_acc = _extract_dates(data, p_start)

        elif rec_type == RT_STRNAME:
            cell_name = _extract_ascii(data, p_start, p_end)
            cells.append((cell_name, cell_mod, cell_acc))
            n_cell += 1

        elif rec_type == RT_ENDSTR:
            cell_name = None

        elif rec_type == RT_TEXT:
            cnt_text += 1
            in_text = 1
            text_layer = -1
            text_type = -1

        elif rec_type == RT_LAYER:
            if payload_len >= 2:
                layer = signed16(data[p_start], data[p_start + 1])
                layers_set.add(layer)
                if in_text:
                    text_layer = layer

        elif rec_type == RT_TEXTTYPE:
            if payload_len >= 2:
                text_type = signed16(data[p_start], data[p_start + 1])

        elif rec_type == RT_STRING:
            text_labels.append((
                _extract_ascii(data, p_start, p_end),
                text_layer if text_layer >= 0 else None,
                text_type if text_type >= 0 else None,
                cell_name,
            ))
            n_text += 1

        elif rec_type == RT_PROPATTR:
            if payload_len >= 2:
                prop_attr = signed16(data[p_start], data[p_start + 1])

        elif rec_type == RT_PROPVALUE:
            properties.append((
                prop_attr if prop_attr >= 0 else None,
                _extract_ascii(data, p_start, p_end),
                cell_name,
            ))
            n_prop += 1

        elif rec_type == RT_REFLIBS:
            reflibs = _extract_ascii(data, p_start, p_end)

        elif rec_type == RT_FONTS:
            fonts = _extract_ascii(data, p_start, p_end)

        elif rec_type == RT_GENERATIONS:
            if payload_len >= 2:
                generations = signed16(data[p_start], data[p_start + 1])

        elif rec_type == RT_ATTRTABLE:
            attrtable = _extract_ascii(data, p_start, p_end)

        elif rec_type == RT_FORMAT:
            if payload_len >= 2:
                format_type = (data[p_start] << 8) | data[p_start + 1]

        elif rec_type == RT_MASK:
            masks_list.append(_extract_ascii(data, p_start, p_end))

        elif rec_type == RT_ENDEL:
            in_text = 0

        elif rec_type == RT_ENDLIB:
            break

        elif rec_type == RT_BOUNDARY:
            cnt_boundary += 1
            in_text = 0
        elif rec_type == RT_PATH:
            cnt_path += 1
            in_text = 0
        elif rec_type == RT_SREF:
            cnt_sref += 1
            in_text = 0
        elif rec_type == RT_AREF:
            cnt_aref += 1
            in_text = 0
        elif rec_type == RT_BOX:
            cnt_box += 1
            in_text = 0
        elif rec_type == RT_NODE:
            cnt_node += 1
            in_text = 0

    return {
        "gds_version": gds_version if gds_version >= 0 else None,
        "library_name": lib_name,
        "library_modified": lib_mod,
        "library_accessed": lib_acc,
        "units_dbu_in_user": dbu_user if has_units else None,
        "units_dbu_in_meters": dbu_meters if has_units else None,
        "format_type": format_type,
        "masks": masks_list,
        "reflibs": reflibs,
        "fonts": fonts,
        "generations": generations,
        "attrtable": attrtable,
        "layers_used": sorted(layers_set),
        "cells": cells,
        "text_labels": text_labels,
        "properties": properties,
        "element_counts": {
            "boundary": cnt_boundary,
            "path": cnt_path,
            "sref": cnt_sref,
            "aref": cnt_aref,
            "text": cnt_text,
            "box": cnt_box,
            "node": cnt_node,
        },
    }


cdef _extract_ascii(const unsigned char[::1] data, Py_ssize_t start, Py_ssize_t end):
    """Extract ASCII string from data[start:end], stripping null padding."""
    while end > start and data[end - 1] == 0:
        end -= 1
    return bytes(data[start:end]).decode("ascii", errors="replace").strip()


cdef _extract_dates(const unsigned char[::1] data, Py_ssize_t offset):
    """Extract two GDSII dates from 24 bytes at offset."""
    cdef int vals[12]
    cdef int i
    for i in range(12):
        vals[i] = signed16(data[offset + i*2], data[offset + i*2 + 1])
    dates = []
    for i in (0, 6):
        try:
            dates.append(datetime(vals[i], vals[i+1], vals[i+2],
                                  vals[i+3], vals[i+4], vals[i+5]))
        except (ValueError, OverflowError):
            dates.append(None)
    return dates[0], dates[1]
