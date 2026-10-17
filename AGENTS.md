# Ledger rules

This repository is a **ledger**: the plans, experiments, observations and decisions of a project, one Markdown file
per record, kept by the people and the AI agents who do the work. `scripts/labledger.py` writes the records, checks
them and builds the tables. This file holds the rules every session uses. Two more hold what only some sessions meet,
each read when its case comes up: [MACHINES.md](MACHINES.md) when the ledger is kept on more than one machine
(numbers, a `sync` that stopped, partial clones, SSH), and [SETUP.md](SETUP.md) to set up the ledger or its skill, to
bring in tables made before it, to change its settings (`labledger.yml`: time zone, commit prefix, language, code
vault) or templates, or to change the tool or take a newer version of it. Every rule in the three files is checked by
the tool: by `validate`, by a command before it writes, or when a commit is made or pushed. What the tool cannot check
is marked *guidance* and says why it is there. A check sees the form of a record, not its meaning: that a note is
filled, not that it says why.

**Every write command commits what it wrote, and pushes it when the branch tracks a remote.** Nobody is asked before a
record is committed (see [Commits](#commits)).

## Records

| Kind | File | Address | Status |
|---|---|---|---|
| area | `areas/<area>/README.md` | `<area>` | — |
| todo | `areas/<area>/<PREFIX>-NNN.md` | `PREFIX-NNN` | open: `idea` `investigate` `planned` `in_progress` `blocked` `parked` · closed: `done` `dropped` |
| exp | `experiments/EXP-NNNN.md` | `EXP-NNNN` | `in_flight` `final` `withdrawn` `superseded` |
| arc | `arcs/ARC-NNN.md` | `ARC-NNN`, `arc:ARC-NNN` | `active` `done` `parked` |
| inv | any other `areas/<area>/<name>.md`: an investigation, review, audit, design or plan | `inv:<name>` | `open` `concluded` |
| decision | `decisions/<date>-<slug>.md` (across areas), `areas/<area>/decisions/<date>-<slug>.md` | `dec:<date>-<slug>` | — |
| issue | `areas/<area>/issues/<slug>.md` | `issue:<slug>` | `open` `promoted` `resolved` |
| state table | `state/<name>.csv`, `state/<name>.md` | none; `commit` takes its path | — |

`validate` checks every record and the rules between records; `validate --help` lists each check:

- **Heads**: each kind's keys, in the order `fmt` writes, the required ones present and their single values filled,
  lists and single values as the kind says.
- **Values**: statuses from the table above, `priority` `P0`–`P2`, dates `YYYY-MM-DD`; `summary` says what a record
  found, in at most 500 characters, and `status_note` why its status is what it is, in at most 300.
- **Names and folders**: every address used once, every file where its kind lives.
- **Links**: every record a head names exists; a body's relative links point at files or folders of the ledger, not
  outside it or into `views/`.
- **Statuses**: a `dropped` TODO and a `parked` arc say why; a closed TODO has its close decision, which `set` writes.
- **Numbers**: a final experiment's `data` tables exist, are linked from its body and read as tables; state tables have
  their columns and cite final experiments.
- **Code and files**: an experiment's `code` holds the entries `code` writes, its `manifest.tsv` the columns `manifest`
  writes.

How the records fit together. The tool computes what this describes; the advice in it is *guidance*:

- **`affects` carries one piece of work to every TODO it moved.** List them all instead of copying the result into
  each TODO. `show <ID>` lists every record that names a TODO, computed each time and never stored; an issue counts
  while it is open. An experiment that moves no TODO leaves `affects` empty.
- **An arc's members are a union**: its own `members` and `exps`, every TODO with `from: [arc:ARC-NNN]`, and every EXP
  with `arc: ARC-NNN`. `new exp --arc ARC-NNN` adds an experiment without opening the arc.
- **`parked` means "not now"** and keeps a TODO out of the 90-day review; **`dropped` means "decided not to"**.
- **An issue is an observation with a life of its own.** Anything can settle it (a TODO, a commit, an experiment, an
  investigation): `resolve issue <slug> --by …` marks it resolved and appends a line naming what did. Promoting it to
  a TODO, `promote issue <slug> --to <ID>` (from `open` only), is one way among those.
- **Every other `.md` file directly in an area folder is an inv**: an investigation, review, audit, design or plan.
  Its `affects` names the issues and TODOs it updated; a TODO made from it says `from: [inv:<name>]`. Other files
  there (not `.md`, or in a subfolder other than `issues/` and `decisions/`) are no records, and nothing checks them.
- **Bodies are edited by hand**, and so are the head fields `set` has no option for, an entry removed from a list, a
  deleted `host` and a record's `date` (`set --date` dates `updated`); `fmt` then puts the head in order. No command
  rewrites a body, apart from trimming the blank lines at its start and end.

`views/` holds the tables and lists `render` builds on each clone, never committed: every TODO in `views/index.md`,
every experiment in `views/log.md`. The write commands render on their own; after a pull or a hand edit, `render`
brings `views/` up to date.

## Numbers and the data behind them

A reader checks a number by opening the table it was computed from.

- **An experiment's `data` names its tables**: `set EXP-NNNN --data "<file>: <what one row is>"` for each CSV, TSV or
  JSON file in `experiments/attachments/EXP-NNNN/`, linked from the body where its numbers are used:
  `[results.csv](attachments/EXP-NNNN/results.csv)`. A `final` experiment has `data`; one that produced no numbers
  says why instead, with the single entry `"n/a (why)"`.
- **A table reads as one**: a header row and one value per cell; a column that holds only numbers ends its name with
  their unit in brackets: `latency[ms]`; `[1]` for a count or a ratio; `[id]` for numbers that name things (a run, a
  seed).
- *Guidance:* one plain value per cell, of any kind: the check sees lists of numbers only, so `a, b` or `0.5/3` pass it
  and are still not one value. Large leftovers of a run (logs, traces, archives) may sit in the attachment folder too,
  or outside the ledger: `data` names the tables the reported numbers come from, not everything a run left behind.

**Current values live in `state/`.** A state table, `state/<name>.csv`, holds what is true now, one value per row:

| Column | What it holds |
|---|---|
| `item` | what the value is (`latency`, `accuracy`) |
| `value`, `unit` | the value and its unit |
| `kind` | `measured` or `estimate` |
| `exp` | the final experiment it comes from; that experiment's `state` names this table |
| `commit` | the version of the code it was measured on |
| `measured_at` | the day it was measured, `YYYY-MM-DD` |
| `source` (optional) | its data file, as `EXP-NNNN/<file>` under `experiments/attachments/` |
| `note` (optional) | anything else |
| any other column | a condition the value holds under (a model, a batch size, a machine) |

- An item appears once per set of conditions across all of `state/`: a newer value replaces the row.
- An experiment's `state` names the state tables it is the source of now, or says `n/a`. `set EXP-NNNN --state "a.csv
  b.csv"` replaces the whole value, so it names every table at once, and commits those tables with the experiment.
- *Guidance:* write a current value once, in `state/`, and cite it elsewhere by its EXP. validate cannot see a copy
  of a value in a TODO or a decision.

## The code and the files a run used

A reader runs an experiment again from the code it ran and the files it read.

- **Before each run, `code EXP-NNNN <repository or folder> …` records its code** in the experiment's `code`. A
  repository, each submodule checked out under it and each repository inside it that git does not track is its commit
  when its work tree holds exactly that commit and a remote-tracking branch holds the commit; otherwise a snapshot:
  one commit of exactly the files of the work tree (untracked ones in, ignored ones out), kept in the code vault and
  named with the commit it was made from. A folder outside git, or in a part its repository ignores, is kept the same
  way, and each repository in it is an entry of its own. Work trees, their indexes and branches stay as they are.
  `--label before` and `--label after` keep the code of two runs apart; `code EXP-NNNN --restore <name> --to
  <folder>` gives the files back.
- **The code vault** is the remote the ledger pushes to, or the ledger itself when it has none; clones of the ledger
  never fetch the snapshots. The code reaches everyone who can read the vault, and no one else: a ledger shared with
  people who may not see all the code names another vault, `code_vault` in `labledger.yml` ([SETUP.md](SETUP.md)).
- **A commit that makes an experiment final needs its `code`**: the entries `code` wrote, or `set EXP-NNNN --code "n/a
  (why)"` when no code of a repository or folder ran. An experiment that was final before this rule keeps what it has.
- **`manifest EXP-NNNN <path> …` lists the files outside the ledger a run read or wrote**: path, machine, size and
  sha256, in `experiments/attachments/EXP-NNNN/manifest.tsv`, where `code` also lists the untracked files over 1 MB,
  which are data. `manifest --check` finds them changed or gone; when files move, `manifest --move <old> <new>`
  points the rows at their new place.
- *Guidance:* put the script that made each table in the attachments, beside it (for a table one command made, that
  command in a `.sh` file); review group 9 lists the final experiments whose tables have none.

## How to work

*Guidance*, all of it: the commands enforce the rules above, and this is the order of work that keeps them easy.

```bash
python3 scripts/labledger.py sync                 # start: bring in what other machines pushed (Commits)
python3 scripts/labledger.py review               # the review queue; the agent works through it
python3 scripts/labledger.py show SIM-012         # a record, its links with their status, and every record naming it
python3 scripts/labledger.py find dma latency --kind todo exp   # records holding every keyword (--body: bodies too)

python3 scripts/labledger.py new issue sim dma-stalls --symptom "…" --path "dma.c:151" --hypothesis "…"
python3 scripts/labledger.py new exp "DMA latency under load" --model "sim v2" --affects "SIM-013: first numbers"
python3 scripts/labledger.py code EXP-0042 ../simulator   # before the run: the code it runs, uncommitted work included
python3 scripts/labledger.py set EXP-0042 --status final --summary "…" --data "latency.csv: one row per load level" --state latency.csv
python3 scripts/labledger.py validate             # green before the turn ends
```

`review` prints each group of the queue with what to do about it. [What goes where](#what-goes-where) names the command
for each case, and `--help` gives every command's options and the exit codes. Numbers come from `new`, which reserves
them on the remote: a number of one's own can be another machine's too ([MACHINES.md](MACHINES.md#numbers)).
validate is green before the turn ends, and hand edits are committed in the turn that finishes them.

## Commits

- **A write command commits what it wrote**: the record, a TODO's close decision, and with `set` on an experiment its
  attachment folder and the state tables its `--state` names.
- **`commit -m "<message>" <address | path> …` commits those and nothing else** (an EXP brings its attachment folder);
  `commit --help` says what a commit is checked for. A hand `git commit` meets the same checks in the pre-commit hook
  the tool wires in ([SETUP.md](SETUP.md#the-tool)).
- **`sync` brings in what other machines pushed**, and the tool runs it when a push is refused because another machine
  pushed first. It replays this clone's own commits on top of the remote's, so they get new ids. When it stops, it
  moves nothing and says why; [MACHINES.md](MACHINES.md#sync) has the common cases.
- *Guidance:* several sessions may share one working tree. Do not use `git add -A`, `git commit -a`, `git pull`,
  `git rebase` or `git stash`: each takes in or rewrites another session's uncommitted files. Do not skip the hooks
  with `--no-verify`, and do not rewrite pushed history with `git push --force`. Cite a commit id once it is pushed,
  since a replay changes the ids of commits that were not.

## What goes where

| What happened | Command | Where it is written |
|---|---|---|
| something was observed | `new issue <area> <slug> --symptom … [--path --hypothesis --blocking]` | `areas/<area>/issues/<slug>.md` |
| more was learned about an issue | (editor), then `set issue:<slug>` | its body, in place |
| an investigation, review, audit or design | `new inv <area> "<title>"`, the body, then `set inv:<name> --status concluded --summary … --affects …` | `areas/<area>/<slug>-<date>.md` |
| an issue was settled | `resolve issue <slug> --by "<EXP, commit, inv:, document>"` | the issue: `resolved` and one line |
| something is to be done | `new todo <area> "<title>" --from …`; for an issue also `promote issue <slug> --to <ID>` | `areas/<area>/<ID>.md` |
| a run is about to start | `new exp "<title>"` once, then `code EXP-NNNN <repository or folder> …` | its `code`; snapshots in the code vault |
| a run produced numbers | its tables and the scripts that made them in the attachments; `manifest EXP-NNNN <path> …` for the files outside the ledger; when done, `set EXP-NNNN --status final --summary … --data …` | `experiments/EXP-NNNN.md`, tables in `experiments/attachments/EXP-NNNN/` |
| a status was judged, or work finished | `set <ID> --status … --note … [--evidence EXP-NNNN]` | the head; a close decision for done or dropped |
| work spanning several TODOs began | `new arc "<title>" --members … --exps … --areas …` | `arcs/ARC-NNN.md` |
| something was decided | `new decision <slug> --text … [--area <area>] [--context --why]` | `areas/<area>/decisions/` or `decisions/` |
| a current value changed | (editor) its row in `state/<name>.csv`, then `set EXP-NNNN --state "<every table it is the source of>"`, which commits them with the experiment | `state/` |
| a new area of work | `new area <id> "<title>" --prefix <PREFIX>` | `areas/<id>/README.md` |
| where is it? | `find <keywords…> [--kind] [--status] [--body]`, `show <address>` | — |
