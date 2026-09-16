from datetime import date

from app.classifier.text import (
    combine_subject_body,
    extract_sender_display_name,
    extract_sender_domain,
    normalize_text,
    parse_email_date,
)


def test_normalize_text_lowercases_and_collapses_whitespace() -> None:
    assert normalize_text("Subject Line", "Body\ntext  here") == "subject line body text here"


def test_combine_subject_body_preserves_case() -> None:
    assert combine_subject_body("Subject Line", "Body\ntext  here") == "Subject Line Body text here"


def test_extract_sender_domain_from_display_name_format() -> None:
    assert extract_sender_domain('"Acme Careers" <careers@acme.com>') == "acme.com"


def test_extract_sender_domain_strips_known_subdomain_prefixes() -> None:
    assert extract_sender_domain("someone@mail.example.com") == "example.com"
    assert extract_sender_domain("alerts@notifications.greenhouse.io") == "greenhouse.io"


def test_extract_sender_domain_bare_address_no_prefix_to_strip() -> None:
    assert extract_sender_domain("notifications@greenhouse.io") == "greenhouse.io"


def test_extract_sender_domain_returns_empty_string_when_no_at_sign() -> None:
    assert extract_sender_domain("not-an-email") == ""


def test_extract_sender_display_name_present() -> None:
    assert extract_sender_display_name('"Acme Careers" <careers@acme.com>') == "Acme Careers"


def test_extract_sender_display_name_absent_for_bare_address() -> None:
    assert extract_sender_display_name("careers@acme.com") is None


def test_parse_email_date_valid_rfc2822() -> None:
    assert parse_email_date("Mon, 5 Jan 2026 10:00:00 +0000") == date(2026, 1, 5)


def test_parse_email_date_invalid_returns_none() -> None:
    assert parse_email_date("not a date") is None
