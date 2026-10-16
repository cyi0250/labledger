#!/usr/bin/env python3
"""labledger.py — a git-tracked ledger that AI agents keep for a research or engineering project.

Every record is a Markdown file whose frontmatter is the source of truth:
  areas/<area>/README.md        type: area   an area of work; its TODOs are numbered <prefix>-NNN
  areas/<area>/<PREFIX>-NNN.md  type: todo   something to do, with where it came from and what settled it
  experiments/EXP-NNNN.md       type: exp    one experiment; its data files live in experiments/attachments/EXP-NNNN/
  arcs/ARC-NNN.md               type: arc    work spanning several TODOs and experiments (a paper, a release)
  areas/<area>/<doc>.md         type: inv    an investigation, review, audit or design document
  decisions/, areas/<area>/decisions/<date>-<slug>.md   one decision per file (dec:<date>-<slug>)
  areas/<area>/issues/<slug>.md                          one observed problem per file (issue:<slug>)
  state/*.md, state/*.csv                                the current best-known values, each citing its EXP
Tables and lists are generated into views/ on each clone (never committed): `render` rebuilds them.

AGENTS.md is the rulebook; `validate` checks every rule it states. Write commands (new, set, resolve, promote) edit
frontmatter only, refuse a record that would not validate, then commit what they wrote and push it when the branch
tracks an upstream. Bodies are edited by hand and committed with `commit`. labledger.yml sets the ledger's time zone and
commit message prefix; templates/ replaces the body skeletons and the words commands write into records.
"""

from __future__ import annotations

import argparse
import contextlib
import csv
import datetime as dt
import difflib
import fcntl
import importlib.util
import io
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from collections import Counter
from dataclasses import dataclass, field, replace
from functools import cached_property
from pathlib import Path, PurePosixPath
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ROOT = Path(__file__).resolve().parents[1]

AREA_FIXED_FILES = {"README.md"}
DEC_DIR, ISSUE_DIR = "decisions", "issues"  # one file per decision (<date>-<slug>.md) and per issue (<slug>.md)
ISSUE_STATUSES = ("open", "promoted", "resolved")
ENTRY_KEY_ORDER = {"dec": ("date", "title"), "issue": ("date", "status", "updated")}
ENTRY_REQUIRED = {"dec": ("date", "title"), "issue": ("date", "status")}
PREFIX_RE = re.compile(r"^[A-Z](?:[A-Z-]*[A-Z])?$")
TODO_STATUS_OPEN = ("idea", "investigate", "planned", "in_progress", "blocked", "parked")
TODO_STATUS_CLOSED = ("done", "dropped")
TODO_STATUSES = TODO_STATUS_OPEN + TODO_STATUS_CLOSED
STALE_STATUSES = ("idea", "investigate", "planned", "in_progress", "blocked")
STALE_DAYS = 90  # an open TODO unchanged this long goes on the review queue
STALE_EXP_DAYS = 14  # an experiment still in_flight after this long
STALE_OPEN_DAYS = 60  # an open issue or investigation not updated this long
PRIORITIES = ("P0", "P1", "P2")
EXP_STATUSES = ("in_flight", "final", "withdrawn", "superseded")
ARC_STATUSES = ("active", "done", "parked")
INV_STATUSES = ("open", "concluded")
SUMMARY_MAX = 500  # an EXP's or inv's summary: what it found
STATUS_NOTE_MAX = 300  # a status_note: why the record's status is what it is
INBOUND_MAX_ROWS = 40
SHOW_BODY_MAX_LINES = 80
ACTIVE_EXP_ROWS = 6

# Numbers are zero-padded to 3 digits (TODO, ARC) or 4 (EXP) and grow past that width: LAB-1000, EXP-10000.
TODO_FILE_RE = re.compile(r"^[A-Z][A-Z-]*-\d{3,}\.md$")
EXP_FILE_RE = re.compile(r"^EXP-\d{4,}\.md$")
ARC_FILE_RE = re.compile(r"^ARC-\d{3,}\.md$")
EXP_ID_RE = re.compile(r"\bEXP-\d{4,}\b")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
HOST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")  # EXP `host`: the server whose paths the body names
NODE_ID_RE = re.compile(r"[A-Z][A-Z-]*-\d{3,}")
LINK_ID_RE = re.compile(r"^([A-Z][A-Z-]*-\d{3,})(?::\s*(.*))?$")
AFFECTS_RE = re.compile(r"^(issue:[A-Za-z0-9][A-Za-z0-9_.-]*|[A-Z][A-Z-]*-\d{3,})(?::\s*(.*))?$")
TAG_RE = re.compile(r"^(issue|inv|dec|arc):(\S+)$")
SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
VIEWS = "views"  # every table and list the tool renders: local to each clone, never committed
TODO_REQUIRED = ("id", "type", "title", "area", "status", "priority", "created", "updated")
TODO_LIST_FIELDS = ("from", "depends_on", "supersedes", "resolves", "evidence")
TODO_KEY_ORDER = ("id", "type", "title", "area", "status", "priority", "created", "updated",
                  "status_note") + TODO_LIST_FIELDS
EXP_REQUIRED = ("id", "type", "title", "date", "status", "affects", "state", "summary")
EXP_KEY_ORDER = ("id", "type", "title", "date", "host", "status", "model", "commit", "arc", "affects",
                 "state", "data", "summary", "superseded_by")
ARC_REQUIRED = ("id", "type", "title", "status", "created", "updated", "members", "exps")
ARC_KEY_ORDER = ("id", "type", "title", "status", "created", "updated", "status_note", "areas",
                 "venue", "location", "members", "exps")
INV_REQUIRED = ("id", "type", "title", "area", "date", "status", "affects")
INV_KEY_ORDER = ("id", "type", "title", "area", "date", "updated", "status", "status_note", "summary", "affects")
AREA_REQUIRED = ("id", "type", "title", "prefix")
AREA_KEY_ORDER = ("id", "type", "title", "prefix", "order")
KEY_ORDER = {"todo": TODO_KEY_ORDER, "exp": EXP_KEY_ORDER, "arc": ARC_KEY_ORDER, "inv": INV_KEY_ORDER, "area": AREA_KEY_ORDER}
LIST_FIELDS = {"todo": TODO_LIST_FIELDS, "exp": ("affects", "data"), "arc": ("areas", "members", "exps"), "inv": ("affects",), "area": ()}
H1_KINDS = ("todo", "exp", "arc", "area")  # inv documents keep their own H1
TODO_SKELETON = """## Problem

<What is wrong and why it matters. Keep what was observed apart from how it is read.>

## Evidence / paths

- <file:line, logs, EXP-NNNN …>

## Done when

<What is true once this is finished.>

## How to check

- <A command to run as written, and what it should print>
"""
EXP_SKELETON = """## Question

<What this experiment wants to know. One or two lines.>

## Setup

<What was changed and where its configuration lives: everything needed to run it again.>

## Commands

```bash
<the commands, ready to run again>
```

## Results

<Summary tables of the measured values, each under a line naming the data file it comes from (frontmatter `data`).
Report what was measured, failures and regressions included.>

## Reading

<What the results say about the question.>

## Next

<The issues, TODOs or experiments this one leads to, or "none".>
"""
ARC_SKELETON = """## Goal

<What is true once this arc is finished. One or two lines.>

## Steps

| Step | What | Output | Status |
|---|---|---|---|
| 1 | <…> | <…> | planned |

## Exit criteria

- <Checkable conditions, judged from the member TODOs' status and the experiments' results.>
"""
INV_SKELETON = """## Question

<What this investigation wants to know, or what it reviews. One or two lines.>

## Method

<The files read, the commands run, the artifacts compared: everything needed to repeat it.>

## Findings

<What was observed, kept apart from how it is read.>

## Conclusion

<What should change. The issues and TODOs it updates go in the frontmatter `affects`.>
"""
AREA_SKELETON = """<What this area covers and what is understood about it now. Its TODOs and investigations are listed by
`python3 scripts/labledger.py show <area>`.>

## Open questions

- <questions for whoever owns the project>
"""
SKELETONS = {"todo": TODO_SKELETON, "exp": EXP_SKELETON, "arc": ARC_SKELETON, "inv": INV_SKELETON, "area": AREA_SKELETON}
AREA_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
LABELS = {  # the words the write commands put into record bodies
    "symptom": "Symptom", "path": "Path", "hypothesis": "Hypothesis", "blocking": "Blocking",  # new issue
    "context": "Context", "decision": "Decision", "why": "Why",  # new decision
    "resolved": "Resolved", "promoted": "Promoted",  # the line resolve issue / promote issue appends
    "evidence": "evidence", "closed_by": "done by {evidence}",  # the close decision of a TODO
    "in_progress": "(in progress)",  # a new EXP's summary
}
NAME = "labledger"  # what this ledger is called: its tool, settings, skill, commit messages, variables and refs carry it
SETTINGS_FILE = "labledger.yml"  # the ledger's settings: `key: value` lines, every key optional
SETTINGS_KEYS = ("timezone", "commit_prefix", "language")
COMMIT_PREFIX = NAME  # a write command's commit message reads `<commit_prefix>(<area>): <what>`
TEMPLATES_DIR = "templates"  # <kind>.md replaces a body skeleton (SKELETONS), labels.yml any of the LABELS
# EXP `state`: `n/a` (a parenthesised note may follow) when the experiment is the source of no state/ row; else the
# state file names.
STATE_NA_RE = re.compile(r"^(n/a|na|none|-)(\s*[(（].*)?$", re.I)
STATE_FILE_RE = re.compile(r"(?<![A-Za-z0-9_.-])([A-Za-z0-9_-]+\.(?:md|csv))\b")
EXP_ABBREV_RE = re.compile(r"\bEXP-\d{4,}(?:/\d{4,})+")
FIND_LIMIT = 40


def read_kv(path: Path, where: str) -> dict:
    """A file of `key: value` lines (SETTINGS_FILE, templates/labels.yml) read with the frontmatter parser; {} when the
    file does not exist."""
    if not path.is_file():
        return {}
    fm, _ = parse_frontmatter("---\n" + path.read_text(encoding="utf-8") + "\n---\n", where)
    return fm


def settings(root: Path) -> dict:
    try:
        return read_kv(root / SETTINGS_FILE, SETTINGS_FILE)
    except FrontmatterError as exc:
        raise SystemExit(str(exc))


def record_today(root: Path | None = None) -> str:
    """Today in the ledger's time zone (`timezone` in labledger.yml), or in this machine's when it names none. Every
    machine that writes to one ledger must stamp the same day, so a ledger kept on several machines names its zone."""
    name = settings(root).get("timezone") if root is not None else None
    now = time.time()  # the clock a test can freeze
    if not name:
        return dt.datetime.fromtimestamp(now).date().isoformat()
    try:
        zone = ZoneInfo(str(name))
    except (ZoneInfoNotFoundError, ValueError):
        raise SystemExit(f"{SETTINGS_FILE}: time zone {name!r} is unknown here (an IANA name such as Europe/Berlin; is tzdata installed?)")
    return dt.datetime.fromtimestamp(now, zone).date().isoformat()


def commit_subject(root: Path, scope: str, what: str) -> str:
    return f"{settings(root).get('commit_prefix') or COMMIT_PREFIX}({scope}): {what}"


def record_host() -> str:
    """This machine's name as the records carry it (EXP `host`, the number counter's log): LABLEDGER_HOST, else the host
    name up to its first dot. `localhost` (a machine left with its install-time name) names no machine."""
    name = os.environ.get("LABLEDGER_HOST") or socket.gethostname().split(".")[0]
    if not HOST_RE.match(name) or name.lower() == "localhost":
        raise SystemExit(f"LABLEDGER_HOST: {name!r} does not name this machine — set LABLEDGER_HOST (letters, digits, . _ -) in the "
                         "shell profile, e.g. `export LABLEDGER_HOST=lab-server`")
    return name


# ───────────────────────────── frontmatter ─────────────────────────────

class FrontmatterError(ValueError):
    pass


def _unquote(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith('"'):
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise FrontmatterError(f"bad quoted string: {raw[:60]}") from exc
    if raw.startswith("'") and raw.endswith("'") and len(raw) >= 2:
        return raw[1:-1].replace("''", "'")
    return raw


def _split_inline_list(raw: str) -> list[str]:
    inner = raw.strip()[1:-1].strip()
    if not inner:
        return []
    items, cur, quote = [], "", None
    for ch in inner:
        if quote:
            cur += ch
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
            cur += ch
        elif ch == ",":
            items.append(_unquote(cur))
            cur = ""
        else:
            cur += ch
    if cur.strip():
        items.append(_unquote(cur))
    return items


def parse_frontmatter(text: str, where: str) -> tuple[dict, str]:
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        raise FrontmatterError(f"{where}: frontmatter must start at line 1 with ---")
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        raise FrontmatterError(f"{where}: frontmatter never closed") from None
    fm: dict = {}
    i = 1
    while i < end:
        line = lines[i]
        if not line.strip() or line.lstrip().startswith("#"):
            i += 1
            continue
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*):(?:\s+(.*))?$", line)
        if m is None:
            raise FrontmatterError(f"{where}:{i + 1}: cannot parse `{line}`")
        key, rest = m.group(1), (m.group(2) or "").strip()
        if key in fm:
            raise FrontmatterError(f"{where}: duplicate key {key}")
        if rest == "" or rest in ("|", ">-", ">", "|-"):
            block = []
            j = i + 1
            while j < end and (lines[j].startswith("  ") or not lines[j].strip()):
                block.append(lines[j])
                j += 1
            while block and not block[-1].strip():
                block.pop()
            if rest == "" and block and all(b.lstrip().startswith("- ") or not b.strip() for b in block):
                fm[key] = [_unquote(b.strip()[2:]) for b in block if b.strip()]
            elif rest == "":
                if block:
                    raise FrontmatterError(f"{where}:{i + 1}: `{key}` has an unrecognised block")
                fm[key] = ""
            else:
                joiner = "\n" if rest.startswith("|") else " "
                fm[key] = joiner.join(b.strip() for b in block if b.strip())
            i = j
            continue
        if rest.startswith("["):
            if not rest.endswith("]"):
                raise FrontmatterError(f"{where}:{i + 1}: `{key}` starts with [ but is not a list — quote the value if it is text")
            fm[key] = _split_inline_list(rest)
        else:
            fm[key] = _unquote(rest)
        i += 1
    body = "\n".join(lines[end + 1:])
    return fm, body


def _yaml_scalar(value: str) -> str:
    if value == "":
        return '""'
    simple = re.fullmatch(r"[A-Za-z0-9_./+:-]+", value) is not None and value.lower() not in ("true", "false", "null", "yes", "no")
    return value if simple else json.dumps(value, ensure_ascii=False)


def emit_frontmatter(fm: dict, key_order: tuple[str, ...]) -> str:
    out = ["---"]
    keys = [k for k in key_order if k in fm] + [k for k in fm if k not in key_order]
    for key in keys:
        value = fm[key]
        if isinstance(value, list):
            if not value:
                out.append(f"{key}: []")
            elif all(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/+-]*", v) for v in value):
                out.append(f"{key}: [{', '.join(value)}]")
            else:
                out.append(f"{key}:")
                out.extend(f"  - {_yaml_scalar(v)}" for v in value)
        else:
            out.append(f"{key}: {_yaml_scalar(str(value))}")
    out.append("---")
    return "\n".join(out) + "\n"


# ───────────────────────────── nodes ─────────────────────────────

@dataclass
class Todo:
    path: Path
    fm: dict
    body: str  # everything after the frontmatter, H1 and inbound block stripped
    raw: str = ""  # the file as read; render/fmt keep its hand-written bytes
    rel: str = ""  # path relative to the repo root
    mentions: frozenset[str] = frozenset()  # TODO IDs written in the text (index_mentions)

    @property
    def id(self) -> str:
        return self.fm["id"]

    @property
    def area(self) -> str:
        return self.fm["area"]

    def links(self, key: str) -> list[str]:
        return list(self.fm.get(key) or [])


@dataclass
class Exp:
    path: Path
    fm: dict
    body: str
    raw: str = ""
    rel: str = ""
    mentions: frozenset[str] = frozenset()

    @property
    def id(self) -> str:
        return self.fm["id"]

    def affects(self) -> list[tuple[str, str]]:
        out = []
        for item in self.fm.get("affects") or []:
            m = LINK_ID_RE.match(item.strip())
            if m is None:
                raise FrontmatterError(f"{self.rel}: bad affects entry `{item}` — an experiment's affects names the TODOs and "
                                       "EXPs it moved, as `ID: note`" + (f"; the issue it settled takes `resolve issue "
                                       f"{item.strip()[6:].split(':')[0]} --by {self.id}`" if item.strip().startswith("issue:") else ""))
            out.append((m.group(1), (m.group(2) or "").strip()))
        return out


@dataclass
class Arc:
    path: Path
    fm: dict
    body: str
    raw: str = ""
    rel: str = ""

    @property
    def id(self) -> str:
        return self.fm["id"]

    def declared(self, key: str) -> list[str]:
        return list(self.fm.get(key) or [])


@dataclass
class Inv:
    path: Path
    fm: dict
    body: str  # verbatim (the document keeps its own H1)
    raw: str = ""
    rel: str = ""
    mentions: frozenset[str] = frozenset()

    @property
    def id(self) -> str:
        return self.fm["id"]

    @property
    def area(self) -> str:
        return self.fm["area"]

    @property
    def updated(self) -> str:
        """When its content last changed: `updated`, else the day it was made."""
        return str(self.fm.get("updated") or self.fm["date"])

    def affects(self) -> list[tuple[str, str]]:
        out = []
        for item in self.fm.get("affects") or []:
            m = AFFECTS_RE.match(item.strip())
            if m is None:
                raise FrontmatterError(f"{self.rel}: bad affects entry `{item}` (a TODO ID or issue:<slug>)")
            out.append((m.group(1), (m.group(2) or "").strip()))
        return out


@dataclass
class Area:
    path: Path
    fm: dict
    body: str  # the README after its H1
    raw: str = ""
    rel: str = ""

    @property
    def id(self) -> str:
        return self.fm["id"]

    @property
    def title(self) -> str:
        return str(self.fm.get("title") or self.id)

    @property
    def prefix(self) -> str:
        return str(self.fm.get("prefix") or "")

    @property
    def order(self) -> int:
        raw = str(self.fm.get("order", ""))
        return int(raw) if raw.isdigit() else 10**9


@dataclass
class Mention:
    date: str
    source: str      # display text
    href: str        # relative link from the todo file, or ""
    kind: str        # affects | investigation | mention | relation | arc | issue | decision
    text: str
    event: bool = True  # False for rows whose date is another record's `updated`, not an event date


