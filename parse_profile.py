"""
LinkedIn MCP profile parser.

Reduces raw MCP get_person_profile output (~60KB) to a compact dict (~2KB)
by extracting key fields and truncating/limiting verbose sections.

Usage:
    from parse_profile import parse_profile
    compact = parse_profile(raw_mcp_output)
"""

from __future__ import annotations

import re
from typing import Any

import yaml


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _first_nonempty(*values: str | None) -> str:
    for v in values:
        if v and v.strip():
            return v.strip()
    return ""


def _clean(text: str) -> str:
    """Collapse whitespace and strip UI noise lines."""
    _NOISE = {
        "Follow", "Message", "More", "Connect", "Connect if you know each other",
        "He/Him", "She/Her", "They/Them", "· 1st", "· 2nd", "· 3rd",
        "Contact info", "All activity", "Posts", "Comments", "Videos", "Images",
        "Verified", "Open to", "Open to work",
    }
    lines = []
    for line in text.splitlines():
        line = line.strip()
        if line and line not in _NOISE and not line.startswith("Loaded "):
            lines.append(line)
    return " ".join(lines)


# ---------------------------------------------------------------------------
# Section parsers
# ---------------------------------------------------------------------------

def _parse_main(text: str) -> dict[str, str]:
    """Extract name, headline, location, followers, about from main_profile."""
    lines = [l.strip() for l in text.splitlines() if l.strip()]

    name = lines[0] if lines else ""

    # Headline: first line that contains | or common lawyer keywords
    headline = ""
    for line in lines[1:8]:
        if "|" in line or any(k in line for k in ("Advocate", "Lawyer", "Attorney", "Solicitor", "Counsel")):
            headline = line
            break

    # Location: looks like "City, State, Country"
    location = ""
    for line in lines[1:10]:
        if re.search(r",\s+\w", line) and len(line) < 80:
            if line not in (name, headline):
                location = line
                break

    # Followers
    followers = ""
    m = re.search(r"([\d,]+)\s+followers", text)
    if m:
        followers = m.group(1)

    # About section: text after "About" heading
    about = ""
    about_match = re.search(r"\bAbout\b\n+(.+?)(?:\n\n|\nFeatured|\nExperience|\nActivity)", text, re.DOTALL)
    if about_match:
        about = _clean(about_match.group(1))[:400]

    return {"name": name, "headline": headline, "location": location,
            "followers": followers, "about": about}


def _parse_experience(text: str, max_roles: int = 3) -> list[dict[str, str]]:
    """Extract top N roles from experience section.

    LinkedIn experience text follows a repeating pattern:
        <Title>
        <Company> · <Employment type>
        <Start> - <End> · <Duration>
        <Location>
        <Description lines...>
    We scan line-by-line and emit a role whenever we see a duration line,
    using the preceding lines to fill title and company.
    """
    roles: list[dict[str, str]] = []
    lines = [l.strip() for l in text.splitlines() if l.strip()]

    # sliding window: keep last few lines to reconstruct title/company
    window: list[str] = []
    EMPLOYMENT_TYPES = ("Full-time", "Part-time", "Contract", "Freelance",
                        "Self-employed", "Internship", "Seasonal")

    for line in lines:
        if line == "Experience":
            continue

        is_duration = bool(re.search(r"\d{4}", line) and re.search(r"yr|mo|Present|\d{4}", line)
                           and re.search(r"-", line))

        if is_duration:
            # Reconstruct role from window
            title = ""
            company = ""
            for prev in reversed(window):
                if "·" in prev and any(t in prev for t in EMPLOYMENT_TYPES):
                    company = prev.split("·")[0].strip()
                elif not title and prev and not any(t in prev for t in EMPLOYMENT_TYPES):
                    title = prev
            if not title and window:
                title = window[-1]

            if title:
                entry: dict[str, str] = {"title": title, "duration": line.strip()}
                if company:
                    entry["company"] = company
                roles.append(entry)
                if len(roles) >= max_roles:
                    break
            window = []
        else:
            window.append(line)

    return roles


def _parse_education(text: str, max_entries: int = 2) -> list[dict[str, str]]:
    """Extract top N education entries."""
    entries: list[dict[str, str]] = []
    blocks = re.split(r"\n{2,}", text.strip())

    for block in blocks:
        lines = [l.strip() for l in block.splitlines() if l.strip()]
        if not lines or lines[0] in ("Education",):
            continue
        entry: dict[str, str] = {"institution": lines[0]}
        if len(lines) > 1:
            entry["degree"] = lines[1]
        if len(lines) > 2 and re.search(r"\d{4}", lines[2]):
            entry["years"] = lines[2]
        entries.append(entry)
        if len(entries) >= max_entries:
            break

    return entries


