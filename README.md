# labledger

A ledger for a research or engineering project that people and AI agents keep together: its plans, experiments,
observations and decisions, one Markdown file per record in a git repository, checked by one Python script.

- **Every number traces back.** An experiment names the CSV tables its numbers come from, and its body links them. A
  current value in `state/` names its experiment, the code version and the day it was measured.
- **Statuses are judged again.** `review` lists what needs another look: TODOs that newer records mention,
  experiments left in flight, issues nobody touched for two months.
- **Agents follow one rulebook.** [`AGENTS.md`](AGENTS.md) holds every rule, and the tool checks the form of each one:
  `validate`, the write commands before they write, and every commit made through the tool or its hooks. What no
  tool can check is marked as guidance. The write commands then commit and push what they wrote.
- **Nothing to install.** One script: Python 3.9 or newer with its standard library alone, on Linux or macOS, and git
  2.28 or newer. Tables are rendered into a local `views/` folder that is never committed, so machines writing at
  once do not collide.

## Start a ledger

1. Copy this repository (on GitHub, use it as a template), or copy `scripts/`, `tests/`, `AGENTS.md`, `CLAUDE.md`,
   `.gitignore` and `.github/` into an empty one. `examples/` can go.
2. Make the first records:

   ```bash
   python3 scripts/labledger.py new area sim "Simulator" --prefix SIM
   python3 scripts/labledger.py new todo sim "Model the DMA engine" --from user
   python3 scripts/labledger.py new exp "DMA latency under load" --affects "SIM-001: first numbers"
   python3 scripts/labledger.py show SIM-001
   ```

   Each write command commits what it wrote, and pushes it once the branch tracks a remote. On a machine whose host
   name is `localhost`, first set `LABLEDGER_HOST` to a name for it (AGENTS.md, Other machines).
3. Point your agent at `AGENTS.md`. Agents that read `AGENTS.md` find it on their own; `CLAUDE.md` imports it for
   Claude Code.

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
labledger.yml, templates/        the ledger's time zone, commit prefix, body skeletons and words (optional)
views/                           tables rendered on each clone (gitignored)
```

## License

0BSD: use, copy, modify and distribute it for any purpose, with no conditions. See [LICENSE](LICENSE).
