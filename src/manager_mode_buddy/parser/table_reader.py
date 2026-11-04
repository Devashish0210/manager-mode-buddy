"""
Reader for FC25's self-describing table format, as found inside ManagerCareer save files and,
in identical form, inside the shipped fifa_ng_db.db.

The format, in full (all of it verified against both files -- see HANDOFF.md):

A table region begins with a 32-byte header, immediately followed by its inline schema: a run
of one 16-byte descriptor per field, then the rows, then (if the table has packed strings) a
string pool.

    header, at desc_start - 32:
        -32  record stride in bytes (u32)
        -28  record size in bits, minus 1 (u32)   -- always stride * 8 - 1; a free checksum
        -24  string pool size in bytes (u32)      -- 0 unless the table has packed strings
        -20  row capacity (u16)  |  rows written (u16)
        -16  unknown (two u16s)
        -12  field count (u32), sometimes with 0x100 or'd in (meaning not yet known)
        -8   zero
        -4   checksum/hash (u32)

    field descriptor, 16 bytes:
        0   typecode (u32)
        4   bit offset of this field within the record (u32)
        8   shortname tag (4 bytes ASCII)
        12  bit depth (u32)

    typecodes:
        0   string, stored raw inline in the record (depth bits of UTF-8, NUL-padded)
        3   integer -- and also DBOFIELDTYPE_DATE, which is a plain day count (see dates.py)
        4   real (IEEE-754 float32, always depth 32)
        13  string, stored Huffman-packed in the table's string pool

Two things about this differ from what earlier sessions of this project believed, and both
mattered:

1. The descriptor was read 4 bytes out of phase, as [tag][depth][typecode][cumulative bits].
   That framing is a rotation of the real one, and it *almost* works: reading at +8 yields
   tag and depth correctly, then picks up the NEXT descriptor's typecode and bit offset. Since
   a following field's bit offset equals this field's end whenever fields are stored in bit
   order, "cumulative bits after this field" was a coherent-looking fiction. It broke wherever
   fields are NOT in bit order (common -- `career_firstnames` stores its 3rd descriptor at bit
   0), and it hid the header, which is why table location needed heuristics at all.

2. Because the header was invisible, tables were located by scanning for field signatures and
   scoring overlap, with a `max_size_ratio` guard that structurally could not match a
   small table (its backward walk ran into the neighbouring table's descriptors). With the
   header visible, every table is found exactly where the container directory says it is:
   242/242 in fifa_ng_db.db and 71/71 across a real save's two containers, each confirming
   itself via the stride/bits identity above and the field-count word.

So there is no candidate scoring, no stride calibration and no merging of rival candidates
here any more: the container tells us where the table is and the table tells us how big its
rows are.
"""
import struct
from dataclasses import dataclass
from typing import Dict, List, Optional

from .db_container import DBContainer
from .meta_schema import FieldDef, TableDef

DESCRIPTOR_SIZE = 16

TYPE_RAW_STRING = 0
TYPE_INTEGER = 3
TYPE_REAL = 4
TYPE_PACKED_STRING = 13

_NULL_STRING_OFFSET = 0xFFFFFFFF
_FIELD_COUNT_FLAG = 0x100
_MAX_DEPTH = 4096


def _is_tag(b4: bytes) -> bool:
    return len(b4) == 4 and all(65 <= c <= 90 or 97 <= c <= 122 for c in b4)


@dataclass(frozen=True)
class SchemaEntry:
    """One field descriptor, as stored inline in the table."""
    typecode: int
    bitstart: int
    tag: str
    depth: int


@dataclass
class TableInstance:
    """A located physical table: where its rows are, how big they are, how to read them."""
    tag: str
    entries: List[SchemaEntry]
    stride: int
    capacity: int
    written: int
    data_start: int
    pool_size: int

    @property
    def pool_start(self) -> int:
        """The string pool sits after the table's whole row area -- all `capacity` slots, not
        just the `written` ones. Confirmed on playernames, where it lands exactly on the
        Huffman tree; every table that has a pool happens to be full, so if one ever turns up
        with capacity > written and packed strings, check this before trusting it."""
        return self.data_start + self.capacity * self.stride


