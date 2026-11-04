"""
These tests build a container in the *real* on-disk layout (see table_reader.py's module
docstring) rather than the rotated-by-4-bytes descriptor framing an earlier version of the
parser assumed. Two properties of the real format are what make the reader simple, and both
are asserted here: the table's own header states its stride and row capacity, and the
container's directory states where the table starts -- so nothing is guessed or scored.
"""
import struct

import pytest

from manager_mode_buddy.parser.db_container import DB_MAGIC, TABLE_HEADER_LEAD
from manager_mode_buddy.parser.meta_schema import FieldDef, TableDef
from manager_mode_buddy.parser.table_reader import (
    TYPE_INTEGER,
    TYPE_PACKED_STRING,
    find_table,
    read_table,
)

TABLE = TableDef(
    name="playernames",
    shortname="BGwe",
    fields=[
        FieldDef(name="nameid", shortname="FuiB", type="DBOFIELDTYPE_INTEGER",
                  depth=19, rangelow=0, rangehigh=500000, key=True, nullable=False),
        FieldDef(name="rating", shortname="UERs", type="DBOFIELDTYPE_INTEGER",
                  depth=7, rangelow=1, rangehigh=99, key=False, nullable=False),
        FieldDef(name="name", shortname="vIys", type="DBOFIELDTYPE_STRING",
                  depth=608, rangelow=0, rangehigh=0, key=False, nullable=False),
    ],
)

STRIDE = 8

# A four-slot Huffman tree over {A, B, C}: node 0 -> (leaf 'A', node 1), node 1 -> (leaf 'B',
# leaf 'C'). A leaf is a slot whose low byte is zero and whose high byte is the output byte.
# So A=0, B=10, C=11.
TREE = struct.pack("<HHHH", 0x4100, 0x0001, 0x4200, 0x4300)
# "CAB" -> 11 0 10, MSB-first, zero-padded to a byte; prefixed with its decoded length.
PACKED_CAB = bytes([3, 0b11010000])
# "A" -> 0
PACKED_A = bytes([1, 0b00000000])

CAB_OFFSET = len(TREE)
A_OFFSET = CAB_OFFSET + len(PACKED_CAB)
POOL = TREE + PACKED_CAB + PACKED_A


def _descriptors() -> bytes:
    """[typecode][bit offset][tag][depth] each. Deliberately declared out of bit order, and
    with the string first at bit 0, exactly as the real playernames table does -- a reader
    that infers bit offsets from descriptor order instead of reading them gets this wrong."""
    return (
        struct.pack("<II", TYPE_PACKED_STRING, 0) + b"vIys" + struct.pack("<I", 608)
        + struct.pack("<II", TYPE_INTEGER, 32) + b"FuiB" + struct.pack("<I", 19)
        + struct.pack("<II", TYPE_INTEGER, 51) + b"UERs" + struct.pack("<I", 7)
    )


def _record(string_offset: int, nameid: int, rating_true_value: int) -> bytes:
    packed = (nameid << 32) | ((rating_true_value - 1) << 51)  # rating rangelow = 1
    return struct.pack("<I", string_offset) + struct.pack("<I", packed >> 32)


def _build(rows: list[bytes], capacity: int, pool: bytes = POOL,
            stale: list[bytes] = ()) -> bytes:
    """`rows` are the table's declared rows; `stale` are leftovers sitting in slots past the
    declared count, which real tables do contain (see read_rows)."""
    descriptors = _descriptors()
    slack = capacity - len(rows) - len(stale)
    assert slack >= 0
    body = b"".join(rows) + b"".join(stale) + bytes(STRIDE * slack)
    header = struct.pack("<III", STRIDE, STRIDE * 8 - 1, len(pool))
    header += struct.pack("<HH", capacity, len(rows))
    header += struct.pack("<HH", 0, 0)  # unknown
    header += struct.pack("<I", len(TABLE.fields))
    header += struct.pack("<I", 0)
    header += struct.pack("<I", 0)  # checksum
    region = b"\x00" * TABLE_HEADER_LEAD + header + descriptors + body + pool

    directory = DB_MAGIC + struct.pack("<Q", 0) + struct.pack("<I", 1) + struct.pack("<I", 0)
    directory += b"BGwe" + struct.pack("<I", 0)
    return directory + region


def test_find_table_reads_stride_and_capacity_from_the_header():
    blob = _build([_record(CAB_OFFSET, 7, 80)], capacity=4)
    instance = find_table(blob, TABLE)
    assert instance is not None
    assert instance.stride == STRIDE
    assert instance.capacity == 4
    assert instance.written == 1
    assert [e.tag for e in instance.entries] == ["vIys", "FuiB", "UERs"]
    assert [e.bitstart for e in instance.entries] == [0, 32, 51]


def test_find_table_returns_none_when_the_container_lacks_the_table():
    blob = _build([_record(CAB_OFFSET, 7, 80)], capacity=4)
    other = TableDef(name="teams", shortname="lyxL", fields=TABLE.fields)
    assert find_table(blob, other) is None


def test_read_table_decodes_ints_with_rangelow_and_unpacks_strings():
    blob = _build([_record(CAB_OFFSET, 7, 80), _record(A_OFFSET, 9, 55)], capacity=4)
    rows = read_table(blob, TABLE, key_field_name="nameid")
    assert rows == [
        {"nameid": 7, "rating": 80, "name": "CAB"},
        {"nameid": 9, "rating": 55, "name": "A"},
    ]


def test_read_table_stops_at_the_written_row_count():
    """Unused slots are not reliably zeroed -- some tables leave stale, entirely plausible-
    looking rows past the end (see read_rows). The header's row count is what bounds the
    table, not "keep going until the data stops looking real"."""
    blob = _build([_record(CAB_OFFSET, 7, 80)], capacity=5,
                   stale=[_record(A_OFFSET, 999, 42), _record(A_OFFSET, 1000, 43)])
    rows = read_table(blob, TABLE, key_field_name="nameid")
    assert [r["nameid"] for r in rows] == [7]


def test_read_table_can_restrict_decoding_to_named_fields():
    blob = _build([_record(CAB_OFFSET, 7, 80)], capacity=4)
    rows = read_table(blob, TABLE, key_field_name="nameid", fields={"name"})
    assert rows == [{"nameid": 7, "name": "CAB"}]


def test_null_string_pointer_decodes_to_none():
    blob = _build([_record(0xFFFFFFFF, 7, 80)], capacity=4)
    rows = read_table(blob, TABLE, key_field_name="nameid")
    assert rows[0]["name"] is None


def test_header_is_rejected_when_stride_and_bit_count_disagree():
    blob = bytearray(_build([_record(CAB_OFFSET, 7, 80)], capacity=4))
    header = 24 + 8 + TABLE_HEADER_LEAD  # directory (one entry) + lead
    struct.pack_into("<I", blob, header + 4, STRIDE * 8)  # should be STRIDE * 8 - 1
    assert find_table(bytes(blob), TABLE) is None


@pytest.mark.parametrize("bad_field_count", [2, 4])
def test_header_is_rejected_when_the_field_count_disagrees(bad_field_count):
    blob = bytearray(_build([_record(CAB_OFFSET, 7, 80)], capacity=4))
    header = 24 + 8 + TABLE_HEADER_LEAD
    struct.pack_into("<I", blob, header + 20, bad_field_count)
    assert find_table(bytes(blob), TABLE) is None
