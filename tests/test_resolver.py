"""The resolver is invariant 3. These are the tests that say so.

docs/08 asks for three: one good, one bad, one rescued. The rest cover the ways a
hostile or sloppy draft can try to get past it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from repoman.core.resolver import Checkout, normalise, reread, resolve, resolve_all
from repoman.core.types import DocSpan, Evidence, FileRange, GitObject, HttpCapture, LocatorDraft

SHA = "9f3c2a1d7e4b8c0a5d6e7f8091a2b3c4d5e6f708"

SOURCE = """package app.model;

public enum UserRole {
    ADMIN,
    MEMBER
}
"""


@pytest.fixture
def ck(tmp_path: Path) -> Checkout:
    src = tmp_path / "src" / "main" / "java" / "app" / "model"
    src.mkdir(parents=True)
    (src / "UserRole.java").write_text(SOURCE)
    return Checkout(root=tmp_path, commitSha=SHA,
                    pages={"9": "The system implements full RBAC across all five user tiers."})


def draft(path="src/main/java/app/model/UserRole.java", start=3, end=6, quote="ADMIN,\n    MEMBER"):
    return LocatorDraft(locator=FileRange(path=path, startLine=start, endLine=end, commitSha="whatever"),
                        quote=quote)


# --- the three from docs/08 ----------------------------------------------------


def test_good_locator_resolves(ck):
    ev = resolve(draft(), ck, submission_id="s")
    assert ev is not None
    assert ev.locator.startLine == 3 and ev.locator.endLine == 6
    assert ev.provenance == "model"


def test_bad_locator_is_dropped(ck):
    """A quote that is not in the file at all. This is the bluff invariant 4 exists to refuse."""
    assert resolve(draft(quote="SUPERADMIN, AUDITOR, OWNER"), ck, submission_id="s") is None


def test_off_by_a_few_lines_is_rescued(ck):
    """The common real failure: right code, wrong line numbers. Rescued, and the range is corrected."""
    ev = resolve(draft(start=14, end=16), ck, submission_id="s")
    assert ev is not None
    assert (ev.locator.startLine, ev.locator.endLine) == (4, 5)


def test_rescue_does_not_reach_past_its_window(ck):
    lines = "\n".join(f"line {i}" for i in range(1, 200))
    (ck.root / "long.txt").write_text(lines + "\n" + SOURCE)
    assert resolve(draft(path="long.txt", start=1, end=2), ck, submission_id="s") is None


# --- what the resolver stores --------------------------------------------------


def test_stored_quote_is_reread_from_disk_not_echoed(ck):
    """Whitespace-insensitive match, but the stored text is the file's own."""
    ev = resolve(draft(quote="ADMIN,      MEMBER"), ck, submission_id="s")
    assert ev is not None
    assert ev.quote == "ADMIN,\n    MEMBER"
    assert ev.quote in SOURCE  # exact substring: this is what lets the UI highlight it


def test_commit_sha_comes_from_the_checkout_not_the_model(ck):
    ev = resolve(draft(), ck, submission_id="s")
    assert ev.locator.commitSha == SHA


# --- hostile and degenerate drafts ---------------------------------------------


def test_path_escaping_the_checkout_is_dropped(ck, tmp_path):
    (tmp_path.parent / "secrets.txt").write_text("ADMIN,\n    MEMBER")
    assert resolve(draft(path="../secrets.txt"), ck, submission_id="s") is None
    assert resolve(draft(path="../../etc/passwd"), ck, submission_id="s") is None


def test_empty_quote_matches_nothing(ck):
    assert resolve(draft(quote=""), ck, submission_id="s") is None
    assert resolve(draft(quote="   \n  "), ck, submission_id="s") is None


def test_missing_file_is_dropped(ck):
    assert resolve(draft(path="src/main/java/app/model/Nope.java"), ck, submission_id="s") is None


def test_line_numbers_past_end_of_file_are_clamped_not_crashed(ck):
    """Clamped to the last line, then rescued from the window around it — which here is the whole file."""
    ev = resolve(draft(start=900, end=1200), ck, submission_id="s")
    assert ev is not None
    assert (ev.locator.startLine, ev.locator.endLine) == (4, 5)


def test_clamped_rescue_stays_bounded_in_a_long_file(ck):
    """The same clamp in a long file cannot reach back to the top: the window is still ±10 lines."""
    (ck.root / "long.txt").write_text(SOURCE + "\n".join(f"line {i}" for i in range(1, 400)))
    assert resolve(draft(path="long.txt", start=900, end=1200), ck, submission_id="s") is None


def test_whole_file_range_still_narrows_the_quote(ck):
    ev = resolve(draft(start=1, end=6), ck, submission_id="s")
    assert ev is not None
    assert (ev.locator.startLine, ev.locator.endLine) == (1, 6)  # the model's region of interest is kept
    assert ev.quote == "ADMIN,\n    MEMBER"  # but the quote is only what was matched


