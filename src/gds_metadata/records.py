"""GDSII record type and data type constants.

Reference: GDSII Stream Format Manual, Release 6.0 (Calma/Cadence).
Only metadata-relevant records are documented here. Geometry records
(BOUNDARY, PATH, XY, etc.) are recognized so the parser can skip them
efficiently, but they carry no semantic weight in this module.
"""

from enum import IntEnum


class RecordType(IntEnum):
    """GDSII record type byte values."""

    HEADER = 0x00
    BGNLIB = 0x01
    LIBNAME = 0x02
    UNITS = 0x03
    ENDLIB = 0x04
    BGNSTR = 0x05
    STRNAME = 0x06
    ENDSTR = 0x07
    BOUNDARY = 0x08
    PATH = 0x09
    SREF = 0x0A
    AREF = 0x0B
    TEXT = 0x0C
    LAYER = 0x0D
    DATATYPE = 0x0E
    WIDTH = 0x0F
    XY = 0x10
    ENDEL = 0x11
    SNAME = 0x12
    COLROW = 0x13
    NODE = 0x15
    TEXTTYPE = 0x16
    PRESENTATION = 0x17
    STRING = 0x19
    STRANS = 0x1A
    MAG = 0x1B
    ANGLE = 0x1C
    REFLIBS = 0x1F
    FONTS = 0x20
    PATHTYPE = 0x21
    GENERATIONS = 0x22
    ATTRTABLE = 0x23
    PROPATTR = 0x2B
    PROPVALUE = 0x2C
    BOX = 0x2D
    BOXTYPE = 0x2E
    PLEX = 0x2F
    BGNEXTN = 0x30
    ENDEXTN = 0x31
    FORMAT = 0x36
    MASK = 0x37
    ENDMASKS = 0x38


class DataType(IntEnum):
    """GDSII data type byte values."""

    NONE = 0x00
    BITARRAY = 0x01
    INT16 = 0x02
    INT32 = 0x03
    REAL4 = 0x04
    REAL8 = 0x05
    ASCII = 0x06


# Records whose payload we need to read for metadata extraction.
# Everything else gets seeked past (zero-copy skip).
METADATA_RECORDS: frozenset[int] = frozenset({
    RecordType.HEADER,
    RecordType.BGNLIB,
    RecordType.LIBNAME,
    RecordType.UNITS,
    RecordType.BGNSTR,
    RecordType.STRNAME,
    RecordType.ENDSTR,
    RecordType.TEXT,
    RecordType.LAYER,
    RecordType.TEXTTYPE,
    RecordType.STRING,
    RecordType.PROPATTR,
    RecordType.PROPVALUE,
    RecordType.REFLIBS,
    RecordType.FONTS,
    RecordType.GENERATIONS,
    RecordType.ATTRTABLE,
    RecordType.FORMAT,
    RecordType.MASK,
    # Element type markers (for counting, 0-byte read)
    RecordType.BOUNDARY,
    RecordType.PATH,
    RecordType.SREF,
    RecordType.AREF,
    RecordType.BOX,
    RecordType.NODE,
    RecordType.ENDEL,
    RecordType.ENDLIB,
})

# Human-readable names for element types we count
ELEMENT_NAMES: dict[int, str] = {
    RecordType.BOUNDARY: "BOUNDARY",
    RecordType.PATH: "PATH",
    RecordType.SREF: "SREF",
    RecordType.AREF: "AREF",
    RecordType.TEXT: "TEXT",
    RecordType.BOX: "BOX",
    RecordType.NODE: "NODE",
}
