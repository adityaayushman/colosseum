---
name: colosseum
description: Run a screened tournament of sub-agents on one task - many drafts, a cheap screening cut, then critique, revise and double-blind judging until one answer wins. Use on /colosseum, "colosseum this", "make them compete", or when the user rejects an answer.
argument-hint: "[--quick | --full | --drafters N --field K] [--kind code|writing|plan|analysis] [--test-cmd CMD --project DIR] [--seed S] <task>"
---

# Colosseum

You orchestrate. You never write, critique or judge an answer yourself, and you never pick a winner.

User input after the command: `$ARGUMENTS` (blank or a placeholder means: take the task from the conversation).

## The engine

The engine is embedded at the end of this file (section "Embedded engine", SHA-256 `d513daae8f414123a78c8813c2e84b448985f922a2597d3880e844bbbdba2522`). Before the first command, make sure it is unpacked:

```bash
E="$HOME/.cache/colosseum/colosseum.py"; SHA=d513daae8f414123a78c8813c2e84b448985f922a2597d3880e844bbbdba2522
check() { [ -f "$E" ] && [ "$(sha256sum "$E" | cut -d' ' -f1)" = "$SHA" ]; }
if ! check; then
  mkdir -p "$(dirname "$E")"
  for f in $(timeout 60 find / -path /proc -prune -o -name SKILL.md -path "*colosseum*" -print 2>/dev/null | head -20); do
    python3 - "$f" "$E" <<'PY'
import sys
t, f = open(sys.argv[1], encoding="utf-8").read(), "`" * 3
b = "<!-- COLOSSEUM-ENGINE-BEGIN -->\n" + f + "python\n"
if b in t:
    s = t.rindex(b) + len(b)
    open(sys.argv[2], "w", encoding="utf-8").write(t[s:t.index(f + "\n<!-- COLOSSEUM-ENGINE-END -->", s)])
PY
    check && break
  done
fi
check && echo ENGINE_OK || echo ENGINE_MISSING
```

If it prints ENGINE_MISSING, use the Write tool to write the code block under "Embedded engine" to `$HOME/.cache/colosseum/colosseum.py` exactly as it appears, then re-run the check. Never run an engine whose hash does not match.

`COL` below means `python3 "$HOME/.cache/colosseum/colosseum.py"`.

The engine keeps all state in `.colosseum/<run>/state.json` under the current directory. Run every `COL` command from the same directory.

## 1. Size and consent

| flag | effect |
| --- | --- |
| default | 32 drafters, top 8 enter the bracket (~86 calls) |
| `--quick` | 12 drafters, top 4 (~34 calls) |
| `--full` | 64 drafters, top 16 (~176 calls) |
| `--drafters N --field K` | custom; K must be a power of two |
| `--kind` | `code`, `writing`, `plan`, `analysis` or `general`. Pick it yourself from the task. It drives strategy learning. |
| `--test-cmd CMD --project DIR` | code tasks: run CMD in a clean copy of DIR with each answer's files overlaid. Tests decide matches when only one side passes. |
| `--seed S` | reproducible cards, screen batches and bracket |

Run `COL plan <size flags>` and tell the user the size in one line.

- The user asked for it (command, "colosseum", "make them compete"): state the size and start.
- It fired because the user is unhappy with an answer, and they never mentioned it: ask once (AskUserQuestion) - default run, `--quick`, or a normal retry - and wait.
- For code tasks with a test suite, confirm the test command with the user before using `--test-cmd`: the engine executes it.

## 2. Task file

Sub-agents see only the task file. Write `.colosseum/task.md` so a stranger can do the job:

- the request, in the user's words where possible;
- every requirement stated anywhere in the conversation (audience, length, format, stack, constraints, what must not change);
- absolute paths of the files that matter, and any pasted data;
- what done looks like, if stated;
- if an answer was rejected: what the user disliked, in their words.

Never add requirements of your own or hint at a preferred answer. If there is a rejected answer, save it verbatim to `.colosseum/baseline.md`.

## 3. Init

```bash
COL init <size flags> --kind KIND --task-file .colosseum/task.md [--baseline-file .colosseum/baseline.md] [--test-cmd "CMD" --project /abs/dir] [--seed S]
```

## 4. Loop

Repeat `COL next` until it prints `DONE`. Each time it either:

- prints `PHASE ...` with jobs grouped in waves: launch each wave as ONE message of parallel Agent calls, one per job - `subagent_type: general-purpose`, `description: colosseum <job id>`, `prompt: Read <brief path> and follow it exactly. It is your whole brief.` Wait for the whole wave, then the next wave, then run `COL next` again; or
- runs test exams itself and may ask you to run `next` again; or
- prints `PROGRESS: ...` - pass that one line to the user.

Missing outputs are re-issued automatically; after the retry limit the engine writes a placeholder and moves on. Never write an answer, critique or verdict file yourself.

Phases: draft -> (exam) -> screen -> per round: critique -> revise -> (exam) -> judge (two judges, order swapped) -> tiebreak only if they disagree -> ... -> final check vs the rejected answer (if any) -> DONE.

Do not read answers, critiques or verdicts during the run. Do not paste pairings or scores mid-run. If context is compacted, run `COL status` then `COL next`.

## 5. Result

Run `COL champion` and `COL report`, then read the champion's answer file (the only answer you read). Tell the user:

1. the winning answer, in full (for code: the changes, and where the files are);
2. why it won: its path through the bracket and the critiques it beat; its card in one line;
3. the numbers: drafters in, calls used, judge agreement rate;
4. if there was a rejected answer: both final-check scores, honestly - say so plainly if the old answer scored higher;
5. where the record and `report.md` live.

Never apply code changes to the user's project. Ask: apply, adjust, or leave?

`COL stats` shows which strategies win most for each kind of task; future runs deal winning strategies more often while still exploring every option.

## Embedded engine

Do not edit. Unpacked by the locator above.

<!-- COLOSSEUM-ENGINE-BEGIN -->
```python
#!/usr/bin/env python3
"""Colosseum engine: a screened, double-judged tournament of sub-agents.

Standard library only, Python 3.8+. The orchestrating Claude session calls
`next` in a loop; this file owns all state, briefs, scoring and learning.

Pipeline: draft -> (exam) -> screen -> [critique -> revise -> (exam) -> judge x2
-> (tiebreak)] per round -> (final vs baseline) -> done.
"""
import argparse
import json
import math
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time

