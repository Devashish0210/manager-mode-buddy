from manager_mode_buddy.attributes import discover, label_for


def test_label_for_known_field():
    assert label_for("shortpassing") == "Short Passing"


def test_label_for_unknown_field_falls_back_to_raw_name():
    assert label_for("someMadeUpField") == "someMadeUpField"


def test_discover_only_returns_available_fields():
    grouped = discover(["acceleration", "sprintspeed", "vision"])
    assert grouped["PACE"][0].field == "acceleration"
    assert grouped["PACE"][1].field == "sprintspeed"
    assert "SHOOTING" not in grouped  # none of shooting's fields were available
    assert grouped["PASSING"][0].field == "vision"


def test_discover_with_no_fields_returns_empty():
    assert discover([]) == {}