# --- doc_span ------------------------------------------------------------------


def test_doc_span_resolves_against_the_cited_page(ck):
    d = LocatorDraft(locator=DocSpan(artifactId="a", page=9), quote="full RBAC across all five user tiers")
    ev = resolve(d, ck, submission_id="s")
    assert ev is not None and ev.quote == "full RBAC across all five user tiers"


def test_doc_span_on_the_wrong_page_is_dropped(ck):
    """No cross-page rescue: AGENTS.md forbids storing a doc_span whose quote is not on that page."""
    d = LocatorDraft(locator=DocSpan(artifactId="a", page=3), quote="full RBAC across all five user tiers")
    assert resolve(d, ck, submission_id="s") is None


# --- captures the model did not make -------------------------------------------


def test_http_capture_must_match_one_a_probe_recorded(tmp_path):
    probe_ev = Evidence(submissionId="s", provenance="probe", probeId="deploy",
                        quote="<title>TaskFlow</title>",
                        locator=HttpCapture(url="https://taskflow.example.app", status=200,
                                            capturedAt="2026-09-18T14:02:11Z", title="TaskFlow"))
    ck = Checkout(root=tmp_path, commitSha=SHA, captures=(probe_ev,))

    ok = LocatorDraft(locator=HttpCapture(url="https://taskflow.example.app", status=200,
                                          capturedAt="2026-09-18T14:02:11Z"), quote="TaskFlow")
    ev = resolve(ok, ck, submission_id="s")
    assert ev is not None and ev.quote == "TaskFlow"

    invented = LocatorDraft(locator=HttpCapture(url="https://evil.example.app", status=200,
                                                capturedAt="2026-09-18T14:02:11Z"), quote="TaskFlow")
    assert resolve(invented, ck, submission_id="s") is None


def test_git_object_must_match_one_a_probe_recorded(tmp_path):
    probe_ev = Evidence(submissionId="s", provenance="probe", probeId="git", quote="initial import",
                        locator=GitObject(commitSha=SHA, authorHash="ab12cd34ef56", committedAt="2026-09-17T09:00:00Z"))
    ck = Checkout(root=tmp_path, commitSha=SHA, captures=(probe_ev,))
    good = LocatorDraft(locator=GitObject(commitSha=SHA, authorHash="ab12cd34ef56",
                                          committedAt="2026-09-17T09:00:00Z"), quote="initial import")
    assert resolve(good, ck, submission_id="s") is not None
    bad = LocatorDraft(locator=GitObject(commitSha="0" * 40, authorHash="ab12cd34ef56",
                                         committedAt="2026-09-17T09:00:00Z"), quote="initial import")
    assert resolve(bad, ck, submission_id="s") is None


# --- resolve_all ---------------------------------------------------------------


def test_resolve_all_splits_kept_from_dropped(ck):
    kept, dropped = resolve_all([draft(), draft(quote="not in this file")], ck, submission_id="s")
    assert len(kept) == 1 and len(dropped) == 1
    assert dropped[0].quote == "not in this file"


# --- the matching primitives ---------------------------------------------------


def test_normalise_collapses_whitespace():
    assert normalise("  a\n\t b  ") == "a b"


def test_reread_returns_the_haystacks_own_text():
    assert reread("a   b\tc", "a b c") == "a   b\tc"
    assert reread("abc", "xyz") is None
    assert reread("abc", "") is None


# --- invisible characters ---------------------------------------------------------
# A live run cited `type: redis` against a file holding `type: red<ZWSP>is`. A model cannot see a
# zero-width character, so it can never reproduce one; letting that kill a citation would break
# any submission carrying a BOM, an emoji joiner or Indic formatting.


@pytest.mark.parametrize("hidden,quote", [
    ("    type: red​is", "type: redis"),          # zero-width space
    ("﻿package app;", "package app;"),            # byte-order mark
    ("a b", "a b"),                               # non-breaking space
    ("count‎ = 1", "count = 1"),                  # left-to-right mark
])
def test_invisible_characters_do_not_break_a_real_citation(hidden, quote):
    assert reread(hidden, quote) is not None


def test_the_stored_quote_still_carries_the_hidden_characters(ck):
    """Folded for matching only. The evaluator must see the tampered bytes, not a cleaned copy."""
    (ck.root / "application.yml").write_text("cache:\n    type: red​is\n", encoding="utf-8")
    d = LocatorDraft(locator=FileRange(path="application.yml", startLine=1, endLine=2, commitSha="x"),
                     quote="cache:\n    type: redis")
    ev = resolve(d, ck, submission_id="s")
    assert ev is not None
    assert "​" in ev.quote


def test_folding_invisibles_does_not_weaken_the_injection_probe():
    """The security signal lives in the probe, not in the resolver's strictness."""
    from repoman.probes import injection

    assert injection._scan_text("  type: red​is")  # still flagged as hidden characters
