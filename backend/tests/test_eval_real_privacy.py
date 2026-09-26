import pytest

from evaluation.real.privacy import (
    FICTIONAL_EMAIL,
    FICTIONAL_FAMILY,
    FICTIONAL_GIVEN,
    FICTIONAL_NAME,
    REPO_ROOT,
    SCRUBBED_URL,
    Identity,
    ensure_outside_repo,
    private_dir,
    scrub,
)

IDENTITY = Identity(name="Mira Tan Okoro", email="mira.okoro@example.org", extra_terms=("12 Elm Row",))


def test_ensure_outside_repo_refuses_the_repo_and_anything_inside_it() -> None:
    with pytest.raises(SystemExit):
        ensure_outside_repo(REPO_ROOT)
    with pytest.raises(SystemExit):
        ensure_outside_repo(REPO_ROOT / "backend" / "evaluation" / "private")


def test_private_dir_honors_the_env_var_and_creates_it(tmp_path, monkeypatch) -> None:
    target = tmp_path / "eval"
    monkeypatch.setenv("JOBTRACKER_REAL_EVAL_DIR", str(target))
    assert private_dir() == target.resolve()
    assert target.is_dir()


def test_private_dir_refuses_an_env_var_pointing_into_the_repo(monkeypatch) -> None:
    monkeypatch.setenv("JOBTRACKER_REAL_EVAL_DIR", str(REPO_ROOT / "tmp-eval"))
    with pytest.raises(SystemExit):
        private_dir()


def test_scrub_replaces_every_form_of_the_users_name() -> None:
    text = "Hi Mira Tan, thanks Mira Tan Okoro. OKORO MIRA TAN applied. Dear Mira, bye Okoro"
    result = scrub(text, IDENTITY)
    assert "mira" not in result.lower() and "okoro" not in result.lower() and "Tan" not in result
    assert f"Hi {FICTIONAL_GIVEN}," in result
    assert f"thanks {FICTIONAL_NAME}." in result
    assert f"bye {FICTIONAL_FAMILY}" in result


def test_scrub_replaces_emails_keeping_only_the_domain_of_others() -> None:
    result = scrub("to mira.okoro@example.org from jane.doe@halvorsen.com", IDENTITY)
    assert FICTIONAL_EMAIL in result
    assert "person@halvorsen.com" in result
    assert "jane.doe" not in result


def test_scrub_replaces_urls_phones_ids_and_extra_terms() -> None:
    text = (
        'Visit https://careers.halvorsen.com/apply?id=abc or <a href="http://x.io/t">x</a>. '
        "Call (206) 555-0142. Req JR2026505093 / 2026-90891. Class of 2027. Lives at 12 Elm Row."
    )
    result = scrub(text, IDENTITY)
    assert "halvorsen.com/apply" not in result and SCRUBBED_URL in result
    assert "555-0142" not in result
    assert "JR0000000000" in result and "0000-00000" in result
    assert "2027" in result  # a year is not an ID
    assert "12 Elm Row" not in result and "[redacted]" in result


def test_scrub_tolerates_empty_text_and_a_single_token_name() -> None:
    assert scrub("", IDENTITY) == ""
    single = Identity(name="Mira", email="m@example.org")
    assert scrub("Hi Mira,", single) == f"Hi {FICTIONAL_GIVEN},"