VERSION = "1.0.0"
ROOT = ".colosseum"
MAX_TRIES = {"draft": 2, "screen": 2, "critique": 2, "revise": 2, "judge": 3, "tiebreak": 3, "final": 2}
EXAM_BUDGET_S = 90
IGNORE_DIRS = {".git", "node_modules", ".colosseum", "__pycache__", ".venv", "venv", ".mypy_cache", ".pytest_cache", "dist", "build"}

# ---------------------------------------------------------------- strategy cards
ANGLES = [
    ("ground-truth", "Ground truth", "Pin down what is verifiably true about the problem before deciding anything. Separate facts from assumptions and build only on facts."),
    ("work-backwards", "Work backwards", "Start from the finished result the person wants and derive each earlier step from it."),
    ("pre-mortem", "Pre-mortem", "Assume your answer has already failed in real use. List the reasons, then design so none of them can happen."),
    ("expert-panel", "Expert panel", "Pick three experts who would see this differently. Take each view in turn, then reconcile them."),
    ("smallest-core", "Smallest core", "Find the one idea the answer cannot do without, get it exactly right, then add only what the task demands."),
    ("example-first", "Example first", "Work one specific, realistic example end to end before you generalise."),
    ("decompose", "Decompose", "Split the task into independent parts, solve each cleanly, then check the seams between them."),
    ("skeptic", "Skeptic", "Question every default and habit for this kind of task. Keep only what survives the question."),
    ("user-journey", "User journey", "Walk through the exact moments the person will use this answer and serve each one."),
    ("tradeoff-map", "Trade-off map", "Lay out the real options with their costs, choose deliberately, and say why."),
]
METHODS = [
    ("careful-pass", "One careful pass", "Write once, slowly, verifying each part before moving to the next."),
    ("draft-review-fix", "Draft, review, fix", "Write a full draft, review it as a harsh critic would, then fix every issue you found."),
    ("plan-then-write", "Plan, then write", "Write a short numbered plan first, then execute it step by step."),
    ("checks-first", "Checks first", "Write the acceptance checks the answer must pass, then write the answer to pass all of them."),
    ("three-sketches", "Three sketches", "Sketch three genuinely different approaches, keep the strongest, develop it fully."),
    ("gather-synthesise", "Gather, then synthesise", "Collect every relevant fact from the task and the files it points to, then synthesise."),
    ("expand-cut", "Expand, then cut", "Write generously, then cut everything that does not earn its place."),
    ("reviewer-questions", "Reviewer questions", "List the questions a demanding reviewer would ask, then make sure the answer settles each one."),
]
PRIORITIES = [
    ("correctness", "Correctness above all", "When in doubt, choose what is provably right over what is fast or elegant."),
    ("simplicity", "Simplicity", "Prefer the simplest thing that fully works. Every extra part must justify itself."),
    ("completeness", "Completeness", "Cover every requirement and every case the task implies. Leave nothing for the reader to fill in."),
    ("edge-cases", "Edge cases", "Spend your effort on the unusual inputs and situations where answers usually break."),
    ("practicality", "Usable today", "Optimise for something the person can act on immediately, with concrete steps."),
    ("reader-clarity", "Reader clarity", "Optimise for how quickly the reader understands and trusts the answer."),
    ("safe-defaults", "Safe defaults", "Choose defaults that fail safely, and make risky choices explicit."),
    ("efficiency", "Efficiency", "Minimise cost, time and moving parts without dropping any requirement."),
]
AXES = {"angle": ANGLES, "method": METHODS, "priority": PRIORITIES}

# ---------------------------------------------------------------- rubric
RUBRIC = [("accuracy", 35), ("coverage", 25), ("resilience", 20), ("actionability", 20)]
RUBRIC_MD = """# Colosseum rubric

Score each criterion 0-10. Total = sum(score / 10 * weight), out of 100.

| criterion | weight | 0 | 5 | 8 | 10 |
| --- | --- | --- | --- | --- | --- |
| accuracy | 35 | wrong or broken | mostly right, one real error | right, minor slips | right, every claim checkable |
| coverage | 25 | ignores the task | meets about half of the stated requirements | meets all but a minor one | meets every stated requirement |
| resilience | 20 | collapses under the critiques | survives some, real holes remain | handles nearly all | every critique fixed or soundly refuted |
| actionability | 20 | reader cannot use it | usable after guesswork | usable with small gaps | usable as is, concrete and clear |

fatal = true only for a flaw you verified that makes the answer wrong or unusable for the task.
A fatal answer never beats a non-fatal one. Length is not quality.
"""

# ---------------------------------------------------------------- brief templates
T_DRAFT = """You are drafter <<cid>> in a Colosseum of <<n>>. Every drafter got the same task, word for word. Your strategy card below is the only thing that differs: follow it for real.

=== TASK ===
<<task>>
=== END TASK ===
<<baseline_note>>
=== YOUR CARD ===
<<card>>
=== END CARD ===

Rules:
1. Meet every requirement in the task. You are scored on the task, not on the card.
2. You cannot ask questions. Where the task is ambiguous, pick the most reasonable reading and list it under a short "Assumptions" heading.
3. Your answer will be screened, critiqued by a rival and judged twice. Close the holes now.
4. Touch nothing outside <<run>>. Scratch space: <<scratch>>.
<<code_note>>
Write the finished answer, for the person who asked, to <<out>>. No drafts, no mention of the card, the tournament or your id.

Then reply with exactly one line: DRAFTED <<cid>>
"""

T_SCREEN = """You are screener <<bid>> in a Colosseum. You will score <<k>> candidate answers to the same task, independently, on one 0-100 scale. Only the best go on to the tournament.

=== TASK ===
<<task>>
=== END TASK ===

Rubric (use it to form one holistic score): <<rubric>>

Candidates, in no particular order:
<<cands>>
<<exam_note>>
Read every candidate fully before scoring any. Score each on its own merits against the task; do not rank by length or style. Touch no file except the output.

Write only this JSON to <<out>>:
{"scores": {<<score_keys>>}, "notes": {"<id>": "one line on its biggest strength or flaw"}}

Then reply with exactly one line: SCREENED <<bid>>
"""

