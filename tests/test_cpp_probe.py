"""The C++ feature probe: deterministic, skeleton-aware, evidence for every hit."""

from __future__ import annotations

from pathlib import Path

from repoman.core.types import Submission
from repoman.probes import cpp

REPO_A = Path("""
class Animal {
public:
    virtual void speak() { }
    virtual ~Animal() { }
};

class Dog: public Animal {
public:
    void speak() override { }
};

class Toolbox {
private:
    int size;
public:
    template<typename T>
    T identity(const T& x) { return x; }
    Toolbox& operator+(const Toolbox& o) { return *this; }
};

int main() {
    Animal* a = new Dog();
    delete a;
    std::unique_ptr<Dog> d = std::make_unique<Dog>();
    std::vector<int> nums;
    try { throw 1; } catch (int e) { }
    // TODO: implement this
    return 0;
}
""")

SUB = Submission(id="s", batchId="b", source="dir", commitSha="x")


def test_features_are_detected_with_locators(tmp_path):
    (tmp_path / "student.cpp").write_text(str(REPO_A))
    r = cpp.probe(tmp_path, SUB)
    hit = {k for k, v in r.data["counts"].items() if v}
    assert {"classes", "inheritance", "virtual", "override", "virtual_dtor", "encapsulation",
            "templates", "operator_overload", "smart_pointers", "raw_new", "raw_delete",
            "exceptions", "stl_containers"} <= hit
    assert r.data["classes"] == ["Animal", "Dog", "Toolbox"]
    assert all(e.provenance == "probe" and e.probeId == "cpp" for e in r.evidence)
    assert all(e.locator.commitSha == "x" for e in r.evidence)


def test_no_files_is_a_quiet_result(tmp_path):
    r = cpp.probe(tmp_path, SUB)
    assert r.data["files"] == 0 and not r.evidence


def test_skeleton_lines_are_not_counted_as_the_students(tmp_path):
    skeleton = "class Base {\npublic:\n    virtual void go() { }\n};\n"
    student_added = skeleton + "class Derived: public Base {\npublic:\n    void go() override { }\n};\n"
    (tmp_path / "s.cpp").write_text(student_added)
    without = cpp.probe(tmp_path, SUB)
    with_baseline = cpp.probe(tmp_path, SUB, baseline=skeleton)
    assert without.data["counts"]["classes"] == 2
    assert with_baseline.data["counts"]["classes"] == 1  # only Derived is theirs
    assert with_baseline.data["studentLines"] < without.data["lines"]


def test_whitespace_differences_do_not_break_skeleton_matching(tmp_path):
    skeleton = "class  Base {\n  public:\n};\n"
    (tmp_path / "s.cpp").write_text("class Base {\npublic:\n};\nclass Mine {\n};\n")
    r = cpp.probe(tmp_path, SUB, baseline=skeleton)
    assert r.data["classes"] == ["Mine"]


def test_derive_baseline_finds_lines_shared_by_most_submissions():
    sources = ["#include <iostream>\nclass A { };\n", "#include <iostream>\nclass B { };\n",
              "#include <iostream>\nclass C { };\n"]
    baseline = cpp.derive_baseline(sources, share=0.8)
    assert "#include <iostream>" in baseline
    assert "class A" not in baseline  # only one submission has it


def test_derive_baseline_needs_at_least_three_submissions():
    assert cpp.derive_baseline(["class A {};", "class A {};"]) == ""


def test_scaffolding_markers_are_reported_as_unfinished(tmp_path):
    (tmp_path / "s.cpp").write_text("int f() {\n    // TODO: implement this\n    return 0;\n}\n")
    r = cpp.probe(tmp_path, SUB)
    assert r.data["todos"] == 1 and "unfinished" in r.summary


def test_real_domjudge_submissions_are_readable():
    """Smoke test against the actual course export, if it is present in this checkout."""
    base = Path("domjudge submissions")
    if not base.is_dir():
        return
    sub_dirs = sorted(d for d in base.iterdir() if d.is_dir())[:5]
    for d in sub_dirs:
        r = cpp.probe(d, SUB)
        assert r.data["files"] >= 1
        assert r.data["counts"]["inheritance"] >= 1  # the shared skeleton for this assignment uses it
