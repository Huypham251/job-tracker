from app.classifier.extractor import classify
from app.classifier.patterns import ATS_DOMAINS, JOB_RELATED_THRESHOLD, STATUS_PATTERNS


def test_status_patterns_cover_all_five_specific_statuses() -> None:
    assert set(STATUS_PATTERNS.keys()) == {"applied", "oa", "interview", "rejected", "offer"}


def test_classify_scores_a_strong_applied_phrase() -> None:
    status_scores, job_signal, negative_signal = classify("thank you for applying", "")
    assert status_scores["applied"] == 3
    assert job_signal == 3
    assert negative_signal == 0


def test_classify_scores_a_strong_oa_phrase() -> None:
    status_scores, job_signal, negative_signal = classify("please complete this online assessment", "")
    assert status_scores["oa"] == 3
    assert job_signal == 3


def test_classify_scores_an_interview_invitation_with_multiple_overlapping_patterns() -> None:
    status_scores, job_signal, negative_signal = classify("interview invitation: invite you to interview", "")
    # three interview patterns fire: "interview (invitation|process)" (+2),
    # "invite you to interview" (+3), and the weak standalone "\binterview\b" (+1)
    assert status_scores["interview"] == 6
    assert job_signal == 6


def test_classify_scores_a_rejection_with_two_overlapping_patterns() -> None:
    status_scores, job_signal, negative_signal = classify(
        "unfortunately we have decided to move forward with other candidates", ""
    )
    # "unfortunately" (+2), "decided ... move forward with other candidates" (+3),
    # "other candidates" (+2), plus generic "\bcandidates?\b" booster (+1)
    assert status_scores["rejected"] == 7
    assert job_signal == 8


def test_classify_scores_an_offer() -> None:
    status_scores, job_signal, negative_signal = classify("we are pleased to offer you the position", "")
    assert status_scores["offer"] == 3
    # generic booster "\bposition\b" also fires (+1)
    assert job_signal == 4


def test_classify_negative_signals_outweigh_a_job_board_digest() -> None:
    status_scores, job_signal, negative_signal = classify(
        "5 new jobs for you unsubscribe view in browser", ""
    )
    assert negative_signal == 7  # "new jobs for you" (3) + "unsubscribe" (2) + "view in browser" (2)
    assert job_signal == 0
    assert all(score == 0 for score in status_scores.values())


def test_classify_applies_domain_relatedness_bonus_for_known_ats_domain() -> None:
    assert "greenhouse.io" in ATS_DOMAINS
    _, job_signal_with_domain, _ = classify("interview invitation", "greenhouse.io")
    _, job_signal_without_domain, _ = classify("interview invitation", "")
    assert job_signal_with_domain == job_signal_without_domain + 2


def test_classify_no_signal_for_unrelated_text() -> None:
    status_scores, job_signal, negative_signal = classify("happy birthday from all of us", "")
    assert job_signal == 0
    assert negative_signal == 0
    assert all(score == 0 for score in status_scores.values())


def test_status_patterns_match_application_is_in_our_system() -> None:
    text = "your application is now in our system and our team will be reviewing it shortly"
    scores, job_signal, _ = classify(text, "")
    assert scores["applied"] > 0


def test_status_patterns_match_got_your_application() -> None:
    text = "we've got your application for the field technician role"
    scores, job_signal, _ = classify(text, "")
    assert scores["applied"] > 0


def test_status_patterns_match_skills_assessment() -> None:
    text = "you've been asked to complete a skills assessment for the financial analyst role"
    scores, job_signal, _ = classify(text, "")
    assert scores["oa"] > 0


def test_status_patterns_match_set_up_a_time_to_chat() -> None:
    text = "we'd love to set up a time to chat about the game designer opening"
    scores, job_signal, _ = classify(text, "")
    assert scores["interview"] > 0


def test_status_patterns_match_available_for_a_chat_about() -> None:
    text = "would you be available for a chat about the security analyst position"
    scores, job_signal, _ = classify(text, "")
    assert scores["interview"] > 0


