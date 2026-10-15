---
id: SORT-001
type: todo
title: "Choose the sort for short lists"
area: sorting
status: done
priority: P2
created: 2026-10-15
updated: 2026-10-15
status_note: "sorted() is faster at every length from 2 to 64; no hand-written sort"
from: [user]
evidence: [EXP-0001]
---
# SORT-001 — Choose the sort for short lists

## Problem

A pure-Python insertion sort is often said to beat the built-in `sorted()` on short lists. The project sorts many
lists of 2 to 64 items: choose between the two from a measurement.

## Evidence / paths

- EXP-0001 times both on shuffled lists of 2 to 64 items.

## Done when

The sort to use is chosen, and the experiment that chose it is named.

## How to check

- `python3 experiments/attachments/EXP-0001/bench.py` prints a `ratio[1]` above 1 at every length (above 1: `sorted()` is faster).