def parse_descriptor(data: bytes, pos: int) -> Optional[SchemaEntry]:
    """Read the 16-byte field descriptor at `pos`, or None if there isn't a plausible one."""
    if pos < 0 or pos + DESCRIPTOR_SIZE > len(data):
        return None
    typecode, bitstart = struct.unpack_from("<II", data, pos)
    tag = data[pos + 8:pos + 12]
    depth, = struct.unpack_from("<I", data, pos + 12)
    if not _is_tag(tag):
        return None
    if depth == 0 or depth > _MAX_DEPTH:
        return None
    if typecode > 64 or bitstart > (1 << 20):
        return None
    return SchemaEntry(typecode, bitstart, tag.decode("latin1"), depth)


def read_table_header(data: bytes, header_start: int, table: Optional[TableDef] = None
                       ) -> Optional[TableInstance]:
    """Parse the 32-byte header at `header_start` plus the descriptor run that follows it.

    Returns None unless the region validates: the descriptor run has to be as long as the
    header's field-count word says, the record-bits word has to equal stride * 8 - 1, and (if
    `table` is given) every descriptor tag has to be one of that table's declared fields."""
    if header_start < 0 or header_start + 32 > len(data):
        return None
    stride, record_bits, pool_size = struct.unpack_from("<III", data, header_start)
    capacity, written = struct.unpack_from("<HH", data, header_start + 12)
    declared_fields, = struct.unpack_from("<I", data, header_start + 20)

    if stride <= 0 or record_bits != stride * 8 - 1:
        return None

    desc_start = header_start + 32
    entries: List[SchemaEntry] = []
    while True:
        entry = parse_descriptor(data, desc_start + DESCRIPTOR_SIZE * len(entries))
        if entry is None:
            break
        entries.append(entry)
    if not entries:
        return None
    if declared_fields not in (len(entries), len(entries) + _FIELD_COUNT_FLAG):
        return None
    if table is not None and not {e.tag for e in entries} <= table.shortname_set:
        return None

    data_start = desc_start + DESCRIPTOR_SIZE * len(entries)
    if data_start + capacity * stride > len(data):
        return None

    return TableInstance(
        tag=table.shortname if table else "",
        entries=entries,
        stride=stride,
        capacity=capacity,
        written=written,
        data_start=data_start,
        pool_size=pool_size,
    )


def find_table(data: bytes, table: TableDef) -> Optional[TableInstance]:
    """Locate `table` in a DB container blob using the container's own directory."""
    try:
        container = DBContainer(data)
    except ValueError:
        return None
    header_start = container.offsets_by_tag.get(table.shortname)
    if header_start is None:
        return None
    instance = read_table_header(data, header_start, table)
    if instance is not None:
        instance.tag = table.shortname
    return instance


# --- Huffman string pool ------------------------------------------------------------------
#
# A table with packed strings keeps one pool after its rows: a canonical Huffman tree followed
# by the packed bytes. A string field's value in a row is a u32 byte offset into that pool
# (0xFFFFFFFF meaning null), pointing at a length byte followed by MSB-first Huffman codes.
# The length is in *bytes of decoded UTF-8*, which is what terminates decoding -- there is no
# end-of-string symbol.
#
# The tree is an array of u16 slots, two per node: node n's children are slots 2n and 2n+1.
# A slot whose low byte is zero (and which isn't zero itself) is a leaf carrying the output
# byte in its high half; anything else is the index of the next node. The tree's length isn't
# stored, so it's recovered by walking it -- the highest node index reached is the last one.

