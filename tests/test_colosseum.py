import io
import json
import os
import random
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "skills", "colosseum"))
import colosseum as C  # noqa: E402


def run_cli(*args):
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = C.main(list(args))
    return rc, buf.getvalue()


class Sim:
    """Stands in for sub-agents: answers every pending job of the current phase."""

    def __init__(self, seed=0, judge_mode="random", drop=(), passing=None):
        self.rng = random.Random(seed)
        self.judge_mode = judge_mode
        self.drop = set(drop)          # job ids that never produce output
        self.passing = passing          # function cid -> bool for code tasks

    def scores(self, a, b, prefer):
        hi = {"accuracy": 9, "coverage": 9, "resilience": 8, "actionability": 8, "clarity": 8, "fatal": False}
        lo = {"accuracy": 5, "coverage": 6, "resilience": 5, "actionability": 6, "clarity": 6, "fatal": False}
        return {a: hi if prefer == a else lo, b: hi if prefer == b else lo}

    def answer(self, st, d):
        for jid, outs, brief, kind in C.jobs(st, d):
            if jid in self.drop:
                continue
            if kind == "text":
                for o in outs:
                    C.wr(o, "content for %s\n" % jid)
                if jid.startswith("D-") and self.passing:
                    cid = jid[2:]
                    C.wr(os.path.join(C.files_dir(d, cid, 0), "flag.txt"), "ok" if self.passing(cid) else "no")
                if jid == "M-merge" and self.passing:
                    C.wr(os.path.join(C.files_dir(d, C.MERGED, 0), "flag.txt"), "ok" if self.passing(C.MERGED) else "no")
            elif kind[0] == "revise":
                cid = kind[1]
                C.wr(outs[0], "revised %s\n" % cid)
                C.wr(outs[1], "#1 ACCEPT - fixed\n")
                if self.passing:
                    C.wr(os.path.join(C.files_dir(d, cid, st["round"]), "flag.txt"), "ok" if self.passing(cid) else "no")
            elif kind[0] == "screen":
                C.wr(outs[0], json.dumps({"scores": {c: self.rng.randint(20, 95) for c in kind[1]}}))
            elif kind[0] == "verdict":
                a, b = kind[1], kind[2]
                if self.judge_mode == "prefer-merged" and C.MERGED in (a, b):
                    prefer = C.MERGED
                elif self.judge_mode == "first":      # always prefers whoever is shown first -> judges disagree
                    prefer = a
                elif self.judge_mode == "lower-id":
                    prefer = min(a, b)
                else:
                    prefer = self.rng.choice([a, b])
                js = {"scores": self.scores(a, b, prefer), "reason": "x", "resolved": {prefer: ["point 1"]}, "open": {}}
                C.wr(outs[0], json.dumps(js))


def drive(sim, limit=200):
    for _ in range(limit):
        rc, out = run_cli("next")
        assert rc == 0, out
        if out.startswith("DONE"):
            return out
        d, st = C.load()
        sim.answer(st, d)
    raise AssertionError("did not finish")


