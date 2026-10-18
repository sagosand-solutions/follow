# SPDX-License-Identifier: AGPL-3.0-or-later
"""The release gate: pool ledger chain, release decision, read-only inventory export and the exact-tree scan.

SYNTHETIC DATA ONLY (ids, hashes and files are invented for the tests). Negative first: each case breaks exactly one binding and must
be refused (exit 2) or reported (exit 1); the one green case needs every binding to hold.
"""
import argparse
import copy
import hashlib
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from server import pool, pool_artifact, pool_ledger, pool_release
from server.pool_ledger import LedgerError
from server.tests.scoring_support import build_scoring_fixture
from server.tests.support import run_cli_captured

CANDIDATE = "c" * 40
PUBLIC = {"index.html": b"<!doctype html><title>synthetic site</title>\n", "data/index.json": b'{"courses": []}\n', "js/app.js": b"// synthetic\n"}


def snapshot(source: str, target: str) -> str:
    """A checkpointed, rollback-mode copy of a database (VACUUM INTO), the way an operator takes a snapshot for the export."""
    import sqlite3
    conn = sqlite3.connect(source)
    try:
        conn.execute("VACUUM INTO ?", (target,))
    finally:
        conn.close()
    return target


def catalog_file(directory: Path, name: str, catalog, drop=(), extra=None, no_exposure=()):
    courses = []
    for course in catalog.course_ids:
        items = [{"question_id": q.question_id, "product_id": q.product_id, "content_sha256": q.content_sha256}
                 for q in catalog.questions.values() if q.course_id == course and q.question_id not in drop]
        if extra and extra["course"] == course:
            items.append({"question_id": extra["id"], "product_id": None if catalog.family == "free" else "python-lifetime",
                          "content_sha256": extra["sha"]})
        if items:
            courses.append({"course_id": course, "N_c": len(items), "questions": items})
    exposures = [{"kind": "question", "id": i["question_id"], "content_sha256": i["content_sha256"], "affected_question_ids": [i["question_id"]]}
                 for c in courses for i in c["questions"] if i["question_id"] not in no_exposure]
    path = directory / name
    path.write_text(json.dumps({"schema_version": 1, "catalog_version": catalog.catalog_version if not extra else extra["version"],
                                "rules_version": "r2", "family": catalog.family, "courses": courses, "exposures": exposures}))
    return str(path)


class Scenario(unittest.TestCase):
    """Release 1 (first ledger from catalogs) and release 2 (after a freeze and a retirement, plus one brand-new question)."""

    def setUp(self):
        self.fx, tmp = build_scoring_fixture(users=("u1",), free=(("syn-free", 4),))
        self.addCleanup(tmp.cleanup)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.trusted = self.dir / "trusted"
        (self.trusted / "pool-ledger").mkdir(parents=True)
        (self.trusted / "release-decisions").mkdir()
        self.public = self.dir / "public"
        for rel, data in PUBLIC.items():
            (self.public / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.public / rel).write_bytes(data)
        self.cat1 = [catalog_file(self.dir, "official-1.json", self.fx.catalogs["official"]), catalog_file(self.dir, "free-1.json", self.fx.catalogs["free"])]
        self.free_version = self.fx.db().connect().execute("SELECT id FROM ranking_versions WHERE family = 'free'").fetchone()[0]

    # ---- helpers ----
    def decision(self, release_id, ledger, catalogs, *, first, previous=None, released=(), overrides=None, tree=None):
        tree = tree or pool_artifact.read_tree(str(self.public))
        _, digests = pool_release._catalogs(catalogs)
        doc = {"schema": 1, "kind": pool_ledger.DECISION_KIND, "release_id": release_id, "candidate_commit": CANDIDATE, "public_tree": tree["tree_id"],
               "artifact_sha256": tree["artifact_sha256"], "previous_ledger_digest": previous, "ledger_digest": ledger["digest"], "first_ledger": first,
               "no_prior_history_attestation": "decisions.md#2026-10-19-no-history" if first else None, "human_approval_ref": "decisions.md#2026-10-19-scoring-yes",
               "counts": ledger["counts"], "open_versions": [{"family": f, "catalog_version": v, "catalog_digest": d} for f, v, d in digests],
               "released": list(released), "decided_at": "2026-10-19T12:00:00Z"}
        doc.update(overrides or {})
        return doc

    def install(self, ledger=None, decision=None):
        if ledger is not None:
            (self.trusted / "pool-ledger" / f"ledger-{ledger['seq']:06d}.json").write_bytes(pool_ledger.dumps(ledger))
        if decision is not None:
            (self.trusted / "release-decisions" / f"{decision['release_id']}.json").write_text(json.dumps(decision))

    def save(self, name, document):
        path = self.dir / name
        path.write_bytes(pool_ledger.dumps(document))
        return str(path)

    def check(self, release_id, ledger_path, catalogs, **kw):
        kw.setdefault("public_root", str(self.public))
        kw.setdefault("candidate", CANDIDATE)
        return pool_release.check_release(trusted_dir=str(self.trusted), release_id=release_id, ledger_path=ledger_path, catalog_paths=catalogs, **kw)

    def first_release(self):
        ledger = pool_ledger.compose("rel-1", None, None, [pool_release.load_catalog(p) for p in self.cat1])
        decision = self.decision("rel-1", ledger, self.cat1, first=True)
        # "trusted" is where release 1 would already be merged: its decision is read from here, its ledger is the history afterwards
        self.install(None, decision)
        return ledger, decision

    def merge_first(self):
        ledger, decision = self.first_release()
        self.install(ledger, None)
        return ledger

    def second_release(self, *, retire=True, new_question=True):
        first = self.merge_first()
        db = self.fx.db()
        self.addCleanup(db.close)
        if retire:
            pool.freeze(db, self.free_version, reason_code="season-end", now=self.fx.clock[0])
            pool.retire(db, "syn-free-f00", release_id="rel-2", reason_code="exposed", operator_id="op-1", now=self.fx.clock[0])
        inventory = pool_release.export_inventory(snapshot(self.fx.path, str(self.dir / f"snap-{len(list(self.dir.glob('snap-*')))}.db")))
        self.inventory_path = self.save("inventory-2.json", inventory)
        free = self.fx.catalogs["free"]
        extra = {"id": "syn-free-new", "course": "syn-free", "sha": hashlib.sha256(b"synthetic new question").hexdigest(), "version": "free-2"} if new_question else None
        self.cat2 = [catalog_file(self.dir, "official-2.json", self.fx.catalogs["official"]),
                     catalog_file(self.dir, "free-2.json", free, drop=("syn-free-f00",) if retire else (), extra=extra) if extra or retire else self.cat1[1]]
        ledger = pool_ledger.compose("rel-2", first, inventory, [pool_release.load_catalog(p) for p in self.cat2])
        return first, inventory, ledger


