from app.applications.models import Application, ApplicationStatus
from app.pipeline import matching


def _make_app(db_session, user, *, company, position, source="manual") -> Application:
    application = Application(
        user_id=user.id,
        company=company,
        position=position,
        status=ApplicationStatus.applied,
        source=source,
    )
    db_session.add(application)
    db_session.commit()
    db_session.refresh(application)
    return application


def test_normalize_strips_suffixes_and_punctuation() -> None:
    assert matching.normalize("Acme, Inc.") == matching.normalize("ACME")


def test_find_candidate_returns_create_when_no_applications_exist(db_session, user) -> None:
    result = matching.find_candidate(db_session, user.id, "Acme", "Software Engineer")
    assert result.action == "create"
    assert result.application is None
    assert result.ambiguous is False


def test_find_candidate_returns_update_for_strong_unambiguous_match(db_session, user) -> None:
    existing = _make_app(db_session, user, company="Acme Inc", position="Software Engineer")
    _make_app(db_session, user, company="Globex", position="Marketing Manager")

    result = matching.find_candidate(db_session, user.id, "Acme", "Software Engineer")

    assert result.action == "update"
    assert result.application.id == existing.id
    assert result.ambiguous is False


def test_find_candidate_flags_ambiguous_for_a_mid_range_score(db_session, user) -> None:
    _make_app(db_session, user, company="Acme Corp", position="Backend Developer")

    result = matching.find_candidate(db_session, user.id, "Acme", "Backend Engineer")

    assert result.ambiguous is True


def test_find_candidate_flags_ambiguous_when_two_candidates_are_close(db_session, user) -> None:
    _make_app(db_session, user, company="Acme Inc", position="Software Engineer I")
    _make_app(db_session, user, company="Acme Inc", position="Software Engineer II")

    result = matching.find_candidate(db_session, user.id, "Acme", "Software Engineer")

    assert result.ambiguous is True


def test_find_candidate_returns_create_when_scores_are_all_low(db_session, user) -> None:
    _make_app(db_session, user, company="Totally Different Co", position="Marketing Manager")

    result = matching.find_candidate(db_session, user.id, "Acme", "Software Engineer")

    assert result.action == "create"
    assert result.ambiguous is False


def test_find_candidate_scopes_to_the_given_user(db_session, user, other_user) -> None:
    _make_app(db_session, other_user, company="Acme Inc", position="Software Engineer")

    result = matching.find_candidate(db_session, user.id, "Acme", "Software Engineer")

    assert result.action == "create"
