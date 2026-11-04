"""
Reader for EA's embedded "DB" container format found inside FC25 ManagerCareer save files
and inside the shipped game database (fifa_ng_db.db). Same magic bytes and directory layout
in both places.

Container layout:

    0   magic "DB\\x00\\x08\\x00\\x00\\x00\\x00"
    8   declared size (u64)
    16  table count (u32)
    20  checksum/hash (u32)
    24  directory: `table count` entries of [shortname tag (4 bytes ASCII)][offset (u32)]
    ..  table regions, at `data_base + offset`, where data_base = 24 + 8 * table count

The offsets are exact, not approximate: for all three containers available to this project
(the 242-table fifa_ng_db.db and both containers in a real save) every declared offset lands
8 bytes before the corresponding table's header, and the tag on each directory entry matches
the field set of the table found there -- 313 of 313. An earlier version of this module read
the directory as [offset][tag] pairs starting at 20, which paired every tag with its
*predecessor's* offset and made the first offset pure garbage; that is where this project's
long-standing "the declared offsets are only a rough hint" belief came from. They aren't.
"""
import struct
from typing import Dict, List, Tuple

DB_MAGIC = b"\x44\x42\x00\x08\x00\x00\x00\x00"

_DIRECTORY_START = 24
_DIRECTORY_ENTRY_SIZE = 8

# Distance from a table's declared directory offset to the start of its 32-byte header.
# Constant across every container inspected; what lives in those 8 bytes is not yet known.
TABLE_HEADER_LEAD = 8


class DBContainer:
    def __init__(self, data: bytes):
        if data[0:8] != DB_MAGIC:
            raise ValueError("not a DB container (bad magic bytes)")
        self.data = data
        self.declared_size = struct.unpack("<Q", data[8:16])[0]
        self.table_count = struct.unpack("<I", data[16:20])[0]
        self.checksum = struct.unpack("<I", data[20:24])[0]
        self.entries: List[Tuple[str, int]] = []  # (shortname tag, declared offset)
        for i in range(self.table_count):
            pos = _DIRECTORY_START + i * _DIRECTORY_ENTRY_SIZE
            tag = data[pos:pos + 4]
            offset = struct.unpack("<I", data[pos + 4:pos + 8])[0]
            try:
                tag_str = tag.decode("ascii")
            except UnicodeDecodeError:
                continue
            self.entries.append((tag_str, offset))
        self.data_base = _DIRECTORY_START + self.table_count * _DIRECTORY_ENTRY_SIZE

    @property
    def offsets_by_tag(self) -> Dict[str, int]:
        """shortname tag -> absolute offset of that table's header within this container."""
        return {tag: self.data_base + off + TABLE_HEADER_LEAD for tag, off in self.entries}

    def table_ranges(self):
        """Yield (tag_str, header_start, next_header_start) per directory entry, in file
        order. The end bound is the next table's header, which is an upper bound on this
        table's extent (rows + string pool), not its exact size -- read the row count and
        stride from the table's own header for that."""
        ordered = sorted(self.entries, key=lambda e: e[1])
        for i, (tag, off) in enumerate(ordered):
            start = self.data_base + off + TABLE_HEADER_LEAD
            end = (self.data_base + ordered[i + 1][1] + TABLE_HEADER_LEAD
                   if i + 1 < len(ordered) else len(self.data))
            yield tag, start, end


def find_db_containers(blob: bytes):
    """Locate and slice out every embedded DB container in a larger blob (e.g. a raw save
    file). Returns a list of bytes objects, one per container, in file order."""
    offsets = []
    start = 0
    while True:
        idx = blob.find(DB_MAGIC, start)
        if idx == -1:
            break
        offsets.append(idx)
        start = idx + 1
    containers = []
    for i, off in enumerate(offsets):
        end = offsets[i + 1] if i + 1 < len(offsets) else len(blob)
        containers.append(blob[off:end])
    return containers