def test_status_patterns_match_wont_be_moving_forward() -> None:
    text = "we won't be moving forward with your candidacy for the data engineer role"
    scores, job_signal, _ = classify(text, "")
    assert scores["rejected"] > 0


def test_status_patterns_match_different_direction() -> None:
    text = "we've chosen to move in a different direction for the level designer role"
    scores, job_signal, _ = classify(text, "")
    assert scores["rejected"] > 0


def test_status_patterns_match_extend_you_an_offer() -> None:
    text = "fieldstone ventures would like to extend you an offer for the investment associate role"
    scores, job_signal, _ = classify(text, "")
    assert scores["offer"] > 0


def test_status_patterns_match_thrilled_to_bring_you_on_board() -> None:
    text = "outpost aerospace is thrilled to bring you on board as our new systems engineer"
    scores, job_signal, _ = classify(text, "")
    assert scores["offer"] > 0


def test_generic_job_patterns_match_opening_and_opportunity() -> None:
    text = "i think you'd be a great fit for the senior mechanical engineer opening at pinnacle robotics"
    _, job_signal, _ = classify(text, "")
    assert job_signal >= JOB_RELATED_THRESHOLD


def test_negative_patterns_suppress_a_newsletter_with_incidental_job_language() -> None:
    # Real DNDA community-newsletter email found during Phase 6 manual testing: a
    # multi-topic digest with an unrelated "position"/"opportunity" mention (a
    # volunteer opportunity, not a job) crosses JOB_RELATED_THRESHOLD on generic
    # patterns alone. The subject line contains "Newsletter" literally.
    text = (
        "riverside neighbors september newsletter plus: our fall cleanup day, a new "
        "mural unveiling, and how to join the tenant council. now hiring: community "
        "outreach coordinator. this position will support neighborhood events. also, "
        "join us for an opportunity to volunteer at the community garden this weekend."
    )
    _, job_signal, negative_signal = classify(text, "riversideneighbors.org")
    assert job_signal - negative_signal < JOB_RELATED_THRESHOLD


def test_negative_patterns_do_not_suppress_a_job_email_with_a_newsletter_footer_mention() -> None:
    # Regression case for the Phase 7 whole-branch review, Finding 2: the unanchored
    # `\bnewsletter\b` negative pattern stacked additively with `unsubscribe` (2+2=4)
    # and could flip a genuine job-related email to a false negative purely because
    # of a routine, far-into-the-body footer mention ("you can unsubscribe from our
    # newsletter at any time") that has nothing to do with the email's actual
    # subject. Anchoring the pattern to the first ~60 characters (where a subject
    # line lives, per text.combine_subject_body's subject-first join) fixes this
    # without weakening the pattern's original real-newsletter-detection purpose —
    # see test_negative_patterns_suppress_a_newsletter_with_incidental_job_language
    # above, which still passes.
    body = (
        "we are pleased to offer you the job offer for the position. "
        + ("lorem ipsum filler text padding out the body so the footer is far away. " * 5)
        + "you can unsubscribe from our newsletter at any time."
    )
    _, job_signal, negative_signal = classify(body.lower(), "")
    assert job_signal - negative_signal >= JOB_RELATED_THRESHOLD


def test_negative_patterns_suppress_a_meetup_style_false_positive() -> None:
    # Real gap already present in evaluation/dataset.jsonl (the "Meetup: Hiring
    # managers panel this Thursday" example) — third-person, broadcast-style framing
    # about hiring as a topic, not a candidate-directed message, trips
    # "interview (?:invitation|process)" plus the generic "\bcandidates?\b" booster.
    text = (
        "meetup: hiring managers panel this thursday come hear from hiring managers "
        "about what they look for in candidates and how the interview process works "
        "at their companies. free pizza provided."
    )
    _, job_signal, negative_signal = classify(text, "devmeetup.com")
    assert job_signal - negative_signal < JOB_RELATED_THRESHOLD
