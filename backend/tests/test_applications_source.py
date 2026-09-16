from app.applications.models import Application, ApplicationStatus
from app.applications.schemas import ApplicationUpdate
from app.applications.service import update_application


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


def test_updating_an_application_reclaims_it_as_manual(db_session, user) -> None:
    application = Application(
        user_id=user.id, company="Acme", position="SWE", source="gmail"
    )
    db_session.add(application)
    db_session.commit()

    updated = update_application(
        db_session, user.id, application.id, ApplicationUpdate(position="Senior SWE")
    )

    assert updated.source == "manual"
