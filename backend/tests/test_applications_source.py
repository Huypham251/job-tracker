from app.applications.models import Application, ApplicationStatus


def test_new_application_defaults_to_manual_source(db_session, user) -> None:
    application = Application(user_id=user.id, company="Acme", position="SWE")
    db_session.add(application)
    db_session.commit()
    db_session.refresh(application)

    assert application.source == "manual"


def test_application_source_can_be_set_to_gmail(db_session, user) -> None:
    application = Application(
        user_id=user.id, company="Acme", position="SWE", source="gmail"
    )
    db_session.add(application)
    db_session.commit()
    db_session.refresh(application)

    assert application.source == "gmail"


def test_other_is_a_valid_status(db_session, user) -> None:
    application = Application(
        user_id=user.id, company="Acme", position="SWE", status=ApplicationStatus.other
    )
    db_session.add(application)
    db_session.commit()
    db_session.refresh(application)

    assert application.status == ApplicationStatus.other
