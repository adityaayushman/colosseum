---
name: colosseum
description: Run a screened tournament of sub-agents on one task - many drafts, a cheap screening cut, then critique, revise and double-blind judging until one answer wins. Use on /colosseum, "colosseum this", "make them compete", or when the user rejects an answer.
argument-hint: "[--quick | --full | --max | --drafters N --field K] [--kind code|writing|plan|analysis] [--budget | --models role=model,...] [--merge N] [--test-cmd CMD --project DIR] [--seed S] <task>"
---

# Colosseum

You orchestrate. You never write, critique or judge an answer yourself, and you never pick a winner.

User input after the command: `$ARGUMENTS` (blank or a placeholder means: take the task from the conversation).

## The engine

<!-- ENGINE-LOCATOR -->
`COL` below means `python3 "${CLAUDE_SKILL_DIR}/colosseum.py"` (if that path looks unexpanded, use this skill's base directory).
<!-- /ENGINE-LOCATOR -->

The engine keeps all state in `.colosseum/<run>/state.json` under the current directory. Run every `COL` command from the same directory.

## 1. Size and consent

| flag | effect |
| --- | --- |
| default | 32 drafters, top 8 enter the bracket (~96 calls) |
| `--quick` | 12 drafters, top 4 (~40 calls) |
| `--full` | 100 drafters, top 16 (~245 calls) |
| `--max` | 256 drafters, top 32 (~575 calls) |
| `--drafters N --field K` | custom, up to 256 drafters; K must be a power of two |
| `--screeners S` | screeners per draft (default 3); each screener's scores are standardised before averaging |
| `--kind` | `code`, `writing`, `plan`, `analysis` or `general`. Pick it yourself from the task. It drives strategy learning. |
| `--test-cmd CMD --project DIR` | code tasks: run CMD in a clean copy of DIR with each answer's files overlaid. Tests decide matches when only one side passes. |
| `--models role=model,...` | model per role. Roles: draft, screen, critique, revise, judge, merge (or `all`). Models: haiku, sonnet, opus, inherit. Default: sonnet everywhere, opus for judge and merge. |
| `--budget` | cheaper preset: haiku drafts and screens, sonnet for the rest |
| `--merge N` | after the final, synthesise the top N finalists (default 3; 0 = off). The merge is delivered only if both judges prefer it to the winner and it does not break tests. |
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

- prints `PHASE ... MODEL: <m>` with jobs grouped in waves: launch each wave as ONE message of parallel Agent calls, one per job - `subagent_type: general-purpose`, `description: colosseum <job id>`, `model: <m>` (omit `model` when it says inherit), `prompt: Read <brief path> and follow it exactly. It is your whole brief.` Wait for the whole wave, then the next wave, then run `COL next` again; or
- runs test exams itself and may ask you to run `next` again; or
- prints `PROGRESS: ...` - pass that one line to the user.

Missing outputs are re-issued automatically; after the retry limit the engine writes a placeholder and moves on. Never write an answer, critique or verdict file yourself. `COL pairings` shows the current matches. `COL record <match> <winner> --reason "..."` overrides one match, and only when the user asks.

Phases: draft -> (exam) -> screen -> per round: critique -> revise -> (exam) -> judge (two judges, order swapped; a 3-judge panel in the final) -> tiebreak only if they disagree -> ... -> merge of the top finalists -> (exam) -> merge check (two judges, order swapped) -> final check vs the rejected answer (if any) -> DONE.

Do not read answers, critiques or verdicts during the run. Do not paste pairings or scores mid-run. If context is compacted, run `COL status` then `COL next`.

## 5. Result

Run `COL champion` and `COL report`, then read the file on the `DELIVER THIS ANSWER` line (the only answer you read). Tell the user:

1. that answer, in full (for code: the changes, and where the files are);
2. why it won: the champion's path through the bracket and the critiques it beat, its card in one line, and whether the merge was accepted (with the two judges' scores) or the champion was kept;
3. the numbers: drafters in, calls used by model, judge agreement rate;
4. if there was a rejected answer: both final-check scores, honestly - say so plainly if the old answer scored higher;
5. where the record and `report.md` live.

Never apply code changes to the user's project. Ask: apply, adjust, or leave?

`COL stats` shows which strategies win most for each kind of task; future runs deal winning strategies more often while still exploring every option. `COL dashboard --out <file>.html` writes a self-contained dashboard of strategy win rates, calls saved, judge agreement and merge acceptance across all runs. Offer it when the user asks how the system is doing.
