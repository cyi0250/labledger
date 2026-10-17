# Setup, settings and the tool

[AGENTS.md](AGENTS.md) holds the rules every session uses. This file holds what a session meets only when it sets up the
ledger or its skill, brings in tables made before it, changes the ledger's settings or templates, or changes the tool.

## Setup and the skill

`setup` starts a ledger and puts its skill on a machine. Without the answers it needs, it prints the questions to ask
the user, each with the answer it recommends on this machine (and the answers already given), and does nothing else: the
agent running it asks them, then runs it again with the answers.

- **In the tool's source repository it makes a new ledger**: `setup --name <ledger> --path <new folder> --remote <git
  URL | none> --timezone <zone> --language <language> --agents <agent …>`. The ledger gets the tool, its hooks, its
  tests and CI, its rulebook (`AGENTS.md`, `MACHINES.md`, `SETUP.md`), `CLAUDE.md` and `.gitignore`, with the
  source's name in them replaced by the new one, in capitals too: `scripts/<ledger>.py`, `<ledger>.yml`,
  `<LEDGER>_HOST`, `refs/<ledger>/ids`, commit messages `<ledger>(…)`. A ledger in another language than English also
  gets the English skeletons and words in `templates/`, to translate. setup commits the ledger, pushes it, turns its
  hooks on and installs the skill.
- **What setup refuses before it makes anything**: a name that is not 2 to 32 lowercase letters and digits starting with
  a letter; a name that a skill in any agent's folder, a command on PATH, a Python module or a namespace of git's own
  refs (`heads`, `tags`, `notes`, …) already answers to; a folder that is not empty; a remote that cannot be reached or
  is not empty. It recommends `ledger`, else the first of `labbook`, `logbook` and `worklog` that is free. *Guidance:*
  the name is a word the user says for this ledger alone, not one of their agents' own commands (`review`, `init`),
  which setup does not know.
- **In a ledger it installs that ledger's skill on this machine**: clone the ledger, then `python3 scripts/<ledger>.py
  setup --agents <agent …>`. Run it again after `language` or `commit_prefix` changes, on every machine.
  [MACHINES.md](MACHINES.md) has what else a second machine needs: its name, a clone without the attachments, a shared
  SSH connection.
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
- *Guidance:* to give a table made before the ledger its units, append them to the original column names (`cycles` →
  `cycles[cycles]`). `sed '1s/\[[^]]*\]//g' <file>` then gives back the original file byte for byte, for the scripts
  and checksum lists that expect it.

## Settings and templates

- **`labledger.yml`** (optional) holds `key: value` lines. `timezone` is an IANA name such as `Europe/Berlin`, the
  zone the write commands stamp today in (default: each machine's own zone; MACHINES.md, Machine names and dates).
  `commit_prefix` is the first word of the commit messages write commands make (default: the ledger's name,
  `labledger`), one word of `A-Z a-z 0-9 . _ -`. `language` names the language the records are written in, which the
  skill asks agents to write in (default: none named; setup reads it when it writes the skill).
- **`templates/<kind>.md`** replaces the body a new record of that kind starts with: `todo`, `exp`, `arc`, `inv` or
  `area`.
- **`templates/labels.yml`** replaces the words commands write into records: `symptom path hypothesis blocking` (`new
  issue`), `context decision why` (`new decision`), `resolved promoted` (the line `resolve` and `promote` add),
  `evidence closed_by` (a close decision; `closed_by` holds `{evidence}`) and `in_progress` (a new experiment's
  summary). Review group 3 looks for the `resolved` word.
- validate refuses an unknown setting, label or template file, and a time zone this machine does not know.

## A newer version of the tool

A ledger runs its own copy of the tool, which keeps the version of the tool it was taken from: `scripts/<ledger>.py
--version` names it and the tool's source repository. To take a newer version, clone that repository (its `main`, or a
release tag) and run the clone's tool with `setup --update <ledger folder>`. Setup and every update end their commit
message with the source commit they took and the blob of each file they wrote, and the update decides from those marks,
against what the ledger has committed:

- **It syncs the ledger first**, and writes nothing while the ledger is not in sync with its remote.
- **A file the ledger left as the tool wrote it is replaced**, and a file the tool never wrote is added.
- **A rulebook file the ledger changed** (`AGENTS.md`, `MACHINES.md`, `SETUP.md`: rules of its own, another language)
  stays as it is. The update puts the new version and the base the ledger changed beside it, in the ledger's `.git`,
  and prints the `git merge-file` command that carries the new version's changes in. When no commit records what the
  tool wrote there (a ledger setup did not make, which kept its own version of the file through every update), there
  is no base: the update puts the new version alone and prints a `diff -u` to carry its changes over by hand.
- **A tool file the ledger changed** (the script, its hooks, its tests, CI) is replaced all the same, and the update
  prints where the ledger's version is (`git show <commit>:<path>`). A file the ledger removed stays removed.
- **It refuses before writing anything**: a clone older than what the ledger took (a lower version, or a source commit
  the clone lacks), uncommitted work in a file it would replace, a file or folder this session cannot write.
- The ledger's own new tool commits and pushes what was written. An update that could not commit (exit code 3) is
  finished by running it again once the problem its commit named is fixed. The skill is written again wherever this
  machine has it; other machines take the new tool with `sync`, then write their skill again with `setup --agents …`.
  `CLAUDE.md` and `.gitignore` are the ledger's own after setup, and an update leaves them.

## The tool

`scripts/labledger.py --version` names the version of the tool and its source repository. It needs Python 3.9 or newer
with its standard library alone, Linux or macOS (it locks files with `fcntl`), and git 2.28 or newer (tested with
2.34). After changing it, `python3 -m pytest tests` passes: each test builds a small ledger in a temporary folder and
runs commands on it; in the tool's source repository one also validates `examples/demo` and one makes a ledger with
`setup`. This ledger itself is checked by validate in CI and by the hooks. A new rule comes with its check and a test.
*Guidance:* it also gets a line in `validate --help` (or in `commit --help`, for a rule only a commit shows), which
nothing checks.

The tool's first commit in a clone sets `core.hooksPath` to `scripts/hooks`, unless git already has a `core.hooksPath`
in any of its config files: then wire the two hooks into that setup by hand. While it is set, git does not run the
scripts in `.git/hooks`. pre-commit checks a hand `git commit` as `commit` does; pre-push validates each tip it pushes
and, when `scripts/` or `tests/` changed or the branch is new, runs the tests if pytest is installed. CI
(`.github/workflows/validate.yml`) validates and runs the tests on every push.
