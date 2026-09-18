from app.classifier.preprocess import preprocess_body


def test_strips_a_standalone_greeting_line() -> None:
    body = "Thank you for applying to Acme Corp\n\nHi Jordan Lee,\n\nWe have received your application."
    result = preprocess_body(body)
    assert "Hi Jordan Lee" not in result
    assert "Thank you for applying to Acme Corp" in result
    assert "We have received your application." in result


def test_truncates_at_a_dashes_signature_block() -> None:
    body = "Thank you for applying to Acme Corp.\n\n--\nJamie Fox\nTalent Acquisition"
    result = preprocess_body(body)
    assert "Jamie Fox" not in result
    assert "Thank you for applying to Acme Corp." in result


def test_truncates_at_a_regards_signoff() -> None:
    body = "We would like to invite you to interview.\n\nBest regards,\nJordan Blake\nHead of Talent"
    result = preprocess_body(body)
    assert "Jordan Blake" not in result
    assert "We would like to invite you to interview." in result


def test_normalizes_smart_quotes_and_dashes() -> None:
    body = "We’ve received your application — thanks!"
    assert preprocess_body(body) == "We've received your application - thanks!"


def test_leaves_ordinary_content_untouched() -> None:
    body = "We have received your application for the Software Engineer position and will be in touch."
    assert preprocess_body(body) == body


def test_does_not_strip_a_real_sentence_that_merely_starts_with_a_greeting_word() -> None:
    body = "Dear applicant, your application has been rejected."
    assert preprocess_body(body) == body


def test_does_not_truncate_when_a_signoff_word_opens_the_body() -> None:
    body = (
        "Congratulations,\n\n"
        "We are pleased to offer you the Software Engineer position at Acme Corp."
    )
    result = preprocess_body(body)
    assert "We are pleased to offer you the Software Engineer position at Acme Corp." in result
