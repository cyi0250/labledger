# labledger

A ledger for a research or engineering project that people and AI agents keep together: its plans, experiments,
observations and decisions, one Markdown file per record in a git repository, checked by one Python script.

- **Every number traces back.** An experiment names the CSV tables its numbers come from, and its body links them. A
  current value in `state/` names its experiment, the code version and the day it was measured.
- **Statuses are judged again.** `review` lists what needs another look: TODOs that newer records mention,
  experiments left in flight, issues nobody touched for two months.
- **Agents record on their own.** A skill named as you call the ledger leads them to it whenever their work produces
  something to keep, and tells them which command records it.
- **Agents follow one rulebook.** [`AGENTS.md`](AGENTS.md) holds the rules every session uses, and points to
  [`MACHINES.md`](MACHINES.md) and [`SETUP.md`](SETUP.md) for the cases only some sessions meet. The tool checks the
  form of each rule: `validate`, the write commands before they write, and every commit made through the tool or its
  hooks. What no tool can check is marked as guidance. The write commands then commit and push what they wrote.
- **Nothing to install.** One script: Python 3.9 or newer with its standard library alone, on Linux or macOS, and git
  2.28 or newer. Tables are rendered into a local `views/` folder that is never committed, so machines writing at
  once do not collide.

## Start a ledger

Ask your AI agent: **"Set up a ledger from https://github.com/cyi0250/labledger."** It clones this repository into a
temporary folder and runs `python3 labledger/scripts/labledger.py setup`, which prints what to ask you first, each with
the answer it recommends on your machine:

- **the name** you call the ledger by: `ledger`, unless something on your machine already answers to that. The ledger
  and its skill carry the name: `scripts/<ledger>.py`, `<ledger>.yml`, commit messages `<ledger>(…)`, and `/<ledger>` in
  your agents (`$<ledger>` in Codex);
- **the folder** it goes in, and **the remote** it is pushed to so that other machines share it (a new private GitHub
  repository without README or license, say), or none;
- its **time zone** and the **language** its records are written in;
- **the agents** that get its skill: Claude Code, Codex, OpenCode, Gemini CLI, Copilot, Cursor.

The agent then runs setup with your answers. setup makes the ledger, commits and pushes it, turns its hooks on, and
installs the skill. From then on your agents record their experiments and data, decisions, defects, investigations and
TODOs as the work produces them, and look up what is recorded before they start. setup runs the same from a terminal.
On another machine, clone the ledger and run `python3 scripts/<ledger>.py setup` in it.

To take a newer version of labledger into a ledger later, clone labledger at that version and run
`python3 labledger/scripts/labledger.py setup --update <ledger folder>`: it replaces the tool, and the rulebook files
the ledger has not changed since setup or the last update. It leaves the ones the ledger changed, and prints the
command that merges the new version into each.

[`examples/demo`](examples/demo) is a small finished ledger: a TODO settled by an experiment, with the benchmark script,
its data table, a state table and two decisions.

## Layout

```
areas/<area>/README.md           an area of work; its TODOs are numbered <PREFIX>-NNN
areas/<area>/<PREFIX>-NNN.md     a TODO
areas/<area>/<name>.md           an investigation, review, audit or design
areas/<area>/issues/<slug>.md    an observation
areas/<area>/decisions/          decisions of one area (decisions/ holds those across areas)
experiments/EXP-NNNN.md          an experiment; its files are in experiments/attachments/EXP-NNNN/
arcs/ARC-NNN.md                  work spanning several TODOs and experiments: a paper, a release
state/<name>.csv                 the current values, each from a final experiment
<ledger>.yml, templates/         the ledger's time zone, language, body skeletons and words (optional)
AGENTS.md, MACHINES.md, SETUP.md the rulebook: every session, several machines, setup and settings
views/                           tables rendered on each clone (gitignored)
```

## License

0BSD: use, copy, modify and distribute it for any purpose, with no conditions. See [LICENSE](LICENSE).
