"""
Parses fifa_ng_db-meta.xml (the shipped game's own DB schema definitions) into an indexed
lookup by table name and by table shortname tag.

This file describes the schema of every table the game's DB engine can use at runtime,
including career-mode-only tables that hold no rows in the static shipped database but get
populated inside a save (e.g. career_playergrowthuserseason). It's the authoritative source
for field names, shortname tags, bit depths, and per-field rangelow/rangehigh -- don't
hand-derive these from raw bytes when this file already declares them.

This file is EA's own data, redistributed by third parties (not shipped with MMB). Users must
supply their own copy locally -- see the README for where to obtain one.
"""
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

_TABLE_RE = re.compile(r'<table\s+([^>]*?)>(.*?)</table>', re.S)
_FIELD_TAG_RE = re.compile(r'<field\s+([^/]+)/>')
_ATTR_RE = re.compile(r'(\w+)="([^"]*)"')


def _parse_attrs(s: str) -> Dict[str, str]:
    return dict(_ATTR_RE.findall(s))


@dataclass
class FieldDef:
    name: str
    shortname: str
    type: str
    depth: int
    rangelow: int
    rangehigh: int
    key: bool
    nullable: bool


@dataclass
class TableDef:
    name: str
    shortname: str
    fields: List[FieldDef] = field(default_factory=list)

    def field_by_shortname(self, tag: str) -> Optional[FieldDef]:
        for f in self.fields:
            if f.shortname == tag:
                return f
        return None

    def field_by_name(self, name: str) -> Optional[FieldDef]:
        for f in self.fields:
            if f.name == name:
                return f
        return None

    @property
    def shortname_set(self):
        return {f.shortname for f in self.fields}


def _parse_int(s, default=0):
    if s is None or s == "":
        return default
    return int(s)


def _parse_fields(body: str) -> List[FieldDef]:
    out = []
    for m in _FIELD_TAG_RE.finditer(body):
        attrs = _parse_attrs(m.group(1))
        out.append(FieldDef(
            name=attrs.get("name", ""),
            shortname=attrs.get("shortname", ""),
            type=attrs.get("type", ""),
            depth=_parse_int(attrs.get("depth"), 0),
            rangelow=_parse_int(attrs.get("rangelow"), 0),
            rangehigh=_parse_int(attrs.get("rangehigh"), 0),
            key=attrs.get("key", "False") == "True",
            nullable=attrs.get("null", "False") == "True",
        ))
    return out


class SchemaIndex:
    def __init__(self, xml_text: str):
        self.by_name: Dict[str, TableDef] = {}
        self.by_tag: Dict[str, TableDef] = {}
        for m in _TABLE_RE.finditer(xml_text):
            header_attrs = _parse_attrs(m.group(1))
            name = header_attrs.get("name")
            tag = header_attrs.get("shortname")
            if not name or not tag:
                continue
            fields = _parse_fields(m.group(2))
            t = TableDef(name=name, shortname=tag, fields=fields)
            self.by_name[name] = t
            self.by_tag[tag] = t

    @classmethod
    def from_file(cls, path: str) -> "SchemaIndex":
        with open(path, encoding="utf-8", errors="replace") as f:
            return cls(f.read())

    def tables_with_field(self, field_name: str) -> List[TableDef]:
        return [t for t in self.by_name.values() if t.field_by_name(field_name)]