T_CRITIQUE = """You are <<cid>> in round <<round>> of a Colosseum, match <<mid>>, facing <<opp>>. Your job now: find what is actually wrong with your rival's answer.

=== TASK ===
<<task>>
=== END TASK ===

Look through your own card:
<<card>>

Rival answer: <<opp_sol>>
Your answer, for reference only: <<own_sol>>
<<exam_note>>
Report only real, checkable problems, strongest first, at most 6:
- ERROR: something false, buggy or logically wrong.
- GAP: a stated requirement missed or half met (quote it).
- BREAK: a concrete input or situation where it fails (give it exactly).
- UNCLEAR: a point the reader cannot act on without guessing.
Tag each CRITICAL, SERIOUS or MINOR. No praise, no invented requirements, no style complaints unless they block use. Touch no file except the output.

Write to <<out>>:
#1 [CRITICAL|SERIOUS|MINOR] <title>
At: <quote or location>
Why: <what is wrong, with the counterexample or quoted requirement>

Then reply with exactly one line: CRITIQUED <<opp>> <count>
"""

T_REVISE = """You are <<cid>> in round <<round>> of a Colosseum, match <<mid>>. Your rival <<opp>> critiqued your answer. Respond, then improve.

=== TASK ===
<<task>>
=== END TASK ===

Your card (keep your approach):
<<card>>

Your current answer: <<own_sol>>
Critique you received: <<critique>>
<<exam_note>>
1. For each point: ACCEPT it and fix it, or REJECT it with concrete evidence (from the task, your answer, or a check you ran). Rejecting without evidence counts as accepting.
2. Write the full revised answer, standalone. The judges read only the new file. Do not restart from scratch and do not copy your rival.
3. If the critique is empty or says NO OUTPUT, write "NO CRITIQUE" as your response and resubmit with any fixes you know it needs.
4. Touch nothing outside <<run>>.
<<code_note>>
Write your point-by-point response to <<resp_out>> (one line per point: "#n ACCEPT|REJECT - reason").
Write the revised answer to <<out>>.

Then reply with exactly one line: REVISED <<cid>>
"""

T_JUDGE = """You are judge <<jid>> for match <<mid>>, round <<round>>, of a Colosseum. Two answers to the same task each took a critique and were revised. Score both. Another judge is scoring this match independently.

=== TASK ===
<<task>>
=== END TASK ===

Rubric: <<rubric>>

Answer <<first>>
- revised answer: <<first_sol>>
- critique it received: <<first_crit>>
- its response: <<first_resp>>

Answer <<second>>
- revised answer: <<second_sol>>
- critique it received: <<second_crit>>
- its response: <<second_resp>>
<<exam_note>>
Method:
1. Read both revised answers fully before scoring either.
2. For each critique point, check the revised answer yourself: FIXED, REFUTED (only if the refutation is right) or OPEN. A response saying "fixed" proves nothing.
3. Look for problems both critics missed.
4. Score every criterion 0-10 with the rubric anchors. fatal = true only for a verified flaw that makes the answer wrong or unusable.
5. Judge the work, not its length or confidence. Touch no file except the output.<<scratch_note>>

Write only this JSON to <<out>>:
{"scores": {"<<first>>": {"accuracy": 0, "coverage": 0, "resilience": 0, "actionability": 0, "fatal": false}, "<<second>>": {"accuracy": 0, "coverage": 0, "resilience": 0, "actionability": 0, "fatal": false}}, "reason": "the decisive difference, one sentence", "resolved": {"<<first>>": ["critique points it beat"], "<<second>>": ["..."]}, "open": {"<<first>>": ["points still standing"], "<<second>>": ["..."]}}

Then reply with exactly one line: JUDGED <<mid>>
"""

T_FINAL = """You are a final checker for a Colosseum. One answer won a tournament; the other is an answer the person already rejected. You are not told which is which. Either can win.

=== TASK ===
<<task>>
=== END TASK ===

Rubric: <<rubric>>

Answer X: <<x>>
Answer Y: <<y>>

Read both fully. Attack each yourself the way a hostile expert would, then score each criterion 0-10. fatal = true only for a verified flaw. Length is not quality. Touch no file except the output.

Write only this JSON to <<out>>:
{"scores": {"X": {"accuracy": 0, "coverage": 0, "resilience": 0, "actionability": 0, "fatal": false}, "Y": {"accuracy": 0, "coverage": 0, "resilience": 0, "actionability": 0, "fatal": false}}, "reason": "the decisive difference, one sentence"}

Then reply with exactly one line: CHECKED
"""


class Err(Exception):
    pass


