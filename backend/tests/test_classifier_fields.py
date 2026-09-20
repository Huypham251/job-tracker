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


def test_find_company_trims_a_trailing_greeting_after_applying_to_template() -> None:
    text = "Thank you for applying to Pinnacle Robotics Hi Jordan Lee, we have received your application."
    company, tier = find_company(text, "careers@pinnaclerobotics.com")
    assert company == "Pinnacle Robotics"
    assert tier == "template"


def test_find_company_trims_a_trailing_greeting_after_position_at_company_template() -> None:
    text = "We would like to invite you to interview for the Product Designer position at Fernwood Design Hi Morgan, please pick a time."
    company, tier = find_company(text, "careers@fernwooddesign.com")
    assert company == "Fernwood Design"


def test_find_company_matches_opening_and_opportunity_keywords() -> None:
    text = "I think you'd be a great fit for the Senior Mechanical Engineer opening at Pinnacle Robotics."
    company, tier = find_company(text, "recruiter@pinnaclerobotics.com")
    assert company == "Pinnacle Robotics"
    assert tier == "template"


def test_find_company_matches_about_the_leading_word() -> None:
    text = "I'm reaching out about the Data Engineer opportunity at Cobalt Data."
    company, tier = find_company(text, "jane.kim@cobaltdata.io")
    assert company == "Cobalt Data"


def test_find_company_matches_is_pleased_to_offer_template() -> None:
    text = "Brightview Energy is pleased to extend an offer for the Electrical Engineer position."
    company, tier = find_company(text, "hr@brightviewenergy.com")
    assert company == "Brightview Energy"
    assert tier == "template"


def test_find_company_dedupes_when_subject_and_body_both_mention_the_company_adjacently() -> None:
    # combine_subject_body joins "Your offer from Brightview Energy" (subject) and
    # "Brightview Energy is pleased to..." (body) with a single space, producing
    # "...Brightview Energy Brightview Energy is pleased..." — without deduping, the
    # capitalized-run capture swallows both mentions as one company name.
    text = "Your offer from Brightview Energy Brightview Energy is pleased to extend an offer for the Electrical Engineer position."
    company, tier = find_company(text, "hr@brightviewenergy.com")
    assert company == "Brightview Energy"
    assert tier == "template"


def test_find_company_strips_careers_and_talent_subdomain_prefixes() -> None:
    company, tier = find_company("no template match here", "talent@careers.pinnaclerobotics.com")
    assert company == "Pinnaclerobotics"
    assert tier == "domain"


def test_find_company_does_not_treat_non_ats_assessment_platform_as_the_company() -> None:
    company, tier = find_company("no template match here", "noreply@testgorilla.com")
    assert company is None
    assert tier == "none"


def test_find_company_prefers_the_apex_domain_over_an_oraclecloud_subdomain() -> None:
    # Real Verisk rejection email found during Phase 6 manual testing: the sender
    # domain is "oraclecloud.verisk.com" — Verisk's own domain, verisk.com, fronted by
    # an "oraclecloud" ATS subdomain label. Before this fix, the first label
    # ("oraclecloud", the platform) was extracted instead of the real employer.
    company, tier = find_company("no template match here", "TalentAcquisition@oraclecloud.verisk.com")
    assert company == "Verisk"
    assert tier == "domain"


def test_find_company_still_treats_workday_tenant_subdomain_as_the_company() -> None:
    # Guards against a general "always prefer the label before the TLD" fix, which
    # would break this opposite, already-correct pattern: here the FIRST label
    # ("acme") is the real company and the platform is the base domain — the reverse
    # of the oraclecloud.verisk.com shape above. The fix must stay a specific,
    # evidenced label addition (oraclecloud.), not a positional rule.
    company, tier = find_company("no template match here", "notify@acme.myworkday.com")
    assert company == "Acme"
    assert tier == "domain"


def test_find_company_falls_back_to_display_name_for_hirevue_interview_invites() -> None:
    # HireVue is an interview/assessment platform, same category as hackerrank.com,
    # codesignal.com, testgorilla.com, codility.com (already in ATS_DOMAINS) — found
    # via a real "Interview with Nike, Inc." email during Phase 6 manual testing that
    # was extracting company="Hirevue" instead of the actual employer.
    text = (
        "INTERVIEW WITH Nike, Inc. Dear Huy Pham, CONGRATULATIONS! You are one step "
        "closer to joining a winning team committed to moving the world forward "
        "through the power of sport. Every job at NIKE, Inc. is"
    )
    company, tier = find_company(text, '"Nike, Inc." <interviews@hirevue.com>')
    assert company == "Nike, Inc."
    assert tier == "display_name"


def test_find_position_matches_as_our_new_template() -> None:
    text = "Outpost Aerospace is thrilled to bring you on board as our new Systems Engineer."
    position, tier = find_position(text, "hr@outpostaerospace.com")
    assert position == "Systems Engineer"
    assert tier == "template"


def test_find_position_matches_as_your_new_template() -> None:
    text = "We're excited to welcome you as your new Machine Learning Engineer."
    position, tier = find_position(text, "hr@meridianlabs.ai")
    assert position == "Machine Learning Engineer"
    assert tier == "template"
