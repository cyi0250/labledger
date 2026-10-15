# Demo ledger

A small ledger kept with labledger: one area, a TODO settled by an experiment, the experiment's benchmark script and
data table, a state table with the current values, and two decisions. From the repository root:

```bash
python3 scripts/labledger.py --root examples/demo validate
python3 scripts/labledger.py --root examples/demo show SORT-001
python3 scripts/labledger.py --root examples/demo render    # tables into examples/demo/views/
```

It was made with these commands, run in this folder. It lives inside the labledger repository rather than in a git
repository of its own, so the commands ran with `--no-commit` (there is no ledger repository to commit to) and
`--offline` (numbers come from this folder, not from the remote of the repository around it). In a ledger of your
own, each command commits what it wrote.

```bash
L="python3 ../../scripts/labledger.py --root . --no-commit --offline"
printf 'timezone: UTC\n' > labledger.yml
$L new area sorting "Sorting short lists" --prefix SORT
$L new todo sorting "Choose the sort for short lists" --from user --priority P2
$L new exp "Insertion sort against sorted() on short lists" --model "CPython 3.11.8: sorted() and a pure-Python insertion sort" \
   --affects "SORT-001: sorted() is faster at every length measured"
mkdir -p experiments/attachments/EXP-0001      # by hand: bench.py in it
(cd experiments/attachments/EXP-0001 && python3 bench.py > timings.csv)
# by hand: the bodies of the area, the TODO and the experiment, and state/sorting.csv
$L set EXP-0001 --status final --summary "…" --state sorting.csv \
   --data "timings.csv: one row per list length — median microseconds per sort for each function, and their ratio"
$L set SORT-001 --status done --evidence EXP-0001 --note "sorted() is faster at every length from 2 to 64; no hand-written sort"
$L new decision dates-in-utc --text "This ledger stamps its dates in UTC (labledger.yml: timezone)" \
   --context "The people who write to it work in several time zones" --why "…"
```

`new exp` wrote this machine's name into `host`. It was deleted from the head by hand, since the body names no local
path: `host` is optional.
