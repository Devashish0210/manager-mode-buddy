from manager_mode_buddy.parser.meta_schema import SchemaIndex

SAMPLE_XML = """
<database>
  <table name="players" shortname="CZUM">
    <field name="playerid" shortname="ykFq" type="DBOFIELDTYPE_INTEGER" depth="19" rangelow="0" rangehigh="500000" key="True"/>
    <field name="overallrating" shortname="UERs" type="DBOFIELDTYPE_INTEGER" depth="7" rangelow="1" rangehigh="99"/>
  </table>
  <table name="teams" shortname="lyxL">
    <field name="teamid" shortname="mCXg" type="DBOFIELDTYPE_INTEGER" depth="18" rangelow="1" rangehigh="200000" key="True"/>
  </table>
</database>
"""


def test_indexes_by_name_and_tag():
    schema = SchemaIndex(SAMPLE_XML)
    assert set(schema.by_name) == {"players", "teams"}
    assert schema.by_tag["CZUM"].name == "players"


def test_field_lookup_by_name_and_shortname():
    table = SchemaIndex(SAMPLE_XML).by_name["players"]
    f = table.field_by_name("overallrating")
    assert f.shortname == "UERs"
    assert f.rangelow == 1
    assert f.rangehigh == 99
    assert table.field_by_shortname("UERs") is f


def test_tables_with_field():
    schema = SchemaIndex(SAMPLE_XML)
    matches = schema.tables_with_field("teamid")
    assert [t.name for t in matches] == ["teams"]
