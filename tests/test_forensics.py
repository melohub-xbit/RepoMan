"""Contribution forensics: fork/reskin detection and cross-submission similarity.

`docs/01` §2 calls this the feature that alone justifies installing RepoMan. Both checks accuse
nobody — they report a shape a human then interprets — so the tests care as much about what stays
quiet as about what fires.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from repoman.core.types import Submission
from repoman.probes import fork, git, similarity


def submission(**kw) -> Submission:
    return Submission(id="s", batchId="b", source="github", commitSha="abc", **kw)


def make_repo(root: Path, commits: list[dict[str, str]]) -> Path:
    """A real git repository; each dict is one commit's {filename: content}."""
    root.mkdir(parents=True, exist_ok=True)
    env = {"GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@e.com",
           "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@e.com"}

    def run(*args):
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True,
                       env={**__import__("os").environ, **env})

    run("init", "-q", ".")
    for i, files in enumerate(commits):
        for name, content in files.items():
            p = root / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
        run("add", "-A")
        run("commit", "-qm", f"commit {i}")
    return root


# --- fork and reskin ---------------------------------------------------------------


class _Offline:
    def get(self, url):
        raise RuntimeError("no network in tests")


def test_a_project_that_arrived_whole_is_flagged(tmp_path):
    """Real work accumulates. A copy shows up complete in its first commit."""
    repo = make_repo(tmp_path / "dump", [
        {f"src/f{i}.py": f"def f{i}():\n    return {i}\n" for i in range(8)},
        {"src/f0.py": "def f0():\n    return 99\n"},
    ])
    data = git.probe(repo, submission()).data
    result = fork.probe(repo, submission(), data, client=_Offline())
    assert "FORK_SUSPECTED" in result.flags
    assert "first commit" in result.summary
    assert result.evidence and result.evidence[0].locator.kind == "git_object"


def test_a_project_that_grew_is_not_flagged(tmp_path):
    repo = make_repo(tmp_path / "grown", [
        {"src/a.py": "a = 1\n"},
        {"src/b.py": "b = 2\n"}, {"src/c.py": "c = 3\n"}, {"src/d.py": "d = 4\n"},
        {"src/e.py": "e = 5\n"}, {"src/f.py": "f = 6\n"}, {"src/g.py": "g = 7\n"},
    ])
    data = git.probe(repo, submission()).data
    assert "FORK_SUSPECTED" not in fork.probe(repo, submission(), data, client=_Offline()).flags


def test_a_tiny_repository_is_not_accused(tmp_path):
    """Two files in one commit is a starter project, not evidence of anything."""
    repo = make_repo(tmp_path / "tiny", [{"a.py": "a = 1\n", "b.py": "b = 2\n"}])
    data = git.probe(repo, submission()).data
    assert "FORK_SUSPECTED" not in fork.probe(repo, submission(), data, client=_Offline()).flags


def test_no_history_means_no_claim(tmp_path):
    (tmp_path / "x.py").write_text("x = 1\n", encoding="utf-8")
    result = fork.probe(tmp_path, submission(), git.probe(tmp_path, submission()).data)
    assert result.flags == [] and "cannot be checked" in result.summary


def test_github_reporting_a_fork_is_enough_on_its_own(tmp_path):
    repo = make_repo(tmp_path / "gh", [{"a.py": "a = 1\n"}, {"b.py": "b = 2\n"}])

    class _Fork:
        def get(self, url):
            class R:
                status_code = 200

                @staticmethod
                def json():
                    return {"fork": True, "parent": {"full_name": "upstream/original"}}
            return R()

    data = git.probe(repo, submission()).data
    sub = submission(repoUrl="https://github.com/team/project")
    result = fork.probe(repo, sub, data, client=_Fork())
    assert "FORK_SUSPECTED" in result.flags
    assert "upstream/original" in result.summary


def test_an_unreachable_github_is_not_evidence(tmp_path):
    repo = make_repo(tmp_path / "off", [{"a.py": "a = 1\n"}, {"b.py": "b = 2\n"}])
    sub = submission(repoUrl="https://github.com/team/project")
    result = fork.probe(repo, sub, git.probe(repo, sub).data, client=_Offline())
    assert result.flags == []
    assert "could not be checked" in result.summary