class Base(unittest.TestCase):
    def setUp(self):
        self.old = os.getcwd()
        self.tmp = tempfile.mkdtemp()
        os.chdir(self.tmp)
        os.environ["COLOSSEUM_HOME"] = os.path.join(self.tmp, "home")
        C.wr("task.md", "Write a haiku about rain.\n")

    def tearDown(self):
        os.chdir(self.old)
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestUnits(Base):
    def test_seed_order(self):
        self.assertEqual(C.seed_order(8), [1, 8, 4, 5, 2, 7, 3, 6])
        self.assertEqual(sorted(C.seed_order(16)), list(range(1, 17)))

    def test_deal_unique_and_balanced(self):
        cards = C.deal(64, "general", random.Random(3), {})
        keys = {(c["angle"], c["method"], c["priority"]) for c in cards}
        self.assertEqual(len(keys), 64)
        counts = {}
        for c in cards:
            counts[c["angle"]] = counts.get(c["angle"], 0) + 1
        self.assertLessEqual(max(counts.values()) - min(counts.values()), 1)

    def test_learning_shifts_allocation(self):
        stats = {}
        for _ in range(30):
            C.stats_bump(stats, "code", {"angle": "pre-mortem", "method": "careful-pass", "priority": "correctness"}, 1, 1)
            C.stats_bump(stats, "code", {"angle": "skeptic", "method": "expand-cut", "priority": "efficiency"}, 0, 1)
        win = lose = 0
        for seed in range(40):
            cards = C.deal(40, "code", random.Random(seed), stats)
            win += sum(1 for c in cards if c["angle"] == "pre-mortem")
            lose += sum(1 for c in cards if c["angle"] == "skeptic")
        self.assertGreater(win, 2 * lose)
        self.assertGreater(lose, 0)  # losers still get explored sometimes
        big = C.deal(200, "code", random.Random(1), stats)
        n = lambda v: sum(1 for c in big if c["angle"] == v)
        self.assertGreater(n("pre-mortem"), n("skeptic"))
        self.assertGreaterEqual(n("skeptic"), 1)

    def test_deck_bigger_than_2160(self):
        self.assertGreaterEqual(len(C.ANGLES) * len(C.METHODS) * len(C.PRIORITIES), 2704)
        self.assertEqual(len(C.METHODS), C.SPREAD)
        self.assertEqual(len(C.PRIORITIES), C.SPREAD)

    def test_no_two_cards_share_two_parts_up_to_169(self):
        for n, seed in ((144, 1), (169, 2), (37, 3), (100, 4)):
            cards = C.deal(n, "general", random.Random(seed), {})
            self.assertEqual(len(cards), n)
            for i in range(n):
                for j in range(i + 1, n):
                    self.assertLessEqual(C.shared_parts(cards[i], cards[j]), 1)

    def test_full_diagonals_are_balanced(self):
        cards = C.deal(39, "general", random.Random(5), {})
        for axis in C.AXES:
            counts = {}
            for c in cards:
                counts[c[axis]] = counts.get(c[axis], 0) + 1
            self.assertEqual(set(counts.values()), {3})

    def test_large_deal_unique(self):
        cards = C.deal(256, "general", random.Random(9), {})
        self.assertEqual(len({(c["angle"], c["method"], c["priority"]) for c in cards}), 256)

    def test_zscore_cancels_harsh_screener(self):
        lenient = C.zscores({"a": 95, "b": 90, "c": 85})
        harsh = C.zscores({"a": 45, "b": 40, "c": 35})
        self.assertEqual(lenient, harsh)
        self.assertEqual(C.zscores({"a": 50, "b": 50}), {"a": 0.0, "b": 0.0})

    def test_vote_fatal_rule(self):
        top = {"accuracy": 10, "coverage": 10, "resilience": 10, "actionability": 10, "clarity": 10}
        low = {"accuracy": 2, "coverage": 2, "resilience": 2, "actionability": 2, "clarity": 2, "fatal": False}
        js = {"scores": {"a": dict(top, fatal=True, fatal_evidence="line 3 divides by zero for n=0"), "b": low}}
        self.assertEqual(C.vote(js, "a", "b"), "b")

    def test_fatal_without_evidence_is_ignored(self):
        top = {"accuracy": 10, "coverage": 10, "resilience": 10, "actionability": 10, "clarity": 10, "fatal": True, "fatal_evidence": " "}
        low = {"accuracy": 2, "coverage": 2, "resilience": 2, "actionability": 2, "clarity": 2, "fatal": False}
        self.assertEqual(C.vote({"scores": {"a": top, "b": low}}, "a", "b"), "a")

    def test_batches_cover_each_draft_k_times(self):
        ids = ["c%02d" % i for i in range(1, 33)]
        bs = C.make_batches(ids, 2, 6, random.Random(0))
        for c in ids:
            self.assertEqual(sum(b.count(c) for b in bs), 2)
        for b in bs:
            self.assertEqual(len(b), len(set(b)))


