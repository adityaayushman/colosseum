# Colosseum

**A tournament of Claude sub-agents for when one answer isn't good enough.**

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
| **Screening cut** | Every draft is scored by 2 independent screeners in shuffled batches. Only the top *K* enter the bracket, so a 32-draft run costs ~86 calls instead of ~187. |
| **Bias-checked judging** | Each match gets two judges with the answers in swapped order. If they disagree, a third judge breaks the tie. The agreement rate is reported, which gives a measurable signal of judge reliability. |
| **Tests as ground truth** | For code, each answer's files are overlaid on a clean copy of your project and your test command is run. Passing drafts rank first in the screen, and when only one side of a match passes, the tests decide without a judge. Your project is never modified. |
| **Strategy learning** | Wins and losses are recorded per strategy and per task kind in `~/.colosseum/stats.json`. Future runs deal the winning strategies more often (Beta-posterior weighting), while every option keeps a minimum share so the system keeps exploring. |
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
                 champion ──► [final check vs rejected answer, both orders] ──► you
```

## Sizes

| preset | drafters | bracket | calls (approx.) |
| --- | --- | --- | --- |
| `--quick` | 12 | 4 | 34 |
| default | 32 | 8 | 86 |
| `--full` | 64 | 16 | 176 |

Tiebreak judges add up to K-1 calls. `python3 colosseum.py plan [flags]` prints the exact plan.

## Strategy cards

A card has three parts: an **angle** (10, e.g. Pre-mortem, Work backwards, Trade-off map), a **method** (8, e.g. Checks first, Three sketches, Expand then cut) and a **priority** (8, e.g. Correctness above all, Edge cases, Usable today). That makes 640 unique cards, and no two drafters get the same one.

## Scoring

Judges score accuracy (35), coverage (25), resilience to critique (20) and actionability (20). An answer with a verified fatal flaw can never beat one without. See `RUBRIC_MD` in the engine.

## Install

**Claude Code:** copy `skills/colosseum/` to `~/.claude/skills/` (global) or `.claude/skills/` (project).

**Claude Cowork:** save `cowork/SKILL.md` as a skill. It carries the engine inside it and unpacks it on first use, with a SHA-256 check. To rebuild it after editing the engine, run `python3 tools/build_cowork.py`.

## Engine commands

```
colosseum.py plan  [size flags]
colosseum.py init  [size flags] --task-file F [--kind K] [--baseline-file F] [--test-cmd C --project D] [--seed S]
colosseum.py next        # does the next step or prints the next wave of jobs
colosseum.py status
colosseum.py champion    # winner, its path, critiques it beat, judge agreement, final check
colosseum.py report      # writes report.md: screen ranking, every match, judge totals
colosseum.py stats [--kind K]   # learned strategy win rates
```

## Tests

```
python3 -m unittest discover -s tests -v
```

The suite runs complete tournaments with simulated agents. It covers judges who always disagree (every match goes to a tiebreak), missing outputs (placeholders and retries), test-decided code matches (and checks that the project is left untouched), the final check against a rejected answer, card uniqueness, and the learning shift.

## Limits

- The sub-agents are all the same model. Their diversity comes from the cards, not from different models.
- The judges are also models. The winner is the strongest answer this run found, not a proof of correctness. That is why the report shows judge agreement and the critiques the winner beat.
- A vague task produces vague answers, so the task file is the most important input.

MIT © 2026 Aditya Ayushman Sahoo