class FirstReleaseTests(Scenario):
    def test_a_first_ledger_with_the_attestation_the_exact_tree_and_a_clean_tree_is_green(self):
        ledger, decision = self.first_release()
        path = self.save("candidate-ledger.json", ledger)
        result = self.check("rel-1", path, self.cat1)
        self.assertEqual(result["findings"], [])
        self.assertEqual((result["receipt"]["seq"], result["receipt"]["ledger"], result["receipt"]["files"]), (1, ledger["digest"], 3))
        self.assertEqual(ledger["counts"]["free"]["ranked"], 4)
        self.assertEqual(ledger["counts"]["official"]["ranked"], len(self.fx.questions["official"]))

    def test_a_first_ledger_is_refused_once_a_trusted_history_exists_and_without_the_attestation(self):
        ledger, decision = self.first_release()
        self.install(ledger, None)                                               # now the history holds ledger 1
        with self.assertRaises(LedgerError) as caught:
            self.check("rel-1", self.save("l.json", ledger), self.cat1)
        self.assertIn("no re-bootstrap", str(caught.exception))
        for broken in ({"no_prior_history_attestation": None}, {"previous_ledger_digest": "a" * 64}, {"first_ledger": False}):
            with self.assertRaises(LedgerError):
                pool_ledger.validate_decision({**decision, **broken})

    def test_a_first_ledger_takes_no_database_inventory(self):
        ledger, _ = self.first_release()
        inventory = self.save("inv.json", pool_release.export_inventory(snapshot(self.fx.path, str(self.dir / f"snap-{len(list(self.dir.glob('snap-*')))}.db"))))
        with self.assertRaises(LedgerError):
            self.check("rel-1", self.save("l.json", ledger), self.cat1, inventory_path=inventory)


