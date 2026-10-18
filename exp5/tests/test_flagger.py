from exp5.flagger import f1_format as F


def test_parse_output_ok_flag_and_invalid():
    assert F.parse_output("OK") == (False, None, True)
    assert F.parse_output("FLAG NO_STRUCTURING") == (True, "NO_STRUCTURING", True)
    assert F.parse_output("FLAG SOMETHING_ELSE") == (True, None, True)   # flagged, concept not recognised
    assert F.parse_output("maybe") == (False, None, False)               # invalid output
    assert F.parse_output("") == (False, None, False)


def test_user_text_appends_notes():
    assert F.user_text({"text": "Buy 5 pens", "notes": ["a", "b"]}) == "Buy 5 pens\nNOTE: a\nNOTE: b"
    assert F.user_text({"text": "Buy 5 pens"}) == "Buy 5 pens"