def test_only_github_urls_are_queried():
    assert fork._api_url("https://gitlab.com/a/b") is None
    assert fork._api_url(None) is None
    assert fork._api_url("https://github.com/team/project").endswith("/repos/team/project")


# --- cross-submission similarity ----------------------------------------------------

SERVICE = """\
package app.service;

public class OrderService {
    public Order place(Order o) {
        validate(o);
        repository.save(o);
        return o;
    }
}
"""


def fp(tmp_path, name, files) -> dict[str, str]:
    root = tmp_path / name
    for path, content in files.items():
        p = root / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return similarity.fingerprint(root)


def test_two_submissions_sharing_a_service_layer_are_paired(tmp_path):
    files = {f"src/S{i}.java": SERVICE.replace("OrderService", f"Service{i}") for i in range(4)}
    a = fp(tmp_path, "a", files)
    b = fp(tmp_path, "b", files)
    overlaps = similarity.compare({"run-a": a, "run-b": b})
    assert len(overlaps) == 1
    assert overlaps[0].share == 1.0 and len(overlaps[0].shared) == 4


def test_a_renamed_copy_is_still_matched(tmp_path):
    """Matching is by content, so filing the same code under a new name does not hide it."""
    a = fp(tmp_path, "ra", {f"src/S{i}.java": SERVICE.replace("place", f"place{i}") for i in range(4)})
    b = fp(tmp_path, "rb", {f"lib/Renamed{i}.java": SERVICE.replace("place", f"place{i}")
                            for i in range(4)})
    overlaps = similarity.compare({"x": a, "y": b})
    assert len(overlaps) == 1 and len(overlaps[0].shared) == 4


def test_reformatting_does_not_hide_a_copy(tmp_path):
    a = fp(tmp_path, "fa", {f"S{i}.java": SERVICE.replace("place", f"p{i}") for i in range(4)})
    squashed = {f"S{i}.java": SERVICE.replace("place", f"p{i}").replace("\n", "\n\n    ")
                for i in range(4)}
    b = fp(tmp_path, "fb", squashed)
    assert len(similarity.compare({"x": a, "y": b})) == 1


def test_unrelated_submissions_are_not_paired(tmp_path):
    a = fp(tmp_path, "ua", {f"a{i}.java": SERVICE.replace("Order", f"Alpha{i}") for i in range(5)})
    b = fp(tmp_path, "ub", {f"b{i}.java": SERVICE.replace("Order", f"Beta{i}") for i in range(5)})
    assert similarity.compare({"x": a, "y": b}) == []


def test_one_shared_file_is_not_enough(tmp_path):
    """Everyone's project contains some identical file. A pair needs real overlap."""
    common = {"Shared.java": SERVICE}
    a = fp(tmp_path, "sa", {**common, **{f"a{i}.java": SERVICE.replace("Order", f"A{i}") for i in range(9)}})
    b = fp(tmp_path, "sb", {**common, **{f"b{i}.java": SERVICE.replace("Order", f"B{i}") for i in range(9)}})
    assert similarity.compare({"x": a, "y": b}) == []


def test_trivial_and_boilerplate_files_are_never_fingerprinted(tmp_path):
    prints = fp(tmp_path, "triv", {"__init__.py": SERVICE, "tiny.py": "x = 1\n",
                                   "real.java": SERVICE})
    assert "__init__.py" not in prints and "tiny.py" not in prints
    assert "real.java" in prints


def test_comparison_is_stable_regardless_of_pair_order(tmp_path):
    files = {f"S{i}.java": SERVICE.replace("place", f"p{i}") for i in range(4)}
    a, b = fp(tmp_path, "oa", files), fp(tmp_path, "ob", files)
    one = similarity.compare({"run-a": a, "run-b": b})[0]
    two = similarity.compare({"run-b": b, "run-a": a})[0]
    assert (one.a, one.b) == (two.a, two.b)  # sorted, so the pair reads the same either way


def test_an_empty_batch_compares_cleanly():
    assert similarity.compare({}) == []
    assert similarity.compare({"only": {"a.java": "h"}}) == []
