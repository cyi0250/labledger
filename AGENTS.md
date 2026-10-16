# Ledger rules

This repository is a **ledger**: the plans, experiments, observations and decisions of a project, one Markdown file per
record, kept by the people and the AI agents who do the work. `scripts/labledger.py` writes the records, checks them
and builds the tables. This file is the only rulebook. Every rule here is checked by the tool: by `validate`, by a
write command before it writes, or when a commit is made. What the tool cannot check is marked *guidance* and says
why it is there. A check sees the form of a record, not its meaning: that a note is filled, not that it says why.

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

Each record starts with a head of `key: value` lines, the source of truth. Its keys, in this order (the ones in
*italics* are optional):

- area: `id type title prefix` *`order`*. `prefix` (capitals and dashes) numbers the area's TODOs unless `new todo` is
  given another; `order` places the area in the area table.
- todo: `id type title area status priority created updated` *`status_note from depends_on supersedes resolves evidence`*.
- exp: `id type title date` *`host`* `status` *`model commit arc`* `affects state` *`data`* `summary` *`superseded_by`*.
- arc: `id type title status created updated` *`status_note areas venue location`* `members exps`.
- inv: `id type title area date` *`updated`* `status` *`status_note summary`* `affects`.
- decision: `date title`. issue: `date status` *`updated`*.

`type` names the kind of record; `id` matches the file name and `area` the folder.

validate checks every record and the folders they live in:

- **Heads.** A head starts at line 1 with `---` and gives each key once. A list is written `[a, b]` on one line or as
  `- item` lines; a text value that starts with `[` is quoted. Each key is known for its kind and is a list or a single
  value as its kind says; each required key is filled. The head is exactly what `fmt` writes: keys in the order above,
  values quoted the same way, and for todo, exp, arc and area the line `# ID — title` after it.
- **Values.** Each status comes from its kind's list; `priority` is `P0`, `P1` or `P2`; dates are `YYYY-MM-DD`;
  `updated` is never before `created`, or before `date` for an inv or an issue. `summary` holds at most 500 characters:
  what the record found; `status_note` at most 300: why its status is what it is. `host` is a host name, and an area's
  `order` a whole number.
- **Names.** Every TODO ID, inv name, `dec:` address and `issue:` address is used once. A decision's file name is its
  date and its title with dashes for spaces. Issue slugs, and decision titles apart from their spaces, use only
  `A-Z a-z 0-9 . _ -`.
- **Folders.** Every folder in `areas/` has a `README.md` with an area head, and every other `.md` file in it is a
  TODO (`<PREFIX>-NNN.md`) or an inv. Every `.md` file in `arcs/` is an `ARC-NNN.md` record or the folder's README, and
  every file in `experiments/` whose name starts with `EXP-` is an `EXP-NNNN.md` record (attachments go in
  `experiments/attachments/`). A `decisions/` or `issues/` folder holds one `.md` file per record.
- **Links.** Every link names a record that exists. A TODO's `from` takes TODO and EXP IDs, `issue:` `inv:` `dec:`
  `arc:` addresses, and `user` (a person asked for it); `resolves` takes the same but `user`; `depends_on`,
  `supersedes` and `evidence` take TODO and EXP IDs. An arc is written `arc:ARC-NNN` in these. An EXP's `affects`
  lists `"ID: one-line note"` entries for TODOs or EXPs, and an inv's `affects` the same for TODOs and
  `issue:<slug>`s. An EXP's `arc` names an arc; an arc's `members` name TODOs, its `exps` EXPs, its `areas` areas.
- **Statuses.** A `dropped` TODO and a `parked` arc have a `status_note` saying why. A TODO that `supersedes` another
  needs the other one closed. A `superseded` experiment names its `superseded_by`. A `done` or `dropped` TODO has a
  close decision, `areas/<area>/decisions/<date>-close-<ID>.md`, which `set` writes.

A write command (`new`, `set`, `resolve`, `promote`) checks the head it is about to write against these rules and
writes nothing that breaks them. Rules between files (a TODO superseding another, a state table and its experiment,
close decisions, links in bodies) are checked by validate and when the commit is made.

How the records fit together. The tool computes what this describes; the advice in it is *guidance*:

