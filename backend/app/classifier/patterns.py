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
    ],
    "oa": [
        (r"online assessment", 3),
        (r"coding (?:challenge|test)", 3),
        (r"hackerrank|codesignal", 3),
        (r"take.?home (?:assignment|test|challenge)", 2),
        (r"technical assessment", 2),
    ],
    "interview": [
        (r"invite you to interview", 3),
        (r"schedule (?:a|your) (?:call|interview)", 3),
        (r"phone screen", 3),
        (r"interview (?:invitation|process)", 2),
        (r"\binterview\b", 1),
    ],
    "rejected": [
        (r"regret to inform", 3),
        (r"will not be moving forward", 3),
        (r"decided (?:not )?to (?:proceed|move forward) with other candidates", 3),
        (r"other candidates", 2),
        (r"unfortunately", 2),
    ],
    "offer": [
        (r"pleased to offer", 3),
        (r"offer of employment", 3),
        (r"extend(?:ing)? an offer", 3),
        (r"job offer", 2),
    ],
}

GENERIC_JOB_PATTERNS: list[tuple[str, int]] = [
    (r"your application", 1),
    (r"\bposition\b", 1),
    (r"\bcandidates?\b", 1),
    (r"recruiting team", 1),
    (r"talent (?:acquisition|team)", 1),
]

NEGATIVE_PATTERNS: list[tuple[str, int]] = [
    (r"jobs matching your search", 3),
    (r"new jobs? for you", 3),
    (r"recommended jobs", 2),
    (r"unsubscribe", 2),
    (r"view (?:this|in) browser", 2),
    (r"%\s*off", 2),
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
    }
)

JOB_RELATED_THRESHOLD = 3
JOB_SIGNAL_NORM = 6.0
MARGIN_NORM = 4.0
DOMAIN_CONFIDENCE_BONUS = 0.1
DOMAIN_RELATEDNESS_BONUS = 2

EXTRACTION_PENALTY: dict[str, float] = {
    "template": 0.0,
    "domain": 0.15,
    "display_name": 0.15,
    "none": 0.35,
}