def _parse_posts(text: str, max_posts: int = 5, max_chars: int = 200) -> list[dict[str, str]]:
    """Extract the N most recent posts with truncated text."""
    posts: list[dict[str, str]] = []

    # Split on "Feed post number N"
    chunks = re.split(r"Feed post number \d+", text)

    for chunk in chunks[1:max_posts + 1]:  # skip preamble before first post
        lines = [l.strip() for l in chunk.splitlines() if l.strip()]

        # Skip repost noise
        is_repost = any("reposted" in l.lower() for l in lines[:3])

        # Find timestamp (e.g. "4w", "2d", "1mo", "3 months ago")
        timestamp = ""
        for line in lines:
            if re.match(r"^\d+[wdmhy]$", line) or "ago" in line or re.match(r"^\d+ (day|week|month|hour)", line):
                timestamp = line
                break

        # Post text: skip name/title/timestamp noise, grab first substantive line
        text_lines = []
        skip_next = False
        for line in lines:
            if skip_next:
                skip_next = False
                continue
            if any(x in line for x in ("reposted", "Follow", "· 2nd", "· 1st", "· 3rd", "Verified")):
                skip_next = True
                continue
            if re.match(r"^\d+[wdmhy]$", line) or "ago" in line:
                continue
            if len(line) > 40:  # substantive line
                text_lines.append(line)
            if len(" ".join(text_lines)) > max_chars:
                break

        body = " ".join(text_lines)[:max_chars]
        if body:
            post: dict[str, str] = {"text": body}
            if timestamp:
                post["when"] = timestamp
            if is_repost:
                post["type"] = "repost"
            posts.append(post)

    return posts


def _parse_contact(structured: dict[str, Any], raw_text: str) -> dict[str, str]:
    """Extract email and website from contact info."""
    contact: dict[str, str] = {}

    # From structured section
    emails = structured.get("emails", [])
    if emails:
        contact["email"] = emails[0]

    websites = structured.get("websites", [])
    if websites:
        contact["website"] = websites[0] if isinstance(websites[0], str) else websites[0].get("url", "")

    # Fallback: scrape email from raw main_profile text
    if "email" not in contact:
        m = re.search(r"[\w.+-]+@[\w-]+\.[a-zA-Z]{2,}", raw_text)
        if m:
            contact["email"] = m.group(0)

    return contact


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def parse_profile(
    raw: dict[str, Any],
    max_posts: int = 5,
    max_experience: int = 3,
    max_education: int = 2,
) -> dict[str, Any]:
    """
    Parse raw get_person_profile MCP output into a compact summary dict.

    Args:
        raw: The dict returned by the LinkedIn MCP get_person_profile tool.
        max_posts: Number of recent posts to include.
        max_experience: Number of experience entries to include.
        max_education: Number of education entries to include.

    Returns:
        Compact dict with keys: url, name, headline, location, followers,
        about, connection_degree, contact, experience, education, recent_posts.
    """
    sections = raw.get("sections", {})
    structured = raw.get("structured_sections", {})
    connection = raw.get("connection", {})

    main_text = sections.get("main_profile", "")
    main = _parse_main(main_text)

    result: dict[str, Any] = {
        "url": raw.get("url", ""),
        "name": main["name"],
        "headline": main["headline"],
        "location": main["location"],
        "followers": main["followers"],
        "about": main["about"],
        "connection_degree": connection.get("degree", ""),
        "contact": _parse_contact(
            structured.get("contact_info", {}),
            main_text,
        ),
        "experience": _parse_experience(sections.get("experience", ""), max_experience),
        "education": _parse_education(sections.get("education", ""), max_education),
        "recent_posts": _parse_posts(sections.get("posts", ""), max_posts),
    }

    return result


