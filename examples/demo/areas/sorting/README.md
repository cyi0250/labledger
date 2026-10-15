---
id: sorting
type: area
title: "Sorting short lists"
prefix: SORT
order: 1
---
# sorting — Sorting short lists

Code in this project sorts many short lists (2 to 64 items). This area keeps what is known about sorting them fast in
CPython. Known now: the built-in `sorted()` beats a hand-written insertion sort at every length from 2 to 64
(EXP-0001); the measured times are in [`state/sorting.csv`](../../state/sorting.csv).

## Open questions

- Does that hold for lists that are almost sorted already? EXP-0001 sorted shuffled lists only.
