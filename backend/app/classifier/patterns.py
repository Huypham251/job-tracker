STATUS_PATTERNS: dict[str, list[tuple[str, int]]] = {
    "applied": [
        (r"thank you for applying", 3),
        (r"application (?:has been )?received", 3),
        # Matches with or without the auxiliary verb: plain "We received your
        # application" (no "'ve"/"have") is an extremely common phrasing that
        # the earlier we(?:'ve| have) received... form (requiring the
        # auxiliary) missed entirely, dropping straight to generic boosters
        # and often landing below JOB_RELATED_THRESHOLD.
        (r"(?:we|i)(?:'ve| have)? received your application", 3),
        (r"successfully applied", 2),
        (r"application is (?:now )?(?:in our system|being reviewed)", 3),
        (r"(?:we|i)(?:'ve| have)? got your application", 2),
    ],
    "oa": [
        (r"online assessment", 3),
        (r"coding (?:challenge|test)", 3),
        (r"hackerrank|codesignal", 3),
        (r"take.?home (?:assignment|test|challenge)", 2),
        (r"technical assessment", 2),
        (r"skills assessment", 3),
    ],
    "interview": [
        (r"invite you to interview", 3),
        (r"schedule (?:a|your) (?:call|interview)", 3),
        (r"phone screen", 3),
        (r"interview (?:invitation|process)", 2),
        (r"\binterview\b", 1),
        (r"set up a time to (?:chat|talk)", 2),
        (r"available for a (?:chat|call) about", 2),
    ],
    "rejected": [
        (r"regret to inform", 3),
        (r"will not be moving forward", 3),
        (r"decided (?:not )?to (?:proceed|move forward) with other candidates", 3),
        (r"other candidates", 2),
        (r"unfortunately", 2),
        (r"won'?t be moving forward", 3),
        (r"move(?:d|ing)? in a different direction", 3),
    ],
    "offer": [
        (r"pleased to offer", 3),
        (r"offer of employment", 3),
        (r"extend(?:ing)? (?:you )?an offer", 3),
        (r"job offer", 2),
        (r"(?:thrilled|excited) to (?:offer|welcome you|bring you on board)", 3),
    ],
}

GENERIC_JOB_PATTERNS: list[tuple[str, int]] = [
    (r"your application", 1),
    (r"\bposition\b", 1),
    (r"\bcandidates?\b", 1),
    (r"recruiting team", 1),
    (r"talent (?:acquisition|team)", 1),
    (r"\bopening\b", 3),
    (r"\bopportunity\b", 3),
]

NEGATIVE_PATTERNS: list[tuple[str, int]] = [
    (r"jobs matching your search", 3),
    (r"new jobs? for you", 3),
    (r"recommended jobs", 2),
    (r"unsubscribe", 2),
    (r"view (?:this|in) browser", 2),
    (r"%\s*off", 2),
    # Both found via real Phase 6 manual-testing/eval-dataset false positives, both
    # weighted to just clear the two known real cases (job_signal=4 in each) without
    # touching the interview/opening/opportunity positive patterns those cases also
    # trip — see the Phase 7 spec §2.3/§2.4 for why the positive patterns themselves
    # are out of scope.
    #
    # "newsletter" is anchored to the first ~60 characters (Phase 7 whole-branch
    # review, Finding 2), not matched anywhere in the text: text.combine_subject_body
    # always puts the subject first, so a genuine newsletter names itself in its own
    # subject line (e.g. "DNDA September Newsletter: A New Destination", or the
    # dataset's "Your weekly career newsletter") within the first few words. An
    # unanchored match instead let a routine footer mention — "you can unsubscribe
    # from our newsletter at any time", hundreds of characters into an otherwise
    # genuine job email's body — stack additively with the `unsubscribe` pattern
    # above (2+2=4) and flip a real job-related message to a false negative, which
    # under this app's idempotency model (each message classified at most once,
    # ever) is silently dropped forever — strictly worse than the review-queue noise
    # this pattern exists to fix. `meetup` has no equivalent evidenced footer
    # collision (a "meetup" mention is not a routine transactional-email footer
    # phrase the way "unsubscribe"/"newsletter" are), so it's left unanchored.
    (r"^.{0,60}\bnewsletter\b", 2),
    (r"\bmeetup\b", 2),
]

ATS_DOMAINS: frozenset[str] = frozenset(
    {
        "greenhouse.io",
        "lever.co",
        "myworkday.com",
        "icims.com",
        "smartrecruiters.com",
        "ashbyhq.com",
        "workable.com",
        "jobvite.com",
        "bamboohr.com",
        "taleo.net",
        # Assessment platforms — not the spec's original "ATS" framing, but the
        # same blocklist reasoning applies: the sender's domain is the platform
        # running the test, never the hiring company, so it must never be used
        # as a domain-derived company guess (app/classifier/fields.py).
        "hackerrank.com",
        "codesignal.com",
        "testgorilla.com",
        "codility.com",
        "hirevue.com",
    }
)

JOB_RELATED_THRESHOLD = 3
# Lowered from 6.0/4.0 (Phase 4b) in Phase 6 Task 10: those values were calibrated
# against evaluation/dataset.jsonl's original 18 template-phrased examples, where a
# genuinely job-related message routinely hits several weighted patterns at once. Real
# mail (per Phase 5's manual test — see CLAUDE.md) usually hits exactly one 3-weight
# status pattern and nothing else, giving net_signal=3, under the old norm that's
# base=3/6*0.6=0.3, capped well below where a human would call the signal "clear."
# 4.5/3.0 let a single strong, unambiguous status match reach base=0.4 and a clean
# top-vs-runner-up margin still saturate at 0.3 — see evaluation/inspect_confidence.py's
# output and evaluation/compare.py's precision_at_threshold before/after for the
# evidence this was checked against, not guessed.
JOB_SIGNAL_NORM = 4.5
MARGIN_NORM = 3.0
DOMAIN_CONFIDENCE_BONUS = 0.1
DOMAIN_RELATEDNESS_BONUS = 2

EXTRACTION_PENALTY: dict[str, float] = {
    "template": 0.0,
    "domain": 0.15,
    "display_name": 0.15,
    "none": 0.35,
}
