from manager_mode_buddy.parser.positions import is_confirmed, position_name


def test_confirmed_codes_map_to_expected_abbreviations():
    expected = {
        0: "GK", 3: "RB", 5: "CB", 7: "LB", 10: "CDM", 12: "RM",
        14: "CM", 16: "LM", 18: "CAM", 23: "RW", 25: "ST", 27: "LW",
    }
    for code, abbrev in expected.items():
        assert position_name(code) == abbrev
        assert is_confirmed(code)


def test_unknown_code_returns_none():
    assert position_name(999) is None
    assert not is_confirmed(999)


def test_none_code_returns_none():
    assert position_name(None) is None