class ChainTests(Scenario):
    def test_the_second_release_after_a_freeze_a_retirement_and_a_new_question_is_green(self):
        first, inventory, ledger = self.second_release()
        decision = self.decision("rel-2", ledger, self.cat2, first=False, previous=first["digest"])
        self.install(None, decision)
        result = self.check("rel-2", self.save("l2.json", ledger), self.cat2, inventory_path=self.inventory_path)
        self.assertEqual(result["findings"], [])
        self.assertEqual((ledger["counts"]["free"]["retired"], ledger["counts"]["free"]["ranked"]), (1, 4))      # f01-f03 + the new one ranked
        self.assertEqual([(e["question_id"], e["from"], e["to"]) for e in ledger["events"]], [("syn-free-f00", "ranked", "retired")])

    def test_the_baseline_is_the_trusted_histories_last_ledger_not_something_the_candidate_brings(self):
        first, inventory, ledger = self.second_release()
        self.install(None, self.decision("rel-2", ledger, self.cat2, first=False, previous=first["digest"]))
        shutil.rmtree(self.trusted / "pool-ledger")                               # no trusted history at all
        with self.assertRaises(LedgerError):
            self.check("rel-2", self.save("l2.json", ledger), self.cat2, inventory_path=self.inventory_path)
        (self.trusted / "pool-ledger").mkdir()
        with self.assertRaises(LedgerError):                                      # a decision naming another previous digest
            self.install(first, self.decision("rel-2", ledger, self.cat2, first=False, previous="b" * 64))
            self.check("rel-2", self.save("l2.json", ledger), self.cat2, inventory_path=self.inventory_path)

    def test_every_way_of_losing_or_rewriting_history_is_refused(self):
        first, inventory, ledger = self.second_release()
        self.install(None, self.decision("rel-2", ledger, self.cat2, first=False, previous=first["digest"]))

        def reseal(mutate):
            doc = copy.deepcopy(ledger)
            mutate(doc)
            doc["counts"] = pool_ledger.counts_of(doc["questions"])
            doc["lifecycle_events_digest"] = pool_ledger.events_digest(doc["events"])
            return pool_ledger.seal(doc)
        row = lambda doc, qid: next(r for r in doc["questions"] if r["question_id"] == qid)
        refs = {"clone_audit_ref": "c-1", "license_ok_ref": "l-1", "exposure_audit_ref": "e-1"}
        later = copy.deepcopy(ledger)                                                # release 3 tries to take a retirement back
        later.update(seq=3, release_id="rel-3", previous_digest=ledger["digest"], events=[])
        next(r for r in later["questions"] if r["question_id"] == "syn-free-f00").update(state="ranked", clone_audit_ref=None)
        later["counts"] = pool_ledger.counts_of(later["questions"])
        later["lifecycle_events_digest"] = pool_ledger.events_digest([])
        with self.assertRaises(LedgerError):
            pool_ledger.verify_step(ledger, pool_ledger.seal({k: v for k, v in later.items() if k != "digest"}))
        cases = {
            "a registered question disappears": lambda d: d["questions"].remove(row(d, "syn-free-f01")),
            "a hash is rewritten": lambda d: row(d, "syn-free-f01").update(content_sha256="9" * 64),
            "a family is rewritten": lambda d: row(d, "syn-free-f01").update(family="official"),
            "ranked jumps to free with no retire": lambda d: (row(d, "syn-free-f01").update(state="free", **refs),
                                                              d["events"].append({"question_id": "syn-free-f01", "from": "retired", "to": "free", "release_id": "rel-2",
                                                                                  "reason_code": "reviewed", **refs})),
            "a state change has no event": lambda d: d["events"].clear(),
            "an event has no state change": lambda d: d["events"].append({"question_id": "syn-free-f02", "from": "ranked", "to": "retired", "release_id": "rel-2",
                                                                            "reason_code": "exposed", "clone_audit_ref": None, "license_ok_ref": None, "exposure_audit_ref": None}),
            "a new question enters as retired": lambda d: row(d, "syn-free-new").update(state="retired"),
            "the sequence jumps": lambda d: d.update(seq=5),
            "the previous digest is wrong": lambda d: d.update(previous_digest="d" * 64),
        }
        for label, mutate in cases.items():
            with self.subTest(label=label):
                try:
                    candidate = reseal(mutate)
                except Exception:
                    continue                                                      # the mutation itself is not representable
                with self.assertRaises(LedgerError):
                    pool_ledger.verify_step(first, candidate)

    def test_a_free_release_needs_a_full_ordered_chain_all_three_references_and_the_decisions_approval(self):
        first, inventory, ledger = self.second_release()
        db = self.fx.db()
        pool.free(db, "syn-free-f00", release_id="rel-3", reason_code="reviewed", operator_id="op-1", now=self.fx.clock[0],
                  clone_audit_ref="c-9", license_ok_ref="l-9", exposure_audit_ref="e-9")
        inventory = pool_release.export_inventory(snapshot(self.fx.path, str(self.dir / f"snap-{len(list(self.dir.glob('snap-*')))}.db")))
        self.inventory_path = self.save("inventory-3.json", inventory)
        full = pool_ledger.compose("rel-2", first, inventory, [pool_release.load_catalog(p) for p in self.cat2])
        self.assertEqual([(e["from"], e["to"]) for e in full["events"]], [("ranked", "retired"), ("retired", "free")], "both events, no shortcut")
        released = [{"question_id": "syn-free-f00", "content_sha256": next(r["content_sha256"] for r in full["questions"] if r["question_id"] == "syn-free-f00"),
                     "clone_audit_ref": "c-9", "license_ok_ref": "l-9", "exposure_audit_ref": "e-9"}]
        good = self.decision("rel-2", full, self.cat2, first=False, previous=first["digest"], released=released)
        self.install(None, good)
        path = self.save("l3.json", full)
        self.assertEqual(self.check("rel-2", path, self.cat2, inventory_path=self.inventory_path)["findings"], [])
        for label, decision in {"no approval": self.decision("rel-2", full, self.cat2, first=False, previous=first["digest"], released=[]),
                                "other references": self.decision("rel-2", full, self.cat2, first=False, previous=first["digest"],
                                                                  released=[{**released[0], "license_ok_ref": "l-other"}]),
                                "an approval for nothing": self.decision("rel-2", full, self.cat2, first=False, previous=first["digest"],
                                                                         released=released + [{**released[0], "question_id": "syn-free-f01"}])}.items():
            with self.subTest(label=label):
                self.install(None, decision)
                with self.assertRaises(LedgerError):
                    self.check("rel-2", path, self.cat2, inventory_path=self.inventory_path)
        shortcut = copy.deepcopy(full)
        shortcut["events"] = shortcut["events"][1:]                               # drop the retire event: ranked -> free is not a move
        shortcut["lifecycle_events_digest"] = pool_ledger.events_digest(shortcut["events"])
        with self.assertRaises(LedgerError):
            pool_ledger.verify_step(first, pool_ledger.seal({k: v for k, v in shortcut.items() if k != "digest"}))

    def test_the_stored_history_must_itself_be_a_valid_unbroken_chain(self):
        first, inventory, ledger = self.second_release()
        self.install(ledger, None)                                                # ledger 2 sits next to ledger 1
        self.assertEqual(len(pool_ledger.load_history(str(self.trusted))), 2)
        (self.trusted / "pool-ledger" / "ledger-000001.json").unlink()            # a gap
        with self.assertRaises(LedgerError):
            pool_ledger.load_history(str(self.trusted))
        (self.trusted / "pool-ledger" / "ledger-000001.json").write_bytes(pool_ledger.dumps(first))
        (self.trusted / "pool-ledger" / "notes.txt").write_text("x")
        with self.assertRaises(LedgerError):
            pool_ledger.load_history(str(self.trusted))
        (self.trusted / "pool-ledger" / "notes.txt").unlink()
        (self.trusted / "pool-ledger" / "ledger-000002.json").write_bytes(pool_ledger.dumps(ledger).replace(b'"seq": 2', b'"seq": 3'))
        with self.assertRaises(LedgerError):
            pool_ledger.load_history(str(self.trusted))


