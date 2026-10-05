# Colosseum

**A tournament of Claude sub-agents for when one answer isn't good enough.**

Project site: https://adityaayushman.github.io/colosseum/ (source in `docs/`; enable GitHub Pages from the `docs` folder on `main`).

Colosseum gives the same task to many sub-agents, each with a different strategy card. It cheaply screens the drafts and sends only the strongest into a knockout bracket. There, rivals critique each other, revise their answers, and are scored by **two judges who read the answers in opposite order**. The answer that survives comes back to you with the record of every critique it beat.

Works as a **Claude Code skill** and in **Claude Cowork**. Standard-library Python 3.8+, no dependencies, no API keys.

```
/colosseum --quick write the onboarding email for new users
/colosseum --kind code --test-cmd "pytest -q" --project /abs/path/to/repo fix the flaky test in tests/test_api.py
/colosseum                      # re-run my last request; beat the answer I didn't like
```

## Why it's different

| | |
| --- | --- |
| **Calibrated screening cut** | Every draft is scored by 3 independent screeners in shuffled batches. Each screener's scores are standardised (z-scores) before averaging, so one harsh or lenient screener cannot reorder the field. Only the top *K* enter the bracket: 100 drafters cost ~245 calls instead of ~595. |
| **Bias-checked judging** | Each match gets two judges with the answers in swapped order; if they disagree, a third breaks the tie. The final always gets a 3-judge panel. A "fatal flaw" only counts when the judge quotes the exact failing part. The agreement rate is reported as a reliability signal. |
| **Tests as ground truth** | For code, each answer's files are overlaid on a clean copy of your project and your test command is run. Passing drafts rank first in the screen, and when only one side of a match passes, the tests decide without a judge. Your project is never modified. |
| **Strategy learning** | Wins and losses are recorded per strategy and per task kind in `~/.colosseum/stats.json`. Future runs deal the winning strategies more often (Beta-posterior weighting), while every option keeps a minimum share so the system keeps exploring. |
| **Model mixing** | Each role has its own model: drafts and screens on a cheaper model, judges and the merge on the strongest one. Use `--budget` for an all-cheap preset or `--models draft=haiku,judge=opus` for your own mix. Calls are logged per model. |
| **Merge of the finalists** | After the final, one synthesiser combines the strongest parts of the top 2-4 answers, using the winner as the base and fixing the weaknesses the judges left open. The merge is delivered **only** if it passes the tests (for code) and **both** judges, reading in swapped order, score it above the winner. Otherwise you get the winner. |
| **Dashboard** | `colosseum.py dashboard` writes a self-contained HTML page of strategy win rates per task kind, calls saved, judge agreement and merge acceptance across all your runs. |
| **Seeded bracket** | Screen rank 1 meets rank 8, 2 meets 7 and so on, so the two strongest drafts can only meet in the final. |

## Pipeline

```
draft (N) ──► [exam] ──► screen (2× per draft) ──► top K, seeded
                                                     │
        ┌────────────────────────────────────────────┘
        ▼
  critique ──► revise ──► [exam] ──► judge A + judge B (swapped order)
        ▲                                   │ disagree? ──► judge C
        └──────────── winners ◄─────────────┘
                         │
     champion + top finalists ──► merge ──► [exam] ──► 2 judges: merge vs champion
                         │            (merge delivered only if both prefer it)
                         ▼
     [final check vs rejected answer, both orders] ──► you
```

## Sizes

| preset | drafters | bracket | calls (approx.) |
| --- | --- | --- | --- |
| `--quick` | 12 | 4 | 40 |
| default | 32 | 8 | 96 |
| `--full` | 100 | 16 | 245 |
| `--max` | 256 | 32 | 575 |

These figures include the 3 merge calls (`--merge 0` removes them) and the final's 3-judge panel. Tiebreak judges add up to K-1 calls. `python3 colosseum.py plan [flags]` prints the exact plan.

## Strategy cards

A card has three parts: an **angle** (16, e.g. Pre-mortem, Constraint map, Put numbers on it), a **method** (13, e.g. Checks first, Red-team pass, Verify every claim) and a **priority** (13, e.g. Correctness above all, Evidence-backed, Scope discipline). That makes **2,704** cards.

For up to **169** drafters, cards are dealt from an orthogonal Latin square: no two drafters share two of their three parts, and every full diagonal uses each angle, method and priority exactly once, so the field stays balanced. Above 169, every card is still unique.

## Scoring

Judges score accuracy (30), coverage (25), resilience to critique (20), actionability (15) and clarity (10). An answer with a verified fatal flaw (one the judge backs with a quote) can never beat one without. See `RUBRIC_MD` in the engine.

## Install

**Claude Code:** copy `skills/colosseum/` to `~/.claude/skills/` (global) or `.claude/skills/` (project).

**Claude Cowork:** save `cowork/SKILL.md` as a skill. It carries the engine inside it and unpacks it on first use, with a SHA-256 check. To rebuild it after editing the engine, run `python3 tools/build_cowork.py`.

## Engine commands

```
colosseum.py plan  [size flags]
colosseum.py init  [size flags] --task-file F [--kind K] [--budget | --models role=model,...] [--merge N]
                   [--baseline-file F] [--test-cmd C --project D] [--seed S]
colosseum.py next        # does the next step or prints the next wave of jobs
colosseum.py status
colosseum.py pairings    # current matches with each side's card
colosseum.py record <match> <winner> --reason "..."   # logged manual override
colosseum.py champion    # winner, its path, critiques it beat, judge agreement, final check
colosseum.py report      # writes report.md: screen ranking, every match, judge totals
colosseum.py stats [--kind K]   # learned strategy win rates
colosseum.py dashboard [--out F] # HTML dashboard across all runs
```

## Tests

```
python3 -m unittest discover -s tests -v
```

The suite runs complete tournaments with simulated agents. It covers judges who always disagree (every match goes to a tiebreak), missing outputs (placeholders and retries), test-decided code matches (and checks that the project is left untouched), the final check against a rejected answer, card uniqueness, and the learning shift. It also covers merges being accepted, rejected without a unanimous verdict, and rejected for breaking tests, plus per-role model routing and the dashboard. The 31 tests also check the Latin-square guarantee at 144 and 169 drafters, that z-score calibration recovers the true ranking when half the screeners are lenient by 50 points (raw averaging gets it wrong), that unquoted fatal flags are ignored, the final's 3-judge panel, manual overrides, and that the Cowork build carries the exact engine.

## Limits

- The sub-agents are all the same model. Their diversity comes from the cards, not from different models.
- The judges are also models. The winner is the strongest answer this run found, not a proof of correctness. That is why the report shows judge agreement and the critiques the winner beat.
- A vague task produces vague answers, so the task file is the most important input.

MIT © 2026 Aditya Ayushman Sahoo
