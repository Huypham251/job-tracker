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


def test_find_position_bounded_capture_skips_leading_unrelated_clause() -> None:
    # Regression case for the unbounded `(?P<position>.+?)` bug: an earlier,
    # unrelated "for the ... position"-shaped clause in the same email must
    # not win over the real interview-invitation sentence later on. With the
    # old unbounded lazy quantifier, the FIRST "for the" in the text
    # ("...time you spent...for the Senior Backend Engineer") would win,
    # producing a ~100-character garbage "position" spanning most of the
    # sentence. Bounding the capture to a handful of word-shaped tokens (like
    # _COMPANY_TOKEN already does for company) makes that first candidate fail
    # to match at all (no "position"/"role" within a few words of "for the
    # time"), so the search correctly falls through to the real match.
    text = (
        "Thank you for the time you spent with our team last week. We would "
        "like to invite you to interview for the Senior Backend Engineer "
        "position at Acme Corp."
    )
    assert find_position(text, "careers@acme.com") == ("Senior Backend Engineer", "template")
    assert find_company(text, "careers@acme.com") == ("Acme Corp", "template")


def test_find_company_capitalization_guard_prevents_overcapture_across_sentence_join() -> None:
    # Regression coverage for the Task 8 _COMPANY_TOKEN capitalization-guard
    # fix (commit ce83abe). Subject and body are joined with a single space
    # and no terminating punctuation (see text.combine_subject_body), so a
    # subject like "Your application to Acme Corp" run straight into a body
    # starting "Thank you for applying..." lets a naive all-word-chars company
    # token capture straight through the join ("Acme Corp Thank you",
    # stopping only once it reaches the boundary word "for"). Requiring each
    # captured word to start with a capital letter breaks that overcapture at
    # "you" (lowercase): the first "application to" occurrence no longer
    # matches at all, so find_company falls through to the second,
    # unambiguous "applying to Acme Corp." mention instead.
    text = (
        "Your application to Acme Corp Thank you for applying to Acme Corp. "
        "We have received your application for the Software Engineer position "
        "and will be in touch."
    )
    assert find_company(text, "careers@acme.com") == ("Acme Corp", "template")


def test_find_company_and_position_via_offer_you_the_phrasing() -> None:
    # Regression coverage for the Task 8 fix adding "you" to
    # _POSITION_AT_COMPANY_RE's leading-word alternation (commit ce83abe):
    # offer emails commonly phrase this as "pleased to offer you the X
    # position at Y", where the word directly before "the" is the direct
    # object "you" rather than one of the original for/to/in prepositions.
    text = "We are pleased to offer you the Backend Engineer position at Acme Corp."
    assert find_company(text, "careers@acme.com") == ("Acme Corp", "template")
    assert find_position(text, "careers@acme.com") == ("Backend Engineer", "template")