# ---------------------------------------------------------------- io helpers
def rd(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


def wr(p, s):
    d = os.path.dirname(p)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(s)
    os.replace(tmp, p)


def jload(p):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def fill(t, **kv):
    for k, v in kv.items():
        t = t.replace("<<%s>>" % k, str(v))
    return t


def ok_text(p):
    try:
        return os.path.getsize(p) > 0
    except OSError:
        return False


def run_dir():
    latest = os.path.join(ROOT, "LATEST")
    if not os.path.exists(latest):
        raise Err("no run found here. Run `init` first, from this directory.")
    d = os.path.join(ROOT, rd(latest).strip())
    if not os.path.exists(os.path.join(d, "state.json")):
        raise Err("LATEST points at %s but it has no state.json" % d)
    return os.path.abspath(d)


def load():
    d = run_dir()
    return d, jload(os.path.join(d, "state.json"))


def save(d, st):
    wr(os.path.join(d, "state.json"), json.dumps(st, indent=1, sort_keys=True))


def self_cmd():
    return 'python3 "%s"' % os.path.abspath(__file__)


# ---------------------------------------------------------------- learning
def stats_path():
    home = os.environ.get("COLOSSEUM_HOME") or os.path.join(os.path.expanduser("~"), ".colosseum")
    return os.path.join(home, "stats.json")


def stats_load():
    return jload(stats_path()) or {}


def stats_bump(stats, kind, card, w, g):
    for k in (kind, "_all"):
        kd = stats.setdefault(k, {})
        for axis in AXES:
            cell = kd.setdefault(axis, {}).setdefault(card[axis], {"w": 0.0, "g": 0.0})
            cell["w"] += w
            cell["g"] += g


def weight(stats, kind, axis, vid):
    c = stats.get(kind, {}).get(axis, {}).get(vid) or {"w": 0, "g": 0}
    return (c["w"] + 1.0) / (c["g"] + 2.0)  # Beta(1,1) posterior mean


def allocate(n, values, weights):
    """Split n slots over values: an exploration floor of one each (when n allows), rest by weight."""
    alloc = {v: 0 for v in values}
    floor = 1 if n >= len(values) else 0
    for v in values:
        alloc[v] = floor
    rest = n - floor * len(values)
    total = sum(weights[v] for v in values)
    raw = {v: rest * weights[v] / total for v in values}
    for v in values:
        alloc[v] += int(math.floor(raw[v]))
    left = n - sum(alloc.values())
    for v in sorted(values, key=lambda v: -(raw[v] - math.floor(raw[v])))[:left]:
        alloc[v] += 1
    return alloc


def deal(n, kind, rng, stats):
    if n > len(ANGLES) * len(METHODS) * len(PRIORITIES):
        raise Err("too many drafters for the card deck")
    pools = {}
    for axis, vals in AXES.items():
        ids = [v[0] for v in vals]
        a = allocate(n, ids, {i: weight(stats, kind, axis, i) for i in ids})
        pool = [i for i in ids for _ in range(a[i])]
        rng.shuffle(pool)
        pools[axis] = pool
    cards = [{"angle": pools["angle"][i], "method": pools["method"][i], "priority": pools["priority"][i]} for i in range(n)]
    for _ in range(5000):  # repair duplicate cards by swapping
        seen, dup = set(), None
        for i, c in enumerate(cards):
            key = (c["angle"], c["method"], c["priority"])
            if key in seen:
                dup = i
                break
            seen.add(key)
        if dup is None:
            return cards
        j = rng.randrange(n)
        ax = rng.choice(["method", "priority"])
        cards[dup][ax], cards[j][ax] = cards[j][ax], cards[dup][ax]
    raise Err("could not deal unique cards")


def card_text(c):
    out = []
    for axis, label in (("angle", "Angle"), ("method", "Method"), ("priority", "Priority")):
        v = next(x for x in AXES[axis] if x[0] == c[axis])
        out.append("%s: %s. %s" % (label, v[1], v[2]))
    return "\n".join(out)


def card_short(c):
    return " + ".join(next(x[1] for x in AXES[a] if x[0] == c[a]) for a in AXES)


# ---------------------------------------------------------------- bracket helpers
def seed_order(k):
    order = [1]
    while len(order) < k:
        m = len(order) * 2 + 1
        order = [x for s in order for x in (s, m - s)]
    return order


def make_matches(alive, rnd):
    return [{"id": "r%d-m%d" % (rnd, i // 2 + 1), "a": alive[i], "b": alive[i + 1], "winner": None, "votes": [], "by": None}
            for i in range(0, len(alive), 2)]


def total(s):
    return round(sum(max(0, min(10, float(s.get(k, 0)))) / 10.0 * w for k, w in RUBRIC), 1)


def valid_scores(js, ids):
    if not isinstance(js, dict) or not isinstance(js.get("scores"), dict):
        return False
    for i in ids:
        s = js["scores"].get(i)
        if not isinstance(s, dict):
            return False
        for k, _ in RUBRIC:
            if not isinstance(s.get(k), (int, float)):
                return False
    return True


def vote(js, a, b):
    """Winner implied by a verdict: fatal rule, then weighted total. None on exact tie."""
    sa, sb = js["scores"][a], js["scores"][b]
    fa, fb = bool(sa.get("fatal")), bool(sb.get("fatal"))
    if fa != fb:
        return b if fa else a
    ta, tb = total(sa), total(sb)
    if ta == tb:
        return a if float(sa.get("accuracy", 0)) > float(sb.get("accuracy", 0)) else (b if float(sb.get("accuracy", 0)) > float(sa.get("accuracy", 0)) else None)
    return a if ta > tb else b


# ---------------------------------------------------------------- paths
def sol(d, cid, r):
    return os.path.join(d, cid, "r%d" % r, "answer.md")


def files_dir(d, cid, r):
    return os.path.join(d, cid, "r%d" % r, "files")


def exam_key(cid, r):
    return "%s@r%d" % (cid, r)


# ---------------------------------------------------------------- notes
def code_note(st, d, cid, r):
    if not st["params"].get("test_cmd") and st["kind"] != "code":
        return ""
    s = ("If your answer changes code: put the exact changes in the answer, AND write every changed or new file, "
         "complete, under %s using the same relative paths as in the project (%s). Never edit the project itself.\n"
         % (files_dir(d, cid, r), st["params"].get("project") or "see the task"))
    if st["params"].get("test_cmd"):
        s += ("Your files will be overlaid on a clean copy of the project and the tests run with: %s\n"
              "Passing tests decides matches when only one side passes. To try them yourself, copy the project into %s first.\n"
              % (st["params"]["test_cmd"], os.path.join(d, "scratch", cid)))
    return s


def exam_line(st, cid, r):
    e = st["exam"].get(exam_key(cid, r))
    if not e:
        return ""
    return "Test result for %s: %s (exit %s). Output tail:\n%s\n" % (cid, "PASS" if e["passed"] else "FAIL", e["exit"], e["tail"][-1500:])


# ---------------------------------------------------------------- job lists
def jobs(st, d):
    """Jobs of the current phase: list of (job_id, [outputs], brief_text, kind_of_output)."""
    ph, task = st["phase"], rd(os.path.join(d, "task.md"))
    rubric = os.path.join(d, "rubric.md")
    out = []
    if ph == "draft":
        bnote = ""
        if st["has_baseline"]:
            bnote = "\nThe person already rejected one answer to this task: %s\nBeat it; do not copy it.\n" % os.path.join(d, "baseline.md")
        for cid in st["ids"]:
            o = sol(d, cid, 0)
            out.append(("D-" + cid, [o], fill(T_DRAFT, cid=cid, n=len(st["ids"]), task=task, baseline_note=bnote, card=card_text(st["cards"][cid]),
                                              run=d, scratch=os.path.join(d, "scratch", cid), code_note=code_note(st, d, cid, 0), out=o), "text"))
    elif ph == "screen":
        for i, batch in enumerate(st["batches"]):
            bid = "b%02d" % (i + 1)
            o = os.path.join(d, "screen", bid + ".json")
            cands = "\n".join("- %s: %s" % (c, sol(d, c, 0)) for c in batch)
            ex = "".join(exam_line(st, c, 0) for c in batch)
            out.append(("S-" + bid, [o], fill(T_SCREEN, bid=bid, k=len(batch), task=task, rubric=rubric, cands=cands,
                                              exam_note=("\nAutomated test results:\n" + ex) if ex else "",
                                              score_keys=", ".join('"%s": 0' % c for c in batch), out=o), ("screen", batch)))
    elif ph in ("critique", "revise"):
        r = st["round"]
        for m in st["matches"]:
            if m.get("by") == "bye":
                continue
            for me, opp in ((m["a"], m["b"]), (m["b"], m["a"])):
                mdir = os.path.join(d, "r%d" % r, m["id"])
                crit_of_me = os.path.join(mdir, "critique-by-%s.md" % opp)
                if ph == "critique":
                    o = os.path.join(mdir, "critique-by-%s.md" % me)
                    out.append(("C-%s-%s" % (m["id"], me), [o], fill(T_CRITIQUE, cid=me, round=r, mid=m["id"], opp=opp, task=task, card=card_text(st["cards"][me]),
                                                                     opp_sol=sol(d, opp, st["sol"][opp]), own_sol=sol(d, me, st["sol"][me]),
                                                                     exam_note=exam_line(st, opp, st["sol"][opp]), out=o), "text"))
                else:
                    o, ro = sol(d, me, r), os.path.join(mdir, "response-%s.md" % me)
                    out.append(("R-%s-%s" % (m["id"], me), [o, ro], fill(T_REVISE, cid=me, round=r, mid=m["id"], opp=opp, task=task, card=card_text(st["cards"][me]),
                                                                         own_sol=sol(d, me, st["sol"][me]), critique=crit_of_me,
                                                                         exam_note=exam_line(st, me, st["sol"][me]), run=d,
                                                                         code_note=code_note(st, d, me, r), resp_out=ro, out=o), ("revise", me)))
    elif ph in ("judge", "tiebreak"):
        r = st["round"]
        for m in st["matches"]:
            if m["winner"]:
                continue
            seats = [("a", m["a"], m["b"]), ("b", m["b"], m["a"])] if ph == "judge" else [("c", m["a"], m["b"]) if r % 2 else ("c", m["b"], m["a"])]
            mdir = os.path.join(d, "r%d" % r, m["id"])
            for jid, first, second in seats:
                o = os.path.join(mdir, "judge-%s.json" % jid)
                ex = exam_line(st, first, r) + exam_line(st, second, r)
                out.append(("J-%s-%s" % (m["id"], jid), [o], fill(
                    T_JUDGE, jid=jid, mid=m["id"], round=r, task=task, rubric=rubric, first=first, second=second,
                    first_sol=sol(d, first, r), first_crit=os.path.join(mdir, "critique-by-%s.md" % second), first_resp=os.path.join(mdir, "response-%s.md" % first),
                    second_sol=sol(d, second, r), second_crit=os.path.join(mdir, "critique-by-%s.md" % first), second_resp=os.path.join(mdir, "response-%s.md" % second),
                    exam_note=("\nAutomated test results (both failed or both passed, so you decide):\n" + ex) if ex else "",
                    scratch_note=(" If running code settles a point, do it only in %s." % os.path.join(d, "scratch", "judge-" + m["id"])) if st["kind"] == "code" else "",
                    out=o), ("verdict", first, second)))
    elif ph == "final":
        champ = st["champion"]
        cs, bs = sol(d, champ, st["sol"][champ]), os.path.join(d, "baseline.md")
        for jid, x, y in (("a", cs, bs), ("b", bs, cs)):
            o = os.path.join(d, "final", "check-%s.json" % jid)
            out.append(("F-" + jid, [o], fill(T_FINAL, task=task, rubric=rubric, x=x, y=y, out=o), ("verdict", "X", "Y")))
    return out


def job_done(j):
    jid, outs, _, kind = j
    if kind == "text":
        return all(ok_text(o) for o in outs)
    if isinstance(kind, tuple) and kind[0] == "revise":
        return all(ok_text(o) for o in outs)
    js = jload(outs[0])
    if js is None:
        return False
    if js.get("placeholder"):
        return True
    if kind[0] == "screen":
        sc = js.get("scores")
        return isinstance(sc, dict) and all(isinstance(sc.get(c), (int, float)) for c in kind[1])
    if kind[0] == "verdict":
        return valid_scores(js, [kind[1], kind[2]])
    return False


def placeholder(st, d, j):
    jid, outs, _, kind = j
    if kind == "text":
        for o in outs:
            if not ok_text(o):
                wr(o, "NO OUTPUT\n")
    elif kind[0] == "revise":
        cid = kind[1]
        prev = st["sol"][cid]
        if not ok_text(outs[0]):
            src = sol(d, cid, prev)
            wr(outs[0], rd(src) if ok_text(src) else "NO OUTPUT\n")
            if os.path.isdir(files_dir(d, cid, prev)) and not os.path.isdir(files_dir(d, cid, st["round"])):
                shutil.copytree(files_dir(d, cid, prev), files_dir(d, cid, st["round"]))
        if not ok_text(outs[1]):
            wr(outs[1], "NO OUTPUT\n")
    else:
        wr(outs[0], json.dumps({"placeholder": True}))


# ---------------------------------------------------------------- exams
def run_exam(st, d, cid, r):
    p = st["params"]
    work = tempfile.mkdtemp(prefix="colosseum-exam-")
    try:
        proj = os.path.join(work, "p")
        shutil.copytree(p["project"], proj, ignore=lambda _dir, names: [n for n in names if n in IGNORE_DIRS])
        fd = files_dir(d, cid, r)
        if os.path.isdir(fd):
            for base, _dirs, fnames in os.walk(fd):
                for fn in fnames:
                    src = os.path.join(base, fn)
                    dst = os.path.join(proj, os.path.relpath(src, fd))
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    shutil.copy2(src, dst)
        try:
            res = subprocess.run(p["test_cmd"], shell=True, cwd=proj, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 timeout=p["test_timeout"])
            code, text = res.returncode, res.stdout.decode("utf-8", "replace")
        except subprocess.TimeoutExpired:
            code, text = -9, "TIMEOUT after %ss" % p["test_timeout"]
        return {"exit": code, "passed": code == 0, "tail": "\n".join(text.strip().splitlines()[-30:])}
    finally:
        shutil.rmtree(work, ignore_errors=True)


def exam_targets(st):
    if st["phase"] == "exam-draft":
        return [(c, 0) for c in st["ids"]]
    return [(c, st["round"]) for m in st["matches"] for c in (m["a"], m["b"])]


# ---------------------------------------------------------------- transitions
def make_batches(ids, k, size, rng):
    batches = []
    nb = max(1, int(math.ceil(len(ids) / float(size))))
    for _ in range(k):
        order = ids[:]
        rng.shuffle(order)
        for i in range(nb):
            b = order[i::nb]
            rng.shuffle(b)
            batches.append(b)
    return batches


def after_screen(st, d, stats):
    rng = random.Random(st["seed"] * 7 + 1)
    sums = {c: [] for c in st["ids"]}
    for i, batch in enumerate(st["batches"]):
        js = jload(os.path.join(d, "screen", "b%02d.json" % (i + 1))) or {}
        for c in batch:
            v = (js.get("scores") or {}).get(c)
            if isinstance(v, (int, float)):
                sums[c].append(max(0.0, min(100.0, float(v))))
    st["screen"] = {c: (round(sum(v) / len(v), 1) if v else 0.0) for c, v in sums.items()}
    tie = {c: rng.random() for c in st["ids"]}
    passed = lambda c: 1 if (st["exam"].get(exam_key(c, 0)) or {}).get("passed") else 0
    ranked = sorted(st["ids"], key=lambda c: (-passed(c), -st["screen"][c], tie[c]))
    k = st["params"]["field"]
    st["ranking"] = ranked
    field = ranked[:k]
    for c in ranked[k:]:
        st["out"][c] = "screen"
        stats_bump(stats, st["kind"], st["cards"][c], 0.0, 0.5)
    for c in field:
        stats_bump(stats, st["kind"], st["cards"][c], 0.5, 0.5)
    st["alive"] = [field[s - 1] for s in seed_order(k)]
    start_round(st, 1)


def start_round(st, r):
    st["round"] = r
    st["matches"] = make_matches(st["alive"], r)
    st["phase"] = "critique"


def decide_by_tests(st):
    if not st["params"].get("test_cmd"):
        return
    r = st["round"]
    for m in st["matches"]:
        pa = (st["exam"].get(exam_key(m["a"], r)) or {}).get("passed")
        pb = (st["exam"].get(exam_key(m["b"], r)) or {}).get("passed")
        if bool(pa) != bool(pb):
            m["winner"], m["by"] = (m["a"] if pa else m["b"]), "tests"


def fallback(st, m):
    return m["a"] if st["screen"].get(m["a"], 0) >= st["screen"].get(m["b"], 0) else m["b"]


def collect_votes(st, d, seats):
    r = st["round"]
    need_tb = False
    for m in st["matches"]:
        if m["winner"]:
            continue
        mdir = os.path.join(d, "r%d" % r, m["id"])
        for jid in seats:
            js = jload(os.path.join(mdir, "judge-%s.json" % jid))
            if js and not js.get("placeholder") and valid_scores(js, [m["a"], m["b"]]):
                m["votes"].append({"judge": jid, "winner": vote(js, m["a"], m["b"]),
                                   "totals": {m["a"]: total(js["scores"][m["a"]]), m["b"]: total(js["scores"][m["b"]])}})
        vs = [v["winner"] for v in m["votes"] if v["winner"]]
        if seats == ["a", "b"]:
            ab = [v for v in m["votes"] if v["judge"] in ("a", "b")]
            if len(ab) == 2:
                st["judge_pairs"] += 1
                if ab[0]["winner"] and ab[0]["winner"] == ab[1]["winner"]:
                    st["judge_agree"] += 1
            if len(vs) == 2 and vs[0] == vs[1]:
                m["winner"], m["by"] = vs[0], "judges"
            else:
                need_tb = True
        else:
            for c in (m["a"], m["b"]):
                if vs.count(c) >= 2:
                    m["winner"], m["by"] = c, "tiebreak"
            if not m["winner"]:
                if len(vs) == 1 and len(m["votes"]) >= 1:
                    m["winner"], m["by"] = vs[0], "single-judge"
                else:
                    m["winner"], m["by"] = fallback(st, m), "fallback-screen-score"
    return need_tb


def finish_round(st, stats):
    r = st["round"]
    winners = []
    for m in st["matches"]:
        w = m["winner"]
        l = m["b"] if w == m["a"] else m["a"]
        st["out"][l] = "r%d" % r
        stats_bump(stats, st["kind"], st["cards"][w], 1.0, 1.0)
        stats_bump(stats, st["kind"], st["cards"][l], 0.0, 1.0)
        winners.append(w)
    st["history"].append({"round": r, "matches": st["matches"]})
    st["alive"] = winners
    if len(winners) == 1:
        st["champion"] = winners[0]
        st["phase"] = "final" if st["has_baseline"] else "done"
    else:
        start_round(st, r + 1)


def advance(st, d, stats):
    ph = st["phase"]
    if ph == "draft":
        st["phase"] = "exam-draft" if st["params"].get("test_cmd") else "screen"
    elif ph == "exam-draft":
        st["phase"] = "screen"
    elif ph == "screen":
        after_screen(st, d, stats)
    elif ph == "critique":
        st["phase"] = "revise"
    elif ph == "revise":
        for m in st["matches"]:
            for c in (m["a"], m["b"]):
                st["sol"][c] = st["round"]
        st["phase"] = "exam" if st["params"].get("test_cmd") else "judge"
        if st["phase"] == "judge":
            decide_by_tests(st)
    elif ph == "exam":
        decide_by_tests(st)
        st["phase"] = "judge"
    elif ph == "judge":
        if collect_votes(st, d, ["a", "b"]):
            st["phase"] = "tiebreak"
        else:
            finish_round(st, stats)
    elif ph == "tiebreak":
        collect_votes(st, d, ["c"])
        finish_round(st, stats)
    elif ph == "final":
        res = {}
        for jid, cx in (("a", "champion"), ("b", "baseline")):
            js = jload(os.path.join(d, "final", "check-%s.json" % jid))
            if js and not js.get("placeholder") and valid_scores(js, ["X", "Y"]):
                cy = "baseline" if cx == "champion" else "champion"
                res[jid] = {cx: total(js["scores"]["X"]), cy: total(js["scores"]["Y"]),
                            cx + "_fatal": bool(js["scores"]["X"].get("fatal")), cy + "_fatal": bool(js["scores"]["Y"].get("fatal")),
                            "reason": js.get("reason", "")}
        st["final"] = res
        st["phase"] = "done"


# ---------------------------------------------------------------- commands
def cmd_plan(a):
    n, k = sizes(a)
    batches = a.screeners * int(math.ceil(n / float(a.batch)))
    rounds = int(math.log2(k))
    per_match = 6
    calls = n + batches + per_match * (k - 1)
    naive = n + 5 * (n - 1)
    print("Colosseum plan: %d drafters -> screen to %d -> %d bracket rounds" % (n, k, rounds))
    print("  draft       %4d calls" % n)
    print("  screen      %4d calls (%d screeners per draft, %d per batch)" % (batches, a.screeners, a.batch))
    print("  bracket     %4d calls (%d matches x 2 critiques, 2 revisions, 2 judges)" % (per_match * (k - 1), k - 1))
    print("  tiebreaks   up to %d more (only when the two judges disagree)" % (k - 1))
    print("  total       ~%d sub-agent calls (+2 with a baseline). A full bracket of %d would be ~%d." % (calls, n, naive))
    print("  waves of %d: ~%d" % (a.wave, int(math.ceil(n / float(a.wave))) + int(math.ceil(batches / float(a.wave))) + rounds * 3))


def sizes(a):
    n, k = a.drafters, a.field
    if a.quick:
        n, k = 12, 4
    if a.full:
        n, k = 64, 16
    if n < 2 or n > 128:
        raise Err("--drafters must be 2..128")
    if k < 2 or k & (k - 1) or k > n:
        raise Err("--field must be a power of two between 2 and --drafters")
    return n, k


def cmd_init(a):
    n, k = sizes(a)
    if not ok_text(a.task_file):
        raise Err("task file %s is missing or empty" % a.task_file)
    if a.test_cmd and not a.project:
        raise Err("--test-cmd needs --project (the directory the tests run in)")
    if a.project and not os.path.isdir(a.project):
        raise Err("--project %s is not a directory" % a.project)
    seed = a.seed if a.seed is not None else random.randrange(10 ** 6)
    rng = random.Random(seed)
    name = time.strftime("run-%Y%m%d-%H%M%S")
    d = os.path.join(ROOT, name)
    if os.path.exists(d):
        name += "-%d" % rng.randrange(1000)
        d = os.path.join(ROOT, name)
    os.makedirs(d)
    wr(os.path.join(d, "task.md"), rd(a.task_file))
    wr(os.path.join(d, "rubric.md"), RUBRIC_MD)
    has_base = bool(a.baseline_file and ok_text(a.baseline_file))
    if has_base:
        wr(os.path.join(d, "baseline.md"), rd(a.baseline_file))
    ids = ["c%02d" % (i + 1) for i in range(n)]
    stats = stats_load()
    cards = dict(zip(ids, deal(n, a.kind, rng, stats)))
    st = {"version": VERSION, "run": name, "seed": seed, "kind": a.kind, "created": time.strftime("%Y-%m-%d %H:%M:%S"),
          "params": {"drafters": n, "field": k, "screeners": a.screeners, "batch": a.batch, "wave": a.wave,
                     "test_cmd": a.test_cmd, "project": os.path.abspath(a.project) if a.project else None, "test_timeout": a.test_timeout},
          "has_baseline": has_base, "ids": ids, "cards": cards, "sol": {c: 0 for c in ids}, "exam": {},
          "batches": make_batches(ids, a.screeners, a.batch, rng), "screen": {}, "ranking": [], "out": {},
          "phase": "draft", "round": 0, "alive": [], "matches": [], "history": [], "tries": {}, "calls": 0,
          "judge_pairs": 0, "judge_agree": 0, "champion": None, "final": {}}
    save(os.path.abspath(d), st)
    wr(os.path.join(ROOT, "LATEST"), name)
    print("run %s, seed %d, kind %s: %d drafters, field %d%s%s" % (name, seed, a.kind, n, k,
          ", tests: " + a.test_cmd if a.test_cmd else "", ", with baseline" if has_base else ""))
    print("cards dealt: %d unique (deck of %d)%s" % (n, len(ANGLES) * len(METHODS) * len(PRIORITIES),
          ", weighted by past results" if stats.get(a.kind) else ""))
    print("next: %s next" % self_cmd())


def cmd_next(a):
    d, st = load()
    stats = stats_load()
    t0 = time.time()
    while True:
        ph = st["phase"]
        if ph == "done":
            save(d, st)
            print("DONE. Champion %s. Run: %s champion" % (st["champion"], self_cmd()))
            return
        if ph in ("exam", "exam-draft"):
            todo = [(c, r) for c, r in exam_targets(st) if exam_key(c, r) not in st["exam"]]
            for c, r in todo:
                if time.time() - t0 > EXAM_BUDGET_S:
                    save(d, st)
                    print("EXAMS: %d of %d still to run. Run `%s next` again." % (len([x for x in todo if exam_key(*x) not in st["exam"]]), len(exam_targets(st)), self_cmd()))
                    return
                st["exam"][exam_key(c, r)] = run_exam(st, d, c, r)
                save(d, st)
            advance(st, d, stats)
            save(d, st)
            continue
        js = jobs(st, d)
        base = ph if ph not in ("exam", "exam-draft") else "draft"
        missing = [j for j in js if not job_done(j)]
        for j in missing:
            if st["tries"].get(j[0], 0) >= MAX_TRIES[base]:
                placeholder(st, d, j)
        missing = [j for j in missing if not job_done(j)]
        if not missing:
            advance(st, d, stats)
            wr(stats_path(), json.dumps(stats, indent=1, sort_keys=True))
            save(d, st)
            if st["phase"] == "critique" and st["round"] >= 1 and ph in ("screen", "judge", "tiebreak"):
                print("PROGRESS: %s" % progress(st))
            continue
        bdir = os.path.join(d, "briefs")
        for j in missing:
            wr(os.path.join(bdir, j[0] + ".md"), j[2])
            st["tries"][j[0]] = st["tries"].get(j[0], 0) + 1
        st["calls"] += len(missing)
        save(d, st)
        w = st["params"]["wave"]
        label = {"draft": "drafting", "screen": "screening", "critique": "critiques", "revise": "revisions",
                 "judge": "judging (2 judges per match, order swapped)", "tiebreak": "tiebreak judges", "final": "final check vs rejected answer"}[ph]
        print("PHASE %s%s: %d job(s) in %d wave(s) of up to %d." % (label, (" round %d" % st["round"]) if st["round"] else "", len(missing),
              int(math.ceil(len(missing) / float(w))), w))
        print("Launch each wave as one message of parallel Agent calls; prompt = 'Read <brief> and follow it exactly. It is your whole brief.'")
        for i in range(0, len(missing), w):
            print("wave %d:" % (i // w + 1))
            for j in missing[i:i + w]:
                print("  %s  %s" % (j[0], os.path.join(bdir, j[0] + ".md")))
        print("When every wave has replied: %s next" % self_cmd())
        return


def progress(st):
    return "round %d starting, %d of %d left" % (st["round"], len(st["alive"]), len(st["ids"]))


def cmd_status(a):
    d, st = load()
    print("run %s | phase %s | round %d | calls so far %d" % (st["run"], st["phase"], st["round"], st["calls"]))
    print("alive: %s" % (", ".join(st["alive"]) or "(screening not done)"))
    if st["judge_pairs"]:
        print("judge agreement: %d/%d" % (st["judge_agree"], st["judge_pairs"]))
    out = {}
    for c, where in st["out"].items():
        out.setdefault(where, []).append(c)
    for where in sorted(out):
        print("out at %s: %s" % (where, ", ".join(sorted(out[where]))))


def summary(st, d):
    c = st["champion"]
    resolved, path = [], []
    for h in st["history"]:
        for m in h["matches"]:
            if m["winner"] == c:
                opp = m["b"] if m["a"] == c else m["a"]
                path.append("%s beat %s (%s)" % (m["id"], opp, m["by"]))
                for jid in ("a", "b", "c"):
                    js = jload(os.path.join(d, "r%d" % h["round"], m["id"], "judge-%s.json" % jid)) or {}
                    for item in ((js.get("resolved") or {}).get(c) or []):
                        if item not in resolved:
                            resolved.append(item)
    return path, resolved


def cmd_champion(a):
    d, st = load()
    if not st["champion"]:
        raise Err("no champion yet; phase is %s" % st["phase"])
    c = st["champion"]
    path, resolved = summary(st, d)
    print("CHAMPION %s" % c)
    print("answer: %s" % sol(d, c, st["sol"][c]))
    if os.path.isdir(files_dir(d, c, st["sol"][c])):
        print("files: %s" % files_dir(d, c, st["sol"][c]))
    print("card: %s" % card_short(st["cards"][c]))
    print("screen: %.1f/100, rank %d of %d" % (st["screen"].get(c, 0), st["ranking"].index(c) + 1, len(st["ids"])))
    print("path: " + "; ".join(path))
    print("critiques beaten:")
    for r in resolved[:15]:
        print("  - %s" % r)
    if st["judge_pairs"]:
        print("judge agreement: %d/%d matches" % (st["judge_agree"], st["judge_pairs"]))
    if st["final"]:
        for jid, f in sorted(st["final"].items()):
            print("final check %s: champion %.1f vs rejected %.1f%s - %s" % (jid, f["champion"], f["baseline"],
                  " (champion fatal)" if f.get("champion_fatal") else "", f.get("reason", "")))
    print("calls used: %d" % st["calls"])
    print("record: %s" % d)


def cmd_report(a):
    d, st = load()
    n = len(st["ids"])
    lines = ["# Colosseum report: %s" % st["run"], "",
             "- kind: %s, seed: %d, drafters: %d, field: %d" % (st["kind"], st["seed"], n, st["params"]["field"]),
             "- sub-agent calls: %d (a full bracket of %d would need ~%d)" % (st["calls"], n, n + 5 * (n - 1)),
             "- judge agreement (swapped order): %d/%d" % (st["judge_agree"], st["judge_pairs"]),
             "- champion: %s (%s)" % (st["champion"], card_short(st["cards"][st["champion"]]) if st["champion"] else "-"), "",
             "## Screen ranking", "", "| rank | id | score | tests | card | went out |", "| --- | --- | --- | --- | --- | --- |"]
    for i, c in enumerate(st["ranking"] or st["ids"]):
        e = st["exam"].get(exam_key(c, 0))
        lines.append("| %d | %s | %.1f | %s | %s | %s |" % (i + 1, c, st["screen"].get(c, 0), ("pass" if e["passed"] else "fail") if e else "-",
                                                     card_short(st["cards"][c]), st["out"].get(c, "champion" if c == st["champion"] else "-")))
    lines += ["", "## Matches", "", "| match | a | b | winner | decided by | judge totals |", "| --- | --- | --- | --- | --- | --- |"]
    for h in st["history"]:
        for m in h["matches"]:
            tot = "; ".join("%s: %s" % (v["judge"], ", ".join("%s %.0f" % kv for kv in sorted(v["totals"].items()))) for v in m["votes"])
            lines.append("| %s | %s | %s | %s | %s | %s |" % (m["id"], m["a"], m["b"], m["winner"], m["by"], tot or "-"))
    p = os.path.join(d, "report.md")
    wr(p, "\n".join(lines) + "\n")
    print(p)


def cmd_stats(a):
    stats = stats_load()
    kinds = [a.kind] if a.kind else sorted(stats)
    if not kinds:
        print("no history yet (%s)" % stats_path())
        return
    for k in kinds:
        print("== %s ==" % k)
        for axis, vals in AXES.items():
            rows = []
            for vid, name, _ in vals:
                c = stats.get(k, {}).get(axis, {}).get(vid, {"w": 0, "g": 0})
                rows.append((weight(stats, k, axis, vid), name, c["w"], c["g"]))
            print("  %s" % axis)
            for wgt, name, w, g in sorted(rows, reverse=True):
                print("    %-26s win %5.1f / %5.1f  weight %.2f" % (name, w, g, wgt))


def main(argv=None):
    p = argparse.ArgumentParser(prog="colosseum", description="Colosseum tournament engine v" + VERSION)
    sub = p.add_subparsers(dest="cmd")

    def sizing(sp):
        sp.add_argument("--drafters", type=int, default=32)
        sp.add_argument("--field", type=int, default=8)
        sp.add_argument("--quick", action="store_true", help="12 drafters, field 4")
        sp.add_argument("--full", action="store_true", help="64 drafters, field 16")
        sp.add_argument("--screeners", type=int, default=2, help="screeners per draft")
        sp.add_argument("--batch", type=int, default=6, help="drafts per screener")
        sp.add_argument("--wave", type=int, default=10)

    sp = sub.add_parser("plan")
    sizing(sp)
    sp = sub.add_parser("init")
    sizing(sp)
    sp.add_argument("--task-file", required=True)
    sp.add_argument("--baseline-file")
    sp.add_argument("--seed", type=int)
    sp.add_argument("--kind", default="general", choices=["general", "code", "writing", "plan", "analysis"])
    sp.add_argument("--test-cmd")
    sp.add_argument("--project")
    sp.add_argument("--test-timeout", type=int, default=300)
    for name in ("next", "status", "champion", "report"):
        sub.add_parser(name)
    sp = sub.add_parser("stats")
    sp.add_argument("--kind")
    a = p.parse_args(argv)
    fn = {"plan": cmd_plan, "init": cmd_init, "next": cmd_next, "status": cmd_status,
          "champion": cmd_champion, "report": cmd_report, "stats": cmd_stats}.get(a.cmd)
    if not fn:
        p.print_help()
        return 2
    try:
        fn(a)
    except Err as e:
        print("error: %s" % e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
```
<!-- COLOSSEUM-ENGINE-END -->
