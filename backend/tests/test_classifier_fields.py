from app.classifier.fields import find_company, find_position


def test_find_company_via_position_at_company_template() -> None:
    text = "Interview Invitation We would like to invite you to interview for the Backend Engineer position at Acme Corp."
    assert find_company(text, "careers@acme.com") == ("Acme Corp", "template")


def test_find_position_via_position_at_company_template() -> None:
    text = "Interview Invitation We would like to invite you to interview for the Backend Engineer position at Acme Corp."
    assert find_position(text, "careers@acme.com") == ("Backend Engineer", "template")


def test_find_company_via_applying_to_template() -> None:
    text = "Thank you for applying to Acme Corp."
    assert find_company(text, "careers@acme.com") == ("Acme Corp", "template")


def test_find_position_via_role_template_without_at_company() -> None:
    text = "We received your application for the Backend Engineer position. Our recruiting team will review it."
    assert find_position(text, "noreply@greenhouse.io") == ("Backend Engineer", "template")


def test_find_company_blocklists_known_ats_domains() -> None:
    text = "We received your application for the Backend Engineer position. Our recruiting team will review it."
    assert find_company(text, "noreply@greenhouse.io") == (None, "none")


def test_find_company_falls_back_to_display_name_when_domain_is_blocklisted() -> None:
    text = "Some update."
    sender = '"Acme Careers" <notifications@greenhouse.io>'
    assert find_company(text, sender) == ("Acme", "display_name")


def test_find_company_falls_back_to_sender_domain_when_not_ats() -> None:
    text = "Some update."
    assert find_company(text, "careers@acme.com") == ("Acme", "domain")


def test_find_company_returns_none_when_no_email_address_present() -> None:
    text = "x"
    assert find_company(text, "not-an-email") == (None, "none")


def test_find_position_returns_none_when_no_pattern_matches() -> None:
    text = "Nothing relevant here."
    assert find_position(text, "x@y.com") == (None, "none")


def test_find_company_stops_at_a_connector_word_instead_of_overcapturing() -> None:
    # Regression case: the company mention is followed by more of the sentence
    # ("for the ... position") rather than immediate punctuation. A naive
    # lazy-quantifier-to-next-punctuation regex would capture the whole
    # remainder of the sentence as the "company" instead of just "Zeta Co".
    text = "We regret to inform you that we will not be moving forward with your application to Zeta Co for the Marketing Analyst position."
    assert find_company(text, "careers@zeta.com") == ("Zeta Co", "template")


def test_find_company_stops_at_a_connector_word_after_at_company_template() -> None:
    text = "We have received your application for the Operations Analyst position at Theta and our recruiting team will follow up."
    assert find_company(text, "careers@theta.com") == ("Theta", "template")
