import struct

import pytest

from manager_mode_buddy.parser.db_container import (
    DB_MAGIC,
    TABLE_HEADER_LEAD,
    DBContainer,
    find_db_containers,
)


def _build_container(tables: list[tuple[bytes, int]], payload: bytes) -> bytes:
    """Build a container whose directory is [tag][offset] pairs starting at byte 24, which is
    the real on-disk layout -- see db_container.py for how the earlier [offset][tag]-from-20
    reading silently shifted every offset by one entry."""
    header = DB_MAGIC
    header += struct.pack("<Q", 0)  # declared_size, unused by our reader
    header += struct.pack("<I", len(tables))
    header += struct.pack("<I", 0)  # checksum
    for tag, offset in tables:
        header += tag + struct.pack("<I", offset)
    return header + payload


def test_parses_table_directory():
    data = _build_container([(b"AAAA", 0), (b"BBBB", 10)], payload=b"\x00" * 40)
    container = DBContainer(data)
    assert container.table_count == 2
    assert container.entries == [("AAAA", 0), ("BBBB", 10)]
    assert container.data_base == 24 + 2 * 8


def test_offsets_by_tag_points_at_the_table_header():
    data = _build_container([(b"AAAA", 0), (b"BBBB", 10)], payload=b"\x00" * 40)
    container = DBContainer(data)
    assert container.offsets_by_tag["AAAA"] == container.data_base + TABLE_HEADER_LEAD
    assert container.offsets_by_tag["BBBB"] == container.data_base + 10 + TABLE_HEADER_LEAD


def test_table_ranges_are_contiguous():
    data = _build_container([(b"AAAA", 0), (b"BBBB", 10)], payload=b"\x00" * 40)
    ranges = list(DBContainer(data).table_ranges())
    assert [r[0] for r in ranges] == ["AAAA", "BBBB"]
    assert ranges[0][2] == ranges[1][1]


def test_rejects_bad_magic():
    with pytest.raises(ValueError):
        DBContainer(b"not a db container" + b"\x00" * 20)


def test_find_db_containers_splits_multiple_blobs():
    c1 = _build_container([(b"AAAA", 0)], payload=b"\x11" * 8)
    c2 = _build_container([(b"BBBB", 0)], payload=b"\x22" * 8)
    blob = b"junk-prefix" + c1 + c2
    found = find_db_containers(blob)
    assert len(found) == 2
    assert found[0].startswith(DB_MAGIC)
    assert found[1].startswith(DB_MAGIC)