- **`affects` carries one piece of work to every TODO it moved.** List them all instead of copying the result into
  each TODO. `show <ID>` lists every record that names a TODO, computed each time and never stored; an issue counts
  while it is open. An experiment that moves no TODO leaves `affects` empty.
- **An arc's members are a union**: its own `members` and `exps`, every TODO with `from: [arc:ARC-NNN]`, and every EXP
  with `arc: ARC-NNN`. `new exp --arc ARC-NNN` adds an experiment without opening the arc. An arc with a `venue` is a
  paper or a talk and appears in `views/papers.md`.
- **`parked` means "not now"** and keeps a TODO out of the 90-day review; **`dropped` means "decided not to"**.
- **An issue is an observation with a life of its own.** Anything can settle it (a TODO, a commit, an experiment, an
  investigation): `resolve issue <slug> --by …` marks it resolved and appends a line naming what did. Promoting it to
  a TODO, `promote issue <slug> --to <ID>` (from `open` only), is one way among those.
- **Every other `.md` file directly in an area folder is an inv**: an investigation, review, audit, design or plan.
  Its `affects` names the issues and TODOs it updated; a TODO made from it says `from: [inv:<name>]`. Other files
  there (not `.md`, or in a subfolder other than `issues/` and `decisions/`) are no records, and nothing checks them.
- **A decision is one file**, so two machines adding decisions to one area never collide.
- The write commands write heads, the skeleton body of a new record, decisions and issues. Bodies are edited by
  hand, and so are the head fields no command changes once the record exists: a `title`; an EXP's or inv's `date`;
  an area's `prefix` and `order`; a TODO's `from`, `depends_on`, `supersedes` and `resolves` (`promote` adds an
  `issue:` to `from`); an EXP's `arc`; an arc's `members`, `exps`, `areas`, `venue` and `location`; removing an entry
  from `affects`, `evidence` or `data`; deleting a `host`. After a hand edit of a head, `fmt` makes it canonical
  again. No command rewrites a body, apart from trimming the blank lines at its start and end.

Tables and lists live in `views/` on each clone and are never committed (the hooks and CI refuse a commit holding
`views/`): `render` writes `views/index.md` (every TODO), `views/areas.md` (areas and arcs), `views/areas/<area>.md`,
`views/log.md` (every experiment), `views/papers.md`, `views/active.md` and `views/state/<name>.md`; `review` writes
`views/review.md`, and prints its queue even where it cannot write the file. The write commands render on their own,
and commit the record even when a view cannot be written; after a pull or another session's write, `render` brings
`views/` up to date.

## Numbers and the data behind them

A reader checks a number by opening the table it was computed from.

- **An experiment's `data` names its tables.** Each entry is `"<file>: <what one row is>"` for a CSV, TSV or JSON file
  in `experiments/attachments/EXP-NNNN/`. A `final` experiment has `data`; one that produced no numbers says why
  instead, with the single entry `"n/a (why)"`. `new exp` leaves `data` out. `set EXP-NNNN --data …` fills it: an
  entry for a file already listed replaces that entry, and `n/a (why)` replaces them all.
- **The body links every file `data` names**, where its numbers are used:
  `[results.csv](attachments/EXP-NNNN/results.csv)`.
- **A table `data` names reads as one.** It is UTF-8 text with a header row, each column named once and not by a
  number, and every row as wide as the header. No cell holds a list of numbers (`1,2,3`, `1;2;3`, `[1, 2, 3]`,
  `(0, 0, 0)`), a number with thousands separators (`1,786,215`) or a fraction of whole numbers (`0/3024`). A column that holds only numbers ends
  its name with their unit in brackets: `latency[ms]`; `[1]` for a count or a ratio; `[id]` for numbers that name
  things (a run, a seed). A JSON file only has to parse. These checks read the file wherever it is on disk: in
  validate, and when a commit that holds it is made. Where it is not on disk (a partial clone, CI), validate checks
  only that git has it.
- *Guidance:* one plain value per cell, of any kind: the check sees lists of numbers only, so `a, b` or `0.5/3` pass it
  and are still not one value. Large leftovers of a run (logs, traces, archives) may sit in the attachment folder too,
  or outside the ledger: `data` names the tables the reported numbers come from, not everything a run left behind.