class StringPool:
    def __init__(self, data: bytes, pool_start: int, pool_size: int):
        self.data = data
        self.start = pool_start
        limit = min(len(data), pool_start + pool_size) if pool_size else len(data)
        self.slots = [struct.unpack_from("<H", data, pool_start + 2 * i)[0]
                       for i in range((limit - pool_start) // 2)]
        self.tree_slots = (self._max_node() + 1) * 2

    def _max_node(self) -> int:
        max_node, pending, seen = 0, [0], set()
        while pending:
            node = pending.pop()
            if node in seen or 2 * node + 1 >= len(self.slots):
                continue
            seen.add(node)
            max_node = max(max_node, node)
            for bit in (0, 1):
                slot = self.slots[2 * node + bit]
                if (slot & 0xFF) == 0 and slot != 0:
                    continue  # leaf
                pending.append(slot)
        return max_node

    def read(self, offset: int) -> Optional[str]:
        if offset == _NULL_STRING_OFFSET:
            return None
        pos = self.start + offset
        if pos >= len(self.data):
            return None
        length = self.data[pos]
        pos += 1
        out = bytearray()
        node, bit = 0, 0
        while len(out) < length:
            byte_index = pos + bit // 8
            if byte_index >= len(self.data):
                break
            slot = self.slots[2 * node + ((self.data[byte_index] >> (7 - bit % 8)) & 1)]
            bit += 1
            if (slot & 0xFF) == 0 and slot != 0:
                out.append(slot >> 8)
                node = 0
            else:
                node = slot
        return out.decode("utf-8", "replace")


# --- record decoding ----------------------------------------------------------------------

def _extract_bits(record: bytes, bitstart: int, depth: int) -> int:
    byte_off = bitstart // 8
    chunk = record[byte_off:byte_off + (depth + 15) // 8 + 1]
    if not chunk:
        return 0
    return (int.from_bytes(chunk, "little") >> (bitstart % 8)) & ((1 << depth) - 1)


def _decode_field(data: bytes, offset: int, entry: SchemaEntry, fdef: FieldDef,
                   pool: Optional[StringPool]):
    if entry.typecode == TYPE_PACKED_STRING:
        if pool is None:
            return None
        pointer, = struct.unpack_from("<I", data, offset + entry.bitstart // 8)
        return pool.read(pointer)
    if entry.typecode == TYPE_RAW_STRING:
        start = offset + entry.bitstart // 8
        raw = data[start:start + entry.depth // 8]
        return raw.split(b"\x00")[0].decode("utf-8", "replace")
    if entry.typecode == TYPE_REAL and entry.depth == 32:
        return struct.unpack_from("<f", data, offset + entry.bitstart // 8)[0]
    record = data[offset:offset + entry.bitstart // 8 + (entry.depth + 15) // 8 + 1]
    return _extract_bits(record, entry.bitstart, entry.depth) + fdef.rangelow


def decode_record(data: bytes, offset: int, instance: TableInstance, table: TableDef,
                   pool: Optional[StringPool] = None,
                   fields: Optional[set] = None) -> Dict[str, object]:
    """Decode one record at `offset` into a {field_name: value} dict. Integer fields have
    their per-table `rangelow` from meta.xml applied (the on-disk value is the offset from
    it, so this is the difference between a real rating and a number that merely looks like
    one); string fields come back as `str`."""
    out: Dict[str, object] = {}
    for entry in instance.entries:
        fdef = table.field_by_shortname(entry.tag)
        if fdef is None:
            continue
        if fields is not None and fdef.name not in fields:
            continue
        out[fdef.name] = _decode_field(data, offset, entry, fdef, pool)
    return out


def read_rows(data: bytes, instance: TableInstance, table: TableDef,
               key_field_name: Optional[str] = None,
               fields: Optional[set] = None) -> List[Dict[str, object]]:
    """Decode the table's rows: the first `written` of its `capacity` slots.

    Reading to `capacity` and dropping rows that look empty is NOT equivalent, and this is the
    single most important thing to get right about this format. Slots past `written` are
    usually zero-filled, but they are not guaranteed to be: `career_playergrowthuserseason` in
    a real save declares 35 rows out of 100 slots and the other 65 hold stale data -- 63 of
    them with a plausible non-zero playerid, some with an in-range `overall`. Those are the
    "decode artifacts" this project spent a long time trying to screen out heuristically.
    They aren't decode artifacts; they are rows the table itself says aren't there.

    Zero-key rows are still skipped when a key field is named, as cheap defence, but on every
    table checked in a real save that now drops nothing: the first `written` slots are always
    populated."""
    pool = None
    if any(e.typecode == TYPE_PACKED_STRING for e in instance.entries):
        pool = StringPool(data, instance.pool_start, instance.pool_size)

    wanted = fields
    if wanted is not None and key_field_name:
        wanted = set(wanted) | {key_field_name}

    rows = []
    for i in range(min(instance.written, instance.capacity)):
        row = decode_record(data, instance.data_start + i * instance.stride, instance, table,
                             pool, wanted)
        if key_field_name and not row.get(key_field_name):
            continue
        rows.append(row)
    return rows


def read_table(data: bytes, table: TableDef, key_field_name: Optional[str] = "playerid",
                fields: Optional[set] = None) -> List[Dict[str, object]]:
    """Locate `table` in a DB container blob and decode its populated rows. Returns [] if the
    container doesn't carry that table at all (each save container holds a different subset,
    so that is the normal case, not an error)."""
    instance = find_table(data, table)
    if instance is None:
        return []
    if key_field_name and table.field_by_name(key_field_name) is None:
        key_field_name = None
    return read_rows(data, instance, table, key_field_name, fields)