class TestRuns(Base):
    def test_quick_run(self):
        self.assertEqual(run_cli("init", "--quick", "--seed", "4", "--task-file", "task.md")[0], 0)
        drive(Sim(1))
        d, st = C.load()
        self.assertEqual(st["phase"], "done")
        self.assertIn(st["champion"], st["ids"])
        self.assertEqual(len(st["history"]), 2)
        self.assertEqual(st["calls"], 12 + 6 + 6 * 3 + 1 + 3 + sum(1 for h in st["history"][:-1] for m in h["matches"] if m["by"] in ("tiebreak", "single-judge", "fallback-screen-score")))
        self.assertTrue(os.path.exists(C.stats_path()))
        self.assertEqual(run_cli("champion")[0], 0)
        self.assertEqual(run_cli("report")[0], 0)

    def test_default_run_with_consistent_judges(self):
        run_cli("init", "--seed", "9", "--task-file", "task.md")
        drive(Sim(2, judge_mode="lower-id"))
        d, st = C.load()
        self.assertEqual(st["judge_agree"], st["judge_pairs"])
        self.assertEqual(st["judge_pairs"], 7)
        self.assertEqual(st["champion"], min(st["ranking"][:8]))

    def test_disagreeing_judges_trigger_tiebreaks(self):
        run_cli("init", "--quick", "--seed", "5", "--task-file", "task.md")
        drive(Sim(3, judge_mode="first"))
        d, st = C.load()
        self.assertEqual(st["judge_agree"], 0)
        bys = {m["by"] for h in st["history"][:-1] for m in h["matches"]}
        self.assertEqual(bys, {"tiebreak"})
        self.assertEqual(st["history"][-1]["matches"][0]["by"], "panel of 3")

    def test_missing_outputs_get_placeholders(self):
        run_cli("init", "--quick", "--seed", "6", "--task-file", "task.md")
        drive(Sim(4, drop={"D-c03", "S-b01", "J-r1-m1-a"}))
        d, st = C.load()
        self.assertEqual(st["phase"], "done")
        self.assertEqual(C.rd(C.sol(d, "c03", 0)).strip(), "NO OUTPUT")

    def test_tests_decide_matches(self):
        proj = os.path.join(self.tmp, "proj")
        C.wr(os.path.join(proj, "flag.txt"), "no")
        cmd = "python3 -c \"import sys; sys.exit(0 if open('flag.txt').read().strip()=='ok' else 1)\""
        run_cli("init", "--quick", "--seed", "7", "--task-file", "task.md", "--kind", "code", "--test-cmd", cmd, "--project", proj)
        good = {"c02", "c05"}
        drive(Sim(5, passing=lambda c: c in good))
        d, st = C.load()
        self.assertIn(st["champion"], good)
        self.assertTrue(all(c in st["ranking"][:2] for c in good))  # passing drafts rank first in the screen
        self.assertEqual(C.rd(os.path.join(proj, "flag.txt")), "no")  # project untouched

    def test_baseline_final_check(self):
        C.wr("old.md", "a bad haiku\n")
        run_cli("init", "--quick", "--seed", "8", "--task-file", "task.md", "--baseline-file", "old.md")
        drive(Sim(6))
        d, st = C.load()
        self.assertEqual(set(st["final"]), {"a", "b"})
        self.assertIn("champion", st["final"]["a"])

    def test_merge_accepted_when_both_judges_prefer_it(self):
        run_cli("init", "--quick", "--seed", "11", "--task-file", "task.md")
        out = drive(Sim(7, judge_mode="prefer-merged"))
        d, st = C.load()
        self.assertTrue(st["merge"]["accepted"])
        self.assertEqual(st["delivered"], C.MERGED)
        self.assertEqual(len(st["merge"]["sources"]), 3)
        self.assertIn("delivered answer: merged", out)
        self.assertIn(C.sol(d, C.MERGED, 0), run_cli("champion")[1])

    def test_merge_rejected_unless_unanimous(self):
        run_cli("init", "--quick", "--seed", "12", "--task-file", "task.md")
        drive(Sim(8, judge_mode="lower-id"))
        d, st = C.load()
        self.assertFalse(st["merge"]["accepted"])
        self.assertEqual(st["delivered"], st["champion"])

    def test_merge_off(self):
        run_cli("init", "--quick", "--seed", "13", "--merge", "0", "--task-file", "task.md")
        drive(Sim(9))
        d, st = C.load()
        self.assertEqual(st["merge"]["by"], "merge off")
        self.assertFalse(os.path.exists(os.path.join(d, "briefs", "M-merge.md")))

    def test_merge_rejected_when_it_breaks_tests(self):
        proj = os.path.join(self.tmp, "proj")
        C.wr(os.path.join(proj, "flag.txt"), "no")
        cmd = "python3 -c \"import sys; sys.exit(0 if open('flag.txt').read().strip()=='ok' else 1)\""
        run_cli("init", "--quick", "--seed", "14", "--task-file", "task.md", "--kind", "code", "--test-cmd", cmd, "--project", proj)
        drive(Sim(10, judge_mode="prefer-merged", passing=lambda c: c in {"c01", "c04", "c07", "c09"}))
        d, st = C.load()
        self.assertFalse(st["merge"]["accepted"])
        self.assertTrue(st["merge"]["by"].startswith("tests"))
        self.assertFalse(os.path.exists(os.path.join(d, "briefs", "MJ-a.md")))

    def test_models_per_role(self):
        run_cli("init", "--quick", "--seed", "15", "--models", "draft=haiku,judge=opus", "--task-file", "task.md")
        rc, out = run_cli("next")
        self.assertIn("MODEL: haiku", out)
        self.assertIn("Pass model: haiku", out)
        sim = Sim(11)
        seen = set()
        for _ in range(60):
            d, st = C.load()
            sim.answer(st, d)
            rc, out = run_cli("next")
            for line in out.splitlines():
                if line.startswith("PHASE"):
                    seen.add((line.split(":")[0], line.rsplit("MODEL: ", 1)[1]))
            if out.startswith("DONE"):
                break
        self.assertIn(("PHASE screening", "sonnet"), seen)
        self.assertTrue(any(p.startswith("PHASE judging") and m == "opus" for p, m in seen))
        d, st = C.load()
        self.assertEqual(st["calls_by_model"]["haiku"], 12)
        self.assertEqual(sum(st["calls_by_model"].values()), st["calls"])

    def test_model_presets_and_errors(self):
        self.assertEqual(C.parse_models("", True)["draft"], "haiku")
        self.assertEqual(set(C.parse_models("all=inherit").values()), {"inherit"})
        self.assertEqual(run_cli("plan", "--models", "draft=gpt")[0], 1)
        self.assertEqual(run_cli("plan", "--models", "chef=haiku")[0], 1)

    def test_dashboard_and_run_log(self):
        for seed in (21, 22):
            run_cli("init", "--quick", "--seed", str(seed), "--kind", "writing", "--task-file", "task.md")
            drive(Sim(seed))
        runs = C.jload(C.runs_path())
        self.assertEqual(len(runs), 2)
        self.assertEqual(runs[0]["kind"], "writing")
        rc, out = run_cli("dashboard", "--out", "dash.html")
        self.assertEqual(rc, 0)
        html = C.rd("dash.html")
        self.assertIn('"writing"', html)
        self.assertNotIn("__DATA__", html)
        self.assertEqual(html.count("<script>"), 1)

    def test_final_gets_three_judges(self):
        run_cli("init", "--quick", "--seed", "31", "--task-file", "task.md")
        drive(Sim(31))
        d, st = C.load()
        fm = st["history"][-1]["matches"][0]
        self.assertTrue(os.path.exists(os.path.join(d, "briefs", "J-%s-c.md" % fm["id"])))
        self.assertEqual(len(fm["votes"]), 3)

    def test_record_override_and_pairings(self):
        run_cli("init", "--quick", "--seed", "32", "--task-file", "task.md")
        sim = Sim(32)
        for _ in range(4):
            d, st = C.load()
            sim.answer(st, d)
            run_cli("next")
        d, st = C.load()
        self.assertIn(st["matches"][0]["id"], run_cli("pairings")[1])
        m = st["matches"][0]
        self.assertEqual(run_cli("record", m["id"], "nobody", "--reason", "x")[0], 1)
        self.assertEqual(run_cli("record", m["id"], m["b"], "--reason", "judge output was corrupted")[0], 0)
        d, st = C.load()
        self.assertTrue(st["matches"][0]["by"].startswith("manual"))
        drive(sim)
        d, st = C.load()
        self.assertEqual(st["history"][0]["matches"][0]["winner"], m["b"])
        self.assertEqual(len(st["overrides"]), 1)

    def test_presets_reach_arena_scale(self):
        rc, out = run_cli("plan", "--full")
        self.assertIn("100 drafters", out)
        rc, out = run_cli("plan", "--max")
        self.assertIn("256 drafters", out)
        self.assertEqual(run_cli("plan", "--drafters", "257")[0], 1)

    def test_calibrated_ranking_beats_lenient_screener(self):
        run_cli("init", "--drafters", "12", "--field", "4", "--seed", "33", "--task-file", "task.md")
        run_cli("next")
        d, st = C.load()
        Sim(33).answer(st, d)
        run_cli("next")
        d, st = C.load()
        self.assertEqual(st["phase"], "screen")
        truth = {c: i for i, c in enumerate(st["ids"])}  # lower index = better
        for i, batch in enumerate(st["batches"]):
            offset = 50 if i % 2 == 0 else 0  # half the screeners are lenient by 50 points
            C.wr(os.path.join(d, "screen", "b%02d.json" % (i + 1)),
                 json.dumps({"scores": {c: 40 - truth[c] + offset for c in batch}}))
        run_cli("next")
        d, st = C.load()
        self.assertEqual(sorted(st["ranking"][:4]), ["c01", "c02", "c03", "c04"])

    def test_judge_briefs_ask_for_all_criteria_and_evidence(self):
        run_cli("init", "--quick", "--seed", "34", "--task-file", "task.md")
        drive(Sim(34))
        d, st = C.load()
        brief = C.rd(os.path.join(d, "briefs", "J-r1-m1-a.md"))
        for k, _ in C.RUBRIC:
            self.assertIn('"%s": 0' % k, brief)
        self.assertIn("fatal_evidence", brief)
        self.assertEqual(sum(w for _, w in C.RUBRIC), 100)

    def test_cowork_build_matches_engine(self):
        root = os.path.join(HERE, "..")
        built = os.path.join(root, "cowork", "SKILL.md")
        if not os.path.exists(built):
            self.skipTest("cowork build not present")
        t = C.rd(built)
        f = "`" * 3
        b = "<!-- COLOSSEUM-ENGINE-BEGIN -->\n" + f + "python\n"
        s = t.rindex(b) + len(b)
        engine = t[s:t.index(f + "\n<!-- COLOSSEUM-ENGINE-END -->", s)]
        self.assertEqual(engine, C.rd(os.path.join(root, "skills", "colosseum", "colosseum.py")))

    def test_plan(self):
        rc, out = run_cli("plan")
        self.assertEqual(rc, 0)
        self.assertIn("32 drafters", out)
        self.assertEqual(run_cli("plan", "--field", "6")[0], 1)


if __name__ == "__main__":
    unittest.main()