- *Guidance:* to give an existing table its units, append them to the original column names (`cycles` →
  `cycles[cycles]`). `sed '1s/\[[^]]*\]//g' <file>` then gives back the original file byte for byte, for the scripts
  and checksum lists that expect it.

**Links.** A relative link in the body of a record or of a Markdown state table points at a file or folder of the
ledger. It is not an absolute path, it does not leave the ledger (name another repository's file in text: repository,
commit, path), and it does not point into `views/`, which is not in git (name the command that makes the table
instead). Links inside code, URLs and `#anchors` are not checked.

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

- validate checks the columns above: `item`, `value` and `commit` are filled, `kind` and `measured_at` are as the
  table says, `exp` is final and its `state` names the table, a `source` exists, and every row is as wide as the
  header. A value's unit is in its `unit` column, so the column names of a state table need none.
- An item appears once per set of conditions across all of `state/`: a newer value replaces the row.
- An experiment's `state` names the state tables it is the source of now, or says `n/a`; `n/a (row taken over by
  EXP-NNNN)` once a later experiment replaced its row. Each table it names exists and cites it (a write command
  only warns about a missing citation; validate refuses it). `set EXP-NNNN --state "a.csv b.csv"` replaces the whole
  value, so it names every table at once.
- A Markdown state table is free text. validate checks its links and its citations: every EXP a state table names
  exists and is written in full (`EXP-0007 / EXP-0008`, never `EXP-0007/0008`).
- *Guidance:* write a current value once, in `state/`, and cite it elsewhere by its EXP. validate cannot see a copy
  of a value in a TODO or a decision.

## How to work

*Guidance*, all of it: the commands enforce the rules above, and this is the order of work that keeps them easy.

```bash
python3 scripts/labledger.py sync                 # start: bring in what other machines pushed (Commits)
python3 scripts/labledger.py review               # the review queue; the agent works through it
python3 scripts/labledger.py show SIM-012         # a record, its links with their status, and every record naming it
python3 scripts/labledger.py find dma latency --kind todo exp   # records holding every keyword (--body: bodies too)

