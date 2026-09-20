import re
from datetime import date as date_type
from email.utils import parsedate_to_datetime

_HEADER_ADDR_RE = re.compile(r"<([^>]+)>")
_DISPLAY_NAME_RE = re.compile(r'^\s*"?([^"<]*?)"?\s*<')
_SUBDOMAIN_PREFIXES = (
    "mail.", "notifications.", "e.", "no-reply.", "noreply.",
    "careers.", "jobs.", "talent.", "recruiting.",
    # ATS/HCM platform labels that appear as a LEADING subdomain of the real
    # employer's own domain (the opposite shape from a tenant subdomain like
    # acme.myworkday.com, where the first label IS the company — see
    # test_find_company_still_treats_workday_tenant_subdomain_as_the_company).
    # Evidenced by a real Verisk email during Phase 6 manual testing:
    # oraclecloud.verisk.com must resolve to "verisk", not "oraclecloud".
    "oraclecloud.",
)
_WHITESPACE_RE = re.compile(r"\s+")


def combine_subject_body(subject: str, body: str) -> str:
    combined = f"{subject} {body}"
    return _WHITESPACE_RE.sub(" ", combined).strip()


def normalize_text(subject: str, body: str) -> str:
    return combine_subject_body(subject, body).lower()


def extract_sender_domain(sender: str) -> str:
    match = _HEADER_ADDR_RE.search(sender)
    address = match.group(1) if match else sender.strip()
    if "@" not in address:
        return ""
    domain = address.rsplit("@", 1)[1].strip().lower()
    for prefix in _SUBDOMAIN_PREFIXES:
        if domain.startswith(prefix):
            domain = domain[len(prefix):]
            break
    return domain


def extract_sender_display_name(sender: str) -> str | None:
    match = _DISPLAY_NAME_RE.match(sender)
    if not match:
        return None
    name = match.group(1).strip()
    return name or None


def parse_email_date(date_header: str) -> date_type | None:
    try:
        return parsedate_to_datetime(date_header).date()
    except (TypeError, ValueError):
        return None
