"""Mass insertion: one archive, many submissions. The DOMjudge/Moodle/Classroom export shape."""

from __future__ import annotations

import io
import time
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from repoman.core.types import Batch, Requirement, Rubric, Scale
from tests.test_web import client  # noqa: F401  (reuse the workspace fixture)


def make_archive(students: dict[str, str]) -> bytes:
    """A .zip with one top-level folder per student, one .cpp inside — the DOMjudge export shape."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, code in students.items():
            zf.writestr(f"{name}/submission.cpp", code)
    return buf.getvalue()


SKELETON = "#include <iostream>\nclass Base {\npublic:\n    virtual void go() { }\n};\n"
STUDENT_A = SKELETON + "class A: public Base {\npublic:\n    void go() override { }\n};\nint main() { return 0; }\n"
STUDENT_B = SKELETON + "class B: public Base {\npublic:\n    void go() override { }\n    int* p = new int;\n};\nint main() { return 0; }\n"


@pytest.fixture
def bulk_batch(tmp_path):
    from repoman.store import LocalStore
    from repoman.web import app as web

    store = LocalStore(tmp_path)
    web.store = store  # module-level swap, same pattern test_web.client uses via monkeypatch elsewhere
    rubric = Rubric(id="rb", sourceText="1. Uses inheritance.", compiledBy="test", requirements=[
        Requirement(id="q1", rubricId="rb", title="Inheritance", statement="A class inherits from a base class.",
                    weight=10.0, scale=Scale(kind="check"), verifiable=True)])
    batch = Batch(id="bulk1", name="Assignment 1", rubricId="rb")
    store.put_json("rubrics/rb.json", rubric)
    store.put_json("batches/bulk1.json", batch)
    return TestClient(web.app), store


def test_bulk_archive_creates_one_run_per_folder(bulk_batch, monkeypatch):
    c, store = bulk_batch
    from repoman.web import app as web
    monkeypatch.setattr(web.pipeline, "run_submission", lambda *a, **k: "stub")  # no model in this test

    archive = make_archive({"student_a": STUDENT_A, "student_b": STUDENT_B})
    r = c.post("/batches/bulk1/submissions", files={"bulk": ("submissions.zip", archive, "application/zip")},
              data={"repo_url": "", "urls": ""}, follow_redirects=False)
    assert r.status_code == 303
    for _ in range(30):
        batch = Batch.model_validate(store.get_json("batches/bulk1.json"))
        if len(batch.runIds) == 2:
            break
        time.sleep(0.1)
    assert len(batch.runIds) == 2
    subs = [store.get_json(f"runs/{rid}/submission.json") for rid in batch.runIds]
    labels = sorted(s["identity"]["team"] for s in subs)
    assert labels == ["student_a", "student_b"]
    assert all(s["source"] == "dir" for s in subs)


def test_bulk_archive_derives_a_skeleton_when_none_given(bulk_batch, monkeypatch):
    c, store = bulk_batch
    from repoman.web import app as web
    seen_baselines = []
    monkeypatch.setattr(web.pipeline, "run_submission",
                        lambda *a, **k: seen_baselines.append(k.get("baseline")) or "stub")

    students = {f"s{i}": SKELETON + f"class C{i}: public Base {{ public: void go() override {{ }} }};\n"
               for i in range(4)}
    archive = make_archive(students)
    c.post("/batches/bulk1/submissions", files={"bulk": ("s.zip", archive, "application/zip")},
          data={"repo_url": "", "urls": ""}, follow_redirects=False)
    for _ in range(30):
        if len(seen_baselines) == 4:
            break
        time.sleep(0.1)
    assert len(seen_baselines) == 4
    assert all(b for b in seen_baselines)
    assert "class Base" in seen_baselines[0]  # the shared skeleton, recovered
    batch = Batch.model_validate(store.get_json("batches/bulk1.json"))
    assert batch.baseline  # persisted for the next import into this batch


def test_explicit_skeleton_upload_is_used_over_derivation(bulk_batch, monkeypatch):
    c, store = bulk_batch
    from repoman.web import app as web
    seen = []
    monkeypatch.setattr(web.pipeline, "run_submission", lambda *a, **k: seen.append(k.get("baseline")) or "stub")

    archive = make_archive({"student_a": STUDENT_A})
    c.post("/batches/bulk1/submissions",
          files={"bulk": ("s.zip", archive, "application/zip"),
                 "skeleton": ("skeleton.cpp", SKELETON.encode(), "text/plain")},
          data={"repo_url": "", "urls": ""}, follow_redirects=False)
    for _ in range(30):
        if seen:
            break
        time.sleep(0.1)
    assert seen and seen[0].strip() == SKELETON.strip()


def test_blind_batch_strips_labels(bulk_batch, monkeypatch):
    c, store = bulk_batch
    from repoman.web import app as web
    monkeypatch.setattr(web.pipeline, "run_submission", lambda *a, **k: "stub")
    batch = Batch.model_validate(store.get_json("batches/bulk1.json"))
    batch.blind = True
    store.put_json("batches/bulk1.json", batch)

    archive = make_archive({"real_student_name": STUDENT_A})
    c.post("/batches/bulk1/submissions", files={"bulk": ("s.zip", archive, "application/zip")},
          data={"repo_url": "", "urls": ""}, follow_redirects=False)
    for _ in range(30):
        b2 = Batch.model_validate(store.get_json("batches/bulk1.json"))
        if b2.runIds:
            break
        time.sleep(0.1)
    sub = store.get_json(f"runs/{b2.runIds[0]}/submission.json")
    assert sub["identity"] is None
    page = c.get(f"/runs/{b2.runIds[0]}").text
    assert "real_student_name" not in page


def test_bulk_import_never_writes_under_the_batches_prefix(bulk_batch, monkeypatch):
    """store.list("batches") globs that prefix recursively for every batch's JSON (app.py: index()).

    A bulk import's unpacked files must live somewhere store.list("batches") never sees them, or
    the very next visit to "All batches" 500s trying to Batch.model_validate a student's .cpp file.
    """
    c, store = bulk_batch
    from repoman.web import app as web
    monkeypatch.setattr(web.pipeline, "run_submission", lambda *a, **k: "stub")

    archive = make_archive({"student_a": STUDENT_A, "student_b": STUDENT_B})
    c.post("/batches/bulk1/submissions", files={"bulk": ("submissions.zip", archive, "application/zip")},
          data={"repo_url": "", "urls": ""}, follow_redirects=False)
    for _ in range(30):
        if len(Batch.model_validate(store.get_json("batches/bulk1.json")).runIds) == 2:
            break
        time.sleep(0.1)

    for key in store.list("batches"):
        assert not key.startswith("batches/bulk1/"), f"bulk import wrote under the batches/ prefix: {key}"
        Batch.model_validate(store.get_json(key))  # every key list("batches") returns must parse

    # exactly the two Batch listing route depends on: this batch, plus whatever the base fixture writes
    assert "batches/bulk1.json" in store.list("batches")