python3 scripts/labledger.py new issue sim dma-stalls --symptom "…" --path "dma.c:151" --hypothesis "…"
python3 scripts/labledger.py new inv sim "DMA path audit" --slug dma-path-audit   # inv:dma-path-audit-<today>
python3 scripts/labledger.py set inv:dma-path-audit-2026-03-02 --status concluded --summary "…" --affects "issue:dma-stalls: two causes"
python3 scripts/labledger.py new todo sim "Fix the DMA stall" --from issue:dma-stalls inv:dma-path-audit-2026-03-02   # say it is SIM-013
python3 scripts/labledger.py promote issue dma-stalls --to SIM-013
python3 scripts/labledger.py new exp "DMA latency under load" --model "sim v2" --commit 347d29d --affects "SIM-013: first numbers"
python3 scripts/labledger.py set EXP-0042 --status final --summary "…" --data "latency.csv: one row per load level" --state latency.csv --no-commit
python3 scripts/labledger.py commit -m "ledger(exp): EXP-0042 results" EXP-0042 state/latency.csv   # with the row edited by hand
python3 scripts/labledger.py set SIM-013 --status done --evidence EXP-0042 --note "no stall at any load"
python3 scripts/labledger.py resolve issue dma-stalls --by EXP-0042
python3 scripts/labledger.py set SIM-007 --note "still waits for the new board"   # judged unchanged: raises `updated`
python3 scripts/labledger.py set SIM-003 --status done --evidence EXP-0007 --date 2026-03-06   # recorded late
python3 scripts/labledger.py new arc "Paper: DMA scheduling" --members SIM-013 --exps EXP-0042 --areas sim --venue "a workshop"
python3 scripts/labledger.py new decision runs-on-tmpfs --area sim --text "…" --context "…" --why "…"
python3 scripts/labledger.py new area docs "Documentation" --prefix DOC
python3 scripts/labledger.py validate             # green before the turn ends
python3 scripts/labledger.py fmt                  # when validate says a head is not canonical
```

The global options `--root`, `--no-commit`, `--no-push` and `--offline` go before or after the command.

Every command ends with one of four exit codes, and says what went wrong on stderr:

| Code | Meaning |
|---|---|
| 0 | done: written, committed and pushed, as far as the clone and the options allow |
| 1 | a check found problems: validate, a refused `commit`, a stopped `sync`, a number the remote did not reserve, `find` with no match |
| 2 | nothing was written: a usage error, a rule the record would break, an unknown address, a session that cannot write where the command writes |
| 3 | the record is written but not committed, or committed but not pushed: do not run the command again, which would write a second record; `commit` it, or let `sync` push it |

`find`, `show`, `review` and `validate` take `--json` and print one JSON object instead of text: the matches; a record's
head and body with what the tool computes about it (a TODO's inbound records, an arc's members); each review group's
addresses (without writing `views/review.md`); the verdict, the problems and the record counts. `new … --json` prints
the record it made as `{"address", "file", "exit"}` and sends everything else to stderr.

A session that may read the ledger but not write it or its .git, such as an agent's sandbox, runs `find`, `show`,
`review` and `validate` as anywhere else. A command that writes checks first that the session can write where it will,
the ledger's folder and, in a git clone, its .git, and otherwise stops with code 2 before writing anything: the write
commands, `commit`, `sync`, `attachments`, and `render` or `fmt` without `--check`.

1. **The agent empties the review queue**, recording each judgment as its group says ([Review queue](#review-queue)).
2. **An experiment starts with `new exp`** and ends with `set EXP-NNNN --status final --summary … --data …`, adding
   `--affects` and `--state` when they apply. When a current value changed, its row in `state/` is edited in the same
   turn and committed with the experiment.
3. **New work starts with `new todo`**, its `--from` naming where it came from.
4. **An arc (`new arc`) holds work no single TODO covers**: a paper, a release, a hardware generation. Its body holds
   the steps and the exit criteria, and its status is judged from its members and its latest experiments
   (`views/active.md`).
5. **Work ends with a status.** `set <ID> --status done --evidence EXP-NNNN --note …` closes a TODO and writes its close
   decision; no file moves.
6. **An investigation starts with `new inv`**; its findings and conclusion go in the body, then `set inv:<name> --status
   concluded --summary … --affects …`.
7. **A decision is made with `new decision`**, under `--area` when it belongs to one area. An observation is `new
   issue`; what settled it, `resolve issue --by …`.
8. **Numbers come from `new`**, never from a hand-made file name ([Other machines](#other-machines)).
9. **validate is green before the turn ends**, and hand edits are committed in the turn that finishes them.

## Review queue

`review` lists what needs judging again, in groups. Raising a record's `updated` (`set <address>`, with `--note` for a
TODO) takes it off groups 1, 2 and 6; the other groups clear when the status they look at changes.

1. Open TODOs with records dated after their `updated`: experiments, investigations and decisions that name them, and
   open issues that do. An investigation or an issue is dated by its `updated`. Record the judgment with `set <ID>
   [--status …] --note … [--evidence EXP-NNNN]`.
2. Open TODOs, `parked` ones aside, unchanged for 90 days: park, drop or go on with them through `set`.
3. Open issues whose body holds the word of the `resolved` label (*Resolved* unless `templates/labels.yml` says
   otherwise; "unresolved" holds it too, so judge each): `resolve issue <slug> --by …` when it is settled.
4. Active arcs whose members have all stopped: `set ARC-NNN --status done|parked --note …`.
5. Experiments in flight for more than 14 days: `set EXP-NNNN --status final|withdrawn …`.
6. Open issues and investigations not updated for 60 days: resolve, promote or conclude it; if it still stands as
   written, `set <address>` raises its `updated`.
7. Done arcs with open members: close the members, or `set ARC-NNN --status active`.
8. Promoted issues whose TODOs are all closed: `resolve issue <slug> --by <TODO>`.

## Commits

Checked when a commit is made, by every write command, by `commit`, and by the pre-commit hook for a hand `git commit`:

- **A commit validates on its own**: the tree it makes passes validate, whatever else is uncommitted. A write command
  commits what it wrote: the record, a TODO's close decision, and with `set` on an experiment its attachment folder.
  When that commit would not validate on its own (an experiment's `state` names a state table not committed yet),
  nothing is committed: the command says why, and the record stays in the working tree. Commit it together with what
  it needs: `commit -m "…" EXP-0042 state/latency.csv`.
- **An edited investigation or issue raises its `updated`.** review dates what one says by its `updated`. A commit
  that changes one but leaves its `updated` as it was, on a day before today, is refused; `set inv:<name>` or `set
  issue:<slug>` raises it. This compares the commit with its parent, so validate, the pre-push hook and CI do not
  check it.

How commits are made:

- **`commit` commits the paths it is given and nothing else**: `commit -m "<message>" <ID | EXP-NNNN | inv:<name> |
  issue:<slug> | dec:<id> | path> …`. An EXP brings its attachment folder; everything else uncommitted stays as it
  was. To make one commit of several writes, give each write `--no-commit` and finish with `commit`. A write command's
  commit message reads `<prefix>(<scope>): <what>`: the scope is the area, or `exp`, `arc` or `decisions`.
- **Hooks check the commits that bypass the tool.** The tool's first commit in a clone sets `core.hooksPath` to
  `scripts/hooks`, unless git already has a `core.hooksPath` in any of its config files (then wire the two hooks into
  that setup yourself). While it is set, git does not run the scripts in `.git/hooks`. pre-commit checks a hand `git
  commit` as above. pre-push validates each tip it pushes and, when `scripts/` or `tests/` changed or the branch is
  new, runs the tests if pytest is installed. CI (`.github/workflows/validate.yml`) validates and runs the tests on
  every push.
- **Each commit is pushed** to the branch's upstream. A push that fails (no network, no answer within 60 s) leaves the
  commit local, and the next commit pushes it. `--no-push` commits without pushing.
- **`sync` brings in what other machines pushed**, and the tool runs it when a push is refused because another machine
  pushed first. It fetches, replays this clone's own commits one by one on top of the remote's in a private worktree
  (`.git/labledger-sync`, validating each), moves the branch, rewrites only the files that changed, and pushes.
  Replayed commits get new ids. When sync stops, it moves nothing and says why:
  - both sides changed the same lines: merge the files it names inside `.git/labledger-sync/` (keep both records,
    delete the `<<<<<<<` `=======` `>>>>>>>` lines), then `sync`; when both added a record under one number, see
    [Other machines](#other-machines);
  - a replayed commit does not validate (two machines made the same `dec:` address): fix it there, then `sync`;
  - an incoming file has uncommitted changes here: commit them, then `sync`;
  - `sync --abort` drops the stopped replay; the branch and the local commits stay as they were.
- *Guidance:* several sessions may share one working tree. Do not use `git add -A`, `git commit -a`, `git pull`,
  `git rebase` or `git stash`: each takes in or rewrites another session's uncommitted files. Do not skip the hooks
  with `--no-verify`, and do not rewrite pushed history with `git push --force`. Cite a commit id once it is pushed,
  since a replay changes the ids of commits that were not.

## Other machines

A ledger kept on several machines shares one remote, a private GitHub repository for instance.

- **Numbers are reserved on the remote.** When the branch tracks an upstream, `new todo`, `new exp` and `new arc` raise
  a counter on it (`refs/labledger/ids`), so two machines never issue the same number. They check the record first and
  reserve its number only when it passes, so a refused record takes none. When that remote cannot be reached, nothing is
  made; `--offline` takes the local maximum + 1 instead and notes it in the clone's copy of the counter. A branch
  without an upstream takes the local maximum + 1. A commit that adds a TODO, an experiment or an arc under a number
  past the clone's copy of the counter, which no `new` reserved, is refused. *Guidance:* gaps are fine and no number is
  reused.
- **Two records under one number** come only from `--offline` (another machine took the same number meanwhile), and
  they are repaired by hand. sync stops on the record's file, and only one record keeps the number. Inside
  `.git/labledger-sync/`, put the other machine's file back (`git show <remote>/<branch>:<path> > <path>`; this
  machine's version stays in `git show CHERRY_PICK_HEAD:<path>`), write this machine's record under a new number
  (`new … --no-commit` reserves one: delete the file it writes), change its `id` and H1, then `sync`. That fixes the
  commit that made the record. The local commits after it are replayed as they are: whatever they put under the old
  number (a mention, a `state` row, an attachment, which the private worktree never holds) now belongs to the other
  machine's record and moves to the new number in a commit after sync. `git log -p @{u}..` in the clone lists those
  commits before sync resumes. *Guidance:* use `--offline` only when the remote cannot be reached.
- **Each machine has a name**: `LABLEDGER_HOST` when it is set (in the shell profile), else the host name up to its
  first dot. `new exp` writes it into `host`, the machine whose local paths the body names (`--host` names another,
  for a run made elsewhere), and the counter's log records it. On a machine called `localhost`, `new exp` without
  `--host` and every number reservation refuse to run until `LABLEDGER_HOST` is set.
- **Dates.** Write commands stamp today in `timezone` from `labledger.yml`, or in each machine's own time zone when
  that names none. *Guidance:* a ledger written from several time zones names one.
- **A shared SSH connection** is opt-in. With `LABLEDGER_SSH_SHARE=1`, the tool keeps one connection per remote host
  open for 10 minutes after its last use (a background `ssh -N` with the socket `labledger-%C` in `$XDG_RUNTIME_DIR`
  or `~/.ssh`), and the fetches and pushes of one command share one handshake. `ControlMaster` in `~/.ssh/config` does
  the same for every program.
- **A machine that does not need the attachments** clones without them:

  ```bash
  git clone --filter=blob:limit=1m --no-checkout <remote> ledger
  cd ledger
  git config core.sparseCheckout true
  git config core.sparseCheckoutCone false
  printf '/*\n!/experiments/attachments/\n' > .git/info/sparse-checkout
  git checkout main
  ```

  validate and the commit checks find the attachments it lacks in git's index. `attachments EXP-NNNN …` brings those
  EXPs' folders in and keeps them; the folders of the EXPs this clone makes are there from the start. `commit` refuses
  a path that reaches attachments left off disk, which would commit their deletion. The sparse-checkout file holds the
  two lines above and the `/experiments/attachments/EXP-NNNN/` lines `attachments` adds; `new exp`, `commit` and
  `sync` stop on any other form. *Guidance:* do not use `git sparse-checkout set|add|init`, which rewrite the file and
  the clone's config.

## Settings and templates

- **`labledger.yml`** (optional) holds `key: value` lines. `timezone` is an IANA name such as `Europe/Berlin`
  (default: each machine's own zone). `commit_prefix` is the first word of the commit messages write commands make
  (default: the ledger's name, `labledger`), one word of `A-Z a-z 0-9 . _ -`. `language` names the language the
  records are written in, which the skill asks agents to write in (default: none named; setup reads it when it writes
  the skill).
- **`templates/<kind>.md`** replaces the body a new record of that kind starts with: `todo`, `exp`, `arc`, `inv` or
  `area`.
- **`templates/labels.yml`** replaces the words commands write into records: `symptom path hypothesis blocking` (`new
  issue`), `context decision why` (`new decision`), `resolved promoted` (the line `resolve` and `promote` add),
  `evidence closed_by` (a close decision; `closed_by` holds `{evidence}`) and `in_progress` (a new experiment's
  summary). Review group 3 looks for the `resolved` word.
- validate refuses an unknown setting, label or template file, and a time zone this machine does not know.

## Setup and the skill

`setup` starts a ledger and puts its skill on a machine. Without the answers it needs, it prints the questions to ask
the user, each with the answer it recommends on this machine (and the answers already given), and does nothing else: the
agent running it asks them, then runs it again with the answers.

- **In the tool's source repository it makes a new ledger**: `setup --name <ledger> --path <new folder> --remote <git
  URL | none> --timezone <zone> --language <language> --agents <agent …>`. The ledger gets the tool, its hooks, its
  tests and CI, `AGENTS.md`, `CLAUDE.md` and `.gitignore`, with the source's name in them replaced by the new one, in
  capitals too: `scripts/<ledger>.py`, `<ledger>.yml`, `<LEDGER>_HOST`, `refs/<ledger>/ids`, commit messages
  `<ledger>(…)`. A ledger in another language than English also gets the English skeletons and words in `templates/`, to
  translate. setup commits the ledger, pushes it, turns its hooks on and installs the skill.
- **What setup refuses before it makes anything**: a name that is not 2 to 32 lowercase letters and digits starting with
  a letter; a name that a skill in any agent's folder, a command on PATH, a Python module or a namespace of git's own
  refs (`heads`, `tags`, `notes`, …) already answers to; a folder that is not empty; a remote that cannot be reached or
  is not empty. It recommends `ledger`, else the first of `labbook`, `logbook` and `worklog` that is free. *Guidance:*
  the name is a word the user says for this ledger alone, not one of their agents' own commands (`review`, `init`),
  which setup does not know.
- **In a ledger it installs that ledger's skill on this machine**: clone the ledger, then `python3 scripts/<ledger>.py
  setup --agents <agent …>`. Run it again after `language` or `commit_prefix` changes, on every machine.
- **The skill is `<skills folder>/<ledger>/SKILL.md`**, one file per folder the chosen agents read: Claude Code
  (`claude`) reads `~/.claude/skills`, and Codex, OpenCode, Gemini CLI, Copilot and Cursor (`codex`, `opencode`,
  `gemini`, `copilot`, `cursor`) read `~/.agents/skills`. Its name and description lead an agent to it when the user
  names the ledger or one of its records, when a run produced numbers, when something was decided, when a defect turned
  up, when work is left for later, and before work the ledger may already hold. Its body says what to record and with
  which command, by the tool's path on this machine, which Claude Code then runs without asking while the skill is in
  use; it asks for records in `language`. setup writes it again each time it runs with `--agents`, and never replaces a
  skill of that name that is not this ledger's.
- **A ledger without a remote** is kept on one machine: its write commands commit without pushing, its numbers are the
  local maximum + 1, and `sync` has nothing to do.

## What goes where

| What happened | Command | Where it is written |
|---|---|---|
| something was observed | `new issue <area> <slug> --symptom … [--path --hypothesis --blocking]` | `areas/<area>/issues/<slug>.md` |
| more was learned about an issue | (editor), then `set issue:<slug>` | its body, in place |
| an investigation, review, audit or design | `new inv <area> "<title>"`, the body, then `set inv:<name> --status concluded --summary … --affects …` | `areas/<area>/<slug>-<date>.md` |
| an issue was settled | `resolve issue <slug> --by "<EXP, commit, inv:, document>"` | the issue: `resolved` and one line |
| something is to be done | `new todo <area> "<title>" --from …`; for an issue also `promote issue <slug> --to <ID>` | `areas/<area>/<ID>.md` |
| a run produced numbers | `new exp "<title>"`; when done, `set EXP-NNNN --status final --summary … --data …` | `experiments/EXP-NNNN.md`, tables in `experiments/attachments/EXP-NNNN/` |
| a status was judged, or work finished | `set <ID> --status … --note … [--evidence EXP-NNNN]` | the head; a close decision for done or dropped |
| work spanning several TODOs began | `new arc "<title>" --members … --exps … --areas …` | `arcs/ARC-NNN.md` |
| something was decided | `new decision <slug> --text … [--area <area>] [--context --why]` | `areas/<area>/decisions/` or `decisions/` |
| a current value changed | (editor) its row in `state/<name>.csv`, then `set EXP-NNNN --state "<every table it is the source of>"` and `commit` both | `state/` |
| a new area of work | `new area <id> "<title>" --prefix <PREFIX>` | `areas/<id>/README.md` |
| where is it? | `find <keywords…> [--kind] [--status] [--body]`, `show <address>` | — |

## The tool

`scripts/labledger.py` needs Python 3.9 or newer with its standard library alone, Linux or macOS (it locks files with
`fcntl`), and git 2.28 or newer (tested with 2.34). After changing it, `python3 -m pytest tests` passes: each test
builds a small ledger in a temporary folder and runs commands on it; in the tool's source repository one also validates
`examples/demo` and one makes a ledger with `setup`. This ledger
itself is checked by validate in CI and by the hooks. A new rule comes with its check and a test.