def parse_profile_yaml(
    raw: dict[str, Any],
    max_posts: int = 5,
    max_experience: int = 3,
    max_education: int = 2,
) -> str:
    """Same as parse_profile but returns a YAML string."""
    result = parse_profile(raw, max_posts=max_posts,
                           max_experience=max_experience,
                           max_education=max_education)
    return yaml.dump(result, allow_unicode=True, sort_keys=False, default_flow_style=False)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def _make_fake_raw() -> dict[str, Any]:
    """Minimal fake MCP output for testing without a live LinkedIn session."""
    return {
        "url": "https://www.linkedin.com/in/testlawyer/",
        "profile_urn": "urn:li:fsd_profile:ABC123",
        "connection": {"status": "2nd", "degree": "2nd", "is_connected": False,
                       "is_pending": False, "is_connectable": True},
        "structured_sections": {
            "contact_info": {
                "emails": ["jane.doe@lawfirm.com"],
                "phones": [],
                "profile_urls": ["https://www.linkedin.com/in/testlawyer/"],
                "websites": ["https://janedoe.law"],
            }
        },
        "sections": {
            "main_profile": """Jane Doe

She/Her

· 2nd

Senior Advocate | Corporate & M&A Specialist | Bombay High Court

Mumbai, Maharashtra, India

·

Contact info

Doe & Partners LLP

National Law School of India University, Bangalore

12,500 followers

·

500+

connections

Follow
Message
More

Connect if you know each other

Connect

About

15+ years handling cross-border M&A transactions, joint ventures and commercial disputes.
Passionate about making legal services accessible and technology-driven. Email: jane.doe@lawfirm.com

Featured

Link

Interview with Jane Doe - M&A Law in India
""",
            "experience": """Experience

Senior Partner

Doe & Partners LLP · Full-time

Jan 2015 - Present · 10 yrs 3 mos

Mumbai, Maharashtra, India · On-site

Leading M&A and corporate practice.

Associate

Cyril Amarchand Mangaldas · Full-time

Jun 2010 - Dec 2014 · 4 yrs 6 mos

Mumbai, Maharashtra, India

Corporate transactions and PE deals.
""",
            "education": """Education

National Law School of India University, Bangalore

B.A. LL.B (Hons)

2005 - 2010

Harvard Law School

LL.M in Corporate Law

2014 - 2015
""",
            "posts": """All activity
Posts
Comments
Loaded 10 Posts posts
Feed post number 1
Jane Doe
Senior Advocate
2w

Excited to share that Doe & Partners just closed a landmark cross-border acquisition deal worth $50M. Grateful to the entire team for their dedication and expertise throughout this complex transaction.

#MergersAndAcquisitions #CorporateLaw #India

Feed post number 2
Jane Doe reposted this
Some Other Person
3w

Indian legal tech is having its moment. AI tools that understand Indian law are finally here.

Feed post number 3
Jane Doe
Senior Advocate
1mo

Happy to announce I'll be speaking at the FICCI Legal Conference next month on the topic of AI in Legal Practice. Looking forward to the conversation!

Feed post number 4
Jane Doe
Senior Advocate
2mo

Contract review used to take my team 3 days. Now we do it in 3 hours. Technology is changing everything about how we practice law.

Feed post number 5
Jane Doe
Senior Advocate
3mo

Filed our 500th trademark application this quarter. Growing practice, growing team.

Feed post number 6
Jane Doe
Senior Advocate
4mo

This should not appear in output with max_posts=5.
""",
        },
        "references": {},
        "contact_info": {
            "emails": ["jane.doe@lawfirm.com"],
            "phones": [],
            "profile_urls": ["https://www.linkedin.com/in/testlawyer/"],
            "websites": ["https://janedoe.law"],
        },
    }


def run_tests() -> None:
    raw = _make_fake_raw()
    result = parse_profile(raw, max_posts=5, max_experience=3, max_education=2)

    print("=== Parsed output (YAML) ===")
    print(parse_profile_yaml(raw, max_posts=5, max_experience=3, max_education=2))
    result = parse_profile(raw, max_posts=5, max_experience=3, max_education=2)
    print()

    failures = []

    def check(desc: str, condition: bool) -> None:
        status = "PASS" if condition else "FAIL"
        print(f"  [{status}] {desc}")
        if not condition:
            failures.append(desc)

    print("=== Tests ===")
    check("name extracted", result["name"] == "Jane Doe")
    check("headline extracted", "Corporate" in result["headline"] or "Advocate" in result["headline"])
    check("location extracted", "Mumbai" in result["location"])
    check("followers extracted", result["followers"] == "12,500")
    check("about non-empty", len(result["about"]) > 20)
    check("email from structured", result["contact"].get("email") == "jane.doe@lawfirm.com")
    check("website extracted", result["contact"].get("website") == "https://janedoe.law")
    check("experience has entries", len(result["experience"]) >= 1)
    check("experience title present", "Partner" in result["experience"][0].get("title", ""))
    check("experience company present", "Doe" in result["experience"][0].get("company", ""))
    check("education has entries", len(result["education"]) >= 1)
    check("education institution present", "National Law" in result["education"][0].get("institution", ""))
    check("max_posts respected (5)", len(result["recent_posts"]) == 5)
    check("post 6 excluded", not any("should not appear" in p["text"] for p in result["recent_posts"]))
    check("repost flagged", any(p.get("type") == "repost" for p in result["recent_posts"]))
    check("post has text", len(result["recent_posts"][0]["text"]) > 10)
    check("post has timestamp", "when" in result["recent_posts"][0])
    check("connection_degree present", result["connection_degree"] == "2nd")
    check("url preserved", result["url"] == "https://www.linkedin.com/in/testlawyer/")

    print()
    if failures:
        print(f"FAILED: {len(failures)} test(s): {failures}")
    else:
        print(f"All {19} tests passed.")


if __name__ == "__main__":
    run_tests()