@dataclass
class Graph:
    root: Path
    todos: dict[str, Todo]
    exps: dict[str, Exp]
    arcs: dict[str, Arc] = field(default_factory=dict)
    invs: dict[str, Inv] = field(default_factory=dict)
    areas: dict[str, Area] = field(default_factory=dict)
    decisions: list[Decision] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    settings: dict = field(default_factory=dict)  # labledger.yml
    labels: dict = field(default_factory=lambda: dict(LABELS))  # LABELS with templates/labels.yml over them

    @cached_property
    def tracked(self) -> set[str] | None:
        """The files git knows here (its index: on disk or not — a partial clone leaves attachments off disk), or None
        when the root is not the top of a git work tree. A hook or a commit sets it from the index it checks."""
        return known_files(self.root)

    @cached_property
    def tracked_dirs(self) -> set[str]:
        return {str(parent) for f in self.tracked or () for parent in PurePosixPath(f).parents if str(parent) != "."}

    def has_file(self, rel: str) -> bool:
        return (self.tracked is not None and rel in self.tracked) or (self.root / rel).is_file()

    def has_dir(self, rel: str) -> bool:
        return rel in self.tracked_dirs or (self.root / rel).is_dir()

    def skeleton(self, kind: str) -> str:
        """The body a new record of `kind` starts with: templates/<kind>.md, else the built-in SKELETONS[kind]."""
        path = self.root / TEMPLATES_DIR / f"{kind}.md"
        return path.read_text(encoding="utf-8") if path.is_file() else SKELETONS[kind]

    def area_order(self) -> list[Area]:
        return sorted(self.areas.values(), key=lambda a: (a.order, a.id))

    def todo_order(self) -> list[Todo]:
        rank = {a.id: i for i, a in enumerate(self.area_order())}

        def key(t: Todo):
            prefix, num = t.id.rsplit("-", 1)
            return (rank.get(t.area, len(rank)), prefix, int(num))
        return sorted(self.todos.values(), key=key)

    def exp_order(self) -> list[Exp]:
        return sorted(self.exps.values(), key=lambda e: int(e.id[4:]))

    def arc_order(self) -> list[Arc]:
        return sorted(self.arcs.values(), key=lambda a: int(a.id[4:]))

    def inv_order(self) -> list[Inv]:
        return sorted(self.invs.values(), key=lambda i: (i.fm.get("date", ""), i.id))

    # arc membership = the arc's declaration ∪ what the newer record declares (`from: arc:` / `arc:`)
    @cached_property
    def members_by_arc(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for arc in self.arcs.values():
            ids = set(arc.declared("members"))
            ids |= {t.id for t in self.todos.values() if f"arc:{arc.id}" in t.links("from")}
            out[arc.id] = sorted(ids, key=_id_sort_key)
        return out

    @cached_property
    def exps_by_arc(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for arc in self.arcs.values():
            ids = set(arc.declared("exps"))
            ids |= {e.id for e in self.exps.values() if e.fm.get("arc") == arc.id}
            out[arc.id] = sorted(ids, key=_id_sort_key)
        return out

    @cached_property
    def issues_by_slug(self) -> dict[str, Issue]:
        return {i.slug: i for i in reversed(self.issues)}  # first entry wins when a slug is (wrongly) reused

    @cached_property
    def decisions_by_id(self) -> dict[str, Decision]:
        return {d.id: d for d in reversed(self.decisions)}

    @cached_property
    def states(self) -> dict[str, str]:
        """state/*.md and state/*.csv by file name — what an EXP `state` claim names."""
        d = self.root / "state"
        paths = sorted([*d.glob("*.md"), *d.glob("*.csv")]) if d.is_dir() else []
        return {p.name: p.read_text(encoding="utf-8") for p in paths}

    @cached_property
    def arcs_by_node(self) -> dict[str, list[Arc]]:
        out: dict[str, list[Arc]] = {}
        for arc in self.arc_order():
            for node_id in self.members_by_arc[arc.id] + self.exps_by_arc[arc.id]:
                out.setdefault(node_id, []).append(arc)
        return out

    def member_ids(self, arc: Arc) -> list[str]:
        return self.members_by_arc.get(arc.id, [])

    def exp_ids(self, arc: Arc) -> list[str]:
        return self.exps_by_arc.get(arc.id, [])

    def arcs_of(self, node_id: str) -> list[Arc]:
        return self.arcs_by_node.get(node_id, [])


def _id_sort_key(node_id: str):
    prefix, num = node_id.rsplit("-", 1)
    return (prefix, int(num))


def strip_h1(body: str, node_id: str) -> str:
    lines = body.lstrip("\n").split("\n")
    if lines and re.match(rf"^# {re.escape(node_id)}\b", lines[0]):
        lines = lines[1:]
    return "\n".join(lines).lstrip("\n")


def load_graph(root: Path = ROOT) -> Graph:
    todos: dict[str, Todo] = {}
    exps: dict[str, Exp] = {}
    arcs: dict[str, Arc] = {}
    invs: dict[str, Inv] = {}
    areas: dict[str, Area] = {}
    problems: list[str] = []
    areas_dir = root / "areas"  # a repository with no records yet has none
    for area_dir in sorted(areas_dir.iterdir()) if areas_dir.is_dir() else []:
        if not area_dir.is_dir():
            continue
        readme = area_dir / "README.md"
        rel = readme.relative_to(root).as_posix()
        text = readme.read_text(encoding="utf-8") if readme.is_file() else ""
        if not text.startswith("---"):
            problems.append(f"{rel}: every area needs a README.md starting with frontmatter (id, type: area, title, prefix, order)")
        else:
            try:
                fm, body = parse_frontmatter(text, rel)
                areas[area_dir.name] = Area(readme, fm, strip_h1(body, area_dir.name), text, rel=rel)
            except FrontmatterError as exc:
                problems.append(str(exc))
        for path in sorted(area_dir.glob("*.md")):
            rel = path.relative_to(root).as_posix()
            if TODO_FILE_RE.match(path.name):
                text = path.read_text(encoding="utf-8")
                try:
                    fm, body = parse_frontmatter(text, rel)
                    body = strip_h1(body, fm.get("id", path.stem))
                except FrontmatterError as exc:
                    problems.append(str(exc))
                    continue
                if fm.get("id") != path.stem:
                    problems.append(f"{rel}: id `{fm.get('id')}` != filename")
                if fm.get("area") != area_dir.name:
                    problems.append(f"{rel}: area `{fm.get('area')}` != directory {area_dir.name}")
                if path.stem in todos:
                    problems.append(f"{rel}: TODO id `{path.stem}` also used by {todos[path.stem].rel} — rename one file")
                todos[path.stem] = Todo(path, fm, body, text, rel=rel)
                continue
            if path.name in AREA_FIXED_FILES:
                continue
            text = path.read_text(encoding="utf-8")
            if not text.startswith("---"):
                problems.append(f"{rel}: area document needs frontmatter (type: inv) — every non-TODO document in areas/ is a node")
                continue
            try:
                fm, body = parse_frontmatter(text, rel)
            except FrontmatterError as exc:
                problems.append(str(exc))
                continue
            if fm.get("type") != "inv":
                problems.append(f"{rel}: type must be inv (an investigation, review, audit, design or plan)")
                continue
            if fm.get("id") != path.stem:
                problems.append(f"{rel}: id `{fm.get('id')}` != filename stem `{path.stem}`")
            if fm.get("area") != area_dir.name:
                problems.append(f"{rel}: area `{fm.get('area')}` != directory {area_dir.name}")
            if path.stem in invs:
                problems.append(f"{rel}: inv id `{path.stem}` also used by {invs[path.stem].rel} — rename one file")
            invs[path.stem] = Inv(path, fm, body, text, rel=rel)
    for path in sorted((root / "experiments").glob("EXP-*.md")):
        if not EXP_FILE_RE.match(path.name):
            continue
        rel = path.relative_to(root).as_posix()
        text = path.read_text(encoding="utf-8")
        try:
            fm, body = parse_frontmatter(text, rel)
            body = strip_h1(body, fm.get("id", path.stem))
        except FrontmatterError as exc:
            problems.append(str(exc))
            continue
        if fm.get("id") != path.stem:
            problems.append(f"{rel}: id `{fm.get('id')}` != filename")
        exps[path.stem] = Exp(path, fm, body, text, rel=rel)
    arcs_dir = root / "arcs"
    if arcs_dir.is_dir():
        for path in sorted(arcs_dir.glob("*.md")):
            rel = path.relative_to(root).as_posix()
            if not ARC_FILE_RE.match(path.name):
                if path.name != "README.md":
                    problems.append(f"{rel}: not an ARC-NNN.md file")
                continue
            text = path.read_text(encoding="utf-8")
            try:
                fm, body = parse_frontmatter(text, rel)
                body = strip_h1(body, fm.get("id", path.stem))
            except FrontmatterError as exc:
                problems.append(str(exc))
                continue
            if fm.get("id") != path.stem:
                problems.append(f"{rel}: id `{fm.get('id')}` != filename")
            arcs[path.stem] = Arc(path, fm, body, text, rel=rel)
    decisions, dec_problems = scan_decisions(root)
    issues, issue_problems = scan_issues(root)
    graph = Graph(root=root, todos=todos, exps=exps, arcs=arcs, invs=invs, areas=areas, decisions=decisions,
                  issues=issues, problems=problems + dec_problems + issue_problems)
    for where, read in ((SETTINGS_FILE, lambda: read_kv(root / SETTINGS_FILE, SETTINGS_FILE)),
                        (f"{TEMPLATES_DIR}/labels.yml", lambda: read_kv(root / TEMPLATES_DIR / "labels.yml", f"{TEMPLATES_DIR}/labels.yml"))):
        try:
            values = read()
        except FrontmatterError as exc:
            graph.problems.append(str(exc))
            continue
        if where == SETTINGS_FILE:
            graph.settings = values
        else:
            graph.labels.update({k: str(v) for k, v in values.items()})
    index_mentions(graph)
    return graph


def todo_id_pattern(todo_ids) -> re.Pattern[str]:
    """One token per TODO ID in free text. The boundaries are ASCII on purpose: a non-Latin letter attached to the ID
    (a particle in a language written without a space before it) does not hide it, and `SIM-LAB-001` mentions
    SIM-LAB-001 only, not LAB-001."""
    prefixes = sorted({i.rsplit("-", 1)[0] for i in todo_ids}, key=len, reverse=True)
    if not prefixes:
        return re.compile(r"(?!x)x")
    return re.compile(r"(?<![A-Za-z0-9-])(?:" + "|".join(re.escape(p) for p in prefixes) + r")-\d{3,}(?![A-Za-z0-9])")


def index_mentions(graph: Graph) -> None:
    """Scan every record once for TODO ID tokens; inbound_for then asks each record `tid in mentions`."""
    token_re = todo_id_pattern(graph.todos)

    def tokens(*texts) -> frozenset[str]:
        found = set()
        for text in texts:
            if not isinstance(text, str):
                text = " ".join(map(str, text or []))
            found.update(m.group(0) for m in token_re.finditer(text))
        return frozenset(found)

    for e in graph.exps.values():
        e.mentions = tokens(e.fm.get("title", ""), e.fm.get("summary", ""), e.body)
    for i in graph.invs.values():
        i.mentions = tokens(i.body)
    for t in graph.todos.values():
        t.mentions = tokens(t.body, t.fm.get("status_note", ""))
    for d in graph.decisions:
        d.mentions = tokens(d.text)
    for iss in graph.issues:
        iss.mentions = tokens(iss.text)


# ───────────────────────────── addresses ─────────────────────────────
# Everything that turns text into a record goes through resolve_address. Accepted forms:
#   TODO ID · EXP-NNNN · ARC-NNN (case-insensitive) · <area> · inv:<id> or the bare inv id
#   · dec:<date>-<slug> · issue:<slug> or the bare slug · arc:ARC-NNN

def did_you_mean(graph: Graph, text: str) -> str:
    """` — did you mean …?` for an address that names nothing: the records of its kind whose name starts or ends with it
    (an investigation or a decision named without its date), else the closest spellings."""
    raw = text.strip()
    addresses = [*graph.todos, *graph.exps, *graph.arcs, *graph.areas, *(f"inv:{k}" for k in graph.invs),
                 *(f"dec:{k}" for k in graph.decisions_by_id), *(f"issue:{k}" for k in graph.issues_by_slug)]
    tag, _, target = raw.rpartition(":") if TAG_RE.match(raw) else ("", "", raw)
    near = [a for a in addresses if a.startswith(f"{tag}:" if tag else "") and target and a != raw
            and (a.split(":")[-1].startswith(target) or a.split(":")[-1].endswith(target))]
    near = near or difflib.get_close_matches(raw, addresses, n=3, cutoff=0.6)
    return f" — did you mean {', '.join(near[:3])}?" if near else ""


def resolve_address(graph: Graph, text: str):
    """(kind, record) for an address — kind is todo/exp/arc/inv/area/dec/issue — or None."""
    raw = text.strip()
    tag = TAG_RE.match(raw)
    if tag:
        kind, target = tag.groups()
        table = {"inv": graph.invs, "arc": graph.arcs, "dec": graph.decisions_by_id, "issue": graph.issues_by_slug}[kind]
        return (kind, table[target]) if target in table else None
    node_id = raw.upper()
    for kind, table in (("todo", graph.todos), ("exp", graph.exps), ("arc", graph.arcs)):
        if node_id in table:
            return kind, table[node_id]
    for kind, table in (("area", graph.areas), ("inv", graph.invs), ("issue", graph.issues_by_slug)):
        if raw in table:
            return kind, table[raw]
    return None


# ───────────────────────────── decisions, issues ─────────────────────────────

@dataclass
class Decision:
    date: str
    slug: str
    file: str
    text: str
    mentions: frozenset[str] = frozenset()
    fm: dict = field(default_factory=dict)
    raw: str = ""

    @property
    def id(self) -> str:
        return f"{self.date}-{self.slug}"


@dataclass
class Issue:
    date: str
    slug: str
    status: str     # open | promoted | resolved
    file: str
    text: str
    mentions: frozenset[str] = frozenset()
    fm: dict = field(default_factory=dict)
    raw: str = ""

    @property
    def is_open(self) -> bool:
        return self.status == "open"

    @property
    def updated(self) -> str:
        """When its content last changed: `updated`, else the day it was made."""
        return str(self.fm.get("updated") or self.date)


def _read_entries(root: Path, kind: str) -> tuple[list[tuple[Path, dict, str, str]], list[str]]:
    """(path, frontmatter, body, raw) of every decision file (decisions/, areas/*/decisions/) or issue file
    (areas/*/issues/), and the problems of those that cannot be read as one. Hidden files (`.gitkeep` keeping an
    empty folder in git) are no records."""
    what = "decision" if kind == "dec" else "issue"
    paths = ([*sorted((root / DEC_DIR).glob("*")), *sorted((root / "areas").glob(f"*/{DEC_DIR}/*"))] if kind == "dec"
             else sorted((root / "areas").glob(f"*/{ISSUE_DIR}/*")))
    out, problems = [], []
    for path in paths:
        if path.name.startswith("."):
            continue
        rel = path.relative_to(root).as_posix()
        if not path.is_file() or path.suffix != ".md":
            problems.append(f"{rel}: not a {what} file — only one {what} per .md file lives in {path.parent.name}/")
            continue
        raw = path.read_text(encoding="utf-8")
        try:
            fm, body = parse_frontmatter(raw, rel)
        except FrontmatterError as exc:
            problems.append(str(exc))
            continue
        keys, required = ENTRY_KEY_ORDER[kind], ENTRY_REQUIRED[kind]
        optional = [k for k in keys if k not in required]
        if not set(required) <= set(fm) <= set(keys):
            problems.append(f"{rel}: {what} frontmatter is {' · '.join(required)}" + (f" (and {' · '.join(optional)})" if optional else ""))
        elif not all(DATE_RE.match(str(fm[k])) for k in ("date", "updated") if k in fm):
            problems.append(f"{rel}: date must be YYYY-MM-DD")
        elif str(fm.get("updated") or fm["date"]) < str(fm["date"]):
            problems.append(f"{rel}: updated < date")
        else:
            out.append((path, fm, body, raw))
    return out, problems


def scan_decisions(root: Path) -> tuple[list[Decision], list[str]]:
    """decisions/<date>-<slug>.md (global) and areas/<area>/decisions/<date>-<slug>.md: frontmatter date and title
    (the slug is the title with dashes for spaces), then the decision. Returns (decisions, problems)."""
    out: list[Decision] = []
    entries, problems = _read_entries(root, "dec")
    for path, fm, body, raw in entries:
        rel = path.relative_to(root).as_posix()
        title = str(fm["title"]).strip()
        slug = re.sub(r"\s+", "-", title)
        if not SLUG_RE.match(slug):
            problems.append(f"{rel}: title `{title}` must be [A-Za-z0-9._-] words so `dec:{fm['date']}-<slug>` is an address")
        elif path.name != f"{fm['date']}-{slug}.md":
            problems.append(f"{rel}: the file name must be `{fm['date']}-{slug}.md` — the date, then the title with dashes")
        else:
            out.append(Decision(fm["date"], slug, rel, (title + " — " + " ".join(body.split())).strip(), fm=fm, raw=raw))
    seen: dict[str, str] = {}
    for d in out:
        if d.id in seen:
            problems.append(f"decision id `{d.id}` is defined in both {seen[d.id]} and {d.file} — rename one so `dec:{d.id}` is unambiguous")
        seen.setdefault(d.id, d.file)
    return out, problems


def scan_issues(root: Path) -> tuple[list[Issue], list[str]]:
    """areas/<area>/issues/<slug>.md: frontmatter date and status (open | promoted | resolved), then the observation and
    its promoted / resolved lines. Returns (issues, problems)."""
    out: list[Issue] = []
    entries, problems = _read_entries(root, "issue")
    for path, fm, body, raw in entries:
        rel = path.relative_to(root).as_posix()
        if not SLUG_RE.match(path.stem):
            problems.append(f"{rel}: the file name is the slug — [A-Za-z0-9._-] so `issue:<slug>` is an address")
        elif fm["status"] not in ISSUE_STATUSES:
            problems.append(f"{rel}: status must be {' | '.join(ISSUE_STATUSES)}")
        else:
            text = " ".join(line.strip() for line in body.split("\n") if line.strip())
            out.append(Issue(fm["date"], path.stem, fm["status"], rel, text, fm=fm, raw=raw))
    for slug, n in Counter(i.slug for i in out).items():
        if n > 1:
            problems.append(f"issue slug `{slug}` is used by {n} files — slugs must be unique so `issue:{slug}` resolves")
    return out, problems


def entry_text(kind: str, fm: dict, body: str) -> str:
    """A decision or issue file: the canonical frontmatter, then the body as written."""
    body = body.strip("\n")
    return emit_frontmatter(fm, ENTRY_KEY_ORDER[kind]) + (body + "\n" if body else "")


def state_claims(value) -> list[str] | None:
    """State files an EXP's `state` field names. [] for n/a (a parenthesised note may follow, e.g. `n/a (row taken over
    by EXP-0035)`), None when the value is neither n/a nor names a state file."""
    text = str(value if value is not None else "").strip()
    if not text or STATE_NA_RE.match(text):
        return []
    names = sorted({m.group(1) for m in STATE_FILE_RE.finditer(text)})
    return names or None


# ───────────────────────────── inbound ─────────────────────────────

def _rel_href(from_path: Path, to_path: Path) -> str:
    import os
    return Path(os.path.relpath(to_path, from_path.parent)).as_posix()


def _clip(text: str, n: int = 160) -> str:
    text = re.sub(r"\s+", " ", text).replace("|", "\\|").strip()
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def inbound_for(graph: Graph, todo: Todo) -> list[Mention]:
    """Every record that names this TODO: declared (`affects`, links, arc membership) or as a token in its text."""
    tid = todo.id
    rows: list[Mention] = []

    def href(path: Path) -> str:
        return _rel_href(todo.path, path)

    for exp in graph.exps.values():
        declared = {a: note for a, note in exp.affects()}
        if tid in declared:
            note = declared[tid] or exp.fm.get("summary", "")
            rows.append(Mention(exp.fm["date"], exp.id, href(exp.path), "affects", _clip(f"{exp.fm['title']} — {note}")))
        elif tid in exp.mentions:
            rows.append(Mention(exp.fm["date"], exp.id, href(exp.path), "mention", _clip(f"{exp.fm['title']} — {exp.fm.get('summary', '')}")))
    for inv in graph.invs.values():
        declared = {a: note for a, note in inv.affects()}
        if tid in declared:
            note = declared[tid] or inv.fm.get("summary", "")
            rows.append(Mention(inv.updated, f"inv:{inv.id}", href(inv.path), "investigation", _clip(f"{inv.fm['title']} — {note}")))
        elif tid in inv.mentions:
            rows.append(Mention(inv.updated, f"inv:{inv.id}", href(inv.path), "mention", _clip(f"{inv.fm['status']} · {inv.fm['title']}")))
    for arc in graph.arcs_of(tid):
        rows.append(Mention(arc.fm["updated"], arc.id, href(arc.path), "arc",
                            _clip(f"{arc.fm['status']} · {arc.fm['title']}"), event=False))
    for other in graph.todos.values():
        if other.id == tid:
            continue
        rel_kinds = [k for k in ("depends_on", "supersedes", "from", "resolves") if tid in other.links(k)]
        if rel_kinds:
            rows.append(Mention(other.fm["updated"], other.id, href(other.path), "relation",
                                _clip(f"{other.id} {'/'.join(rel_kinds)} {tid} · {other.fm['status']} · {other.fm['title']}"), event=False))
        elif tid in other.mentions:
            rows.append(Mention(other.fm["updated"], other.id, href(other.path), "mention", _clip(f"{other.fm['status']} · {other.fm['title']}"), event=False))
    for d in graph.decisions:
        if tid in d.mentions:
            rows.append(Mention(d.date, f"dec:{d.id}", href(graph.root / d.file), "decision", _clip(d.text)))
    for iss in graph.issues:
        if iss.is_open and tid in iss.mentions:
            rows.append(Mention(iss.updated, f"issue:{iss.slug}", href(graph.root / iss.file), "issue", _clip(iss.text)))
    rows.sort(key=lambda m: (m.date, m.source), reverse=True)
    return rows


def render_inbound_block(todo: Todo, rows: list[Mention]) -> str:
    """What `show` prints under a TODO: every record that names it, computed from the graph (never stored in a file)."""
    lines = ["## Inbound records (computed, never stored)", ""]
    if not rows:
        lines.append("_No record names or mentions this TODO._")
    else:
        updated = todo.fm["updated"]
        lines += ["| Date | Source | Kind | What |", "|---|---|---|---|"]
        for m in rows:
            new = " **new**" if m.event and m.date > updated else ""
            src = f"[{m.source}]({m.href})" if m.href else m.source
            lines.append(f"| {m.date}{new} | {src} | {m.kind} | {m.text} |")
        newer = sum(1 for m in rows if m.event and m.date > updated)
        lines.append("")
        lines.append(f"_{newer} inbound since updated {updated} — the `review` queue asks for this TODO's status again._" if newer
                     else f"_Nothing new since updated {updated}._")
    return "\n".join(lines) + "\n"


# ───────────────────────────── renderers ─────────────────────────────

def canonical_head(kind: str, fm: dict) -> str:
    """Frontmatter in key order plus, for todo/exp/arc, the `# ID — title` line. validate requires every node
    file to start with exactly this and fmt writes it; nothing else in a node file is ever regenerated."""
    head = emit_frontmatter(fm, KEY_ORDER[kind])
    if kind in H1_KINDS:
        head += f"# {fm.get('id', '')} — {fm.get('title', '')}\n"
    return head


def split_head(raw: str, kind: str, node_id: str) -> str:
    """Everything after the frontmatter and (todo/exp/arc) the H1 line, as written."""
    lines = raw.split("\n")
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        return raw
    rest = "\n".join(lines[end + 1:])
    return strip_h1(rest, node_id) if kind in H1_KINDS else rest


def formatted_text(kind: str, fm: dict, raw: str) -> str:
    """What fmt writes: the canonical head, then the rest of the file byte-for-byte."""
    rest = split_head(raw, kind, fm.get("id", "")).strip("\n")
    if not rest:
        return canonical_head(kind, fm)
    return canonical_head(kind, fm) + ("\n" if kind in H1_KINDS else "") + rest + "\n"


def new_node_text(kind: str, fm: dict, skeleton: str) -> str:
    return canonical_head(kind, fm) + "\n" + skeleton.strip("\n") + "\n"


def _arc_progress(graph: Graph, arc: Arc) -> tuple[int, int, str]:
    members = [graph.todos[i] for i in graph.member_ids(arc) if i in graph.todos]
    done = sum(1 for t in members if t.fm["status"] in TODO_STATUS_CLOSED)
    exps = [graph.exps[i] for i in graph.exp_ids(arc) if i in graph.exps]
    latest = max(exps, key=lambda e: (e.fm["date"], e.id), default=None)
    last = f"{latest.id} ({latest.fm['date']})" if latest else "—"
    return done, len(members), last


GENERATED = "> Generated by `python3 scripts/labledger.py render` on this clone — views/ is not in git; do not edit."


def render_arcs_table(graph: Graph, rel_prefix: str) -> list[str]:
    lines = ["| Arc | Title | Status | Members (closed / all) | Experiments | Latest experiment |", "|---|---|---|---:|---:|---|"]
    for arc in graph.arc_order():
        done, total, last = _arc_progress(graph, arc)
        lines.append(f"| [{arc.id}]({rel_prefix}{arc.id}.md) | {_clip(arc.fm['title'], 100)} | {arc.fm['status']} | "
                     f"{done} / {total} | {len(graph.exp_ids(arc))} | {last} |")
    return lines


def render_index(graph: Graph) -> str:
    todos = graph.todo_order()
    lines = ["# TODO index", "",
             f"{GENERATED} The source of truth is the frontmatter of each `areas/<area>/<ID>.md`.", "",
             "## TODOs", "", "| ID | Title | Priority | Status | Area |", "|---|---|---|---|---|"]
    for t in todos:
        lines.append(f"| [{t.id}](../{t.rel}) | {t.fm['title']} | {t.fm['priority']} | {t.fm['status']} | {t.area} |")
    lines += ["", f"**Total**: {len(todos)} TODOs", "", "## By status", "", "| Status | Count |", "|---|---:|"]
    status = Counter(t.fm["status"] for t in todos)
    lines += [f"| {s} | {status[s]} |" for s in TODO_STATUSES if status[s]]
    lines += ["", "## By priority", "", "| Priority | Count |", "|---|---:|"]
    pri = Counter(t.fm["priority"] for t in todos)
    lines += [f"| {p} | {pri[p]} |" for p in PRIORITIES if pri[p]]
    if graph.arcs:
        lines += ["", f"## Arcs ({len(graph.arcs)})", "",
                  "> Work that spans several TODOs and experiments (`arcs/ARC-NNN.md`). The members of the active arcs are in "
                  "[`active.md`](active.md).", ""]
        lines += render_arcs_table(graph, "../arcs/")
    return "\n".join(lines) + "\n"


def render_area_items(graph: Graph, area: str, prefix: str) -> list[str]:
    todos = [t for t in graph.todo_order() if t.area == area]
    if not todos:
        return ["_No TODOs._"]
    lines = ["| ID | Title | Priority | Status |", "|---|---|---|---|"]
    for t in todos:
        lines.append(f"| [{t.id}]({prefix}{t.id}.md) | {t.fm['title']} | {t.fm['priority']} | {t.fm['status']} |")
    return lines


def render_area_investigations(graph: Graph, area: str, prefix: str) -> list[str]:
    invs = [i for i in graph.inv_order() if i.area == area]
    if not invs:
        return ["_No investigations._"]
    lines = ["| Date | Document | Status | Summary | Affects |", "|---|---|---|---|---|"]
    for inv in reversed(invs):
        targets = ", ".join(a for a, _ in inv.affects()) or "—"
        lines.append(f"| {inv.fm['date']} | [{inv.fm['title']}]({prefix}{inv.path.name}) `inv:{inv.id}` | {inv.fm['status']} | "
                     f"{_clip(inv.fm.get('summary', ''), 200)} | {_clip(targets, 120)} |")
    return lines


def render_area_view(graph: Graph, ar: Area) -> str:
    prefix = f"../../areas/{ar.id}/"
    lines = [f"# {ar.id} — {ar.title}", "",
             f"{GENERATED} The area's description, decisions and issues are in [`areas/{ar.id}/`]({prefix}README.md); "
             f"`python3 scripts/labledger.py show {ar.id}` computes the same tables.", "",
             "## TODOs", ""]
    lines += render_area_items(graph, ar.id, prefix)
    lines += ["", "## Investigations", ""]
    lines += render_area_investigations(graph, ar.id, prefix)
    return "\n".join(lines) + "\n"


def render_areas_view(graph: Graph) -> str:
    lines = ["# Areas and arcs", "",
             f"{GENERATED} One area's tables are in `views/areas/<area>.md`, every TODO in [`index.md`](index.md).", "",
             "## Areas", "", "| Area | Views | TODOs | By priority | Open / closed | Investigations |", "|---|---|---:|---|---|---:|"]
    for ar in graph.area_order():
        todos = [t for t in graph.todos.values() if t.area == ar.id]
        pri = Counter(t.fm["priority"] for t in todos)
        dist = ", ".join(f"{p} {pri[p]}" for p in PRIORITIES if pri[p])
        open_n = sum(1 for t in todos if t.fm["status"] in TODO_STATUS_OPEN)
        n_inv = sum(1 for i in graph.invs.values() if i.area == ar.id)
        lines.append(f"| {ar.title} | [`{ar.id}`](areas/{ar.id}.md) · [README](../areas/{ar.id}/README.md) | {len(todos)} | {dist} | "
                     f"{open_n} / {len(todos) - open_n} | {n_inv} |")
    lines += ["", "## Arcs", ""]
    if not graph.arcs:
        lines.append("_No arcs yet — `python3 scripts/labledger.py new arc \"<title>\"` makes one._")
    else:
        lines += render_arcs_table(graph, "../arcs/")
    return "\n".join(lines) + "\n"


def render_active(graph: Graph) -> str:
    lines = ["# Active arcs", "",
             f"{GENERATED} The source of truth is the frontmatter of `arcs/ARC-NNN.md` and of its members; an arc's steps and "
             "exit criteria are in its body.", ""]
    active = [a for a in graph.arc_order() if a.fm["status"] == "active"]
    others = [a for a in graph.arc_order() if a.fm["status"] != "active"]
    if not active:
        lines.append("_No active arcs._")
    for arc in active:
        lines.append(f"## [{arc.id}](../arcs/{arc.id}.md) — {arc.fm['title']}")
        lines.append("")
        meta = [f"created {arc.fm['created']}", f"updated {arc.fm['updated']}"]
        if arc.fm.get("areas"):
            meta.append("areas " + ", ".join(arc.fm["areas"]))
        if arc.fm.get("venue"):
            meta.append(f"venue {arc.fm['venue']}")
        lines.append("_" + " · ".join(meta) + "_")
        if arc.fm.get("status_note"):
            lines.append("")
            lines.append(f"> {arc.fm['status_note']}")
        members = [graph.todos[i] for i in graph.member_ids(arc) if i in graph.todos]
        lines.append("")
        if members:
            lines += ["| Member | Status | Priority | Updated | Title |", "|---|---|---|---|---|"]
            for t in members:
                lines.append(f"| [{t.id}](../{t.rel}) | {t.fm['status']} | {t.fm['priority']} | {t.fm['updated']} | {_clip(t.fm['title'], 90)} |")
        else:
            lines.append("_No member TODOs._")
        exps = sorted((graph.exps[i] for i in graph.exp_ids(arc) if i in graph.exps), key=lambda e: (e.fm["date"], e.id), reverse=True)
        lines.append("")
        if exps:
            lines += [f"| Latest experiments (of {len(exps)}) | Date | Status | Summary |", "|---|---|---|---|"]
            for e in exps[:ACTIVE_EXP_ROWS]:
                lines.append(f"| [{e.id}](../{e.rel}) | {e.fm['date']} | {e.fm['status']} | {_clip(e.fm['summary'], 180)} |")
        else:
            lines.append("_No experiments._")
        lines.append("")
    if others:
        lines += ["## Other arcs (done / parked)", ""]
        lines += render_arcs_table(graph, "../arcs/")
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def render_papers(graph: Graph) -> str:
    papers = [a for a in graph.arc_order() if a.fm.get("venue")]
    lines = ["# Papers", "",
             f"{GENERATED} The arcs with a `venue` (a paper, a talk). Each arc's body says which experiments it uses and where "
             "the manuscript lives; `new exp \"<title>\" --arc ARC-NNN` adds an experiment to one.", "",
             "| Arc | Title | Venue | Status | Experiments | Location |", "|---|---|---|---|---|---|"]
    for arc in papers:
        exps = graph.exp_ids(arc)
        span = f"{exps[0]}…{exps[-1]} ({len(exps)})" if len(exps) > 1 else (exps[0] if exps else "—")
        loc = f"`{arc.fm['location']}`" if arc.fm.get("location") else "—"
        lines.append(f"| [{arc.id}](../arcs/{arc.id}.md) | {arc.fm['title']} | {arc.fm['venue']} | {arc.fm['status']} | {span} | {loc} |")
    if not papers:
        lines.append("| — | (no arc has a venue) | | | | |")
    return "\n".join(lines) + "\n"


def render_log(graph: Graph) -> str:
    exps = graph.exp_order()
    lines = ["# Experiment log", "",
             f"{GENERATED} One line per experiment; the source of truth is the frontmatter of each `experiments/EXP-NNNN.md`. "
             "`python3 scripts/labledger.py new exp \"<title>\"` starts one (the number is reserved on the upstream; gaps are allowed).", "",
             "| EXP | Date | Status | Model | Title | Summary | Commit | Affects / arc / state |", "|---|---|---|---|---|---|---|---|"]
    for e in exps:
        affects = ", ".join(a for a, _ in e.affects()) or "—"
        arcs = ", ".join(a.id for a in graph.arcs_of(e.id))
        state = e.fm.get("state", "n/a")
        link = f"[{e.id}](../experiments/{e.id}.md)"
        tail = f"{affects}" + (f" · arc: {arcs}" if arcs else "") + f" · state: {_clip(state, 60)}"
        lines.append(f"| {link} | {e.fm['date']} | {e.fm['status']} | {_clip(e.fm.get('model', ''), 80)} | {_clip(e.fm['title'], 120)} | "
                     f"{_clip(e.fm['summary'], SUMMARY_MAX)} | {_clip(e.fm.get('commit', ''), 60)} | {tail} |")
    lines += ["", f"**Total**: {len(exps)} experiments · status: " + ", ".join(f"{s} {n}" for s, n in Counter(e.fm['status'] for e in exps).most_common())]
    return "\n".join(lines) + "\n"


def render_state_table(name: str, text: str) -> str:
    rows = [r for r in csv.reader(io.StringIO(text)) if r]
    lines = [f"# state/{name}", "", f"{GENERATED} The source of truth is [`state/{name}`](../../state/{name}).", ""]
    if rows:
        lines += ["| " + " | ".join(_clip(c, 200) for c in rows[0]) + " |", "|" + "---|" * len(rows[0])]
        lines += ["| " + " | ".join(_clip(c, 200) for c in r) + " |" for r in rows[1:]]
    return "\n".join(lines) + "\n"


def planned_views(graph: Graph, root: Path) -> dict[Path, str]:
    """Every rendered table and list, as {path: text}. They live in views/ on each clone and are never committed."""
    views = root / VIEWS
    out = {views / "index.md": render_index(graph), views / "areas.md": render_areas_view(graph), views / "log.md": render_log(graph),
           views / "papers.md": render_papers(graph), views / "active.md": render_active(graph)}
    for ar in graph.area_order():
        out[views / "areas" / f"{ar.id}.md"] = render_area_view(graph, ar)
    for name, text in graph.states.items():
        if name.endswith(".csv"):
            out[views / "state" / f"{name[:-4]}.md"] = render_state_table(name, text)
    return out


def stale_views(graph: Graph, root: Path) -> tuple[dict[Path, str], list[Path], list[Path]]:
    """(planned views, views to (re)write, leftover views of areas or state tables that no longer exist)."""
    views = planned_views(graph, root)
    changed = [p for p, text in views.items() if not p.is_file() or p.read_text(encoding="utf-8") != text]
    leftover = [p for d in ("areas", "state") for p in sorted((root / VIEWS / d).glob("*.md")) if p not in views]
    return views, changed, leftover


def stale_view_lines(graph: Graph, root: Path) -> list[str]:
    """What `render --check` (and validate's note) lists: a `- views/…` line per view `render` would rewrite or remove."""
    _, changed, leftover = stale_views(graph, root)
    return [f"- {p.relative_to(root).as_posix()}" for p in changed + leftover]


def run_render(root: Path, check: bool) -> int:
    graph = load_graph(root)
    if graph.problems:
        print("render aborted — fix these first:")
        for p in graph.problems:
            print(f"- {p}")
        return 1
    if check:
        stale = stale_view_lines(graph, root)
        if stale:
            print(f"{VIEWS}/ is stale (run `python3 scripts/labledger.py render`):", *stale, sep="\n")
            return 1
        print(f"{VIEWS}/ is up to date")
        return 0
    views, changed, leftover = stale_views(graph, root)
    for p in changed:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(views[p], encoding="utf-8")
    for p in leftover:
        p.unlink()
    n = len(changed) + len(leftover)
    print(f"rendered {n} view(s)" if n else "nothing to render")
    return 0


def cmd_render(args) -> int:
    return run_render(args.root, args.check)


# ───────────────────────────── fmt ─────────────────────────────

def planned_formats(graph: Graph) -> dict[Path, str]:
    """Node files whose head is not canonical, as {path: formatted text}."""
    out: dict[Path, str] = {}
    for kind, nodes in (("todo", graph.todos), ("exp", graph.exps), ("arc", graph.arcs), ("inv", graph.invs), ("area", graph.areas)):
        for n in nodes.values():
            text = formatted_text(kind, n.fm, n.raw)
            if text != n.raw:
                out[n.path] = text
    for kind, entries in (("dec", graph.decisions), ("issue", graph.issues)):
        for e in entries:
            text = entry_text(kind, e.fm, split_head(e.raw, kind, ""))
            if text != e.raw:
                out[graph.root / e.file] = text
    return out


def cmd_fmt(args) -> int:
    root = args.root
    graph = load_graph(root)
    if graph.problems:
        print("fmt aborted — fix these first:")
        for p in graph.problems:
            print(f"- {p}")
        return 1
    changed = planned_formats(graph)
    if args.check:
        if changed:
            print("record heads are not canonical (run `python3 scripts/labledger.py fmt`):")
            for p, text in changed.items():
                print(f"- {p.relative_to(root).as_posix()}: {first_difference(p.read_text(encoding='utf-8'), text)}")
            return 1
        print("node heads are canonical")
        return 0
    for p, text in changed.items():
        p.write_text(text, encoding="utf-8")
    print(f"formatted {len(changed)} file(s)" if changed else "nothing to format")
    return 0


# ───────────────────────────── validate ─────────────────────────────
# One rule set per node kind. validate runs check_node over every loaded node; the write commands (new … / set /
# promote) run it over the node they are about to write and refuse to write one that validate would reject.
# check_file covers what only a file on disk can violate; validate_cross covers rules between files.

REQUIRED = {"todo": TODO_REQUIRED, "exp": EXP_REQUIRED, "arc": ARC_REQUIRED, "inv": INV_REQUIRED, "area": AREA_REQUIRED}
STATUS_VOCAB = {"todo": TODO_STATUSES, "exp": EXP_STATUSES, "arc": ARC_STATUSES, "inv": INV_STATUSES}
DATE_FIELDS = {"todo": ("created", "updated"), "exp": ("date",), "arc": ("created", "updated"), "inv": ("date", "updated"), "area": ()}
LINK_TAGS = {"from": ("issue", "inv", "dec", "arc", "user"), "resolves": ("issue", "inv", "dec", "arc")}  # other link fields: IDs only


# ───────────────────────────── data, links, state tables ─────────────────────────────

DATA_EXTENSIONS = (".csv", ".tsv", ".json")
DATA_NA_RE = re.compile(r"^n/a\s*\((.*\S.*)\)$", re.I)  # an experiment without numbers: `n/a (why)`
DATA_ENTRY_RE = re.compile(r"^([^\s:][^:]*?):\s+(\S.*)$")  # `<file>: <what one row is>`
UNIT_RE = re.compile(r"\[[^\[\]]+\]$")  # a column name ends with its unit: `latency[ms]`, `count[1]`
NUMBER_RE = re.compile(r"^[-+]?(\d+(\.\d*)?|\.\d+)([eE][-+]?\d+)?$")
_NUM = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
SEVERAL_VALUES_RE = re.compile(rf"^[\[(]?\s*{_NUM}(?:\s*[,;]\s*{_NUM})+\s*[\])]?$|^\d+\s*/\s*\d+$")  # 1,2,3 · [1, 2] · (0, 0) · 1;2 · 1,786,215 · 0/3024
LINK_RE = re.compile(r"\]\(\s*<?([^()<>\s]+)>?(?:\s+\"[^\"]*\")?\s*\)")
FENCE_RE = re.compile(r"^(```|~~~).*?^\1", re.M | re.S)
CODE_SPAN_RE = re.compile(r"`[^`\n]*`")
STATE_COLUMNS = ("item", "value", "unit", "kind", "exp", "commit", "measured_at")  # every state table has these
STATE_OPTIONAL = ("source", "note")  # every other column is a condition of the value
STATE_KINDS = ("measured", "estimate")


def known_files(root: Path) -> set[str] | None:
    """The files in git's index at `root`, or None when `root` is not the top of a git work tree."""
    r = _run(root, "rev-parse", "--show-toplevel")
    if r.returncode or Path(r.stdout.strip()).resolve() != root.resolve():
        return None
    return {f for f in _git(root, "ls-files", "-z").split("\0") if f}


def body_links(text: str) -> list[str]:
    """The relative link targets of a Markdown text outside code (no URL, mail address or bare #anchor), anchors cut."""
    out = []
    for m in LINK_RE.finditer(CODE_SPAN_RE.sub("", FENCE_RE.sub("", text))):
        target = m.group(1)
        if target.startswith("#") or re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", target):
            continue
        out.append(target.split("#", 1)[0].split("?", 1)[0])
    return [t for t in out if t]


def link_targets(rel: str, text: str) -> list[tuple[str, str]]:
    """(target as written, the repo path it points at — or "/…" absolute, "../…" outside) for each link of a file's text."""
    base = PurePosixPath(rel).parent
    out = []
    for target in body_links(text):
        path = target if target.startswith("/") else os.path.normpath(str(base / target)).replace(os.sep, "/")
        out.append((target, path))
    return out


def link_problems(graph: Graph, rel: str, text: str) -> list[str]:
    """A record's relative links point at files or folders of the ledger: not outside it, not into views/ (not in git)."""
    out = []
    for target, path in link_targets(rel, text):
        if path.startswith("/"):
            out.append(f"{rel}: link `{target}` is an absolute path — link within the ledger with a relative path")
        elif path == ".." or path.startswith("../"):
            out.append(f"{rel}: link `{target}` leaves the ledger — name another repository's file in text (repository, commit, path)")
        elif path == VIEWS or path.startswith(VIEWS + "/"):
            out.append(f"{rel}: link `{target}` points into {VIEWS}/, which is not in git — name the command (`render`, `show`) instead")
        elif not (graph.has_file(path) or graph.has_dir(path)):
            out.append(f"{rel}: link `{target}` points at {path}, which is not in the ledger")
    return out


def table_problems(path: Path) -> list[str]:
    """A data file as it stands on disk: JSON parses; a CSV or TSV has a header row, as many fields on every row, one
    plain value per cell, and the unit at the end of the name of every column that holds only numbers."""
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return ["not UTF-8 text"]
    if path.suffix.lower() == ".json":
        try:
            json.loads(text)
        except json.JSONDecodeError as exc:
            return [f"not JSON ({exc})"]
        return []
    rows = [r for r in csv.reader(io.StringIO(text), delimiter="\t" if path.suffix.lower() == ".tsv" else ",") if r]
    if not rows:
        return ["empty — a table starts with a header row"]
    header, out = [h.strip() for h in rows[0]], []
    if any(not h or NUMBER_RE.match(h) for h in header):
        out.append("the first row must be a header: a name for every column")
    elif len(set(header)) != len(header):
        out.append("two columns have the same name")
    for n, row in enumerate(rows[1:], 2):
        if len(row) != len(header):
            out.append(f"row {n} has {len(row)} fields, the header {len(header)}")
            break
    for j, name in enumerate(header):
        cells = [r[j].strip() for r in rows[1:] if j < len(r) and r[j].strip()]
        if cells and all(NUMBER_RE.match(c) for c in cells) and name and not UNIT_RE.search(name):
            out.append(f"column `{name}` holds numbers: end its name with their unit in brackets, like `{name}[<unit>]` (`[ms]`, `[MB]`) — `[1]` for a "
                       f"count or a ratio, `[id]` for numbers that name things (a run, a seed)")
        several = next((c for c in cells if SEVERAL_VALUES_RE.match(c)), None)
        if several:
            out.append(f"column `{name}` has `{several}` in one cell — one plain value per cell (no lists, fractions or "
                       f"thousands separators)")
    return out


def data_problems(graph: Graph, exp) -> list[str]:
    """An EXP's `data`: `<file>: <what one row is>` entries — each a CSV, TSV or JSON file in experiments/attachments/
    EXP-NNNN/, in the ledger and linked from the body — or the single entry `n/a (why)`. A final experiment has it."""
    entries, rel = exp.fm.get("data") or [], exp.rel
    if not isinstance(entries, list):
        return []
    if not entries:
        return [f"{rel}: a final experiment names the files its numbers come from — "
                f"`set {exp.id} --data \"results.csv: one row per run\"`, or `--data \"n/a (why)\"`"] if exp.fm.get("status") == "final" else []
    if any(DATA_NA_RE.match(e.strip()) for e in entries):
        return [f"{rel}: `data` is either `n/a (why)` or a list of files, not both"] if len(entries) > 1 else []
    linked = {path for _, path in link_targets(rel, exp.body)}
    out = []
    for entry in entries:
        m = DATA_ENTRY_RE.match(entry.strip())
        if m is None:
            out.append(f"{rel}: `data` entry `{entry}` is neither `<file>: <what one row is>` nor `n/a (why)`")
            continue
        name = m.group(1).strip()
        path = f"{ATTACHMENTS}{exp.id}/{name}"
        if name.startswith("/") or ".." in PurePosixPath(name).parts:
            out.append(f"{rel}: `data` file `{name}` must be a path inside {ATTACHMENTS}{exp.id}/")
        elif PurePosixPath(name).suffix.lower() not in DATA_EXTENSIONS:
            out.append(f"{rel}: `data` file `{name}` must be CSV, TSV or JSON — a table stays readable and checkable outside an archive")
        elif not graph.has_file(path):
            out.append(f"{rel}: `data` file {path} is not in the ledger")
        else:
            if path not in linked:
                out.append(f"{rel}: the body does not link `data` file {name} — link it where its numbers are used "
                           f"([{name}](attachments/{exp.id}/{name}))")
            if (graph.root / path).is_file():
                out += [f"{path}: {p}" for p in table_problems(graph.root / path)]
    return out


def state_table_problems(graph: Graph) -> list[str]:
    """state/*.csv: one row per current value, with the columns STATE_COLUMNS (+ source, note, and condition columns).
    A row's EXP is final and names the file in its `state`; an item under the same conditions appears once in all of state/."""
    out, seen = [], {}
    for name, text in graph.states.items():
        if not name.endswith(".csv"):
            continue
        where = f"state/{name}"
        rows = [r for r in csv.reader(io.StringIO(text)) if r]
        if not rows:
            out.append(f"{where}: empty — a header row comes first")
            continue
        header = [h.strip() for h in rows[0]]
        missing = [c for c in STATE_COLUMNS if c not in header]
        if missing:
            out.append(f"{where}: missing column(s) {', '.join(missing)} (a state table has {', '.join(STATE_COLUMNS)})")
            continue
        conditions = [h for h in header if h not in STATE_COLUMNS + STATE_OPTIONAL]
        for n, row in enumerate(rows[1:], 2):
            if len(row) != len(header):
                out.append(f"{where}:{n}: {len(row)} fields, the header {len(header)}")
                continue
            r = dict(zip(header, (c.strip() for c in row)))
            exp = graph.exps.get(r["exp"])
            if exp is None:
                out.append(f"{where}:{n}: exp `{r['exp']}` is no experiment")
            elif exp.fm.get("status") != "final":
                out.append(f"{where}:{n}: {exp.id} is {exp.fm.get('status')} — a current value comes from a final experiment")
            elif name not in (state_claims(exp.fm.get("state")) or []):
                out.append(f"{where}:{n}: {exp.id}'s `state` does not name {name} — `set {exp.id} --state \"{name}\"`")
            if not r["item"] or not r["value"]:
                out.append(f"{where}:{n}: item and value are required")
            if r["kind"] not in STATE_KINDS:
                out.append(f"{where}:{n}: kind `{r['kind']}` is not {' or '.join(STATE_KINDS)}")
            if not DATE_RE.match(r["measured_at"]):
                out.append(f"{where}:{n}: measured_at `{r['measured_at']}` is not YYYY-MM-DD")
            if not r["commit"]:
                out.append(f"{where}:{n}: commit is required — the code version the value was measured on")
            if r.get("source") and not graph.has_file(f"{ATTACHMENTS}{r['source']}"):
                out.append(f"{where}:{n}: source {ATTACHMENTS}{r['source']} is not in the ledger")
            key = (r["item"], *(f"{c}={r[c]}" for c in conditions))
            if key in seen:
                out.append(f"{where}:{n}: {r['item']} under the same conditions is also {seen[key]} — keep one current value")
            else:
                seen[key] = f"{where}:{n}"
    return out


def link_problem(graph: Graph, ref: str, tags: tuple[str, ...] = ()) -> str | None:
    """Why `ref` cannot be linked: a bare ID must be a TODO or EXP; `tags` are the tag kinds accepted here."""
    if ref == "user":
        return None if "user" in tags else "entry `user` is only allowed in `from`"
    found = resolve_address(graph, ref)
    if NODE_ID_RE.fullmatch(ref):
        if found and found[0] == "arc":
            return f"entry `{ref}` must be written `arc:{ref}`"
        return None if found and found[0] in ("todo", "exp") else f"points at unknown {ref}"
    m = TAG_RE.match(ref)
    if m is None or m.group(1) not in tags:
        accepted = "/".join(f"{k}:" if k != "user" else k for k in tags)
        return f"entry `{ref}` is not an ID" if not tags else f"entry `{ref}` is neither an ID nor {accepted}"
    if found is None:
        kind, target = m.groups()
        what = {"issue": "issue slug", "inv": "inv (areas/*/<id>.md with type: inv)",
                "dec": "decision (decisions/<date>-<slug>.md or areas/*/decisions/)", "arc": "arc"}[kind]
        return f"points at unknown {what} `{target}`"
    return None


def check_node(graph: Graph, kind: str, node) -> list[str]:
    """Problems with one node's frontmatter: keys, required fields, vocab, dates, links into the graph."""
    fm, rel = node.fm, node.rel
    out: list[str] = []
    order, lists = KEY_ORDER[kind], LIST_FIELDS[kind]
    for key, value in fm.items():
        if key not in order:
            out.append(f"{rel}: unknown key `{key}` (allowed: {', '.join(order)})")
        elif key in lists and not isinstance(value, list):
            out.append(f"{rel}: `{key}` must be a list")
        elif key not in lists and isinstance(value, list):
            out.append(f"{rel}: `{key}` must be a single value — quote it if it starts with [ or contains a comma")
    for key in REQUIRED[kind]:
        if key not in fm or fm[key] in ("", None):
            out.append(f"{rel}: missing `{key}`")
    if fm.get("type") != kind:
        out.append(f"{rel}: type must be {kind}")
    if kind in STATUS_VOCAB and fm.get("status") not in STATUS_VOCAB[kind]:
        out.append(f"{rel}: status `{fm.get('status')}` not in {STATUS_VOCAB[kind]}")
    for key in DATE_FIELDS[kind]:
        if key in fm and not DATE_RE.match(str(fm[key])):
            out.append(f"{rel}: `{key}` must be YYYY-MM-DD")
    if "created" in fm and "updated" in fm and str(fm["updated"]) < str(fm["created"]):
        out.append(f"{rel}: updated < created")
    if kind == "inv" and "updated" in fm and str(fm["updated"]) < str(fm.get("date", "")):
        out.append(f"{rel}: updated < date")
    status, note = fm.get("status"), fm.get("status_note")
    if note and len(str(note)) > STATUS_NOTE_MAX:
        out.append(f"{rel}: status_note is {len(str(note))} chars, over {STATUS_NOTE_MAX} — say why the status is what it is; the results "
                   "stay in their records")
    if kind == "todo":
        if fm.get("priority") not in PRIORITIES:
            out.append(f"{rel}: priority `{fm.get('priority')}` not in {PRIORITIES}")
        if fm.get("area") not in graph.areas:
            out.append(f"{rel}: area `{fm.get('area')}` is not an area (areas/<area>/README.md)")
        if status == "dropped" and not note:
            out.append(f"{rel}: dropped needs a status_note saying why")
        for key in TODO_LIST_FIELDS:
            for ref in (fm.get(key) if isinstance(fm.get(key), list) else []):
                problem = link_problem(graph, ref, LINK_TAGS.get(key, ()))
                if problem:
                    out.append(f"{rel}: `{key}` {problem}")
        for ref in node.links("supersedes"):
            target = graph.todos.get(ref)
            if target and target.fm.get("status") not in TODO_STATUS_CLOSED:
                out.append(f"{rel}: supersedes {ref} but {ref} is still `{target.fm.get('status')}` (close it as done/dropped)")
    elif kind == "exp":
        if len(str(fm.get("summary", ""))) > SUMMARY_MAX:
            out.append(f"{rel}: summary is {len(str(fm['summary']))} chars, over {SUMMARY_MAX} — move detail into the body")
        if fm.get("host") and not HOST_RE.match(str(fm["host"])):
            out.append(f"{rel}: host `{fm['host']}` is not a host name (the server whose local paths the body names)")
        claims = state_claims(fm.get("state"))
        if claims is None:
            out.append(f"{rel}: `state` must name the state/ file(s) whose rows this experiment is the source of "
                       f"({', '.join(graph.states) or 'state/ holds none yet'}), or n/a")
        for name in claims or []:
            if name not in graph.states:
                out.append(f"{rel}: `state` names {name}, but state/{name} is not in this tree (a new state table is committed "
                           f"together with its experiment: `commit -m … {fm.get('id', rel)} state/{name}`)")
        if isinstance(fm.get("affects", []), list):
            try:
                for ref, _ in node.affects():
                    if ref not in graph.todos and ref not in graph.exps:
                        out.append(f"{rel}: affects unknown {ref}")
            except FrontmatterError as exc:
                out.append(str(exc))
        if fm.get("arc") and fm["arc"] not in graph.arcs:
            out.append(f"{rel}: arc `{fm['arc']}` does not exist (arcs/{fm['arc']}.md)")
        if status == "superseded" and not fm.get("superseded_by"):
            out.append(f"{rel}: superseded needs `superseded_by`")
        if isinstance(fm.get("data"), list) or status == "final":
            out += data_problems(graph, node)
        if fm.get("superseded_by") and fm["superseded_by"] not in graph.exps:
            out.append(f"{rel}: superseded_by unknown {fm['superseded_by']}")
    elif kind == "arc":
        for key, pool, what in (("members", graph.todos, "TODO"), ("exps", graph.exps, "EXP")):
            for ref in (fm.get(key) if isinstance(fm.get(key), list) else []):
                if ref not in pool:
                    out.append(f"{rel}: `{key}` points at unknown {what} {ref}")
        for area in (fm.get("areas") if isinstance(fm.get("areas"), list) else []):
            if area not in graph.areas:
                out.append(f"{rel}: areas entry `{area}` is not an area (areas/{area}/README.md)")
        if status == "parked" and not note:
            out.append(f"{rel}: parked needs a status_note saying what it waits for")
    elif kind == "inv":
        if len(str(fm.get("summary", ""))) > SUMMARY_MAX:
            out.append(f"{rel}: summary is {len(str(fm['summary']))} chars, over {SUMMARY_MAX} — move detail into the body")
        if fm.get("area") not in graph.areas:
            out.append(f"{rel}: area `{fm.get('area')}` is not an area (areas/<area>/README.md)")
        if isinstance(fm.get("affects", []), list):
            try:
                for ref, _ in node.affects():
                    if ref.startswith("issue:"):
                        if ref[6:] not in graph.issues_by_slug:
                            out.append(f"{rel}: affects unknown issue slug `{ref[6:]}`")
                    elif ref not in graph.todos:
                        out.append(f"{rel}: affects unknown TODO {ref}")
            except FrontmatterError as exc:
                out.append(str(exc))
    elif kind == "area":
        if fm.get("id") != node.path.parent.name:
            out.append(f"{rel}: id `{fm.get('id')}` != directory {node.path.parent.name}")
        if "prefix" in fm and not PREFIX_RE.match(str(fm["prefix"])):
            out.append(f"{rel}: prefix `{fm['prefix']}` must be capital letters and dashes, like LAB or LAB-SIM")
        if "order" in fm and not str(fm["order"]).isdigit():
            out.append(f"{rel}: order must be an integer")
    return out


def first_difference(have: str, want: str) -> str:
    """The first line where a file's head differs from the head fmt writes."""
    a, b = have.splitlines(), want.splitlines()
    for i, (x, y) in enumerate(zip(a, b), 1):
        if x != y:
            return f"line {i} is `{x[:80]}`, fmt writes `{y[:80]}`"
    return f"line {min(len(a), len(b)) + 1} is where they part"


def check_file(kind: str, node) -> list[str]:
    """What only the file on disk can violate: the canonical head."""
    head = canonical_head(kind, node.fm)
    if not node.raw.startswith(head):
        return [f"{node.rel}: frontmatter/H1 is not canonical ({first_difference(node.raw, head)}) — run `python3 scripts/labledger.py fmt`"]
    return []


def validate_cross(graph: Graph, root: Path) -> list[str]:
    """Rules between files: settings and templates, state/ citations, close decisions, canonical entry heads, stray files."""
    out: list[str] = []
    for key in graph.settings:
        if key not in SETTINGS_KEYS:
            out.append(f"{SETTINGS_FILE}: unknown key `{key}` (allowed: {', '.join(SETTINGS_KEYS)})")
    if graph.settings.get("timezone"):
        try:
            ZoneInfo(str(graph.settings["timezone"]))
        except (ZoneInfoNotFoundError, ValueError):
            out.append(f"{SETTINGS_FILE}: timezone `{graph.settings['timezone']}` is not an IANA time zone name here (like Europe/Berlin)")
    if "commit_prefix" in graph.settings and not SLUG_RE.match(str(graph.settings["commit_prefix"])):
        out.append(f"{SETTINGS_FILE}: commit_prefix `{graph.settings['commit_prefix']}` must be one word of [A-Za-z0-9._-]")
    for key in sorted(set(graph.labels) - set(LABELS)):
        out.append(f"{TEMPLATES_DIR}/labels.yml: unknown label `{key}` (allowed: {', '.join(LABELS)})")
    if "{evidence}" not in graph.labels["closed_by"]:
        out.append(f"{TEMPLATES_DIR}/labels.yml: closed_by must hold {{evidence}}, where the close decision names its EXPs")
    templates = root / TEMPLATES_DIR
    known = {f"{kind}.md" for kind in SKELETONS} | {"labels.yml"}
    for path in sorted(templates.iterdir()) if templates.is_dir() else []:
        if path.name not in known and not path.name.startswith("."):
            out.append(f"{TEMPLATES_DIR}/{path.name}: not a template (known: {', '.join(sorted(known))})")
    for e in graph.exps.values():
        for name in state_claims(e.fm.get("state")) or []:
            if name in graph.states and not re.search(rf"\b{re.escape(e.id)}\b", graph.states[name]):
                out.append(f"{e.rel}: `state` says it updated {name} but state/{name} never cites {e.id} — "
                           f"cite it in the row, or set `state: n/a (...)` if a later experiment overwrote the row")
    closed = {(Path(d.file).parent.as_posix(), d.slug) for d in graph.decisions}
    for t in graph.todos.values():
        if t.fm.get("status") in TODO_STATUS_CLOSED and (f"areas/{t.area}/{DEC_DIR}", f"close-{t.id}") not in closed:
            out.append(f"{t.rel}: {t.fm['status']} but areas/{t.area}/{DEC_DIR}/ has no `<date>-close-{t.id}.md` decision — "
                       f"`set {t.id} --status {t.fm['status']} --note ...` writes it")
    for kind, entries in (("dec", graph.decisions), ("issue", graph.issues)):
        for e in entries:
            head = emit_frontmatter(e.fm, ENTRY_KEY_ORDER[kind])
            if not e.raw.startswith(head):
                out.append(f"{e.file}: frontmatter is not canonical ({first_difference(e.raw, head)}) — run `python3 scripts/labledger.py fmt`")
    for path in (root / "experiments").glob("EXP-*"):
        if path.is_file() and not EXP_FILE_RE.match(path.name):
            out.append(f"experiments/{path.name}: not an EXP-NNNN.md file — move attachments under experiments/attachments/")
    for name, text in graph.states.items():
        for m in EXP_ID_RE.finditer(text):
            if m.group(0) not in graph.exps:
                out.append(f"state/{name}: unknown {m.group(0)}")
        for m in EXP_ABBREV_RE.finditer(text):
            out.append(f"state/{name}: `{m.group(0)}` — write every ID in full (EXP-NNNN / EXP-NNNN) so the citation is checkable")
    out += state_table_problems(graph)
    texts = [(n.rel, n.body) for table in (graph.todos, graph.exps, graph.arcs, graph.invs, graph.areas) for n in table.values()]
    texts += [(e.file, split_head(e.raw, kind, "")) for kind, entries in (("dec", graph.decisions), ("issue", graph.issues))
              for e in entries]
    texts += [(f"state/{name}", text) for name, text in graph.states.items() if name.endswith(".md")]
    for rel, text in texts:
        out += link_problems(graph, rel, text)
    return out


NODE_TABLES = ("todo", "exp", "arc", "inv", "area")


def validate_graph(graph: Graph, root: Path) -> list[str]:
    problems = list(graph.problems)
    for kind in NODE_TABLES:
        for node in getattr(graph, kind + "s").values():
            problems += check_node(graph, kind, node) + check_file(kind, node)
    return problems + validate_cross(graph, root)


def refuse(problems: list[str]) -> int:
    """The write commands' answer to a node that validate would reject: say why, write nothing, exit 2."""
    print("refused — the record would not validate:", file=sys.stderr)
    for p in problems:
        print(f"- {p}", file=sys.stderr)
    return 2


def tree_problems(root: Path, committed: bool = False, tracked: set[str] | None = None) -> tuple[Graph, list[str]]:
    """validate's verdict on a tree on disk. `committed`: the tree is a commit's (a hook's checkout, CI) and must not
    carry views/, which each clone renders for itself. `tracked`: the files of that commit, when the tree checked out
    leaves some of them (the attachments) off disk."""
    graph = load_graph(root)
    if tracked is not None:
        graph.tracked = tracked
    problems = validate_graph(graph, root)
    if committed and (root / VIEWS).exists():
        problems.append(f"{VIEWS}/ is committed — each clone renders it and it stays out of git (`git rm -r --cached {VIEWS}`)")
    return graph, problems


def cmd_validate(args) -> int:
    root = args.root
    graph, problems = tree_problems(root, committed=args.committed)
    if problems:
        print("validation failed:")
        for p in problems:
            print(f"- {p}")
        return 1
    print(f"validation passed: {len(graph.todos)} todo, {len(graph.exps)} exp, {len(graph.arcs)} arc, "
          f"{len(graph.invs)} inv, {len(graph.areas)} area")
    if not args.committed:
        stale = stale_view_lines(graph, root)
        if stale:
            print(f"note: {len(stale)} view(s) in {VIEWS}/ are stale — `python3 scripts/labledger.py render` rebuilds them. "
                  f"{VIEWS}/ is local to this clone\n  (gitignored, never committed), so this is not a failure: a new clone has "
                  f"none, and a frontmatter edited by hand\n  leaves it behind (the write commands render on their own):", *stale, sep="\n")
    return 0


# ───────────────────────────── review ─────────────────────────────

def cmd_review(args) -> int:
    root = args.root
    graph = load_graph(root)
    if graph.problems:
        print("review aborted — run validate first:")
        for p in graph.problems:
            print(f"- {p}")
        return 1
    today = dt.date.fromisoformat(args.today or record_today(args.root))
    cutoff = (today - dt.timedelta(days=STALE_DAYS)).isoformat()
    unabsorbed, stale = [], []
    for t in graph.todo_order():
        if t.fm["status"] not in TODO_STATUS_OPEN:
            continue
        rows = [m for m in inbound_for(graph, t) if m.event and m.date > t.fm["updated"]]
        if rows:
            unabsorbed.append((t, rows))
        if t.fm["status"] in STALE_STATUSES and t.fm["updated"] < cutoff:
            stale.append(t)
    lines = [f"# Review queue — {today.isoformat()}", "",
             "> Written by `python3 scripts/labledger.py review`. **The agent works through it**: read each item's evidence "
             "(`show <address>`), judge it again and record the judgment as its group says. A TODO judged unchanged still gets "
             "`set <ID> --note \"<one-line reason>\"`, which raises `updated` and takes it off groups 1 and 2.", ""]
    lines += [f"## 1. Open TODOs with records newer than their `updated` — {len(unabsorbed)} "
              f"(`set <ID> [--status …] --note … [--evidence EXP-NNNN]`)", ""]
    for t, rows in unabsorbed:
        lines.append(f"### {t.id} · {t.fm['status']} · {t.fm['priority']} · updated {t.fm['updated']} — {t.fm['title']}")
        for m in rows[:12]:
            lines.append(f"- {m.date} {m.source} [{m.kind}] {m.text}")
        if len(rows) > 12:
            lines.append(f"- … {len(rows) - 12} more (`show {t.id}`)")
        lines.append("")
    lines += [f"## 2. Open TODOs unchanged for {STALE_DAYS} days or more — {len(stale)} (parked, dropped, or `--note` why it goes on)", ""]
    for t in stale:
        lines.append(f"- {t.id} · {t.fm['status']} · {t.fm['priority']} · updated {t.fm['updated']} — {t.fm['title']}")
    word = graph.labels["resolved"]  # the word `resolve issue` writes, in the ledger's language
    self_resolved = [i for i in graph.issues if i.is_open and word.lower() in i.text.lower()]
    lines += ["", f"## 3. Open issues whose body holds the word `{word}` — {len(self_resolved)} "
              f"(`resolve issue <slug> --by …` when it is settled)", ""]
    for i in self_resolved:
        lines.append(f"- {i.date} `{i.slug}` ({i.file})")
    arc_stale = []
    for a in graph.arc_order():
        if a.fm["status"] != "active":
            continue
        members = [graph.todos[i] for i in graph.member_ids(a) if i in graph.todos]
        if members and all(t.fm["status"] in TODO_STATUS_CLOSED for t in members):
            arc_stale.append((a, "every member is closed — done?"))
        elif members and all(t.fm["status"] in ("parked", "dropped", "done") for t in members):
            arc_stale.append((a, "every open member is parked — parked?"))
    lines += ["", f"## 4. Active arcs whose members have all stopped — {len(arc_stale)} (`set ARC-NNN --status done|parked --note …`)", ""]
    for a, why in arc_stale:
        lines.append(f"- {a.id} · {a.fm['title']} — {why}")
    exp_cutoff = (today - dt.timedelta(days=STALE_EXP_DAYS)).isoformat()
    in_flight = [e for e in graph.exp_order() if e.fm["status"] == "in_flight" and e.fm["date"] < exp_cutoff]
    lines += ["", f"## 5. Experiments in flight for more than {STALE_EXP_DAYS} days — {len(in_flight)} "
              f"(`set EXP-NNNN --status final|withdrawn …`)", ""]
    for e in in_flight:
        lines.append(f"- {e.id} · since {e.fm['date']} — {e.fm['title']}")
    open_cutoff = (today - dt.timedelta(days=STALE_OPEN_DAYS)).isoformat()
    old_open = [(f"issue:{i.slug}", i.updated, i.file) for i in graph.issues if i.is_open and i.updated < open_cutoff]
    old_open += [(f"inv:{i.id}", i.updated, i.rel) for i in graph.inv_order() if i.fm["status"] == "open" and i.updated < open_cutoff]
    lines += ["", f"## 6. Open issues and investigations not updated for {STALE_OPEN_DAYS} days — {len(old_open)} (resolve, "
              f"promote or conclude it; if it still stands as written, `set <address>` raises `updated`)", ""]
    for address, updated, rel in old_open:
        lines.append(f"- `{address}` · updated {updated} ({rel})")
    done_open = []
    for a in graph.arc_order():
        still = [i for i in graph.member_ids(a) if i in graph.todos and graph.todos[i].fm["status"] in TODO_STATUS_OPEN]
        if a.fm["status"] == "done" and still:
            done_open.append((a, still))
    lines += ["", f"## 7. Done arcs with open members — {len(done_open)} (close the members, or `set ARC-NNN --status active`)", ""]
    for a, still in done_open:
        lines.append(f"- {a.id} · {a.fm['title']} — open: {', '.join(still)}")
    promoted_closed = []
    for i in graph.issues:
        todos = [t for t in graph.todo_order() if f"issue:{i.slug}" in t.links("from")]
        if i.status == "promoted" and todos and all(t.fm["status"] in TODO_STATUS_CLOSED for t in todos):
            promoted_closed.append((i, todos))
    lines += ["", f"## 8. Promoted issues whose TODOs are all closed — {len(promoted_closed)} "
              f"(`resolve issue <slug> --by <TODO>`)", ""]
    for i, todos in promoted_closed:
        lines.append(f"- `{i.slug}` — " + ", ".join(f"{t.id} {t.fm['status']}" for t in todos))
    text = "\n".join(lines) + "\n"
    (root / VIEWS).mkdir(exist_ok=True)
    (root / VIEWS / "review.md").write_text(text, encoding="utf-8")
    print(text, end="")
    print(f"(written to views/review.md)")
    return 0


# ───────────────────────────── show ─────────────────────────────

def _print_body(body: str, rel: str) -> None:
    body_lines = body.strip("\n").split("\n")
    print("\n".join(body_lines[:SHOW_BODY_MAX_LINES]))
    if len(body_lines) > SHOW_BODY_MAX_LINES:
        print(f"\n… ({len(body_lines) - SHOW_BODY_MAX_LINES} more lines in {rel})")


def describe_ref(graph: Graph, ref: str) -> str:
    """`status · date · title` of whatever a link field points at (TODO, EXP, issue:, inv:, dec:, arc:, user)."""
    if ref == "user":
        return "the user asked"
    found = resolve_address(graph, ref)
    if found is None:
        return "unknown"
    kind, r = found
    if kind == "todo":
        return f"{r.fm['status']} · {r.fm['priority']} · updated {r.fm['updated']} · {_clip(r.fm['title'], 90)}"
    if kind == "arc":
        return f"{r.fm['status']} · updated {r.fm['updated']} · {_clip(r.fm['title'], 90)}"
    if kind in ("exp", "inv"):
        return f"{r.fm['status']} · {r.fm['date']} · {_clip(r.fm['title'], 90)}"
    if kind == "dec":
        return f"decision · {r.date} · {_clip(r.text, 90)}"
    if kind == "issue":
        return f"{r.status} · {r.date} · {_clip(r.text, 90)}"
    return f"area · {r.title}"


def _print_arcs(graph: Graph, node_id: str) -> None:
    arcs = graph.arcs_of(node_id)
    if arcs:
        print("\n## Arcs\n" + "\n".join(f"- {a.id} · {a.fm['status']} · {a.fm['title']}" for a in arcs))


def show_area(graph: Graph, ar: Area) -> None:
    print(emit_frontmatter(ar.fm, AREA_KEY_ORDER), end="")
    print(f"# {ar.id} — {ar.title}\n")
    _print_body(ar.body, ar.rel)
    todos = [t for t in graph.todo_order() if t.area == ar.id]
    print("\n## TODOs" + (f" ({len(todos)})" if todos else " — none"))
    for t in todos:
        print(f"- {t.id} · {t.fm['status']} · {t.fm['priority']} · updated {t.fm['updated']} · {_clip(t.fm['title'], 100)}")
    invs = [i for i in graph.inv_order() if i.area == ar.id]
    print("\n## Investigations" + (f" ({len(invs)})" if invs else " — none"))
    for i in invs:
        print(f"- {i.fm['date']} inv:{i.id} · {i.fm['status']} · {_clip(i.fm['title'], 100)}")


def show_todo(graph: Graph, t: Todo) -> None:
    print(emit_frontmatter(t.fm, TODO_KEY_ORDER), end="")
    print(f"# {t.id} — {t.fm['title']}\n")
    _print_body(t.body, t.rel)
    relations = [(key, ref) for key in ("from", "depends_on", "supersedes", "resolves", "evidence") for ref in t.links(key)]
    if relations:
        print("\n## Links")
        for key, ref in relations:
            print(f"- {key} {ref} · {describe_ref(graph, ref)}")
    _print_arcs(graph, t.id)
    print("\n" + render_inbound_block(t, inbound_for(graph, t)), end="")


def show_exp(graph: Graph, e: Exp) -> None:
    print(emit_frontmatter(e.fm, EXP_KEY_ORDER), end="")
    print(f"# {e.id} — {e.fm['title']}\n")
    _print_body(e.body, e.rel)
    targets = e.affects()
    if targets:
        print("\n## affects")
        for ref, note in targets:
            t = graph.todos.get(ref)
            print(f"- {ref} · {t.fm['status'] if t else '?'} · {t.fm['title'] if t else ''}{' — ' + note if note else ''}")
    _print_arcs(graph, e.id)


def show_arc(graph: Graph, a: Arc) -> None:
    print(emit_frontmatter(a.fm, ARC_KEY_ORDER), end="")
    print(f"# {a.id} — {a.fm['title']}\n")
    _print_body(a.body, a.rel)
    members = [graph.todos[i] for i in graph.member_ids(a) if i in graph.todos]
    print("\n## Member TODOs" + (f" ({len(members)})" if members else " — none"))
    for t in members:
        print(f"- {t.id} · {t.fm['status']} · {t.fm['priority']} · updated {t.fm['updated']} · {t.fm['title']}")
    exps = [graph.exps[i] for i in graph.exp_ids(a) if i in graph.exps]
    print("\n## Experiments" + (f" ({len(exps)})" if exps else " — none"))
    for x in exps:
        print(f"- {x.id} · {x.fm['date']} · {x.fm['status']} · {_clip(x.fm['title'], 100)}")


def show_inv(graph: Graph, i: Inv) -> None:
    print(emit_frontmatter(i.fm, INV_KEY_ORDER), end="")
    _print_body(i.body, i.rel)
    targets = i.affects()
    if targets:
        print("\n## affects")
        for ref, note in targets:
            t = graph.todos.get(ref)
            label = f"{t.fm['status']} · {t.fm['title']}" if t else "(issue)"
            print(f"- {ref} · {label}{' — ' + note if note else ''}")


def show_decision(graph: Graph, d: Decision) -> None:
    print(f"decision {d.date} {d.slug} — {d.file}\n\n{d.text}")


def show_issue(graph: Graph, hit: Issue) -> None:
    slug = hit.slug
    print(f"issue [{hit.status}] {hit.date} {slug} — {hit.file}\n")
    print(hit.text)
    mentions = []
    for exp in graph.exps.values():
        if slug in exp.body or slug in exp.fm.get("summary", ""):
            mentions.append((exp.fm["date"], exp.id, exp.fm["title"]))
    for t in graph.todos.values():
        if slug in t.body or slug in t.fm.get("status_note", "") or f"issue:{slug}" in t.links("from") + t.links("resolves"):
            mentions.append((t.fm["updated"], t.id, t.fm["title"]))
    for inv in graph.invs.values():
        if any(ref == f"issue:{slug}" for ref, _ in inv.affects()) or slug in inv.body:
            mentions.append((inv.fm["date"], f"inv:{inv.id}", inv.fm["title"]))
    print("\n## Records that mention it")
    for date, src, title in sorted(mentions, reverse=True)[:INBOUND_MAX_ROWS] or [("", "", "(none)")]:
        print(f"- {date} {src} {_clip(title, 100)}")


SHOW = {"area": show_area, "todo": show_todo, "exp": show_exp, "arc": show_arc, "inv": show_inv,
        "dec": show_decision, "issue": show_issue}


def cmd_show(args) -> int:
    graph = load_graph(args.root)
    found = resolve_address(graph, args.id)
    if found is None:
        print(f"unknown address {args.id} (ID · area · inv:<id> · dec:<date>-<slug> · issue:<slug>){did_you_mean(graph, args.id)}",
              file=sys.stderr)
        return 2
    kind, record = found
    SHOW[kind](graph, record)
    return 0


# ───────────────────────────── new ─────────────────────────────

def no_area(area: str) -> int:
    print(f"unknown area {area} — `new area {area} \"<title>\" --prefix <PREFIX>` makes it", file=sys.stderr)
    return 2


def cmd_new_area(args) -> int:
    root = args.root
    graph = load_graph(root)
    if not AREA_ID_RE.match(args.id):
        print(f"area `{args.id}` must be lowercase letters, digits and dashes — it is the folder name", file=sys.stderr)
        return 2
    if args.id in graph.areas:
        print(f"area {args.id} already exists (areas/{args.id}/README.md)", file=sys.stderr)
        return 2
    taken = [a.id for a in graph.areas.values() if a.prefix == args.prefix]
    if taken:
        print(f"prefix {args.prefix} is area {taken[0]}'s — two areas would number the same TODOs", file=sys.stderr)
        return 2
    order = args.order if args.order is not None else max((a.order for a in graph.areas.values() if a.order < 10**9), default=0) + 1
    path = root / "areas" / args.id / "README.md"
    fm = {"id": args.id, "type": "area", "title": args.title, "prefix": args.prefix, "order": str(order)}
    area = Area(path, fm, "", rel=path.relative_to(root).as_posix())
    problems = check_node(graph, "area", area)
    if problems:
        return refuse(problems)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(new_node_text("area", fm, graph.skeleton("area")), encoding="utf-8")
    print(f"created {area.rel} — its TODOs are numbered {args.prefix}-NNN (`new todo {args.id} \"<title>\"`)")
    return written(args, [area.rel], commit_subject(root, args.id, f"add area {args.id} — {_oneline(args.title, 60)}"))


def cmd_new_todo(args) -> int:
    root = args.root
    graph = load_graph(root)
    area = args.area
    if area not in graph.areas:
        return no_area(area)
    prefix = args.prefix or graph.areas[area].prefix
    if not prefix:
        print(f"areas/{area}/README.md declares no `prefix`; pass --prefix", file=sys.stderr)
        return 2
    if not PREFIX_RE.match(prefix):  # before a number is reserved: <prefix>-NNN.md would not be read as a TODO
        print(f"prefix `{prefix}` must be capital letters and dashes, like LAB or LAB-SIM", file=sys.stderr)
        return 2
    nums = [int(t.id.rsplit("-", 1)[1]) for t in graph.todos.values() if t.id.rsplit("-", 1)[0] == prefix]
    node_id = new_id(args, prefix, nums, prefix + "-{:03d}")
    if isinstance(node_id, int):
        return node_id
    path = root / "areas" / area / f"{node_id}.md"
    if path.exists():
        print(f"{path} already exists", file=sys.stderr)
        return 1
    today = record_today(args.root)
    fm = {"id": node_id, "type": "todo", "title": args.title, "area": area, "status": args.status,
          "priority": args.priority, "created": today, "updated": today}
    from_ids = list(args.from_ids or [])
    if args.arc:
        from_ids.append(f"arc:{args.arc}")
    if from_ids:
        fm["from"] = from_ids
    if args.depends_on:
        fm["depends_on"] = args.depends_on
    skeleton = graph.skeleton("todo")
    todo = Todo(path, fm, skeleton, new_node_text("todo", fm, skeleton), rel=path.relative_to(root).as_posix())
    problems = check_node(graph, "todo", todo)
    if problems:
        return refuse(problems)
    path.write_text(todo.raw, encoding="utf-8")
    print(f"created {path.relative_to(root).as_posix()}")
    return written(args, [todo.rel], commit_subject(root, area, f"add {node_id} — {_oneline(args.title, 60)}"))


def cmd_new_exp(args) -> int:
    root = args.root
    graph = load_graph(root)
    host = args.host or record_host()
    try:
        exps = sparse_exps(root)
    except CommitError as exc:
        print(f"not created — {exc}", file=sys.stderr)
        return 2
    node_id = new_id(args, "EXP", [int(e.id[4:]) for e in graph.exps.values()], "EXP-{:04d}")
    if isinstance(node_id, int):
        return node_id
    path = root / "experiments" / f"{node_id}.md"
    if path.exists():
        print(f"{path} already exists", file=sys.stderr)
        return 1
    fm = {"id": node_id, "type": "exp", "title": args.title, "date": args.date or record_today(args.root), "host": host,
          "status": "in_flight", "model": args.model or "", "commit": args.commit or "",
          "affects": args.affects or [], "state": args.state or "n/a", "summary": args.summary or graph.labels["in_progress"]}
    if args.arc:
        fm["arc"] = args.arc
    skeleton = graph.skeleton("exp")
    problems = check_node(graph, "exp", Exp(path, fm, skeleton, rel=path.relative_to(root).as_posix()))
    if problems:
        return refuse(problems)
    path.parent.mkdir(exist_ok=True)  # the first experiment of a repository
    path.write_text(new_node_text("exp", fm, skeleton), encoding="utf-8")
    if exps is not None:  # a partial clone checks out the attachments of the EXPs it makes
        include_attachments(root, exps, [node_id])
    print(f"created {path.relative_to(root).as_posix()} — when the results are in: `set {node_id} --status final --summary … --data …`")
    return written(args, [path.relative_to(root).as_posix()], commit_subject(root, "exp", f"open {node_id} — {_oneline(args.title, 60)}"),
                   _flat(args.summary) if args.summary else "")


def cmd_new_arc(args) -> int:
    root = args.root
    graph = load_graph(root)
    node_id = new_id(args, "ARC", [int(a.id[4:]) for a in graph.arcs.values()], "ARC-{:03d}")
    if isinstance(node_id, int):
        return node_id
    path = root / "arcs" / f"{node_id}.md"
    if path.exists():
        print(f"{path} already exists", file=sys.stderr)
        return 1
    today = record_today(args.root)
    fm = {"id": node_id, "type": "arc", "title": args.title, "status": args.status, "created": today, "updated": today}
    if args.areas:
        fm["areas"] = args.areas
    if args.venue:
        fm["venue"] = args.venue
    if args.location:
        fm["location"] = args.location
    fm["members"] = args.members or []
    fm["exps"] = args.exps or []
    skeleton = graph.skeleton("arc")
    problems = check_node(graph, "arc", Arc(path, fm, skeleton, rel=path.relative_to(root).as_posix()))
    if problems:
        return refuse(problems)
    (root / "arcs").mkdir(exist_ok=True)
    path.write_text(new_node_text("arc", fm, skeleton), encoding="utf-8")
    print(f"created {path.relative_to(root).as_posix()} — write its goal, steps and exit criteria in the body; `new exp --arc {node_id}` adds experiments")
    return written(args, [path.relative_to(root).as_posix()], commit_subject(root, "arc", f"add {node_id} — {_oneline(args.title, 60)}"))


# ───────────────────────────── find ─────────────────────────────

def _oneline(text: str, n: int = 140) -> str:
    text = re.sub(r"\s+", " ", str(text)).strip()
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def cmd_find(args) -> int:
    """Keyword search over titles, summaries, status notes, issue entries and decision text (bodies with --body)."""
    graph = load_graph(args.root)
    words = [w.lower() for w in args.words]
    body = args.body

    def hit(*texts) -> bool:
        blob = " ".join(str(t) for t in texts if t).lower()
        return all(w in blob for w in words)

    rows: list[tuple[str, str, str, str, str]] = []  # date, kind, address, status, line
    for t in graph.todos.values():
        if hit(t.id, t.fm["title"], t.fm.get("status_note", ""), t.body if body else ""):
            rows.append((t.fm["updated"], "todo", t.id, t.fm["status"], f"{t.fm['priority']} · {t.fm['title']}"))
    for e in graph.exps.values():
        if hit(e.id, e.fm["title"], e.fm.get("summary", ""), e.fm.get("model", ""), e.body if body else ""):
            rows.append((e.fm["date"], "exp", e.id, e.fm["status"], f"{e.fm['title']} — {e.fm.get('summary', '')}"))
    for a in graph.arcs.values():
        if hit(a.id, a.fm["title"], a.fm.get("status_note", ""), a.body if body else ""):
            rows.append((a.fm["updated"], "arc", a.id, a.fm["status"], a.fm["title"]))
    for i in graph.invs.values():
        if hit(i.id, i.fm["title"], i.fm.get("summary", ""), i.fm.get("status_note", ""), i.body if body else ""):
            rows.append((i.fm["date"], "inv", f"inv:{i.id}", i.fm["status"], i.fm["title"]))
    for iss in graph.issues:
        if hit(iss.slug, iss.text):
            rows.append((iss.date, "issue", f"issue:{iss.slug}", iss.status, iss.text))
    for d in graph.decisions:
        if hit(d.slug, d.text):
            rows.append((d.date, "dec", f"dec:{d.id}", "—", d.text))
    if args.kind:
        rows = [r for r in rows if r[1] in args.kind]
    if args.status:
        rows = [r for r in rows if r[3] in args.status]
    rows.sort(key=lambda r: (r[0], r[2]), reverse=True)
    for date, _, address, status, line in rows[:args.limit]:
        print(f"{address} · {status} · {date} · {_oneline(line)}")
    if len(rows) > args.limit:
        print(f"… {len(rows) - args.limit} more (--limit {len(rows)})")
    if not rows:
        print("no match", file=sys.stderr)
        return 1
    return 0


# ───────────────────────────── issues, decisions, investigations ─────────────────────────────

def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def cmd_new_issue(args) -> int:
    root = args.root
    graph = load_graph(root)
    if args.area not in graph.areas:
        return no_area(args.area)
    slug = args.slug
    if not SLUG_RE.match(slug):
        print(f"slug `{slug}` must be kebab-case [A-Za-z0-9._-]", file=sys.stderr)
        return 2
    if slug in graph.issues_by_slug:
        print(f"issue slug `{slug}` already exists in {graph.issues_by_slug[slug].file}", file=sys.stderr)
        return 2
    date = args.date or record_today(args.root)
    if not DATE_RE.match(date):
        print("--date must be YYYY-MM-DD", file=sys.stderr)
        return 2
    path = root / "areas" / args.area / ISSUE_DIR / f"{slug}.md"
    if path.exists():
        print(f"{path.relative_to(root).as_posix()} already exists", file=sys.stderr)
        return 2
    label = graph.labels
    lines = [f"- {label[key]}: {_flat(value)}" for key, value in (("symptom", args.symptom), ("path", args.path),
                                                               ("hypothesis", args.hypothesis), ("blocking", args.blocking)) if value]
    path.parent.mkdir(exist_ok=True)
    path.write_text(entry_text("issue", {"date": date, "status": "open"}, "\n".join(lines)), encoding="utf-8")
    print(f"added issue:{slug} — {path.relative_to(root).as_posix()}")
    return written(args, [path.relative_to(root).as_posix()], commit_subject(root, args.area, f"issue {slug}"), _flat(args.symptom))


def _issue_to_move(graph: Graph, slug: str, target: str, allowed_from: tuple[str, ...]) -> Issue | int:
    """The issue that may move to `target`, or the exit code of a refusal (said on stderr)."""
    iss = graph.issues_by_slug.get(slug)
    if iss is None:
        print(f"unknown issue slug `{slug}` (`find {slug}` / `show {slug}`)", file=sys.stderr)
        return 2
    if iss.status == target:
        print(f"issue:{slug} is already {target} ({iss.file})", file=sys.stderr)
        return 1
    if iss.status not in allowed_from:
        print(f"issue:{slug} is {iss.status} ({iss.file}) — only {' or '.join(allowed_from)} issues can be {target}", file=sys.stderr)
        return 1
    return iss


def _move_issue(root: Path, iss: Issue, target: str, line: str, today: str) -> str:
    """Set the issue's status to `target`, its `updated` to `today`, and append `line` (its promoted / resolved line) to
    the body. Returns its path."""
    body = split_head(iss.raw, "issue", iss.slug).strip("\n")
    (root / iss.file).write_text(entry_text("issue", {**iss.fm, "status": target, "updated": today},
                                            (body + "\n" if body else "") + line), encoding="utf-8")
    return iss.file


def cmd_resolve_issue(args) -> int:
    root = args.root
    graph = load_graph(root)
    iss = _issue_to_move(graph, args.slug, "resolved", allowed_from=("open", "promoted"))
    if isinstance(iss, int):
        return iss
    today = record_today(args.root)
    line = f"- {graph.labels['resolved']} ({today}): {_flat(args.by)}" + (f" — {_flat(args.note)}" if args.note else "")
    rel = _move_issue(root, iss, "resolved", line, today)
    print(f"issue:{args.slug} → resolved ({rel})")
    return written(args, [rel], commit_subject(root, Path(rel).parts[1], f"resolve issue {args.slug}"), line[2:])


def cmd_promote_issue(args) -> int:
    root = args.root
    graph = load_graph(root)
    found = resolve_address(graph, args.to)
    if found is None or found[0] != "todo":
        print(f"unknown TODO {args.to} — create it first: `new todo <area> \"<title>\" --from issue:{args.slug}`", file=sys.stderr)
        return 2
    todo = found[1]
    iss = _issue_to_move(graph, args.slug, "promoted", allowed_from=("open",))
    if isinstance(iss, int):
        return iss
    tag = f"issue:{args.slug}"
    fm = None
    if tag not in todo.links("from"):  # checked before either file is written: a refusal leaves both as they were
        fm = dict(todo.fm)
        fm["from"] = todo.links("from") + [tag]
        problems = check_node(graph, "todo", replace(todo, fm=fm))
        if problems:
            return refuse(problems)
    today = record_today(args.root)
    line = f"- {graph.labels['promoted']} ({today}): {todo.id}" + (f" — {_flat(args.note)}" if args.note else "")
    rel = _move_issue(root, iss, "promoted", line, today)
    print(f"issue:{args.slug} → promoted ({rel}) — {todo.id}")
    if fm is not None:
        todo.path.write_text(formatted_text("todo", fm, todo.raw), encoding="utf-8")
        print(f"{todo.id}: from += {tag}")
    return written(args, [rel, todo.rel], commit_subject(root, Path(rel).parts[1], f"promote issue {args.slug} to {todo.id}"), line[2:])


def cmd_new_decision(args) -> int:
    root = args.root
    graph = load_graph(root)
    slug = args.slug
    if not SLUG_RE.match(slug):
        print(f"slug `{slug}` must be kebab-case [A-Za-z0-9._-]", file=sys.stderr)
        return 2
    date = args.date or record_today(args.root)
    if not DATE_RE.match(date):
        print("--date must be YYYY-MM-DD", file=sys.stderr)
        return 2
    dec_id = f"{date}-{slug}"
    if dec_id in graph.decisions_by_id:
        print(f"dec:{dec_id} already exists", file=sys.stderr)
        return 2
    if args.area and args.area not in graph.areas:
        return no_area(args.area)
    path = (root / "areas" / args.area if args.area else root) / DEC_DIR / f"{dec_id}.md"
    if path.exists():
        print(f"{path.relative_to(root).as_posix()} already exists", file=sys.stderr)
        return 2
    label = graph.labels
    lines = [f"- {label[key]}: {_flat(value)}" for key, value in (("context", args.context), ("decision", args.text),
                                                               ("why", args.why)) if value]
    path.parent.mkdir(exist_ok=True)
    path.write_text(entry_text("dec", {"date": date, "title": slug}, "\n".join(lines)), encoding="utf-8")
    print(f"added dec:{dec_id} — {path.relative_to(root).as_posix()}")
    return written(args, [path.relative_to(root).as_posix()], commit_subject(root, args.area or "decisions", f"decide {slug}"), _flat(args.text))


def _slugify(title: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", title).strip("-").lower()


def cmd_new_inv(args) -> int:
    root = args.root
    graph = load_graph(root)
    if args.area not in graph.areas:
        return no_area(args.area)
    date = args.date or record_today(args.root)
    if not DATE_RE.match(date):
        print("--date must be YYYY-MM-DD", file=sys.stderr)
        return 2
    slug = args.slug or _slugify(args.title)
    if not slug or not SLUG_RE.match(slug):
        print("pass --slug <ascii-kebab-case> — the title has no ASCII words to derive a file name from", file=sys.stderr)
        return 2
    stem = f"{slug}-{date}"
    path = root / "areas" / args.area / f"{stem}.md"
    if path.exists() or stem in graph.invs:
        print(f"inv:{stem} already exists", file=sys.stderr)
        return 2
    fm = {"id": stem, "type": "inv", "title": args.title, "area": args.area, "date": date, "status": args.status}
    if args.summary:
        fm["summary"] = args.summary
    fm["affects"] = args.affects or []
    problems = check_node(graph, "inv", Inv(path, fm, "", rel=path.relative_to(root).as_posix()))
    if problems:
        return refuse(problems)
    path.write_text(canonical_head("inv", fm) + f"# {args.title}\n\n" + graph.skeleton("inv"), encoding="utf-8")
    print(f"created {path.relative_to(root).as_posix()} — write the findings and conclusion, then "
          f"`set inv:{stem} --status concluded --summary … --affects \"ID: note\"` for the issues and TODOs it updates")
    return written(args, [path.relative_to(root).as_posix()], commit_subject(root, args.area, f"add inv:{stem} — {_oneline(args.title, 50)}"))


# ───────────────────────────── set ─────────────────────────────

SET_OPTIONS = {"status": ("todo", "exp", "arc", "inv"), "note": ("todo", "arc", "inv"), "priority": ("todo",),
               "evidence": ("todo",), "affects": ("exp", "inv"), "summary": ("exp", "inv"), "state": ("exp",),
               "data": ("exp",), "superseded_by": ("exp",), "host": ("exp",), "model": ("exp",), "commit": ("exp",)}


def affects_key(entry: str) -> str:
    """The target an `"ID: note"` / `"issue:<slug>: note"` affects entry names."""
    m = AFFECTS_RE.match(entry.strip())
    return m.group(1) if m else entry.strip()


def data_key(entry: str) -> str:
    """The file a `"<file>: <what one row is>"` data entry names."""
    m = DATA_ENTRY_RE.match(entry.strip())
    return m.group(1).strip() if m else entry.strip()


SET_INSTEAD = {  # what to use where an option does not apply, for the mix-ups agents make
    ("note", "exp"): " — an experiment says what it found in --summary",
    ("status", "issue"): " — an issue's status moves with `resolve issue <slug> --by …` or `promote issue <slug> --to <ID>`",
}


def cmd_set(args) -> int:
    root = args.root
    graph = load_graph(root)
    found = resolve_address(graph, args.id)
    if found is None or found[0] in ("area", "dec"):
        print(f"set edits a TODO / EXP-NNNN / ARC-NNN / inv:<id> / issue:<slug>; `{args.id}` is "
              + ("not one" if found else f"unknown{did_you_mean(graph, args.id)}"), file=sys.stderr)
        return 2
    kind, node = found
    given = {opt: getattr(args, opt) for opt in SET_OPTIONS if getattr(args, opt) not in (None, [])}
    for opt in given:
        if kind not in SET_OPTIONS[opt]:
            print(f"--{opt.replace('_', '-')} does not apply to {kind} {args.id} (only {', '.join(SET_OPTIONS[opt])})"
                  + SET_INSTEAD.get((opt, kind), ""), file=sys.stderr)
            return 2
    today = args.date or record_today(args.root)
    if not DATE_RE.match(today):
        print("--date must be YYYY-MM-DD", file=sys.stderr)
        return 2
    if kind == "issue":  # its status moves with resolve / promote; set raises its `updated` after an edit by hand
        if today < node.date:
            print(f"--date {today} is before the issue's date {node.date}", file=sys.stderr)
            return 2
        (root / node.file).write_text(entry_text("issue", {**node.fm, "updated": today}, split_head(node.raw, "issue", "")),
                                      encoding="utf-8")
        print(f"issue:{node.slug}: updated {node.updated} → {today}")
        return written(args, [node.file], commit_subject(root, Path(node.file).parts[1], f"issue {node.slug} touched"))
    fm = dict(node.fm)
    changes: list[str] = []
    if "status" in given and fm.get("status") != given["status"]:
        changes.append(f"status {fm.get('status')} → {given['status']}")
    if "status" in given:
        fm["status"] = given["status"]
    if "note" in given:
        fm["status_note"] = _flat(given["note"])
        changes.append("status_note")
    if "priority" in given:
        fm["priority"] = given["priority"]
        changes.append(f"priority {given['priority']}")
    if "evidence" in given:
        current = list(fm.get("evidence") or [])
        fm["evidence"] = current + [r for r in given["evidence"] if r not in current]
        changes.append("evidence")
    if "affects" in given:
        incoming = {affects_key(item) for item in given["affects"]}
        fm["affects"] = [c for c in (fm.get("affects") or []) if affects_key(c) not in incoming] + [i.strip() for i in given["affects"]]
        changes.append("affects")
    if "summary" in given:
        fm["summary"] = _flat(given["summary"])
        changes.append("summary")
    if "state" in given:
        fm["state"] = _flat(given["state"])
        changes.append("state")
    if "data" in given:  # an entry replaces the one for the same file; `n/a (why)` replaces them all
        incoming = [_flat(d) for d in given["data"]]
        kept = [] if any(DATA_NA_RE.match(d) for d in incoming) else [
            d for d in (fm.get("data") or []) if not DATA_NA_RE.match(d) and data_key(d) not in {data_key(i) for i in incoming}]
        fm["data"] = kept + incoming
        changes.append("data")
    if "superseded_by" in given:
        fm["superseded_by"] = given["superseded_by"]
        changes.append("superseded_by")
    if "host" in given:
        fm["host"] = given["host"]
        changes.append(f"host {given['host']}")
    if "model" in given:
        fm["model"] = given["model"]
        changes.append("model")
    if "commit" in given:
        fm["commit"] = given["commit"]
        changes.append("commit")
    if kind in ("todo", "arc", "inv"):
        before = fm.get("updated") or fm.get("date")  # an investigation without `updated` was last changed the day it was made
        if before != today:
            changes.append(f"updated {before} → {today}")
        fm["updated"] = today
    problems = check_node(graph, kind, replace(node, fm=fm))
    if problems:
        return refuse(problems)
    close_block = None
    if kind == "todo" and fm.get("status") in TODO_STATUS_CLOSED:
        dec_dir = f"areas/{node.area}/{DEC_DIR}"
        if not any(Path(d.file).parent.as_posix() == dec_dir and d.slug == f"close-{node.id}" for d in graph.decisions):
            evidence = list(fm.get("evidence") or [])
            note = _flat(args.note) if args.note else str(fm.get("status_note") or "")
            if not note and evidence:
                note = graph.labels["closed_by"].replace("{evidence}", ", ".join(evidence))
            if not note:
                print(f"closing {node.id} as {fm['status']} needs --note (or --evidence EXP-NNNN) for the close decision", file=sys.stderr)
                return 2
            tail = f" ({graph.labels['evidence']} {', '.join(evidence)})" if evidence else ""
            close_block = (f"{dec_dir}/{today}-close-{node.id}.md",
                           entry_text("dec", {"date": today, "title": f"close {node.id}"}, f"- {fm['status']}: {note}{tail}"))
    if "state" in given:
        for name in state_claims(fm["state"]) or []:
            if name in graph.states and not re.search(rf"\b{re.escape(node.id)}\b", graph.states[name]):
                print(f"warning: state/{name} does not cite {node.id} yet — update that row in the same turn (validate checks it)", file=sys.stderr)
    node.path.write_text(formatted_text(kind, fm, node.raw), encoding="utf-8")
    if close_block:
        (root / close_block[0]).parent.mkdir(exist_ok=True)
        (root / close_block[0]).write_text(close_block[1], encoding="utf-8")
        changes.append(f"close decision → {close_block[0]}")
    print(f"{node.id}: " + ("; ".join(changes) if changes else "no change"))
    rels = [node.rel] + ([close_block[0]] if close_block else [])
    if kind == "exp" and (root / ATTACHMENTS / node.id).is_dir():  # the data files its `data` names go in with it
        rels.append(f"{ATTACHMENTS}{node.id}")
    scope = {"exp": "exp", "arc": "arc"}.get(kind) or node.fm.get("area") or kind
    fields = [c.split(" ")[0].replace("status_note", "note") for c in changes if not c.startswith(("updated", "close decision"))]
    what = given["status"] if "status" in given and "status" in fields else " ".join(f for f in fields if f != "status") or "touched"
    detail = given.get("note") or given.get("summary") or ""
    return written(args, rels, commit_subject(root, scope, f"{node.id} {what}"), _flat(detail) if detail else "")


# ───────────────────────────── commit ─────────────────────────────

ATTACHMENTS = "experiments/attachments/"
# A partial clone (AGENTS.md, "Other machines") checks out everything but the attachments, less the attachment directories of
# the EXPs it made or brought in with `attachments`: its sparse-checkout file is SPARSE_BASE, then SPARSE_EXP_RE lines.
SPARSE_BASE = ("/*", f"!/{ATTACHMENTS}")
SPARSE_EXP_RE = re.compile(rf"^/{re.escape(ATTACHMENTS)}(EXP-\d{{4,}})/$")


class CommitError(Exception):
    pass


def _run(root: Path, *args: str, env: dict[str, str] | None = None, stdin: str | None = None,
         timeout: float | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *args], input=stdin, capture_output=True, encoding="utf-8", env=env,
                          timeout=timeout)


def _git(root: Path, *args: str, env: dict[str, str] | None = None, stdin: str | None = None) -> str:
    r = _run(root, *args, env=env, stdin=stdin)
    if r.returncode:
        raise CommitError(f"git {args[0]}: {(r.stderr or r.stdout).strip()}")
    return r.stdout


def commit_blocker(root: Path) -> str | None:
    """Why `root` cannot take a commit right now, or None."""
    r = _run(root, "rev-parse", "--show-toplevel", "--absolute-git-dir")
    if r.returncode:
        return "not a git work tree"
    top, git_dir = r.stdout.splitlines()[:2]
    if Path(top).resolve() != root.resolve():
        return f"{root} is not the top of its git work tree ({top})"
    if _run(root, "symbolic-ref", "-q", "HEAD").returncode:
        return "HEAD is detached"
    for marker in ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-merge", "rebase-apply"):
        if (Path(git_dir) / marker).exists():
            return f"{marker} is in progress"
    return None


def _sparse_file(root: Path) -> Path:
    path = Path(_git(root, "rev-parse", "--git-path", "info/sparse-checkout").strip())
    return path if path.is_absolute() else root / path


def sparse_exps(root: Path) -> set[str] | None:
    """The EXPs whose attachments this clone checks out, or None when it checks out every file."""
    if _run(root, "config", "--bool", "core.sparseCheckout").stdout.strip() != "true":
        return None
    path = _sparse_file(root)
    lines = [l.strip() for l in path.read_text(encoding="utf-8").splitlines()] if path.is_file() else []
    lines = [l for l in lines if l and not l.startswith("#")]
    cone = _run(root, "config", "--bool", "core.sparseCheckoutCone").stdout.strip() == "true"
    if cone or tuple(lines[:2]) != SPARSE_BASE or not all(SPARSE_EXP_RE.match(l) for l in lines[2:]):
        raise CommitError(f"this clone's sparse checkout is not the one labledger.py knows — non-cone mode, and in {path} "
                          f"`/*`, `!/{ATTACHMENTS}`, then `/{ATTACHMENTS}EXP-NNNN/` lines (AGENTS.md, Other machines)")
    return {SPARSE_EXP_RE.match(l).group(1) for l in lines[2:]}


def off_disk(rel: str, exps: set[str] | None) -> bool:
    """Whether this clone leaves `rel` (a file or a directory) off disk: an attachment of an EXP it has not brought in."""
    return exps is not None and rel.startswith(ATTACHMENTS) and rel[len(ATTACHMENTS):].split("/")[0] not in exps


def include_attachments(root: Path, exps: set[str], ids: list[str]) -> list[str]:
    """Put these EXPs' attachment directories into this partial clone's checkout (`exps`: those in it now) — the
    sparse-checkout file only, git materializes them. Returns the ones that were not in it."""
    added = [i for i in dict.fromkeys(ids) if i not in exps]
    if added:
        path = _sparse_file(root)
        lead = "" if path.read_bytes().endswith(b"\n") else "\n"
        with open(path, "a", encoding="utf-8") as fh:  # appended, not rewritten: another session may add its EXP meanwhile
            fh.write(lead + "".join(f"/{ATTACHMENTS}{i}/\n" for i in added))
    return added


def _index_from(root: Path, env: dict[str, str], rev: str | None) -> None:
    """Load `rev`'s tree into the empty index that `env` names without reading a blob: `read-tree` (like a plain
    `write-tree`) checks that every blob is here, and a partial clone then downloads every attachment it left out."""
    if not rev:
        _git(root, "read-tree", "--empty", env=env)
        return
    _git(root, "update-index", "-z", "--index-info", env=env, stdin=_git(root, "ls-tree", "-r", "-z", "--full-tree", rev))


def _materialize(root: Path, env: dict[str, str], dest: Path) -> set[str]:
    """Check out the index that `env` names under `dest`: everything but the attachments, less their tables (CSV, TSV,
    JSON) that this clone has on disk — validate reads those, and a partial clone must not download the others. Returns
    every file the index holds: validate finds there what is not on disk."""
    files = {p for p in _git(root, "ls-files", "-z", env=env).split("\0") if p}
    out = [p for p in files if not p.startswith(ATTACHMENTS)
           or (PurePosixPath(p).suffix.lower() in DATA_EXTENSIONS and (root / p).is_file())]
    _git(root, "checkout-index", f"--prefix={dest}/", "--stdin", "-z", env=env, stdin="\0".join(out) + "\0")
    return files


def unbumped(root: Path, graph: Graph, head: str, env: dict[str, str]) -> list[str]:
    """Investigations and issues that the index `env` names changes from `head` while their `updated` stays behind
    today: review dates the TODOs they mention by `updated`, so an edit that leaves it behind is never queued."""
    if not head:
        return []
    changed = set(_git(root, "diff-index", "--cached", "--no-renames", "--name-only", "-z", head, env=env).split("\0"))
    today, out = record_today(root), []
    for rel, updated, address in ([(i.rel, i.updated, f"inv:{i.id}") for i in graph.invs.values()]
                                  + [(i.file, i.updated, f"issue:{i.slug}") for i in graph.issues]):
        if rel not in changed or updated == today:
            continue
        before = _run(root, "show", f"{head}:{rel}")
        try:
            old = parse_frontmatter(before.stdout, rel)[0] if before.returncode == 0 else None
        except FrontmatterError:
            old = None
        if old is not None and str(old.get("updated") or old.get("date")) == updated:
            out.append(f"{rel}: changed, but `updated` is still {updated} — `python3 scripts/labledger.py set {address}` "
                       f"raises it to today, so review sees the TODOs it mentions")
    return out


def commit_paths(root: Path, rels: list[str], message: str, attempts: int = 5) -> str:
    """Commit `rels` as they stand in the working tree on top of HEAD. Every other uncommitted change stays out,
    so sessions sharing one working tree never commit each other's half-written records. The commit must validate on its own. HEAD moves by compare-and-swap
    (retried if another commit lands meanwhile); the working tree is never written. Returns the new commit id, or
    "" when the paths add nothing to HEAD."""
    branch = _git(root, "symbolic-ref", "-q", "HEAD").strip()
    exps = sparse_exps(root)
    if exps is not None:
        for r in rels:
            hidden = off_disk(r, exps) or any(l.startswith("S ") for l in _git(root, "ls-files", "-t", "-z", "--", r).split("\0"))
            if hidden:  # its files are not on disk because of the sparse checkout — `add -A` would commit their deletion
                name = r[len(ATTACHMENTS):].split("/")[0] if r.startswith(ATTACHMENTS) else ""
                raise CommitError(f"`{r}` reaches attachments this partial clone leaves off disk — " + (
                    f"`python3 scripts/labledger.py attachments {name}` brings them in; then commit again"
                    if SPARSE_EXP_RE.match(f"/{ATTACHMENTS}{name}/") else "commit the records by their addresses instead"))
    for _ in range(attempts):
        head = _run(root, "rev-parse", "-q", "--verify", "HEAD").stdout.strip()
        with tempfile.TemporaryDirectory(prefix="labledger-commit-") as tmp:
            env = {**os.environ, "GIT_INDEX_FILE": str(Path(tmp) / "index")}
            _index_from(root, env, head)
            tracked = [p for p in _git(root, "ls-files", "-z", "--", *rels, env=env).split("\0") if p]
            present = [r for r in rels if (root / r).exists() or any(t == r or t.startswith(r.rstrip("/") + "/") for t in tracked)]
            if present:
                _git(root, "add", "-A", "--", *present, env=env)
            tree_dir = Path(tmp) / "tree"
            graph, problems = tree_problems(tree_dir, committed=True, tracked=_materialize(root, env, tree_dir))
            problems += unbumped(root, graph, head, env)
            if problems:
                raise CommitError("the commit would not validate on its own:\n" + "\n".join(f"  - {p}" for p in problems))
            tree = _git(root, "write-tree", "--missing-ok", env=env).strip()  # an attachment a partial clone lacks is on the upstream
        if head and tree == _git(root, "rev-parse", f"{head}^{{tree}}").strip():
            return ""
        new = _git(root, "commit-tree", tree, *(["-p", head] if head else []), stdin=message).strip()
        if _run(root, "update-ref", "-m", f"labledger commit: {message.splitlines()[0]}", branch, new, head).returncode == 0:
            _refresh_index(root, head, new, exps)
            return new
    raise CommitError(f"{branch} kept moving during {attempts} attempts — run the command again")


def _refresh_index(root: Path, old: str, new: str, exps: set[str] | None) -> None:
    """Point the real index at HEAD for the paths the new commit changed, leaving the working tree alone, so
    `git status` shows only what is still uncommitted. An attachment a partial clone leaves off disk is marked so
    (skip-worktree) — the reset drops the mark, and the missing file would show as deleted."""
    base = old or _git(root, "hash-object", "-t", "tree", "/dev/null").strip()
    changes = _changed(root, base, new)  # --no-renames: a rename lists both paths
    changed = [p for _, p in changes]
    hidden = [p for status, p in changes if status != "D" and off_disk(p, exps)]
    env = {**os.environ, "GIT_LITERAL_PATHSPECS": "1"}
    for _ in range(20):  # the index lock may be held by another git command for a moment
        if _run(root, "reset", "-q", "--pathspec-from-file=-", "--pathspec-file-nul", env=env,
                stdin="\0".join(changed) + "\0").returncode == 0 and (not hidden or _run(
                    root, "update-index", "--skip-worktree", "-z", "--stdin", stdin="\0".join(hidden) + "\0").returncode == 0):
            return
        time.sleep(0.25)
    print(f"warning: the index still shows the old HEAD for {len(changed)} path(s) — `git reset -q` refreshes it", file=sys.stderr)


PUSH_TIMEOUT = 60  # seconds; a push that has not finished by then is abandoned and retried by the next commit
SSH_PERSIST = 600  # seconds the shared SSH connection to an upstream host outlives its last use
_SSH_SHARED: dict[str, str | None] = {}  # remote URL → the ControlPath this process found or started (None: none)


def _ssh_target(url: str) -> list[str] | None:
    """ssh's arguments naming the host of an SSH remote URL — `[user@]host:path` or `ssh://[user@]host[:port]/path` —
    or None for any other transport (a local path, file://, https://)."""
    if m := re.match(r"^ssh://([^/]+)/", url):
        host, port = m.group(1), None
        if hp := re.fullmatch(r"(.+):(\d+)", host):
            host, port = hp.groups()
        return (["-p", port] if port else []) + [host]
    m = re.match(r"^([^/:]+):(?!//)", url)  # scp-like: a colon before the first slash
    return [m.group(1)] if m else None


def _ssh_start(url: str) -> str | None:
    """Find or start the shared connection to the host of `url`; its ControlPath, or None."""
    target = _ssh_target(url)
    # a socket path holds 107 bytes: the directory, "/labledger-", %C (40) and the 17 that ssh appends while creating it
    home = next((d for d in (os.environ.get("XDG_RUNTIME_DIR"), os.path.expanduser("~/.ssh"))
                 if d and len(d) <= 39 and os.path.isdir(d)), None)
    if not target or not home or not shutil.which("ssh"):
        return None
    path = f"{home}/labledger-%C"  # ssh fills %C in from the host it reaches, so a socket never serves another host
    base = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", "-o", f"ControlPath={path}"]
    quiet = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}

    def alive() -> bool:
        with contextlib.suppress(subprocess.TimeoutExpired):  # a wedged master counts as none
            return subprocess.run([*base, "-O", "check", *target], timeout=10, **quiet).returncode == 0
        return False

    with open(Path(home) / "labledger-ssh.lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)  # one starter at a time; the next one finds it alive
        if not alive():
            with contextlib.suppress(subprocess.TimeoutExpired):  # a master that cannot listen stays in the foreground
                subprocess.run([*base, "-N", "-o", "ControlMaster=auto", "-o", f"ControlPersist={SSH_PERSIST}", *target],
                               timeout=30, **quiet)
            if not alive():
                return None
    return path


def _ssh_shared(root: Path, remote: str) -> str | None:
    """The ControlPath of a live shared SSH connection (OpenSSH ControlMaster) to the host of `remote`, started when
    none is alive. The fetches and pushes of one command — `new` makes three — and of the commands of the next
    SSH_PERSIST s then pay the SSH handshake (about 1.6 s of the 2.3 s a GitHub round trip takes) once. The tool
    starts it itself, its stdio on /dev/null: a master forked by git's ssh inherits the caller's stderr, and an
    OpenSSH that leaves it open there would make the command wait until the connection expires. None when `remote`
    is not reached over SSH or no connection comes up — git then connects on its own."""
    url = _run(root, "remote", "get-url", remote).stdout.strip()
    if url not in _SSH_SHARED:
        _SSH_SHARED[url] = _ssh_start(url)
    return _SSH_SHARED[url]


def _network_env(root: Path, remote: str) -> dict[str, str]:
    """Never let a push or fetch stop to ask for a password or passphrase: fail instead, and the commit stays local.
    With LABLEDGER_SSH_SHARE=1, git shares one SSH connection to the host (_ssh_shared) — unless the caller or this clone
    chose its own ssh."""
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    if "GIT_SSH_COMMAND" not in env and not _run(root, "config", "--get", "core.sshCommand").stdout.strip():
        shared = _ssh_shared(root, remote) if os.environ.get("LABLEDGER_SSH_SHARE") == "1" else None
        env["GIT_SSH_COMMAND"] = "ssh -o BatchMode=yes -o ConnectTimeout=15" + (
            f" -o ControlMaster=no -o ControlPath={shlex.quote(shared)}" if shared else "")
    return env


def _upstream(root: Path) -> tuple[str, str, str] | None:
    """(remote, its branch, the remote-tracking ref) that the current branch pushes to and syncs with, or None."""
    branch = _run(root, "symbolic-ref", "-q", "--short", "HEAD").stdout.strip()
    remote = _run(root, "config", "--get", f"branch.{branch}.remote").stdout.strip() if branch else ""
    merge = _run(root, "config", "--get", f"branch.{branch}.merge").stdout.strip() if branch else ""
    tracking = _run(root, "rev-parse", "-q", "--symbolic-full-name", "@{u}").stdout.strip()
    if not remote or remote == "." or not merge or not tracking:
        return None
    return remote, merge, tracking


def _push(root: Path, remote: str, merge: str, tracking: str) -> tuple[str, str | None]:
    """Push the branch tip when the upstream lacks it: ("ok", report or None), ("rejected", None) when the upstream
    has commits this clone lacks, or ("failed", report) when the push was refused otherwise or got no answer."""
    tip = _run(root, "rev-parse", "-q", "--verify", "HEAD").stdout.strip()

    def upstream_has_tip() -> bool:
        return _run(root, "merge-base", "--is-ancestor", tip, tracking).returncode == 0

    if upstream_has_tip():
        return "ok", None
    env = _network_env(root, remote)
    try:
        r = _run(root, "push", "--porcelain", remote, f"{tip}:{merge}", env=env, timeout=PUSH_TIMEOUT)
        if r.returncode == 0:
            return "ok", f"pushed {tip[:7]} to {remote}"
        if "[rejected]" not in r.stdout:  # the pre-push check refused, the remote refused, or it could not be reached
            lines = [l for l in f"{r.stderr}\n{r.stdout}".splitlines() if l.strip()]  # git names a refusal's reason on stdout
            return "failed", ("not pushed — the commit stays local; the next write, `commit` or `sync` pushes it:\n  "
                              + "\n  ".join(lines[-12:] or ["git push failed"]))
        _run(root, "fetch", "-q", remote, env=env, timeout=PUSH_TIMEOUT)  # a session sharing this clone may have pushed a descendant
    except subprocess.TimeoutExpired:
        return "failed", (f"not pushed — {remote} did not answer within {PUSH_TIMEOUT} s; the commit stays local, and the next write, "
                          "`commit` or `sync` pushes it")
    return ("ok", None) if upstream_has_tip() else ("rejected", None)


def push_upstream(root: Path) -> tuple[bool, str | None]:
    """Push the current branch tip to the branch it tracks when that branch lacks it. When another server pushed
    first, sync: bring its commits in, replay this clone's on top, push. Returns whether the upstream has the tip now
    (true too when there is no upstream) and a one-line report (more when a sync stops), or None for nothing to say."""
    up = _upstream(root)
    if up is None:
        return True, None
    state, report = _push(root, *up)
    if state != "rejected":
        return state == "ok", report
    return sync(root)


# ───────────────────────────── sync ─────────────────────────────

SYNC_WAIT = 120  # seconds a sync waits for another sync of the same clone to finish
# The private worktree is sparse (no attachments on disk) through these flags alone: `git sparse-checkout` would rewrite
# the clone's shared config (repositoryformatversion, extensions.worktreeConfig).
SPARSE = ("-c", "core.sparseCheckout=true", "-c", "core.sparseCheckoutCone=false")
CONFLICT_MARK_RE = re.compile(r"^(<{7}|>{7})(?: |$)", re.M)


def _sync_paths(root: Path) -> tuple[Path, Path, Path]:
    """The private worktree where local commits are replayed (never the shared working tree), the state of a replay
    that stopped (the upstream commit it replays onto, the local commits already replayed), and the lock."""
    common = Path(_git(root, "rev-parse", "--git-common-dir").strip())
    common = common if common.is_absolute() else (root / common).resolve()
    return common / "labledger-sync", common / "labledger-sync.json", common / "labledger-sync.lock"


def _lock_sync(lock: Path):
    """This clone's sync lock — released when the file closes, also when the process dies; None after SYNC_WAIT s."""
    fh = open(lock, "a")
    deadline = time.monotonic() + SYNC_WAIT
    while True:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fh
        except BlockingIOError:
            if time.monotonic() >= deadline:
                fh.close()
                return None
            time.sleep(0.2)


def drop_replay(root: Path) -> bool:
    """Throw away a replay that stopped. The branch and the working tree never moved; the local commits stay."""
    wt, state, _ = _sync_paths(root)
    had = wt.exists() or state.exists()
    if wt.exists():
        _run(root, "worktree", "remove", "--force", str(wt))
        shutil.rmtree(wt, ignore_errors=True)
    _run(root, "worktree", "prune")
    state.unlink(missing_ok=True)
    return had


def _pending_onto(root: Path, local: str) -> str | None:
    """The upstream commit that a stopped replay replays onto, when that replay is intact and still needed."""
    wt, state, _ = _sync_paths(root)
    if not (state.exists() and (wt / ".git").exists()):
        return None
    onto = json.loads(state.read_text(encoding="utf-8"))["onto"]
    return None if _run(root, "merge-base", "--is-ancestor", onto, local).returncode == 0 else onto


def _replay_onto(root: Path, onto: str) -> tuple[Path, dict]:
    """The stopped replay onto `onto`, or a fresh one: a private worktree at `onto`, sparse — a replay never needs
    the attachments on disk."""
    wt, state, _ = _sync_paths(root)
    if state.exists() and (wt / ".git").exists():
        replay = json.loads(state.read_text(encoding="utf-8"))
        if replay["onto"] == onto:
            return wt, replay
    drop_replay(root)
    _git(root, "worktree", "add", "-q", "--no-checkout", "--detach", str(wt), onto)
    info = Path(_git(wt, "rev-parse", "--absolute-git-dir").strip()) / "info"
    info.mkdir(exist_ok=True)
    (info / "sparse-checkout").write_text(f"/*\n!/{ATTACHMENTS}\n", encoding="utf-8")
    _git(wt, *SPARSE, "read-tree", "-mu", "HEAD")
    replay = {"onto": onto, "head": onto, "done": []}
    state.write_text(json.dumps(replay), encoding="utf-8")
    return wt, replay


def _stopped(wt: Path, what: str, items: list[str]) -> str:
    return (f"not synced — {what}:\n" + "".join(f"  - {i}\n" for i in items)
            + f"  fix the files in {wt}/, then run `python3 scripts/labledger.py sync` again (`sync --abort` gives the replay up);"
            + " the local commits stay local until then")


def _replay(root: Path, wt: Path, replay: dict, commits: list[str]) -> str | None:
    """Replay `commits` (oldest first) in the private worktree, each validated on its own tree. A stopped replay
    resumes with what the operator did there: a merged conflict, fixes to a replayed commit that did not validate,
    or a pick they finished with git themselves. Returns why it stopped, or None."""
    _, state, _ = _sync_paths(root)
    commits = list(commits)
    onto = replay["onto"]

    def head() -> str:
        return _git(wt, "rev-parse", "HEAD").strip()

    def subject(c: str) -> str:
        return f"{c[:7]} \"{_git(root, 'log', '-1', '--format=%s', c).strip()}\""

    def ident(repo: Path, c: str) -> str:
        return _git(repo, "log", "-1", "--format=%an%x00%ae%x00%ad%x00%s", c)

    def index_is_head() -> bool:
        return _run(wt, *SPARSE, "diff", "--cached", "--quiet", "HEAD").returncode == 0

    def record(c: str | None) -> None:
        if c:
            replay["done"].append(c)
        replay["head"] = head()
        state.write_text(json.dumps(replay), encoding="utf-8")

    def invalid(c: str) -> str | None:
        problems = tree_problems(wt, committed=True)[1]
        return _stopped(wt, f"the local commit {subject(c)} replayed onto the upstream does not validate", problems) if problems else None

    picking = _run(wt, "rev-parse", "-q", "--verify", "CHERRY_PICK_HEAD").stdout.strip()
    if picking:  # a conflict the operator was asked to merge by hand
        _git(wt, *SPARSE, "add", "-A")
        staged = [p for p in _git(wt, *SPARSE, "diff", "--cached", "--name-only", "-z", "HEAD").split("\0") if p]
        marked = [p for p in staged if (wt / p).is_file()
                  and CONFLICT_MARK_RE.search((wt / p).read_text(encoding="utf-8", errors="replace"))]
        if marked:
            return _stopped(wt, f"these files still carry conflict markers (replaying {subject(picking)})", marked)
        r = _run(wt, *SPARSE, "cherry-pick", "--skip" if index_is_head() else "--continue", env={**os.environ, "GIT_EDITOR": "true"})
        if r.returncode:
            return _stopped(wt, f"finishing {subject(picking)} failed", (r.stderr or r.stdout).strip().splitlines()[-6:])
        record(picking)
        if picking in commits:
            commits.remove(picking)
    else:
        for made in _git(wt, "rev-list", "--reverse", f"{replay['head']}..HEAD").split():  # picks finished with git by hand
            if not commits or ident(wt, made) != ident(root, commits[0]):
                return _stopped(wt, f"the private worktree has a commit sync cannot match to a local one ({made[:7]}); "
                                    "`sync --abort`, then sync again", [str(wt)])
            record(commits.pop(0))
        if _git(wt, *SPARSE, "status", "--porcelain"):  # fixes to a replayed commit that did not validate
            if head() == onto:
                return _stopped(wt, "the private worktree has changes but no replayed commit to put them in", [str(wt)])
            _git(wt, *SPARSE, "add", "-A")
            r = _run(wt, *SPARSE, "commit", "-q", "--amend", "--no-edit")
            if r.returncode:
                return _stopped(wt, "the fixed commit was refused", (r.stderr or r.stdout).strip().splitlines()[-8:])
            record(None)
    if head() != onto and replay["done"] and (stop := invalid(replay["done"][-1])):
        return stop
    for c in commits:
        r = _run(wt, *SPARSE, "cherry-pick", c)
        if r.returncode:
            unmerged = [p for p in _git(wt, *SPARSE, "diff", "--name-only", "-z", "--diff-filter=U").split("\0") if p]
            if unmerged:
                return _stopped(wt, f"the local commit {subject(c)} and the upstream changed the same lines — merge each "
                                    f"file by hand (keep both sides' records, drop the <<<<<<< ======= >>>>>>> lines)", unmerged)
            if not (_run(wt, "rev-parse", "-q", "--verify", "CHERRY_PICK_HEAD").returncode == 0 and index_is_head()):
                return _stopped(wt, f"replaying {subject(c)} failed", (r.stderr or r.stdout).strip().splitlines()[-6:])
            _git(wt, *SPARSE, "cherry-pick", "--skip")  # nothing left of it: the upstream already has this change
            record(c)
            continue
        record(c)
        if stop := invalid(c):
            return stop
    return None


def _changed(root: Path, old: str, new: str) -> list[tuple[str, str]]:
    out = _git(root, "diff", "--name-status", "-z", "--no-renames", old, new).split("\0")
    return [(out[i], out[i + 1]) for i in range(0, len(out) - 1, 2)]


def _worktree_is(root: Path, rel: str, rev: str) -> bool:
    """Whether the working tree's `rel` is what `rev` has there (both absent counts)."""
    f = root / rel
    blob = _run(root, "rev-parse", "-q", "--verify", f"{rev}:{rel}").stdout.strip()
    if not blob:
        return not (f.exists() or f.is_symlink())
    return f.is_file() and _git(root, "hash-object", "--", rel).strip() == blob


def _overlap(root: Path, old: str, new: str) -> list[str]:
    """Files that moving the branch old → new rewrites but that hold uncommitted work here (another session's):
    changed or staged against `old`, or untracked where a new file arrives — unless already what `new` has."""
    changes = _changed(root, old, new)
    dirty = {p for p in _git(root, "diff", "--name-only", "-z", old).split("\0") if p}
    dirty |= {p for p in _git(root, "diff", "--cached", "--name-only", "-z", old).split("\0") if p}
    busy = [p for status, p in changes if p in dirty or (status == "A" and ((root / p).exists() or (root / p).is_symlink()))]
    return [p for p in busy if not _worktree_is(root, p, new)]


def _update_working_tree(root: Path, old: str, new: str, exps: set[str] | None) -> int:
    """Rewrite in the working tree exactly the files that differ between `old` and `new` (none holds uncommitted
    work — checked first), then point the index at `new` for them. Attachments a partial clone leaves off disk stay
    off it. Returns how many files changed on disk."""
    changes = [(status, rel) for status, rel in _changed(root, old, new) if not off_disk(rel, exps)]
    for status, rel in changes:
        if status == "D":
            (root / rel).unlink(missing_ok=True)
            parent = (root / rel).parent
            while parent != root and parent.is_dir() and not any(parent.iterdir()):
                parent.rmdir()
                parent = parent.parent
    written = [rel for status, rel in changes if status != "D"]
    if written:
        with tempfile.TemporaryDirectory(prefix="labledger-sync-") as tmp:
            env = {**os.environ, "GIT_INDEX_FILE": str(Path(tmp) / "index")}
            _index_from(root, env, new)
            _git(root, "checkout-index", "-f", "-z", "--stdin", env=env, stdin="\0".join(written) + "\0")
    _refresh_index(root, old, new, exps)
    return len(changes)


def _bring_in(root: Path, remote: str, tracking: str) -> tuple[bool, str | None]:
    """Move the branch onto the fetched upstream: fast-forward, or replay the local-only commits on top of it. The
    branch moves by compare-and-swap (sessions keep committing meanwhile); then the working tree follows."""
    branch = _git(root, "symbolic-ref", "-q", "HEAD").strip()
    upstream = _git(root, "rev-parse", tracking).strip()
    exps = sparse_exps(root)
    for _ in range(5):
        local = _git(root, "rev-parse", "HEAD").strip()
        onto = _pending_onto(root, local) or upstream
        if _run(root, "merge-base", "--is-ancestor", onto, local).returncode == 0:
            drop_replay(root)
            return True, None
        base = _git(root, "merge-base", local, onto).strip()
        if _git(root, "rev-list", "--merges", f"{base}..{local}").strip():
            return False, f"not synced — the local commits since {base[:7]} include a merge; sync replays a straight line of commits only"
        local_commits = _git(root, "rev-list", "--reverse", f"{base}..{local}").split()
        incoming = int(_git(root, "rev-list", "--count", f"{local}..{onto}").strip())
        if local_commits:
            wt, replay = _replay_onto(root, onto)
            stop = _replay(root, wt, replay, [c for c in local_commits if c not in replay["done"]])
            if stop:
                return False, stop
            new = _git(wt, "rev-parse", "HEAD").strip()
        else:
            new = onto
        busy = _overlap(root, local, new)
        if busy:
            return False, (f"not synced — {remote} changed files that hold uncommitted work here (another session's):\n"
                           + "".join(f"  - {p}\n" for p in busy)
                           + "  nothing moved; commit or finish those records, then run `python3 scripts/labledger.py sync`")
        if _run(root, "update-ref", "-m", f"labledger sync onto {remote} {onto[:7]}", branch, new, local).returncode:
            continue  # a session committed meanwhile: replay that commit as well
        files = _update_working_tree(root, local, new, exps)
        drop_replay(root)
        with contextlib.redirect_stdout(io.StringIO()):
            rendered = run_render(root, check=False) == 0
        replayed = int(_git(root, "rev-list", "--count", f"{onto}..{new}").strip())
        return True, (f"synced — {incoming} commit(s) from {remote}"
                      + (f", {replayed} local commit(s) replayed on top (new ids)" if replayed else "")
                      + f"; {files} file(s) updated here" + ("" if rendered else "; views/ not rendered — run validate"))
    return False, "not synced — the branch kept moving; run `python3 scripts/labledger.py sync` again"


def sync(root: Path, push: bool = True, attempts: int = 3) -> tuple[bool, str]:
    """Bring in what other servers pushed and push what only this clone has. Local-only commits are replayed one by
    one on top of the upstream in a private worktree (3-way merge of each commit, validated on its own); the branch
    moves by compare-and-swap; the working tree gets exactly the files that changed. Stops without moving anything
    when both sides changed the same lines, when a replayed commit does not validate, or when an incoming file holds
    uncommitted work here. Returns (in sync, report)."""
    blocker = commit_blocker(root)
    if blocker:
        return False, f"not synced — {blocker}"
    up = _upstream(root)
    if up is None:
        return True, "nothing to sync — this branch tracks no upstream (a ledger shared with other machines tracks one: `git branch -u origin/main`)"
    remote, merge, tracking = up
    lock = _lock_sync(_sync_paths(root)[2])
    if lock is None:
        return False, f"not synced — another sync of this clone has run for {SYNC_WAIT} s; the next push tries again"
    lines: list[str] = []
    try:
        return _sync_locked(root, remote, merge, tracking, push, attempts, lines)
    except CommitError as exc:  # git refused something unexpected: say so rather than fail the write that led here
        return False, "\n".join(lines + [f"not synced — {exc}"])
    finally:
        lock.close()


def _sync_locked(root: Path, remote: str, merge: str, tracking: str, push: bool, attempts: int,
                 lines: list[str]) -> tuple[bool, str]:
    for _ in range(attempts):
        try:
            r = _run(root, "fetch", "-q", remote, env=_network_env(root, remote), timeout=PUSH_TIMEOUT)
        except subprocess.TimeoutExpired:
            r = subprocess.CompletedProcess([], 1, "", f"no answer within {PUSH_TIMEOUT} s")
        if r.returncode:
            return False, "\n".join(lines + [f"not synced — cannot fetch from {remote}: {(r.stderr or r.stdout).strip()[-300:]}"])
        done, report = _bring_in(root, remote, tracking)
        if report:
            lines.append(report)
        if not done:
            return False, "\n".join(lines)
        if not push:
            return True, "\n".join(lines)
        state, report = _push(root, remote, merge, tracking)
        if report:
            lines.append(report)
        if state != "rejected":
            return state == "ok", "\n".join(lines)
    return False, "\n".join(lines + [f"not pushed — {remote} kept moving; run `python3 scripts/labledger.py sync` again"])


# ───────────────────────────── numbers ─────────────────────────────

ID_REF = "refs/labledger/ids"  # on the upstream: the last number issued per prefix (EXP, ARC, each TODO prefix)
ID_FILE = "ids.json"
RESERVE_ATTEMPTS = 8


class ReserveError(Exception):
    pass


def _tree_max(root: Path, rev: str, key: str) -> int:
    """The highest number `key` has among the record files of `rev`."""
    pattern = {"EXP": r"experiments/EXP-(\d{4,})\.md", "ARC": r"arcs/ARC-(\d{3,})\.md"}.get(
        key, rf"areas/[^/]+/{re.escape(key)}-(\d{{3,}})\.md")
    names = _git(root, "ls-tree", "-r", "--name-only", rev, "experiments", "arcs", "areas").splitlines()
    return max((int(m.group(1)) for name in names if (m := re.fullmatch(pattern, name))), default=0)


def reserve_number(root: Path, key: str, local_max: int, fmt: str) -> int | None:
    """Reserve the next number for `key` on the upstream: fetch the counter ref, raise it past every number known
    (its own, this working tree's, the upstream branch's), push it back. The push is a compare-and-swap — one that
    lost the race to another server's is refused, and the counter is read again — so two servers never issue the
    same number. None when this clone has no upstream (nobody to share numbers with)."""
    up = _upstream(root)
    if up is None:
        return None
    remote, merge, tracking = up
    who = record_host()  # the counter's log names the server; a server without a name reserves nothing
    env = _network_env(root, remote)
    for _ in range(RESERVE_ATTEMPTS):
        try:
            r = _run(root, "fetch", "-q", remote, "+refs/labledger/*:refs/labledger/*", f"+{merge}:{tracking}", env=env,
                     timeout=PUSH_TIMEOUT)
            if r.returncode:
                raise ReserveError(f"cannot fetch from {remote}: {(r.stderr or r.stdout).strip()[-300:]}")
            old = _run(root, "rev-parse", "-q", "--verify", ID_REF).stdout.strip()
            ids = json.loads(_git(root, "cat-file", "blob", f"{old}:{ID_FILE}")) if old else {}
            n = max(int(ids.get(key, 0)), local_max, _tree_max(root, tracking, key)) + 1
            ids[key] = n
            blob = _git(root, "hash-object", "-w", "--stdin", stdin=json.dumps(ids, sort_keys=True) + "\n").strip()
            tree = _git(root, "mktree", stdin=f"100644 blob {blob}\t{ID_FILE}\n").strip()
            # A token of its own: two sessions of one machine (one git identity, one second) would otherwise build the same
            # commit, and the second push would pass as the first one's — two records under one number.
            new = _git(root, "commit-tree", tree, *(["-p", old] if old else []),
                       stdin=f"reserve {fmt.format(n)} ({who}, {os.urandom(4).hex()})\n").strip()
            r = _run(root, "push", "--porcelain", remote, f"{new}:{ID_REF}", env=env, timeout=PUSH_TIMEOUT)
        except subprocess.TimeoutExpired:
            raise ReserveError(f"{remote} did not answer within {PUSH_TIMEOUT} s")
        except (CommitError, ValueError) as exc:
            raise ReserveError(f"the number counter {ID_REF} cannot be read or written: {exc}")
        if r.returncode == 0:
            _run(root, "update-ref", ID_REF, new)
            return n
        if "[rejected]" not in r.stdout:  # not a race lost to another server's push: the remote refused it, or no answer
            raise ReserveError(f"cannot push the number counter to {remote}: {f'{r.stderr} {r.stdout}'.strip()[-400:]}")
    raise ReserveError(f"the number counter on {remote} kept moving ({RESERVE_ATTEMPTS} attempts)")


def new_id(args, key: str, nums: list[int], fmt: str) -> str | int:
    """The id a new record takes (`fmt` like "EXP-{:04d}"): its number reserved on the upstream so that two servers
    never issue the same one; with no upstream, or with --offline, the local maximum + 1. An int is a refusal's exit code."""
    local = max(nums, default=0)
    try:
        n = None if args.offline else reserve_number(args.root, key, local, fmt)
    except ReserveError as exc:
        print(f"not created — no number could be reserved: {exc}\n  nothing was written. Try again when the upstream "
              "answers, or add --offline to take the local maximum + 1 (if another server takes the same number "
              "meanwhile, sync stops on it)", file=sys.stderr)
        return 1
    return fmt.format(local + 1 if n is None else n)


ZERO_OID = "0" * 40
HOOKS_DIR = "scripts/hooks"
HOOK_HINT = ("commit records with `python3 scripts/labledger.py commit -m \"…\" <ID | path> …` — it commits only those paths "
             "and checks the commit on its own")


def ensure_hooks(root: Path) -> None:
    """Point this clone at the repository's hooks once, so a hand `git commit` and every push are checked as well."""
    if (root / HOOKS_DIR).is_dir() and not _run(root, "config", "--get", "core.hooksPath").stdout.strip():
        if _run(root, "config", "--local", "core.hooksPath", HOOKS_DIR).returncode == 0:
            print(f"hooks: core.hooksPath → {HOOKS_DIR} (hand commits and pushes are validated from now on)")


def _clean_env() -> dict[str, str]:
    """The environment without the GIT_* variables a hook inherits — a child that runs git on other repos (the
    test suite) must not be pointed at this one."""
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def _hook_pre_commit(root: Path) -> int:
    head = _run(root, "rev-parse", "-q", "--verify", "HEAD").stdout.strip()
    env = dict(os.environ)  # git names the index being committed in GIT_INDEX_FILE
    with tempfile.TemporaryDirectory(prefix="labledger-hook-") as tmp:
        graph, problems = tree_problems(Path(tmp), committed=True, tracked=_materialize(root, env, Path(tmp)))
        problems += unbumped(root, graph, head, env)
    if problems:
        print("labledger pre-commit: this commit would not validate:", *(f"  - {p}" for p in problems), f"  {HOOK_HINT}",
              sep="\n", file=sys.stderr)
        return 1
    return 0


def _hook_pre_push(root: Path, lines: list[str]) -> int:
    failed = False
    for line in lines:
        parts = line.split()
        if len(parts) != 4 or parts[1] == ZERO_OID:  # malformed, or deleting a remote ref
            continue
        local_ref, local_sha, remote_ref, remote_sha = parts
        if not remote_ref.startswith("refs/heads/"):  # the number counter (refs/labledger/ids) carries no records
            continue
        if remote_sha != ZERO_OID and _run(root, "cat-file", "-e", f"{remote_sha}^{{commit}}",
                                           env={**os.environ, "GIT_NO_LAZY_FETCH": "1"}).returncode:
            continue  # another server pushed first: git refuses this push ("fetch first") whatever the hook says, and
            #           the push after the sync is checked — the tool tests are not spent on it
        with tempfile.TemporaryDirectory(prefix="labledger-hook-") as tmp:
            env = {**os.environ, "GIT_INDEX_FILE": str(Path(tmp) / "index")}
            _index_from(root, env, local_sha)
            tree = Path(tmp) / "tree"
            problems = tree_problems(tree, committed=True, tracked=_materialize(root, env, tree))[1]
            if problems:
                print(f"labledger pre-push: {local_ref} ({local_sha[:7]}) would not validate:", *(f"  - {p}" for p in problems),
                      sep="\n", file=sys.stderr)
                failed = True
                continue
            tool_changed = remote_sha == ZERO_OID or _run(root, "diff", "--quiet", remote_sha, local_sha, "--",
                                                           "scripts", "tests").returncode != 0
            if not (tool_changed and (tree / "tests").is_dir()):
                continue
            try:
                import pytest  # noqa: F401
            except ImportError:
                print("labledger pre-push: pytest is missing here — the tool tests run in CI only", file=sys.stderr)
                continue
            r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"], cwd=tree,
                               capture_output=True, text=True, env=_clean_env())
            if r.returncode:
                print(f"labledger pre-push: the tool tests fail at {local_sha[:7]}:", *r.stdout.strip().splitlines()[-15:],
                      sep="\n", file=sys.stderr)
                failed = True
    return 1 if failed else 0


def cmd_hook(args) -> int:
    if args.name == "pre-commit":
        return _hook_pre_commit(args.root)
    return _hook_pre_push(args.root, sys.stdin.read().splitlines())


UNFINISHED = 3  # exit code: the record is written but not committed, or committed but not pushed — finish it, do not rerun


def autocommit(args, rels: list[str], subject: str, body: str = "") -> bool:
    """After a write command wrote and rendered: commit what it wrote, and push it. A write that cannot be committed
    on its own stays in the working tree with the reason printed. Returns whether the write is finished: committed and
    pushed, or not to be (--no-commit, --no-push, no git, no upstream)."""
    if getattr(args, "no_commit", False):
        return True
    blocker = commit_blocker(args.root)
    if blocker == "not a git work tree":
        return True
    if blocker:
        print(f"not committed — {blocker}", file=sys.stderr)
        return False
    ensure_hooks(args.root)
    try:
        new = commit_paths(args.root, rels, subject + (f"\n\n{body}" if body else ""))
    except CommitError as exc:
        print(f"not committed — {exc}\n  commit it with what it needs: python3 scripts/labledger.py commit -m \"…\" {' '.join(rels)} …",
              file=sys.stderr)
        return False
    print(f"committed {new[:7]} {subject}" if new else "nothing new to commit")
    pushed, report = (True, None) if getattr(args, "no_push", False) else push_upstream(args.root)
    if report:
        print(report, file=sys.stdout if pushed else sys.stderr)
    return pushed


def written(args, rels: list[str], subject: str, body: str = "") -> int:
    """How every write command ends: render, then commit what it wrote. A file the render cannot read (another
    session's unfinished note, say) stops the views but not the commit: the commit carries `rels` only and is
    validated on its own tree. The record is written either way: a write left uncommitted or unpushed ends with
    UNFINISHED, never the failure of a refusal, since a rerun would write a second record."""
    if run_render(args.root, check=False):
        print(f"{VIEWS}/ are not re-rendered until those are fixed; the record is written")
    return 0 if autocommit(args, rels, subject, body) else UNFINISHED


def commit_targets(root: Path, graph: Graph, targets: list[str]) -> list[str] | str:
    """Repo-relative paths for `commit` targets (addresses or paths); a string is the error."""
    rels: list[str] = []
    for target in targets:
        path = Path(target) if Path(target).is_absolute() else root / target
        if "/" in target or target.endswith(".md") or path.exists():
            try:
                rels.append(path.resolve().relative_to(root).as_posix())
            except ValueError:
                return f"{target} is outside {root}"
            continue
        found = resolve_address(graph, target)
        if found is None:
            return f"unknown address or path `{target}`{did_you_mean(graph, target)}"
        kind, rec = found
        if kind in ("issue", "dec"):
            rels.append(rec.file)
            continue
        rels.append(rec.rel)
        if kind == "exp" and (root / ATTACHMENTS / rec.id).is_dir():
            rels.append(f"{ATTACHMENTS}{rec.id}")
    return list(dict.fromkeys(rels))


def cmd_commit(args) -> int:
    root = args.root
    blocker = commit_blocker(root)
    if blocker:
        print(f"cannot commit — {blocker}", file=sys.stderr)
        return 2
    rels = commit_targets(root, load_graph(root), args.targets)
    if isinstance(rels, str):
        print(rels, file=sys.stderr)
        return 2
    ensure_hooks(root)
    message = args.message if args.message is not None else Path(args.file).read_text(encoding="utf-8")
    try:
        new = commit_paths(root, rels, message)
    except CommitError as exc:
        print(f"not committed — {exc}", file=sys.stderr)
        return 1
    print(f"committed {new[:7]} {message.splitlines()[0]}" if new else "nothing to commit — HEAD already has these paths")
    pushed, report = (True, None) if args.no_push else push_upstream(root)
    if report:
        print(report, file=sys.stdout if pushed else sys.stderr)
    return 0 if pushed else UNFINISHED


def cmd_sync(args) -> int:
    root = args.root
    if commit_blocker(root) == "not a git work tree":
        print("cannot sync — not a git work tree", file=sys.stderr)
        return 2
    if args.abort:
        lock = _lock_sync(_sync_paths(root)[2])
        if lock is None:
            print(f"a sync of this clone has run for {SYNC_WAIT} s — try again when it ends", file=sys.stderr)
            return 1
        try:
            had = drop_replay(root)
        finally:
            lock.close()
        print("dropped the stopped replay; the branch and the local commits are as they were" if had else "no stopped replay")
        return 0
    ok, report = sync(root, push=not args.no_push)
    print(report or "in sync with the upstream")
    return 0 if ok else 1


def cmd_attachments(args) -> int:
    root = args.root
    graph = load_graph(root)
    unknown = [i for i in args.ids if i not in graph.exps]
    if unknown:
        print(f"unknown {', '.join(unknown)} — attachments belong to an EXP-NNNN", file=sys.stderr)
        return 2
    try:
        exps = sparse_exps(root)
    except CommitError as exc:
        print(f"cannot bring attachments in — {exc}", file=sys.stderr)
        return 2
    if exps is None:
        print("this clone checks out every attachment — nothing to bring in")
        return 0
    path = _sparse_file(root)
    before = path.read_text(encoding="utf-8")
    if include_attachments(root, exps, args.ids):
        env = _network_env(root, (_upstream(root) or ("origin",))[0])  # the promisor remote the clone was made from
        r = _run(root, "sparse-checkout", "reapply", env=env)  # one batched download of what is missing
        if r.returncode:
            path.write_text(before, encoding="utf-8")  # a directory named there but not on disk would commit as deleted
            print(f"not brought in — git sparse-checkout reapply: {(r.stderr or r.stdout).strip()[-400:]}", file=sys.stderr)
            return 1
    for i in dict.fromkeys(args.ids):
        files = [f for f in (root / ATTACHMENTS / i).rglob("*") if f.is_file()]
        print(f"{ATTACHMENTS}{i}/: {len(files)} file(s), {sum(f.stat().st_size for f in files) / 1e6:.1f} MB" if files
              else f"{i}: no attachments yet — its directory is checked out here from now on")
    return 0


# ───────────────────────────── setup ─────────────────────────────

SOURCE_NAME = "lab" "ledger"  # the name in the tool's source files, which setup replaces in a new ledger (split so the replacing skips it)
NAME_RE = re.compile(r"^[a-z][a-z0-9]{1,31}$")  # a file name, a Python module, a skill name and, in capitals, a variable prefix
NAME_CHOICES = ("ledger", "labbook", "logbook", "worklog")  # setup recommends the first one nothing on the machine answers to
GIT_REF_SPACES = ("heads", "tags", "remotes", "notes", "replace", "stash", "bisect", "prefetch", "rewritten", "worktree")
LEDGER_FILES = ("scripts/labledger.py", "scripts/hooks/pre-commit", "scripts/hooks/pre-push", "tests/test_labledger.py",
                ".github/workflows/validate.yml", "AGENTS.md", "CLAUDE.md", ".gitignore")  # what a new ledger is made of
AGENT_SKILLS = {  # agent: a folder of its user's skills it reads, and the folder that is there once the agent is installed
    "claude": ("~/.claude/skills", "~/.claude"),  # Claude Code reads its own folder only
    "codex": ("~/.agents/skills", "~/.codex"),  # the others all read the shared one
    "opencode": ("~/.agents/skills", "~/.config/opencode"),
    "gemini": ("~/.agents/skills", "~/.gemini"),
    "copilot": ("~/.agents/skills", "~/.copilot"),
    "cursor": ("~/.agents/skills", "~/.cursor"),
}
SKILL = """---
name: labledger
description: Records the user's work in `labledger`, their git ledger of plans, experiments, decisions and observations. Use when the user mentions `labledger` or one of its records (EXP-0042, a TODO ID, issue:<slug>, dec:<date>-<slug>); when a run, benchmark or test produced numbers worth keeping; when something was decided; when a defect, a surprise or an open question turned up; when work is left for a later session; and before starting work `labledger` may already hold.
allowed-tools: Bash(python3 {root}/scripts/labledger.py *)
metadata:
  ledger-root: {root}
---

# The ledger `labledger`

`labledger` is the git repository `{root}`: one Markdown file per record, written and checked by its tool. Every
command below runs as `python3 {root}/scripts/labledger.py <command>`. A write command commits what it wrote, and pushes
it when the ledger has a remote; the user set the ledger up that way, so record without asking. The rulebook is
`{root}/AGENTS.md`: read it before the first write of a session.{language}

## Look first

1. Once per session, `sync` takes in what other sessions and machines wrote.
2. `find <keywords> [--body]` and `show <ID>` find what is recorded already. A follow-up on a question already recorded
   goes into that record (more runs into the same experiment, more findings into the same issue), and a new record
   names the records it builds on.

## Record what happened

| What happened | Command |
|---|---|
| a run produced numbers | `new exp "<title>"`; its tables go in `experiments/attachments/EXP-NNNN/` with each numeric column's unit in its name (`latency[ms]`) and are linked from the body; when it is done, `set EXP-NNNN --status final --summary "…" --data "<file>: <what one row is>"` |
| something was decided | `new decision <slug> --text "…" --why "…" [--area <area>]` |
| a defect, a surprise or an open question | `new issue <area> <slug> --symptom "…" [--path "…" --hypothesis "…"]` |
| an investigation, review or design | `new inv <area> "<title>"`, then the body, then `set inv:<name> --status concluded --summary "…"` |
| work for later | `new todo <area> "<title>" --from <where it came from>` |
| work finished | `set <ID> --status done --evidence EXP-NNNN --note "…"` |
| an issue settled | `resolve issue <slug> --by <EXP-NNNN, a commit, inv:<name>>` |
| no area fits | `new area <id> "<title>" --prefix <PREFIX>` |
| the user asks what needs attention | `review`, then judge each item as its group says |

A body is edited in place, then committed with `commit -m "{prefix}(<scope>): <what>" <ID> …`, its scope the area,
`exp`, `arc` or `decisions`. AGENTS.md, "What goes where", has every case.

## Others write here too

- A record's number comes from `new`, never from a file name of your own.
- Other sessions write in the same folder at the same time. Commit through the tool only:
  `git add -A`, `git commit -a`, `git pull`, `git rebase`, `git stash`, `--no-verify` and `git push --force` take in
  or rewrite their work.
- `validate` passes before the turn ends.
"""
QUESTIONS = (  # what setup asks before it makes a ledger, in order
    ("name", "what the user calls the ledger. Their agents' skill answers to it (/<name>, $<name> in Codex), and the tool "
             "(scripts/<name>.py), its settings (<name>.yml), its commit messages and variables carry it: 2 to 32 "
             "lowercase letters and digits starting with a letter, not a common word the user says about other things, "
             "and not the name of an agent's own command (review, init, …)"),
    ("path", "a new or empty folder for the ledger's git repository"),
    ("remote", "an empty git repository the ledger is pushed to, so that other machines share it and numbers are reserved "
               "there: a new private GitHub repository without README or license, or one made with `git init --bare -b main`; "
               "or none to keep the ledger on this machine"),
    ("timezone", "the time zone of the day every record is stamped with, the same on every machine"),
    ("language", "the language the agents write the records in"),
    ("agents", f"which agents get the skill ({', '.join(AGENT_SKILLS)})"),
)


def renamed(text: str, name: str) -> str:
    """The source's text as the ledger `name` carries it: every name, variable and ref follows the new name."""
    return text.replace(SOURCE_NAME, name).replace(SOURCE_NAME.upper(), name.upper())


def machine_timezone() -> str:
    """This machine's IANA time zone (TZ, /etc/timezone, the target of /etc/localtime), or UTC."""
    names = [os.environ.get("TZ", "").lstrip(":")]
    with contextlib.suppress(OSError):
        names.append(Path("/etc/timezone").read_text(encoding="utf-8").strip())
    with contextlib.suppress(OSError):
        names.append(os.readlink("/etc/localtime").partition("zoneinfo/")[2])
    for name in filter(None, names):
        with contextlib.suppress(ZoneInfoNotFoundError, ValueError):
            ZoneInfo(name)
            return name
    return "UTC"


def skill_dirs(agents) -> dict[Path, list[str]]:
    """The skill folders to write for `agents`, each once: two agents may read one folder through a link."""
    dirs: dict[Path, list[str]] = {}
    for agent in agents:
        dirs.setdefault(Path(AGENT_SKILLS[agent][0]).expanduser().resolve(), []).append(agent)
    return dirs


def skill_root(folder: Path) -> Path | None:
    """The ledger a skill folder belongs to, when setup wrote it: the `ledger-root` of its SKILL.md."""
    with contextlib.suppress(OSError):
        m = re.search(r"^  ledger-root: (.+)$", (folder / "SKILL.md").read_text(encoding="utf-8"), re.M)
        return Path(m.group(1)) if m else None
    return None


def skills_taken(name: str, dirs, root: Path | None) -> list[str]:
    """The skills called `name` in `dirs` that are not the skill of the ledger at `root` (every one, for a new ledger)."""
    return [f"a skill ({d / name})" for d in dirs if (d / name).exists() and (root is None or skill_root(d / name) != root)]


def name_taken(name: str, dirs) -> list[str]:
    """What already answers to `name` on this machine, which a new ledger called so would be mistaken for."""
    if not NAME_RE.match(name):
        return ["not 2 to 32 lowercase letters and digits starting with a letter"]
    out = skills_taken(name, dirs, None)
    if name in GIT_REF_SPACES:
        out.append(f"a namespace of git's own refs: the number counter refs/{name}/ids would land in it")
    spec = importlib.util.find_spec(name)
    if spec is not None and Path(spec.origin or name).resolve() != Path(__file__).resolve():
        out.append(f"a Python module ({spec.origin}): scripts/{name}.py would hide it from the tool")
    if shutil.which(name):
        out.append(f"a command ({shutil.which(name)})")
    return out


def remote_problem(remote: str) -> str | None:
    """Why a new ledger cannot be pushed to `remote`: it is not reached, or it holds something already."""
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    env.setdefault("GIT_SSH_COMMAND", "ssh -o BatchMode=yes -o ConnectTimeout=15")
    try:
        r = subprocess.run(["git", "ls-remote", remote], capture_output=True, encoding="utf-8", env=env, timeout=PUSH_TIMEOUT)
    except subprocess.TimeoutExpired:
        return f"the remote {remote} did not answer within {PUSH_TIMEOUT} s"
    if r.returncode:
        return f"the remote {remote} cannot be reached — {(r.stderr or r.stdout).strip()}"
    if r.stdout.strip():
        return (f"the remote {remote} is not empty — a new ledger goes into a new empty repository (on GitHub: without "
                "README, license or .gitignore)")
    return None


def make_ledger(path: Path, name: str, timezone: str, language: str, remote: str | None) -> None:
    """Make the ledger `name` in the empty folder `path` from the tool's source files, every name in them replaced, commit
    it, and push it to `remote` when there is one. A ledger in another language than English gets the skeletons and
    words in English under templates/, to translate. CommitError when git refuses."""
    files = []

    def write(rel: str, text: str) -> Path:
        files.append(rel)
        (path / rel).parent.mkdir(parents=True, exist_ok=True)
        (path / rel).write_text(text, encoding="utf-8")
        return path / rel

    for rel in LEDGER_FILES:
        shutil.copymode(ROOT / rel, write(renamed(rel, name), renamed((ROOT / rel).read_text(encoding="utf-8"), name)))
    write(f"{name}.yml", f"timezone: {timezone}\nlanguage: {language}\n")
    if language.lower() not in ("english", "en"):
        for kind, text in SKELETONS.items():
            write(f"{TEMPLATES_DIR}/{kind}.md", renamed(text, name))
        write(f"{TEMPLATES_DIR}/labels.yml", "".join(f"{k}: {json.dumps(v, ensure_ascii=False)}\n" for k, v in LABELS.items()))
    _git(path, "init", "-q", "-b", "main")
    _git(path, "add", "--", *files)
    _git(path, "commit", "-q", "-m", f"{name}: start the ledger")
    print(f"made the ledger {name} in {path}: scripts/{name}.py, {name}.yml, AGENTS.md, the hooks, the tests and CI")
    if remote:
        _git(path, "remote", "add", "origin", remote)
        try:  # before the hooks are on: the first push would run the test suite of a tool copied as it is
            r = _run(path, "push", "-q", "-u", "origin", "main", env=_network_env(path, "origin"), timeout=PUSH_TIMEOUT)
        except subprocess.TimeoutExpired:
            raise CommitError(f"git push: {remote} did not answer within {PUSH_TIMEOUT} s")
        if r.returncode:
            raise CommitError(f"git push to {remote}: {(r.stderr + r.stdout).strip()}")
        print(f"pushed main to {remote}")
    ensure_hooks(path)


def install_skill(root: Path, name: str, language: str | None, prefix: str, dirs: dict[Path, list[str]]) -> list[str]:
    """Write the skill `name` of the ledger at `root` into each skill folder; returns where it went."""
    text = (renamed(SKILL, name).replace("{root}", str(root)).replace("{prefix}", prefix)
            .replace("{language}", f" Write the records in {language}." if language else ""))
    out = []
    for d, agents in dirs.items():
        (d / name).mkdir(parents=True, exist_ok=True)
        (d / name / "SKILL.md").write_text(text, encoding="utf-8")
        out.append(f"{d / name / 'SKILL.md'} ({', '.join(agents)})")
    return out


def cmd_setup(args) -> int:
    """Without the answers it needs, setup prints the questions to ask the user and does nothing else: the agent that
    runs it asks, then runs it again with the answers."""
    found = [a for a, (_, home) in AGENT_SKILLS.items() if Path(home).expanduser().is_dir()]
    tool = f"python3 {Path(__file__).resolve()} setup"
    if NAME != SOURCE_NAME:  # a ledger's own tool: put the skill of its ledger on this machine
        if not args.agents:
            print(f"nothing set up yet — ask the user which agents on this machine get the skill `{NAME}`, which lets them "
                  f"record in {ROOT} on their own (agents: {', '.join(AGENT_SKILLS)}; found here: {', '.join(found) or 'none'}), "
                  f"then run:\n  {tool} --agents {' '.join(found or AGENT_SKILLS)}")
            return 2
        dirs = skill_dirs(args.agents)
        taken = skills_taken(NAME, dirs, ROOT)
        if taken:
            print(f"not installed — `{NAME}` is already {'; '.join(taken)}", file=sys.stderr)
            return 2
        ledger = settings(ROOT)
        print(f"installed the skill {NAME}:", *install_skill(ROOT, NAME, ledger.get("language"), ledger.get("commit_prefix") or NAME,
                                                             dirs), sep="\n  ")
        return 0
    every = skill_dirs(AGENT_SKILLS)
    answers = {"name": args.name, "path": args.path, "remote": args.remote, "timezone": args.timezone,
               "language": args.language, "agents": " ".join(args.agents) if args.agents else None}
    if None in answers.values():
        taken = {n: name_taken(n, every) for n in dict.fromkeys((args.name, *NAME_CHOICES)) if n}
        free = next((n for n in NAME_CHOICES if not taken[n]), None)
        name = args.name or free or "<name>"
        recommended = {"name": name, "path": f"~/{name}", "remote": "none", "timezone": machine_timezone(), "language": "English",
                       "agents": " ".join(found or AGENT_SKILLS)}
        print("nothing set up yet — ask the user each question, leading with the recommended answer, then run setup with the answers:")
        for n, (key, what) in enumerate(((k, w) for k, w in QUESTIONS if answers[k] is None), 1):
            note = {"name": "" if free else f" (none of {', '.join(NAME_CHOICES)} is free here: ask for another)",
                    "timezone": " (this machine's)", "agents": " (found here)" if found else ""}.get(key, "")
            print(f"{n}. --{key}: {what}\n   recommended: {recommended[key]}{note}")
            if key == "name":
                for other, why in taken.items():
                    if why:
                        print(f"   taken here: {other} — {'; '.join(why)}")
        if answers["name"] and taken[answers["name"]]:
            print(f"the name `{answers['name']}` is taken here — {'; '.join(taken[answers['name']])}")
        print(f"\n  {tool} " + " ".join(f"--{k} {answers[k] if answers[k] is not None else recommended[k]}" for k in answers))
        return 2
    path = args.path.expanduser().resolve()
    problems = [f"name `{args.name}` is {why}" for why in name_taken(args.name, every)]
    if path.exists() and (not path.is_dir() or any(path.iterdir())):
        problems.append(f"{path} is not an empty folder — to put an existing ledger's skill on this machine, run that "
                        f"ledger's own setup: python3 {path}/scripts/<name>.py setup")
    try:
        ZoneInfo(args.timezone)
    except (ZoneInfoNotFoundError, ValueError):
        problems.append(f"time zone `{args.timezone}` is not an IANA name this machine knows (like Europe/Berlin)")
    remote = None if args.remote == "none" else args.remote
    if remote and not problems:
        problems += filter(None, [remote_problem(remote)])
    if problems:
        print("nothing set up —", *problems, sep="\n  ", file=sys.stderr)
        return 2
    path.mkdir(parents=True, exist_ok=True)
    try:
        make_ledger(path, args.name, args.timezone, args.language, remote)
    except CommitError as exc:
        for made in path.iterdir():  # the folder was empty: a setup that failed leaves nothing behind to clear
            shutil.rmtree(made) if made.is_dir() else made.unlink()
        print(f"nothing set up — {exc}\n  fix that and run setup again", file=sys.stderr)
        return 1
    print(f"installed the skill {args.name}:", *install_skill(path, args.name, args.language, args.name, skill_dirs(args.agents)),
          sep="\n  ")
    if (path / TEMPLATES_DIR).is_dir():
        print(f"next: new records start from the English skeletons in {TEMPLATES_DIR}/. Translate each {TEMPLATES_DIR}/<kind>.md "
              f"and the values in {TEMPLATES_DIR}/labels.yml into {args.language} (keep the keys and {{evidence}}), then commit "
              f"them: python3 {path}/scripts/{args.name}.py commit -m \"{args.name}(templates): skeletons in {args.language}\" "
              f"{TEMPLATES_DIR}")
    print(f"agents record in it on their own from their next session; on another machine, clone it and run "
          f"`python3 scripts/{args.name}.py setup` there")
    return 0


# ───────────────────────────── main ─────────────────────────────

def main(argv: list[str]) -> int:
    # The global options go before or after the command: every parser takes them (`command`), and none fills in a default
    # (SUPPRESS), so a command's parser never resets one given before it. The defaults are the namespace parse_args starts from.
    common = argparse.ArgumentParser(add_help=False, argument_default=argparse.SUPPRESS)
    common.add_argument("--root", type=Path, help="the ledger's root (default: the directory above scripts/)")
    common.add_argument("--no-commit", action="store_true", help="write commands leave what they wrote uncommitted (commit it later with `commit`)")
    common.add_argument("--no-push", action="store_true", help="commit, but do not push to the upstream (the next commit pushes it)")
    common.add_argument("--offline", action="store_true",
                        help="new: take the local maximum + 1 instead of reserving the number on the upstream (only when the "
                             "upstream cannot be reached; if another machine takes the same number, sync stops on it)")

    def command(**kwargs) -> argparse.ArgumentParser:
        return argparse.ArgumentParser(parents=[common], **kwargs)

    parser = command(prog="labledger.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True, parser_class=command)
    p = sub.add_parser("validate", help="check every record and the rules between them"); p.set_defaults(func=cmd_validate)
    p.add_argument("--committed", action="store_true",
                   help="the tree is a commit's checkout (hooks, CI): views/ must be absent, and stale views are not reported")
    p = sub.add_parser("render", help="rebuild the tables in views/"); p.add_argument("--check", action="store_true"); p.set_defaults(func=cmd_render)
    p = sub.add_parser("sync", help="bring in what other machines pushed and replay this clone's commits on top"); p.set_defaults(func=cmd_sync)
    p.add_argument("--abort", action="store_true", help="drop a stopped replay (.git/labledger-sync); the branch and the local commits stay")
    p = sub.add_parser("attachments", help="partial clone: check out these EXPs' attachment folders from now on")
    p.add_argument("ids", nargs="+", metavar="EXP-NNNN"); p.set_defaults(func=cmd_attachments)
    p = sub.add_parser("fmt", help="rewrite record heads (frontmatter, H1) in canonical form"); p.add_argument("--check", action="store_true"); p.set_defaults(func=cmd_fmt)
    p = sub.add_parser("review", help="the queue of records whose status needs judging"); p.add_argument("--today"); p.set_defaults(func=cmd_review)
    p = sub.add_parser("show", help="a record with its links and inbound records"); p.add_argument("id"); p.set_defaults(func=cmd_show)
    p = sub.add_parser("new", help="start a record"); kind = p.add_subparsers(dest="kind", required=True, parser_class=command)
    ar = kind.add_parser("area", help="areas/<id>/README.md: an area of work whose TODOs are numbered <prefix>-NNN"); ar.set_defaults(func=cmd_new_area)
    ar.add_argument("id", help="the folder name: lowercase letters, digits and dashes"); ar.add_argument("title")
    ar.add_argument("--prefix", required=True, help="the TODO ID prefix: capital letters and dashes, like LAB or LAB-SIM")
    ar.add_argument("--order", type=int, help="the area's place in the area table (default: after the others)")
    t = kind.add_parser("todo", help="areas/<area>/<PREFIX>-NNN.md"); t.set_defaults(func=cmd_new_todo)
    t.add_argument("area"); t.add_argument("title")
    t.add_argument("--priority", default="P1", choices=PRIORITIES)
    t.add_argument("--status", default="planned", choices=TODO_STATUS_OPEN)
    t.add_argument("--prefix", help="the ID prefix (default: the area's)")
    t.add_argument("--from", dest="from_ids", nargs="*", help="where it comes from: EXP-NNNN | issue:<slug> | inv:<id> | dec:<id> | arc:ARC-NNN | user")
    t.add_argument("--depends-on", dest="depends_on", nargs="*")
    t.add_argument("--arc", help="the arc it belongs to (adds arc:ARC-NNN to `from`)")
    e = kind.add_parser("exp", help="experiments/EXP-NNNN.md (status: in_flight)"); e.set_defaults(func=cmd_new_exp)
    e.add_argument("title"); e.add_argument("--model", help="what was measured (a model, a design, a dataset)")
    e.add_argument("--commit", help="the code version(s) it ran on"); e.add_argument("--date")
    e.add_argument("--host", help="the machine its local paths are on (default: this one — LABLEDGER_HOST, else the host name)")
    e.add_argument("--affects", nargs="*", help='the TODOs it moves: "ID: note" or "ID"')
    e.add_argument("--state"); e.add_argument("--summary")
    e.add_argument("--arc", help="the arc it belongs to, ARC-NNN")
    a = kind.add_parser("arc", help="arcs/ARC-NNN.md: work spanning several TODOs and experiments"); a.set_defaults(func=cmd_new_arc)
    a.add_argument("title")
    a.add_argument("--status", default="active", choices=ARC_STATUSES)
    a.add_argument("--members", nargs="*", help="member TODO IDs")
    a.add_argument("--exps", nargs="*", help="member EXP IDs")
    a.add_argument("--areas", nargs="*", help="the areas it touches")
    a.add_argument("--venue", help="where a paper or talk goes — such arcs are listed in views/papers.md")
    a.add_argument("--location", help="where the manuscript or the working copy lives")
    i = kind.add_parser("issue", help="areas/<area>/issues/<slug>.md (status: open)"); i.set_defaults(func=cmd_new_issue)
    i.add_argument("area"); i.add_argument("slug")
    i.add_argument("--symptom", required=True, help="what was observed")
    i.add_argument("--path", help="the files or lines involved"); i.add_argument("--hypothesis")
    i.add_argument("--blocking", help="what it blocks, if anything"); i.add_argument("--date", help="default: today")
    d = kind.add_parser("decision", help="areas/<area>/decisions/<date>-<slug>.md, or decisions/<date>-<slug>.md without --area")
    d.set_defaults(func=cmd_new_decision)
    d.add_argument("slug"); d.add_argument("--text", required=True, help="the decision")
    d.add_argument("--area", help="the area the decision belongs to (none: a decision across areas)")
    d.add_argument("--context"); d.add_argument("--why"); d.add_argument("--date", help="default: today")
    v = kind.add_parser("inv", help="areas/<area>/<slug>-<date>.md: an investigation, review, audit or design (type: inv)")
    v.set_defaults(func=cmd_new_inv)
    v.add_argument("area"); v.add_argument("title")
    v.add_argument("--slug", help="the start of the file name (ASCII kebab-case); default: from the title's ASCII words")
    v.add_argument("--date", help="default: today"); v.add_argument("--summary")
    v.add_argument("--status", default="open", choices=INV_STATUSES)
    v.add_argument("--affects", nargs="*", help='the TODOs and issues it updates: "ID: note" or "issue:<slug>: note"')
    p = sub.add_parser("find", help="find records by keyword"); p.set_defaults(func=cmd_find)
    p.add_argument("words", nargs="+", help="keywords that must all appear (case-insensitive)")
    p.add_argument("--kind", nargs="*", choices=("todo", "exp", "arc", "inv", "issue", "dec"))
    p.add_argument("--status", nargs="*", help="status filter (issues: open, promoted, resolved)")
    p.add_argument("--body", action="store_true", help="search the bodies too")
    p.add_argument("--limit", type=int, default=FIND_LIMIT)
    p = sub.add_parser("resolve", help="mark an issue resolved, with a line saying what resolved it (from open or promoted)")
    rk = p.add_subparsers(dest="kind", required=True, parser_class=command)
    r = rk.add_parser("issue"); r.set_defaults(func=cmd_resolve_issue); r.add_argument("slug")
    r.add_argument("--by", required=True, help="what resolved it: EXP-NNNN | a commit | inv:<id> | a document")
    r.add_argument("--note")
    p = sub.add_parser("promote", help="mark an issue promoted and link it from the TODO's `from` (from open)")
    pk = p.add_subparsers(dest="kind", required=True, parser_class=command)
    pr = pk.add_parser("issue"); pr.set_defaults(func=cmd_promote_issue); pr.add_argument("slug")
    pr.add_argument("--to", required=True, help="the TODO it became (make it first: `new todo … --from issue:<slug>`)")
    pr.add_argument("--note")
    p = sub.add_parser("set", help="change a record's frontmatter; a TODO's, arc's, investigation's or issue's `updated` becomes today")
    p.set_defaults(func=cmd_set)
    p.add_argument("id", help="TODO ID | EXP-NNNN | ARC-NNN | inv:<id> | issue:<slug> (an issue takes no option: its `updated` is raised)")
    p.add_argument("--status"); p.add_argument("--note", help="the status note (todo, arc, inv)")
    p.add_argument("--priority", choices=PRIORITIES)
    p.add_argument("--evidence", nargs="*", help="todo: the EXP IDs it was settled by (added to the list)")
    p.add_argument("--affects", nargs="*", help='exp, inv: "ID: note" (an entry for the same ID is replaced)')
    p.add_argument("--summary", help="exp, inv")
    p.add_argument("--state", help="exp: the state/ files it is the source of, or n/a")
    p.add_argument("--data", nargs="*", help='exp: "<file>: <what one row is>" for each table in experiments/attachments/EXP-NNNN/ '
                   '(an entry for the same file is replaced), or "n/a (why)"')
    p.add_argument("--superseded-by", dest="superseded_by", help="exp: with status superseded")
    p.add_argument("--host", help="exp: the machine its local paths are on")
    p.add_argument("--model", help="exp"); p.add_argument("--commit", help="exp")
    p.add_argument("--date", help="the date of `updated` and of a close decision (default: today; for records made late)")
    p = sub.add_parser("commit", help="commit these records and files only (other uncommitted work stays as it is)")
    p.set_defaults(func=cmd_commit)
    p.add_argument("targets", nargs="+", help="ID | EXP-NNNN (with its attachments) | ARC-NNN | inv:<id> | issue:<slug> | dec:<id> | area | path")
    msg = p.add_mutually_exclusive_group(required=True)
    msg.add_argument("-m", "--message", help="the commit message (`<prefix>(<area>): <what>`; a body after a blank line)")
    msg.add_argument("-F", "--file", help="a file holding the commit message")
    p = sub.add_parser("setup", help="make a new ledger (in the tool's source repository), or put this ledger's skill on this machine; "
                                     "without its answers it prints the questions to ask the user")
    p.set_defaults(func=cmd_setup)
    p.add_argument("--name", help="a new ledger: what it is called")
    p.add_argument("--path", type=Path, help="a new ledger: the new or empty folder it goes in")
    p.add_argument("--remote", help="a new ledger: the empty git repository it is pushed to, or none")
    p.add_argument("--timezone", help="a new ledger: the IANA time zone of its dates, like Europe/Berlin")
    p.add_argument("--language", help="a new ledger: the language its records are written in")
    p.add_argument("--agents", nargs="+", choices=tuple(AGENT_SKILLS), help="the agents on this machine that get the skill")
    p = sub.add_parser("hook", help="run by the git hooks: pre-commit validates the index to commit, pre-push the tips to push")
    p.set_defaults(func=cmd_hook)
    p.add_argument("name", choices=("pre-commit", "pre-push"))
    p.add_argument("hook_args", nargs="*", help="what git passes (pre-push: the remote's name and URL)")
    args = parser.parse_args(argv, argparse.Namespace(root=ROOT, no_commit=False, no_push=False, offline=False))
    args.root = args.root.resolve()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