class BindingTests(Scenario):
    def ready(self):
        first, inventory, ledger = self.second_release()
        self.ledger2 = ledger
        self.first = first
        self.decision2 = self.decision("rel-2", ledger, self.cat2, first=False, previous=first["digest"])
        self.install(None, self.decision2)
        self.ledger_path = self.save("l2.json", ledger)

    def refused(self, **kw):
        kw.setdefault("inventory_path", self.inventory_path)
        with self.assertRaises(LedgerError):
            self.check("rel-2", kw.pop("ledger_path", self.ledger_path), kw.pop("catalogs", self.cat2), **kw)

    def test_the_decision_binds_candidate_ledger_counts_and_release(self):
        self.ready()
        self.refused(candidate="d" * 40)
        for override in ({"ledger_digest": "e" * 64}, {"counts": pool_ledger.empty_counts()}, {"release_id": "rel-9"}):
            self.install(None, {**self.decision2, **override}) if "release_id" not in override else None
            with self.subTest(override=override):
                self.refused() if "release_id" not in override else None
        self.install(None, self.decision2)

    def test_the_scanned_files_must_be_the_pinned_tree_and_artifact(self):
        self.ready()
        (self.public / "data" / "index.json").write_bytes(b'{"courses": [1]}\n')            # one byte differs
        self.refused()
        (self.public / "data" / "index.json").write_bytes(PUBLIC["data/index.json"])
        self.assertEqual(self.check("rel-2", self.ledger_path, self.cat2, inventory_path=self.inventory_path)["findings"], [])
        (self.public / "js" / "extra.js").write_bytes(b"// added\n")                           # an added file
        self.refused()
        (self.public / "js" / "extra.js").unlink()
        empty = self.dir / "empty-root"
        empty.mkdir()
        self.refused(public_root=str(empty))                                                   # an empty temporary directory
        other = self.dir / "other-site"
        (other / "x").mkdir(parents=True)
        (other / "x" / "y.txt").write_bytes(b"not the release")
        self.refused(public_root=str(other))
        link = self.dir / "link-root"
        link.symlink_to(self.public)
        self.refused(public_root=str(link))

    @unittest.skipUnless(shutil.which("git"), "git is needed")
    def test_the_candidate_commits_public_tree_is_verified_against_git_when_a_repository_is_given(self):
        self.ready()
        repo = self.dir / "repo"
        shutil.copytree(self.public, repo / "public")
        env = {"PATH": os.environ["PATH"], "HOME": str(self.dir), "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"}
        run = lambda *a: subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True, text=True, env=env).stdout.strip()
        run("init", "-q")
        run("add", "-A")
        run("-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "-m", "synthetic")
        commit, tree = run("rev-parse", "HEAD"), run("rev-parse", "HEAD:public")
        self.assertEqual(tree, pool_artifact.read_tree(str(self.public))["tree_id"])
        decision = {**self.decision2, "candidate_commit": commit}
        self.install(None, decision)
        self.assertEqual(self.check("rel-2", self.ledger_path, self.cat2, inventory_path=self.inventory_path, candidate=commit, git_repo=str(repo))["findings"], [])
        (repo / "public" / "js" / "app.js").write_bytes(b"// changed\n")
        run("add", "-A")
        run("-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "-m", "other")
        newer = run("rev-parse", "HEAD")
        self.install(None, {**self.decision2, "candidate_commit": newer})
        with self.assertRaises(LedgerError):                                                   # the files scanned are not that commit's public tree
            self.check("rel-2", self.ledger_path, self.cat2, inventory_path=self.inventory_path, candidate=newer, git_repo=str(repo))
        with self.assertRaises(LedgerError):
            self.check("rel-2", self.ledger_path, self.cat2, inventory_path=self.inventory_path, candidate=commit, git_repo=str(self.dir / "no-repo"))

    def test_the_open_versions_the_decision_lists_are_the_only_authority_for_what_is_ranked(self):
        self.ready()
        self.refused(catalogs=self.cat2[:1])                                                   # a listed version is not supplied
        self.refused(catalogs=self.cat2 + [self.cat1[1]])                                      # an unlisted older version is supplied
        tampered = catalog_file(self.dir, "free-2-tampered.json", self.fx.catalogs["free"], drop=("syn-free-f00", "syn-free-f01"))
        self.refused(catalogs=[self.cat2[0], tampered])                                        # different bytes than the listed digest
        gap = catalog_file(self.dir, "free-2-gap.json", self.fx.catalogs["free"], drop=("syn-free-f00",), no_exposure=("syn-free-f02",),
                           extra={"id": "syn-free-new", "course": "syn-free", "sha": hashlib.sha256(b"synthetic new question").hexdigest(), "version": "free-2"})
        self.refused(catalogs=[self.cat2[0], gap])                                             # an exposure gap
        self.refused(catalogs=[self.cat2[0], self.cat2[0]])
        decision = {**self.decision2, "open_versions": self.decision2["open_versions"][:1]}
        self.install(None, decision)
        self.refused(catalogs=self.cat2[:1])                                                   # ledger ranks questions no listed version holds

    def test_the_database_inventory_is_required_and_must_agree_with_the_ledger(self):
        self.ready()
        with self.assertRaises(LedgerError):
            self.check("rel-2", self.ledger_path, self.cat2)                                  # none supplied
        inventory = pool_ledger.load_inventory(self.inventory_path)
        def tampered(mutate):
            doc = copy.deepcopy(inventory)
            mutate(doc)
            return self.save("inv-t.json", pool_ledger.seal({k: v for k, v in doc.items() if k != "digest"}))
        row = lambda d, q: next(r for r in d["questions"] if r["question_id"] == q)
        for label, mutate in {"a registered question is missing": lambda d: d["questions"].remove(row(d, "syn-free-f01")),
                              "a state differs": lambda d: row(d, "syn-free-f01").update(state="retired"),
                              "a hash differs": lambda d: row(d, "syn-free-f01").update(content_sha256="8" * 64),
                              "an event the ledger holds is absent": lambda d: d["events"].clear()}.items():
            with self.subTest(label=label):
                with self.assertRaises(LedgerError):
                    self.check("rel-2", self.ledger_path, self.cat2, inventory_path=tampered(mutate))
        with open(self.inventory_path, "r+b") as handle:                                       # a doctored inventory file: digest no longer matches
            data = handle.read().replace(b'"state": "retired"', b'"state": "ranked"')
            handle.seek(0)
            handle.write(data)
        self.refused()

    def test_protected_ids_hashes_and_copies_in_the_exact_tree_are_findings_but_a_freed_question_is_not(self):
        self.ready()
        leaks = {"syn-free-new": "data/leak-new.json", "syn-free-f00": "data/leak-retired.json", "syn-free-f01": "data/leak-ranked.json"}
        for qid, rel in leaks.items():
            (self.public / rel).write_text(json.dumps({"id": qid}))
        # the decision must pin the tree that was actually built, so re-pin it; findings are about content, not about binding
        tree = pool_artifact.read_tree(str(self.public))
        self.install(None, self.decision("rel-2", self.ledger2, self.cat2, first=False, previous=self.first["digest"], tree=tree))
        found = self.check("rel-2", self.ledger_path, self.cat2, inventory_path=self.inventory_path)["findings"]
        self.assertEqual(sorted((rel, qid) for rel, qid, _ in found), sorted((rel, qid) for qid, rel in leaks.items()))
        official = next(iter(self.fx.catalogs["official"].questions.values()))
        (self.public / "data" / "copy.txt").write_text(f"x {official.content_sha256} y")
        tree = pool_artifact.read_tree(str(self.public))
        self.install(None, self.decision("rel-2", self.ledger2, self.cat2, first=False, previous=self.first["digest"], tree=tree))
        again = self.check("rel-2", self.ledger_path, self.cat2, inventory_path=self.inventory_path)["findings"]
        self.assertIn(("data/copy.txt", official.question_id, "content hash"), again)


class DocumentTests(Scenario):
    """The documents themselves: every field is digest-bound, and the rules for references and the first ledger hold on their own."""

    def sealed(self, ledger, mutate):
        doc = copy.deepcopy(ledger)
        mutate(doc)
        doc["counts"] = pool_ledger.counts_of(doc["questions"])
        doc["lifecycle_events_digest"] = pool_ledger.events_digest(doc["events"])
        return pool_ledger.seal({k: v for k, v in doc.items() if k != "digest"})

    def test_a_ledger_file_edited_in_any_field_without_resealing_is_refused(self):
        ledger, _ = self.first_release()
        path = self.dir / "edit.json"
        text = pool_ledger.dumps(ledger).decode()
        edits = {"release id": text.replace('"release_id": "rel-1"', '"release_id": "rel-9"'),
                 "a hash": text.replace(ledger["questions"][0]["content_sha256"], "7" * 64, 1),
                 "the counts": text.replace('"ranked": 4', '"ranked": 3', 1),
                 "the sequence": text.replace('"seq": 1', '"seq": 2', 1),
                 "the digest": text.replace(ledger["digest"], "0" * 64),
                 "the event digest": text.replace(ledger["lifecycle_events_digest"], "1" * 64)}
        for label, edited in edits.items():
            with self.subTest(label=label):
                path.write_text(edited)
                with self.assertRaises(LedgerError):
                    pool_ledger.load_ledger(path)
        metadata_only = self.sealed(ledger, lambda d: d.update(release_id="rel-9"))
        self.assertNotEqual(metadata_only["digest"], ledger["digest"], "the digest covers metadata, not only the rows")
        self.assertNotEqual(pool_ledger.seal({**{k: v for k, v in ledger.items() if k != "digest"}, "lifecycle_events_digest": "2" * 64})["digest"], ledger["digest"])

    def test_a_resealed_ledger_with_a_wrong_event_digest_or_a_non_ranked_first_row_is_refused(self):
        ledger, _ = self.first_release()
        wrong = pool_ledger.seal({**{k: v for k, v in ledger.items() if k != "digest"}, "lifecycle_events_digest": "3" * 64})
        with self.assertRaises(LedgerError):
            pool_ledger.validate_ledger(wrong)                                             # the outer digest is valid, the inner binding is not
        retired_first = self.sealed(ledger, lambda d: d["questions"][0].update(state="retired"))
        (self.trusted / "pool-ledger" / "ledger-000001.json").write_bytes(pool_ledger.dumps(retired_first))
        with self.assertRaises(LedgerError):
            pool_ledger.load_history(str(self.trusted))                                    # a first ledger registers plain ranked questions only

    def test_references_are_required_exactly_where_the_rules_say(self):
        first, inventory, ledger = self.second_release()
        free = lambda d: next(r for r in d["questions"] if r["question_id"] == "syn-free-f01")
        refs = {"clone_audit_ref": "c-1", "license_ok_ref": "l-1", "exposure_audit_ref": "e-1"}
        def make(mutate):
            return lambda: pool_ledger.validate_ledger(self.sealed(ledger, mutate))
        for label, mutate in {
                "a free row without a licence reference": lambda d: free(d).update(state="free", clone_audit_ref="c-1", license_ok_ref=None, exposure_audit_ref="e-1"),
                "a free row without an exposure reference": lambda d: free(d).update(state="free", clone_audit_ref="c-1", license_ok_ref="l-1", exposure_audit_ref=None),
                "a ranked row carrying references": lambda d: free(d).update(clone_audit_ref="c-1"),
                "a retired row carrying a licence reference": lambda d: next(r for r in d["questions"] if r["state"] == "retired").update(license_ok_ref="l-1"),
                "a free event without a reference": lambda d: d["events"].append({"question_id": "syn-free-f01", "from": "retired", "to": "free", "release_id": "rel-2",
                                                                                    "reason_code": "reviewed", "clone_audit_ref": "c-1", "license_ok_ref": None, "exposure_audit_ref": "e-1"}),
                "a retire event carrying a licence reference": lambda d: d["events"][0].update(license_ok_ref="l-1"),
                "an event with a bad reason code": lambda d: d["events"][0].update(reason_code="Not A Code"),
                "an event that is not a move": lambda d: d["events"][0].update(**{"from": "ranked", "to": "free"})}.items():
            with self.subTest(label=label):
                with self.assertRaises(LedgerError):
                    make(mutate)()
        # events and rows must carry the SAME references
        db = self.fx.db()
        pool.free(db, "syn-free-f00", release_id="rel-3", reason_code="reviewed", operator_id="op-1", now=1.0,
                  clone_audit_ref="c-9", license_ok_ref="l-9", exposure_audit_ref="e-9")
        inventory = pool_release.export_inventory(snapshot(self.fx.path, str(self.dir / "snap-refs.db")))
        full = pool_ledger.compose("rel-2", first, inventory, [pool_release.load_catalog(p) for p in self.cat2])
        pool_ledger.verify_step(first, full)
        mismatched = self.sealed(full, lambda d: next(e for e in d["events"] if e["to"] == "free").update(license_ok_ref="l-other"))
        with self.assertRaises(LedgerError):
            pool_ledger.verify_step(first, mismatched)

    def test_a_first_ledger_carries_no_events_and_a_decision_must_list_the_open_versions(self):
        ledger, decision = self.first_release()
        with_event = self.sealed(ledger, lambda d: d["events"].append({"question_id": "syn-free-f00", "from": "ranked", "to": "retired", "release_id": "rel-1",
                                                                         "reason_code": "exposed", "clone_audit_ref": None, "license_ok_ref": None, "exposure_audit_ref": None}))
        (self.trusted / "pool-ledger" / "ledger-000001.json").write_bytes(pool_ledger.dumps(with_event))
        with self.assertRaises(LedgerError):
            pool_ledger.load_history(str(self.trusted))
        with self.assertRaises(LedgerError):
            pool_ledger.validate_decision({**decision, "open_versions": []})

    def test_the_decision_rejects_every_malformed_field(self):
        ledger, decision = self.first_release()
        for key, bad in {"release_id": "bad id", "candidate_commit": "abc", "public_tree": "z" * 40, "artifact_sha256": "q", "ledger_digest": 5,
                         "human_approval_ref": "", "decided_at": "", "first_ledger": "yes", "counts": {}, "released": {}, "kind": "other", "schema": 2}.items():
            with self.subTest(key=key):
                with self.assertRaises(LedgerError):
                    pool_ledger.validate_decision({**decision, key: bad})
        with self.assertRaises(LedgerError):
            pool_ledger.validate_decision({**decision, "extra": 1})


class MoreBindingTests(Scenario):
    def prepared(self, **kw):
        first, inventory, ledger = self.second_release(**kw)
        self.first, self.ledger2 = first, ledger
        self.decision2 = self.decision("rel-2", ledger, self.cat2, first=False, previous=first["digest"])
        self.install(None, self.decision2)
        self.ledger_path = self.save("l2.json", ledger)

    def refused(self, **kw):
        kw.setdefault("inventory_path", self.inventory_path)
        with self.assertRaises(LedgerError):
            self.check("rel-2", self.ledger_path, kw.pop("catalogs", self.cat2), **kw)

    def test_a_catalog_listed_by_digest_must_be_byte_identical_even_if_its_content_is_the_same(self):
        self.prepared()
        reformatted = self.dir / "free-2-reformatted.json"
        reformatted.write_text(json.dumps(json.loads(Path(self.cat2[1]).read_text()), indent=2))
        self.refused(catalogs=[self.cat2[0], str(reformatted)])

    def test_an_open_version_the_decision_forgot_to_list_leaves_ranked_questions_unaccounted_for(self):
        self.prepared(new_question=False)
        self.install(None, {**self.decision2, "open_versions": [v for v in self.decision2["open_versions"] if v["family"] == "free"]})
        self.refused(catalogs=[self.cat2[1]])                                               # the official pool is still ranked in the ledger

    def test_the_decisions_artifact_digest_must_match_even_when_the_tree_id_does(self):
        self.prepared()
        self.install(None, {**self.decision2, "artifact_sha256": "f" * 64})
        self.refused()


class InventoryExportTests(unittest.TestCase):
    def setUp(self):
        self.fx, tmp = build_scoring_fixture(users=("u1",), free=(("syn-free", 3),))
        self.addCleanup(tmp.cleanup)
        self.snaps = tempfile.TemporaryDirectory()
        self.addCleanup(self.snaps.cleanup)

    def snap(self):
        return snapshot(self.fx.path, str(Path(self.snaps.name) / f"snap-{len(list(Path(self.snaps.name).glob('snap-*')))}.db"))

    def files_state(self, path):
        base = Path(path)
        return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in base.parent.iterdir() if p.name.startswith(base.name)}

    def test_the_export_reads_without_changing_the_database_its_journal_mode_or_its_files(self):
        import sqlite3
        copy_path = self.snap()
        before = self.files_state(copy_path)
        check = sqlite3.connect(f"file:{copy_path}?mode=ro&immutable=1", uri=True)
        mode = check.execute("PRAGMA journal_mode").fetchone()[0]
        rows = check.execute("SELECT COUNT(*) FROM pool_questions").fetchone()[0]
        check.close()
        inventory = pool_release.export_inventory(copy_path)
        self.assertEqual(len(inventory["questions"]), rows)
        self.assertEqual(self.files_state(copy_path), before, "not one byte of the database changed")
        self.assertEqual(sorted(p.name for p in Path(copy_path).parent.iterdir() if p.name.startswith(Path(copy_path).name)), [Path(copy_path).name],
                         "no -shm or -wal file appeared")
        check = sqlite3.connect(f"file:{copy_path}?mode=ro&immutable=1", uri=True)
        self.assertEqual(check.execute("PRAGMA journal_mode").fetchone()[0], mode)
        check.close()

    def test_a_snapshot_with_unapplied_journal_data_is_refused_because_immutable_would_miss_it(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "live.db"
            conn = sqlite3.connect(path)
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA wal_autocheckpoint = 0")
            conn.execute("CREATE TABLE t (x)")
            conn.execute("INSERT INTO t VALUES (1)")
            conn.commit()
            self.assertGreater(Path(str(path) + "-wal").stat().st_size, 0)
            with self.assertRaises(LedgerError) as caught:
                pool_release.export_inventory(str(path))
            self.assertIn("checkpointed snapshot", str(caught.exception))
            conn.close()

    def test_a_missing_foreign_or_unmigrated_database_is_refused_and_nothing_is_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("absent.db",):
                with self.assertRaises(LedgerError):
                    pool_release.export_inventory(str(Path(tmp) / name))
                self.assertFalse((Path(tmp) / name).exists())
            import sqlite3
            foreign = Path(tmp) / "foreign.db"
            sqlite3.connect(foreign).executescript("CREATE TABLE other (x)").close() if False else None
            conn = sqlite3.connect(foreign)
            conn.execute("CREATE TABLE other (x)")
            conn.commit()
            conn.close()
            with self.assertRaises(LedgerError):
                pool_release.export_inventory(str(foreign))
            old = Path(tmp) / "old.db"
            conn = sqlite3.connect(old)
            conn.executescript("CREATE TABLE schema_migrations (version INTEGER); INSERT INTO schema_migrations VALUES (6);"
                               "CREATE TABLE pool_questions (x); CREATE TABLE pool_lifecycle_events (x);")
            conn.commit()
            conn.close()
            with self.assertRaises(LedgerError):
                pool_release.export_inventory(str(old))

    def test_free_events_carry_the_references_that_were_actually_used(self):
        db = self.fx.db()
        self.addCleanup(db.close)
        version = db.connect().execute("SELECT id FROM ranking_versions WHERE family = 'free'").fetchone()[0]
        pool.freeze(db, version, reason_code="season-end", now=1.0)
        pool.retire(db, "syn-free-f00", release_id="rel-2", reason_code="exposed", operator_id="op-1", now=2.0, clone_audit_ref="c-1")
        pool.free(db, "syn-free-f00", release_id="rel-3", reason_code="reviewed", operator_id="op-1", now=3.0,
                  clone_audit_ref="c-2", license_ok_ref="l-2", exposure_audit_ref="e-2")
        inventory = pool_release.export_inventory(self.snap())
        events = [e for e in inventory["events"] if e["question_id"] == "syn-free-f00"]
        self.assertEqual([(e["from"], e["to"], e["clone_audit_ref"], e["license_ok_ref"], e["exposure_audit_ref"]) for e in events],
                         [("ranked", "retired", "c-1", None, None), ("retired", "free", "c-2", "l-2", "e-2")])
        self.assertEqual(next(r for r in inventory["questions"] if r["question_id"] == "syn-free-f00")["state"], "free")


class CommandLineTests(Scenario):
    def cli(self, *argv):
        return run_cli_captured(pool.main, ["--db", self.fx.path, *argv])

    def test_the_commands_run_end_to_end_and_every_refusal_is_exit_two(self):
        out = self.dir / "cli-ledger-1.json"
        code, stdout, err = self.cli("build-ledger", "--release-id", "rel-1", "--out", str(out), "--catalog", self.cat1[0], "--catalog", self.cat1[1])
        self.assertEqual(code, 0, err)
        self.assertIn("ledger written: seq 1", stdout)
        self.assertEqual(self.cli("build-ledger", "--release-id", "rel-1", "--out", str(out), "--catalog", self.cat1[0])[0], 2, "never overwrites")
        ledger = pool_ledger.load_ledger(out)
        self.install(None, self.decision("rel-1", ledger, self.cat1, first=True))
        self.assertEqual(self.cli("verify-ledger", "--trusted-checkout", str(self.trusted))[1].strip(), "ledger history valid: 0 ledger(s)")
        tree = self.cli("tree-digest", "--public-root", str(self.public))[1]
        self.assertIn(pool_artifact.read_tree(str(self.public))["tree_id"], tree)
        args = ("check-release", "--trusted-checkout", str(self.trusted), "--release-id", "rel-1", "--ledger", str(out), "--public-root", str(self.public),
                "--candidate", CANDIDATE, "--catalog", self.cat1[0], "--catalog", self.cat1[1])
        code, stdout, err = self.cli(*args)
        self.assertEqual(code, 0, err)
        self.assertIn("release gate OK: release rel-1 seq 1", stdout)
        self.assertEqual(self.cli(*args[:args.index("--candidate")], "--candidate", "f" * 40, *args[args.index("--catalog"):])[0], 2)
        with self.assertRaises((SystemExit, argparse.ArgumentError)):
            self.cli("check-release", "--trusted-checkout", str(self.trusted), "--release-id", "rel-1")                # missing arguments
        (self.public / "leak.json").write_text('{"id": "syn-free-f00"}')
        self.install(None, self.decision("rel-1", ledger, self.cat1, first=True))      # re-pin the tree that now holds a leak
        code, stdout, err = self.cli(*args)
        self.assertEqual((code, stdout), (1, ""))
        self.assertIn("leak.json: syn-free-f00 (id)", err)

    def test_export_inventory_and_a_later_ledger_through_the_commands(self):
        first = self.merge_first()
        inv = self.dir / "cli-inv.json"
        snap = snapshot(self.fx.path, str(self.dir / "cli-snap.db"))
        self.assertEqual(run_cli_captured(pool.main, ["--db", snap, "export-inventory", "--out", str(inv)])[0], 0)
        self.assertEqual(run_cli_captured(pool.main, ["--db", snap, "export-inventory", "--out", str(inv)])[0], 2)
        nxt = self.dir / "cli-ledger-2.json"
        code, _, err = self.cli("build-ledger", "--release-id", "rel-2", "--out", str(nxt), "--previous", str(self.trusted / "pool-ledger" / "ledger-000001.json"),
                                "--inventory", str(inv), "--catalog", self.cat1[0], "--catalog", self.cat1[1])
        self.assertEqual(code, 0, err)
        self.assertEqual(self.cli("verify-ledger", "--trusted-checkout", str(self.trusted), "--ledger", str(nxt))[0], 0)
        self.assertEqual(self.cli("build-ledger", "--release-id", "rel-2", "--out", str(self.dir / "x.json"), "--previous", str(nxt), "--catalog", self.cat1[0])[0], 2)
        self.assertEqual(first["seq"], 1)


if __name__ == "__main__":
    unittest.main()
