"""labledger.py — behaviour tests on a minimal ledger built in a temp dir, plus a validate of the demo ledger."""
from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import labledger as ll  # noqa: E402

README = """# ledger

## Areas

The area and arc tables are in `views/areas.md` (`render` makes them).
"""
AREA_README = """
## TODOs

The TODO table is in `views/areas/<area>.md` (`render` makes it).
"""
PREFIX = {"lab": "LAB", "docs": "DOC", "sim": "SIM-TEST"}
TODAY = ll.record_today()


@pytest.fixture(autouse=True)
def plain_machine(monkeypatch, tmp_path_factory):
    """Every test runs on a machine with a usable name (a fresh install's is localhost) and with git's own defaults: the
    tester's global and system git configuration (a global hooks path, signing, aliases) never reaches the test repos."""
    monkeypatch.setenv("LABLEDGER_HOST", "test-server")
    empty = tmp_path_factory.getbasetemp() / "gitconfig"
    empty.touch()
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for var in ("LABLEDGER_SSH_SHARE", "GIT_SSH_COMMAND"):
        monkeypatch.delenv(var, raising=False)


def entry(path: Path, kind: str, fm: dict, body: str) -> None:
    """Write one decision (kind "dec") or issue file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(ll.entry_text(kind, fm, body), encoding="utf-8")


def seed(root: Path, areas=("lab",)) -> Path:
    (root / "experiments").mkdir(parents=True)
    (root / "README.md").write_text(README, encoding="utf-8")
    entry(root / "decisions" / "2026-09-01-seed-decision.md", "dec", {"date": "2026-09-01", "title": "seed-decision"},
          "decided to open LAB-001")
    for n, area in enumerate(areas, 1):
        d = root / "areas" / area
        d.mkdir(parents=True)
        fm = {"id": area, "type": "area", "title": f"{area} title", "prefix": PREFIX[area], "order": str(n)}
        (d / "README.md").write_text(ll.canonical_head("area", fm) + AREA_README, encoding="utf-8")
        entry(d / "issues" / f"seed-issue-{area}.md", "issue", {"date": "2026-09-01", "status": "open"}, "Seen while working on LAB-001.")
        entry(d / "decisions" / f"2026-09-02-seed-decision-{area}.md", "dec", {"date": "2026-09-02", "title": f"seed-decision-{area}"},
              "Nothing yet.")
    return root


def run(root: Path, *argv: str) -> int:
    return ll.main(["--root", str(root), *argv])


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = seed(tmp_path / "ledger")
    assert run(root, "new", "todo", "lab", "first TODO", "--from", "user") == 0
    assert run(root, "new", "exp", "first experiment", "--affects", "LAB-001: progress", "--summary", "short summary") == 0
    assert run(root, "new", "arc", "first arc", "--members", "LAB-001", "--exps", "EXP-0001", "--areas", "lab") == 0
    inv = root / "areas" / "lab" / "review-2026-09-03.md"
    fm = {"id": "review-2026-09-03", "type": "inv", "title": "review", "area": "lab", "date": "2026-09-03",
          "status": "concluded", "summary": "gist", "affects": ["LAB-001: review result"]}
    inv.write_text(ll.canonical_head("inv", fm) + "# review\n\nBody.\n", encoding="utf-8")
    assert run(root, "render") == 0
    return root


def todo_path(root: Path, node_id="LAB-001") -> Path:
    return root / "areas" / "lab" / f"{node_id}.md"


def test_new_numbers_and_validate_green(repo: Path, capsys):
    assert todo_path(repo).is_file() and (repo / "experiments" / "EXP-0001.md").is_file() and (repo / "arcs" / "ARC-001.md").is_file()
    assert run(repo, "new", "todo", "lab", "second") == 0 and todo_path(repo, "LAB-002").is_file()
    assert run(repo, "validate") == 0
    assert "validation passed" in capsys.readouterr().out
    for view in ("index.md", "areas.md", "areas/lab.md", "log.md", "papers.md", "active.md"):
        assert (repo / "views" / view).is_file(), view


def test_numbers_past_the_padding_width_are_records_like_the_rest(repo: Path, capsys):
    """Numbers are padded to 3 digits (TODO, ARC) or 4 (EXP) and keep counting past that: LAB-1000, ARC-1000 and
    EXP-10000 are read, linked, mentioned and ordered like the narrower ones."""
    graph = ll.load_graph(repo)
    for old, new in (("LAB-001", "LAB-999"), ("ARC-001", "ARC-999"), ("EXP-0001", "EXP-9999")):
        kind, node = ll.resolve_address(graph, old)
        fm = {**node.fm, "id": new, **({"arc": "ARC-999"} if kind == "exp" else {})}
        node.path.with_name(f"{new}.md").write_text(ll.new_node_text(kind, fm, "Body."), encoding="utf-8")
    assert run(repo, "new", "todo", "lab", "thousandth") == 0
    assert run(repo, "new", "arc", "thousandth arc", "--members", "LAB-1000") == 0
    assert run(repo, "new", "exp", "ten-thousandth", "--arc", "ARC-999", "--affects", "LAB-1000: progress") == 0
    exp = repo / "experiments" / "EXP-10000.md"
    exp.write_text(exp.read_text(encoding="utf-8") + "\nWorked on LAB-1000.\n", encoding="utf-8")
    assert run(repo, "validate") == 0
    graph = ll.load_graph(repo)
    assert "LAB-1000" in graph.todos and "ARC-1000" in graph.arcs and "EXP-10000" in graph.exps
    assert graph.exp_ids(graph.arcs["ARC-999"]) == ["EXP-0001", "EXP-9999", "EXP-10000"], "ordered by number"
    assert "LAB-1000" in graph.exps["EXP-10000"].mentions
    capsys.readouterr()
    assert run(repo, "show", "LAB-1000") == 0
    assert "EXP-10000" in capsys.readouterr().out


def test_a_repository_without_records_runs_from_its_first_record(tmp_path: Path, capsys):
    """A fresh template holds a README and empty folders kept by `.gitkeep` (git stores no empty folder, so areas/ and
    experiments/ may be missing altogether). Every read command runs on it, and the first record makes its folder."""
    root = tmp_path / "template"
    for folder in ("decisions", "arcs", "state"):
        (root / folder).mkdir(parents=True)
        (root / folder / ".gitkeep").write_text("", encoding="utf-8")
    (root / "README.md").write_text(README, encoding="utf-8")
    for argv, rc in ((("validate",), 0), (("render",), 0), (("review",), 0), (("find", "x"), 1), (("show", "x"), 2)):
        assert run(root, *argv) == rc, argv
    assert run(root, "new", "exp", "first experiment") == 0 and (root / "experiments" / "EXP-0001.md").is_file()
    assert run(root, "new", "decision", "first-rule", "--text", "first decision") == 0
    assert run(root, "validate") == 0


def test_render_writes_views_only(repo: Path, capsys):
    exp = repo / "experiments" / "EXP-0001.md"
    exp.write_text(exp.read_text(encoding="utf-8") + "\n\n  odd   spacing\t\n\n\n", encoding="utf-8")
    todo = todo_path(repo)
    todo.write_text(todo.read_text(encoding="utf-8") + "\n## A hand-written section\n\n  tabs\tand   spaces   \n", encoding="utf-8")
    records = {p: p.read_bytes() for p in repo.rglob("*.md") if "views" not in p.relative_to(repo).parts}
    (repo / "views" / "areas" / "gone.md").write_text("an old area\n", encoding="utf-8")
    assert run(repo, "render") == 0
    assert {p: p.read_bytes() for p in records} == records, "render writes nothing outside views/"
    assert not (repo / "views" / "areas" / "gone.md").exists(), "a view of an area that no longer exists is removed"
    assert run(repo, "render", "--check") == 0
    assert "[LAB-001](../../areas/lab/LAB-001.md)" in (repo / "views" / "areas" / "lab.md").read_text(encoding="utf-8")
    assert "[EXP-0001](../experiments/EXP-0001.md)" in (repo / "views" / "log.md").read_text(encoding="utf-8")
    assert run(repo, "show", "LAB-001") == 0
    assert "| [EXP-0001](../../experiments/EXP-0001.md) | affects |" in capsys.readouterr().out, "show computes the inbound records"


def test_a_committed_tree_carries_no_views(repo: Path, capsys):
    assert run(repo, "validate", "--committed") == 1, "a commit's checkout has no views/"
    assert "views/ is committed" in capsys.readouterr().out
    shutil.rmtree(repo / "views")
    assert run(repo, "validate", "--committed") == 0
    assert run(repo, "validate") == 0
    assert "views/ are stale" in capsys.readouterr().out


def test_noncanonical_head_is_reported_and_fmt_restores_bytes(repo: Path, capsys):
    exp = repo / "experiments" / "EXP-0001.md"
    original = exp.read_bytes()
    lines = exp.read_text(encoding="utf-8").split("\n")
    i, j = lines.index(next(l for l in lines if l.startswith("title:"))), lines.index(next(l for l in lines if l.startswith("status:")))
    lines[i], lines[j] = lines[j], lines[i]
    lines[[k for k, l in enumerate(lines) if l.startswith("# EXP-0001")][0]] = "# EXP-0001 — old title"
    exp.write_text("\n".join(lines), encoding="utf-8")
    assert run(repo, "validate") == 1
    assert "not canonical" in capsys.readouterr().out
    assert run(repo, "render", "--check") == 0, "render must not treat a record head as a generated view"
    assert run(repo, "fmt") == 0
    assert exp.read_bytes() == original


def test_unquoted_bracket_title_is_a_parse_error_not_a_rewrite(repo: Path, capsys):
    todo = todo_path(repo)
    text = todo.read_text(encoding="utf-8").replace('title: "first TODO"', "title: [WIP] a hand-written title")
    todo.write_text(text, encoding="utf-8")
    assert run(repo, "validate") == 1
    assert "starts with [ but is not a list" in capsys.readouterr().out
    assert run(repo, "render") == 1
    assert todo.read_text(encoding="utf-8") == text, "a record that fails to parse must be left untouched"


def test_schema_rejects_list_in_scalar_and_unknown_key(repo: Path, capsys):
    todo = todo_path(repo)
    text = todo.read_text(encoding="utf-8").replace('title: "first TODO"', "title: [a, b]\nfoo: bar")
    todo.write_text(text, encoding="utf-8")
    assert run(repo, "validate") == 1
    out = capsys.readouterr().out
    assert "`title` must be a single value" in out and "unknown key `foo`" in out


def test_mentions_count_a_non_latin_suffix_but_not_a_longer_id(tmp_path: Path, capsys):
    root = seed(tmp_path / "r", areas=("lab", "docs", "sim"))
    assert run(root, "new", "todo", "lab", "a") == 0
    assert run(root, "new", "todo", "docs", "b", "--prefix", "TEST") == 0
    assert run(root, "new", "todo", "sim", "c") == 0
    assert run(root, "new", "exp", "mention in the body") == 0
    exp = root / "experiments" / "EXP-0001.md"
    exp.write_text(exp.read_text(encoding="utf-8") + "\nSIM-TEST-001 is being set up; see LAB-001を参照.\n", encoding="utf-8")
    assert run(root, "render") == 0
    graph = ll.load_graph(root)
    assert graph.exps["EXP-0001"].mentions == {"SIM-TEST-001", "LAB-001"}
    for todo_id, mentioned in (("LAB-001", True), ("SIM-TEST-001", True), ("TEST-001", False)):
        capsys.readouterr()
        assert run(root, "show", todo_id) == 0
        assert ("[EXP-0001]" in capsys.readouterr().out) is mentioned, todo_id


def test_inbound_lists_declared_and_mentioned_records(repo: Path):
    rows = ll.inbound_for(ll.load_graph(repo), ll.load_graph(repo).todos["LAB-001"])
    kinds = {(m.source, m.kind) for m in rows}
    assert ("EXP-0001", "affects") in kinds
    assert ("inv:review-2026-09-03", "investigation") in kinds
    assert ("ARC-001", "arc") in kinds
    assert ("dec:2026-09-01-seed-decision", "decision") in kinds
    assert ("issue:seed-issue-lab", "issue") in kinds


def test_review_queue_lists_inflow_newer_than_updated(repo: Path, capsys):
    assert run(repo, "new", "exp", "future experiment", "--date", "2099-01-01", "--affects", "LAB-001") == 0
    assert run(repo, "review", "--today", "2099-01-02") == 0
    out = capsys.readouterr().out
    assert "### LAB-001" in out and "EXP-0002" in out
    todo = todo_path(repo)
    todo.write_text(todo.read_text(encoding="utf-8").replace(f"updated: {TODAY}", "updated: 2099-01-02", 1), encoding="utf-8")
    assert run(repo, "render") == 0
    assert run(repo, "review", "--today", "2099-01-03") == 0
    assert "### LAB-001" not in capsys.readouterr().out


def test_review_queues_what_has_waited_too_long(repo: Path, capsys):
    """Beyond the TODOs: experiments left in flight, open issues and investigations nobody updated, done arcs with open
    members, and promoted issues whose TODOs are all closed."""
    assert run(repo, "new", "todo", "lab", "fix the hang") == 0
    assert run(repo, "promote", "issue", "seed-issue-lab", "--to", "LAB-002") == 0
    assert run(repo, "set", "LAB-002", "--status", "done", "--note", "fixed") == 0
    assert run(repo, "new", "issue", "lab", "old-one", "--symptom", "x", "--date", "2026-01-01") == 0
    assert run(repo, "new", "inv", "lab", "Open question", "--date", "2026-01-01") == 0
    assert run(repo, "new", "issue", "lab", "went-away", "--symptom", "the hang is resolved after a reboot") == 0
    assert run(repo, "set", "ARC-001", "--status", "done", "--note", "the goal moved") == 0
    capsys.readouterr()
    assert run(repo, "review", "--today", "2099-01-01") == 0
    out = capsys.readouterr().out
    for line in (f"- EXP-0001 · since {TODAY} — first experiment",
                 "- `issue:old-one` · updated 2026-01-01 (areas/lab/issues/old-one.md)",
                 "- `inv:open-question-2026-01-01` · updated 2026-01-01 (areas/lab/open-question-2026-01-01.md)",
                 "- ARC-001 · first arc — open: LAB-001",
                 "- `seed-issue-lab` — LAB-002 done",
                 f"- {TODAY} `went-away` (areas/lab/issues/went-away.md)"):
        assert line in out, line
    assert "`inv:review-2026-09-03`" not in out, "a concluded investigation waits for nothing"
    assert run(repo, "review", "--today", TODAY) == 0
    out = capsys.readouterr().out
    assert "EXP-0001 · since" not in out and "`issue:old-one`" in out, "an experiment of today is not late; an issue of January is"


def test_an_edited_investigation_or_issue_raises_its_updated(hooked: Path, capsys):
    """review dates the TODOs an investigation or issue mentions by its `updated`. A commit that changes one and leaves
    `updated` behind today is refused — by `commit` and by the hook of a hand commit — and `set <address>` raises it."""
    todo = todo_path(hooked)
    todo.write_text(todo.read_text(encoding="utf-8").replace(f"created: {TODAY}\nupdated: {TODAY}", "created: 2026-09-01\nupdated: 2026-09-04"),
                    encoding="utf-8")
    assert run(hooked, "commit", "-m", "x", "LAB-001") == 0
    inv = hooked / "areas" / "lab" / "review-2026-09-03.md"
    inv.write_text(inv.read_text(encoding="utf-8") + "\nLAB-001 needs another look.\n", encoding="utf-8")
    head = git(hooked, "rev-parse", "HEAD")
    capsys.readouterr()
    assert run(hooked, "commit", "-m", "x", "inv:review-2026-09-03") == 1 and git(hooked, "rev-parse", "HEAD") == head
    assert ("changed, but `updated` is still 2026-09-03 — `python3 scripts/labledger.py set inv:review-2026-09-03`"
            in capsys.readouterr().err)
    assert run(hooked, "review") == 0 and "inv:review-2026-09-03" not in capsys.readouterr().out
    assert run(hooked, "set", "inv:review-2026-09-03") == 0 and f"updated: {TODAY}" in inv.read_text(encoding="utf-8")
    assert git(hooked, "status", "--porcelain") == ""
    assert run(hooked, "review") == 0 and f"- {TODAY} inv:review-2026-09-03 [investigation]" in capsys.readouterr().out
    issue = hooked / "areas" / "lab" / "issues" / "seed-issue-lab.md"
    issue.write_text(issue.read_text(encoding="utf-8") + "Seen again.\n", encoding="utf-8")
    git(hooked, "add", str(issue))
    r = subprocess.run(["git", "-C", str(hooked), "commit", "-q", "-m", "by hand"], capture_output=True, text=True)
    assert r.returncode != 0 and "set issue:seed-issue-lab" in r.stderr
    assert run(hooked, "set", "issue:seed-issue-lab") == 0 and f"updated: {TODAY}" in issue.read_text(encoding="utf-8")
    issue.write_text(issue.read_text(encoding="utf-8") + "Seen once more the same day.\n", encoding="utf-8")
    git(hooked, "commit", "-q", "-am", "the same day: `updated` is today already")
    assert git(hooked, "status", "--porcelain") == ""


def test_show_resolves_every_address_kind(repo: Path, capsys):
    for address in ("LAB-001", "EXP-0001", "ARC-001", "inv:review-2026-09-03", "dec:2026-09-01-seed-decision", "issue:seed-issue-lab", "seed-issue-lab"):
        assert run(repo, "show", address) == 0, address
        assert capsys.readouterr().out.strip()
    assert run(repo, "show", "NOPE-999") == 2


def test_stale_views_are_a_note_not_a_failure(repo: Path, capsys):
    exp = repo / "experiments" / "EXP-0001.md"
    exp.write_text(exp.read_text(encoding="utf-8").replace("summary: \"short summary\"", "summary: \"changed summary\""), encoding="utf-8")
    assert run(repo, "validate") == 0, "views/ are a local cache, not part of a commit"
    out = capsys.readouterr().out
    assert "view(s) in views/ are stale" in out and "gitignored" in out and "edited by hand" in out
    assert run(repo, "render", "--check") == 1
    listed = [line for line in capsys.readouterr().out.splitlines() if line.startswith("- ")]
    assert "- views/log.md" in listed and set(listed) <= set(out.splitlines()), "validate names the views render --check lists"
    assert run(repo, "render") == 0 and run(repo, "render", "--check") == 0


def test_the_demo_ledger_validates(capsys):
    """The ledger this tool lives in is checked by validate itself — in CI, and by the hooks on every commit and push, which
    know git's index; this suite also runs in the pre-push hook's copy of a tip, which holds no attachments but tables."""
    demo = ll.ROOT / "examples" / "demo"
    if not demo.is_dir():
        pytest.skip("no examples/demo in this repository")
    assert ll.main(["--root", str(demo), "validate"]) == 0, capsys.readouterr().out


def test_area_readme_declares_prefix_and_order(tmp_path: Path, capsys):
    root = seed(tmp_path / "r", areas=("sim", "lab"))   # seeded in this order: sim=1, lab=2
    assert run(root, "new", "todo", "lab", "a") == 0 and todo_path(root).is_file()
    assert run(root, "new", "todo", "sim", "b") == 0 and (root / "areas/sim/SIM-TEST-001.md").is_file()
    table = (root / "views" / "areas.md").read_text(encoding="utf-8")
    assert table.index("areas/sim/") < table.index("areas/lab/"), "the area table follows `order`"
    index = (root / "views" / "index.md").read_text(encoding="utf-8")
    assert index.index("SIM-TEST-001") < index.index("LAB-001"), "index follows the area order too"
    assert run(root, "show", "lab") == 0 and "LAB-001" in capsys.readouterr().out
    twin = root / "areas" / "sim" / "LAB-001.md"
    twin.write_text(todo_path(root).read_text(encoding="utf-8").replace("area: lab", "area: sim"), encoding="utf-8")
    assert run(root, "validate") == 1, "one ID names one record, whatever its area"
    assert "areas/sim/LAB-001.md: TODO id `LAB-001` also used by areas/lab/LAB-001.md" in capsys.readouterr().out
    twin.unlink()
    readme = root / "areas" / "lab" / "README.md"
    readme.write_text(readme.read_text(encoding="utf-8").split("---\n", 2)[2], encoding="utf-8")   # drop the frontmatter
    assert run(root, "validate") == 1
    assert "areas/lab/README.md: every area needs a README.md starting with frontmatter" in capsys.readouterr().out
    assert run(root, "new", "todo", "lab", "c") == 2


# ───────────────────────────── write path ─────────────────────────────

def test_new_area_starts_an_area_and_its_numbering(repo: Path, capsys):
    assert run(repo, "new", "area", "data-pipeline", "Data pipeline", "--prefix", "PIPE") == 0
    text = (repo / "areas" / "data-pipeline" / "README.md").read_text(encoding="utf-8")
    assert "\nprefix: PIPE\norder: 2\n---\n# data-pipeline — Data pipeline\n" in text and "## Open questions" in text
    assert run(repo, "new", "todo", "data-pipeline", "first step") == 0 and (repo / "areas" / "data-pipeline" / "PIPE-001.md").is_file()
    for argv in (("Data_Pipe", "x", "--prefix", "DP"), ("data-pipeline", "again", "--prefix", "DP"),
                 ("other", "x", "--prefix", "LAB"), ("other", "x", "--prefix", "pipe")):
        assert run(repo, "new", "area", *argv) == 2, argv
    assert not (repo / "areas" / "other").exists()
    assert run(repo, "validate") == 0


def write_state(root: Path, rows: str = "") -> None:
    (root / "state").mkdir(exist_ok=True)
    (root / "state" / "health.md").write_text(f"# health\n\n```yaml\nkind: state\n```\n\n{rows}\n", encoding="utf-8")


def test_new_issue_promote_and_resolve(repo: Path, capsys):
    issue = repo / "areas" / "lab" / "issues" / "board-hangs.md"
    assert run(repo, "new", "issue", "lab", "board-hangs", "--symptom", "status register stuck at 0x2", "--path", "main.c:151") == 0
    assert issue.read_text(encoding="utf-8") == f"---\ndate: {TODAY}\nstatus: open\n---\n- Symptom: status register stuck at 0x2\n- Path: main.c:151\n"
    assert run(repo, "new", "issue", "lab", "board-hangs", "--symptom", "dup") == 2
    assert run(repo, "new", "issue", "lab", "bad slug!", "--symptom", "x") == 2
    assert run(repo, "validate") == 0
    assert run(repo, "new", "todo", "lab", "look into the hang") == 0
    assert run(repo, "promote", "issue", "board-hangs", "--to", "LAB-002") == 0
    text = issue.read_text(encoding="utf-8")
    assert "\nstatus: promoted\n" in text and text.endswith(f"- Path: main.c:151\n- Promoted ({TODAY}): LAB-002\n")
    assert "from: [issue:board-hangs]" in todo_path(repo, "LAB-002").read_text(encoding="utf-8"), "promote links the TODO back"
    assert run(repo, "promote", "issue", "board-hangs", "--to", "LAB-002") == 1, "already promoted"
    assert run(repo, "resolve", "issue", "board-hangs", "--by", "EXP-0001", "--note", "does not reproduce") == 0
    text = issue.read_text(encoding="utf-8")
    assert "\nstatus: resolved\n" in text
    assert text.endswith(f"- Promoted ({TODAY}): LAB-002\n- Resolved ({TODAY}): EXP-0001 — does not reproduce\n"), "history stays with the issue"
    hit = ll.load_graph(repo).issues_by_slug["board-hangs"]
    assert hit.status == "resolved" and not hit.is_open
    assert run(repo, "resolve", "issue", "nope", "--by", "x") == 2
    assert run(repo, "validate") == 0


def test_new_decision_area_and_global(repo: Path, capsys):
    assert run(repo, "new", "decision", "use-tmpfs", "--area", "lab", "--text", "use tmpfs", "--context", "the disk is slow", "--why", "twice as fast") == 0
    path = repo / "areas" / "lab" / "decisions" / f"{TODAY}-use-tmpfs.md"
    assert path.read_text(encoding="utf-8") == f"---\ndate: {TODAY}\ntitle: use-tmpfs\n---\n- Context: the disk is slow\n- Decision: use tmpfs\n- Why: twice as fast\n"
    assert run(repo, "new", "decision", "use-tmpfs", "--area", "lab", "--text", "again") == 2, "same dec: id"
    assert run(repo, "new", "decision", "global-rule", "--text", "global | rule") == 0
    assert (repo / "decisions" / f"{TODAY}-global-rule.md").read_text(encoding="utf-8").endswith("\n- Decision: global | rule\n")
    assert run(repo, "show", f"dec:{TODAY}-global-rule") == 0 and "global | rule" in capsys.readouterr().out
    assert run(repo, "show", f"dec:{TODAY}-use-tmpfs") == 0
    assert run(repo, "new", "decision", "bad slug!", "--text", "x") == 2
    assert run(repo, "validate") == 0


def test_new_inv_is_a_canonical_node(repo: Path, capsys):
    assert run(repo, "new", "inv", "lab", "Data path audit", "--summary", "gist",
               "--affects", "LAB-001: reviewed", "issue:seed-issue-lab: checked again") == 0
    path = repo / "areas" / "lab" / f"data-path-audit-{TODAY}.md"
    text = path.read_text(encoding="utf-8")
    assert text.startswith(f"---\nid: data-path-audit-{TODAY}\ntype: inv\n") and "\n# Data path audit\n" in text and "## Conclusion" in text
    assert run(repo, "validate") == 0 and run(repo, "fmt", "--check") == 0
    assert f"inv:data-path-audit-{TODAY}" in (repo / "views" / "areas" / "lab.md").read_text(encoding="utf-8")
    assert run(repo, "new", "inv", "lab", "日本語の題名") == 2, "no ASCII word to name the file from"
    assert run(repo, "new", "inv", "lab", "日本語の題名", "--slug", "non-latin-review") == 0
    assert run(repo, "new", "inv", "lab", "x", "--affects", "NOPE-001") == 2


def test_set_closes_todo_and_writes_the_close_decision(repo: Path, capsys):
    todo = todo_path(repo)
    todo.write_text(todo.read_text(encoding="utf-8").replace(f"created: {TODAY}\nupdated: {TODAY}", "created: 2020-01-01\nupdated: 2020-01-01"), encoding="utf-8")
    assert run(repo, "render") == 0
    assert run(repo, "set", "LAB-001", "--status", "dropped") == 2, "dropped needs a note"
    assert run(repo, "set", "LAB-001", "--status", "done", "--evidence", "EXP-0001", "--note", "settled by the first experiment") == 0
    text = todo.read_text(encoding="utf-8")
    assert "status: done\n" in text and f"updated: {TODAY}" in text and "evidence: [EXP-0001]" in text and 'status_note: "settled by the first experiment"' in text
    decisions = repo / "areas" / "lab" / "decisions"
    dec = decisions / f"{TODAY}-close-LAB-001.md"
    assert dec.read_text(encoding="utf-8") == f'---\ndate: {TODAY}\ntitle: "close LAB-001"\n---\n- done: settled by the first experiment (evidence EXP-0001)\n'
    assert run(repo, "validate") == 0
    before = sorted(decisions.iterdir())
    assert run(repo, "set", "LAB-001", "--status", "done") == 0 and sorted(decisions.iterdir()) == before, "no duplicate close decision"
    assert run(repo, "set", "LAB-001", "--note", "closed again", "--date", "2099-01-01") == 0
    assert sorted(decisions.iterdir()) == before, "not on another day either"
    assert run(repo, "show", "LAB-001") == 0 and f"dec:{TODAY}-close-LAB-001" in capsys.readouterr().out, "the close decision names its TODO"
    assert run(repo, "new", "todo", "lab", "second") == 0
    second = todo_path(repo, "LAB-002")
    second.write_text(second.read_text(encoding="utf-8").replace("status: planned", "status: done"), encoding="utf-8")
    assert run(repo, "render") == 0
    assert run(repo, "validate") == 1 and "has no `<date>-close-LAB-002.md` decision" in capsys.readouterr().out
    assert run(repo, "set", "NOPE-1", "--note", "x") == 2
    assert run(repo, "set", "ARC-001", "--status", "parked") == 2, "parked arc needs a note"
    assert run(repo, "set", "ARC-001", "--status", "parked", "--note", "waiting") == 0
    assert run(repo, "set", "EXP-0001", "--note", "x") == 2, "an EXP has summary, not status_note"


def test_exp_state_claims_must_be_cited_in_state_files(repo: Path, capsys):
    write_state(repo)
    assert run(repo, "set", "EXP-0001", "--status", "final", "--state", "health.md smoke row", "--data", "n/a (a smoke check)") == 0
    assert run(repo, "validate") == 1
    assert "state/health.md never cites EXP-0001" in capsys.readouterr().out
    write_state(repo, "| llsim | pass | EXP-0001 |")
    assert run(repo, "validate") == 0
    write_state(repo, "| llsim | pass | EXP-0001/0002 |")
    assert run(repo, "validate") == 1 and "write every ID in full" in capsys.readouterr().out
    write_state(repo)
    assert run(repo, "set", "EXP-0001", "--state", "n/a (health.md row taken over by a later experiment)") == 0
    assert run(repo, "validate") == 0
    assert run(repo, "set", "EXP-0001", "--state", "somewhere") == 2


def exp_table(root: Path, text: str, name: str = "runs.csv", link: bool = True) -> Path:
    """A table in EXP-0001's attachment folder, linked from its body unless `link` is False."""
    path = root / "experiments" / "attachments" / "EXP-0001" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    exp = root / "experiments" / "EXP-0001.md"
    if link:
        exp.write_text(exp.read_text(encoding="utf-8") + f"\nFrom [{name}](attachments/EXP-0001/{name}).\n", encoding="utf-8")
    return path


def test_a_final_experiment_names_the_tables_its_numbers_come_from(repo: Path, capsys):
    """`data` names the tables (CSV, TSV, JSON) in the EXP's attachment folder that its numbers come from, each linked from
    the body, or says `n/a (why)`. A final experiment has it, and each table reads as one: a header, rows as wide as the
    header, one plain value per cell, a unit at the end of the name of every column of numbers."""
    assert run(repo, "set", "EXP-0001", "--status", "final") == 2
    assert "a final experiment names the files its numbers come from" in capsys.readouterr().err
    good = "model,latency[ms],runs[1]\nresnet,12.5,3\nmobilenet,4.25,3\n"
    table = exp_table(repo, good, link=False)
    assert run(repo, "set", "EXP-0001", "--status", "final", "--data", "runs.csv: one row per model") == 2
    assert "the body does not link `data` file runs.csv" in capsys.readouterr().err
    exp_table(repo, good)
    for data, problem in (("runs.xlsx: one row per model", "must be CSV, TSV or JSON"), ("gone.csv: rows", "is not in the ledger"),
                          ("../EXP-0002/runs.csv: rows", "must be a path inside"), ("runs.csv", "is neither"),
                          ("n/a", "is neither")):
        assert run(repo, "set", "EXP-0001", "--status", "final", "--data", data) == 2, data
        assert problem in capsys.readouterr().err, data
    assert run(repo, "set", "EXP-0001", "--status", "final", "--data", "runs.csv: one row per model") == 0
    for text, problem in (("model,latency\nresnet,12.5\n", "column `latency` holds numbers: end its name with their unit"),
                          ('model,cycles[1]\nresnet,"1,786,215"\n', "column `cycles[1]` has `1,786,215` in one cell"),
                          ("model,passed[1]\nresnet,0/3024\n", "column `passed[1]` has `0/3024` in one cell"),
                          ('model,cycles[1]\nresnet,"[1200, 1215]"\n', "column `cycles[1]` has `[1200, 1215]` in one cell"),
                          ('model,mismatches[1]\nresnet,"(0, 0, 0)"\n', "column `mismatches[1]` has `(0, 0, 0)` in one cell"),
                          ("model,runs[1]\nresnet,3;4;5\n", "column `runs[1]` has `3;4;5` in one cell"),
                          ("model,latency[ms]\nresnet,12.5,3\n", "row 2 has 3 fields, the header 2"),
                          ("resnet,12.5\nmobilenet,4.25\n", "the first row must be a header")):
        table.write_text(text, encoding="utf-8")
        assert run(repo, "validate") == 1 and f"experiments/attachments/EXP-0001/runs.csv: {problem}" in capsys.readouterr().out, text
    table.write_text(good, encoding="utf-8")
    exp_table(repo, "seed[id]\tloss[1]\n1\t0.5\n", "seeds.tsv")
    exp_table(repo, "{", "board.json")
    assert run(repo, "set", "EXP-0001", "--data", "seeds.tsv: one row per seed", "board.json: the board's own report") == 2
    assert "board.json: not JSON" in capsys.readouterr().err
    exp_table(repo, '{"cycles": 1585980}', "board.json", link=False)
    assert run(repo, "set", "EXP-0001", "--data", "seeds.tsv: one row per seed", "board.json: the board's own report") == 0
    assert run(repo, "set", "EXP-0001", "--data", "runs.csv: one row per model and batch") == 0
    data = ["seeds.tsv: one row per seed", "board.json: the board's own report", "runs.csv: one row per model and batch"]
    assert ll.load_graph(repo).exps["EXP-0001"].fm["data"] == data, "an entry for the same file is replaced, the others stay"
    assert run(repo, "set", "EXP-0001", "--data", "runs.csv: rows", "n/a (no numbers)") == 2
    assert "either `n/a (why)` or a list of files, not both" in capsys.readouterr().err
    assert run(repo, "set", "EXP-0001", "--data", "n/a (a smoke run: pass or fail, no numbers)") == 0
    assert ll.load_graph(repo).exps["EXP-0001"].fm["data"] == ["n/a (a smoke run: pass or fail, no numbers)"], "n/a replaces them all"
    assert run(repo, "validate") == 0


def test_set_commits_an_experiment_with_its_tables_and_the_commit_reads_them(grepo: Path, capsys):
    """`set` commits the EXP's attachment folder along, so the commit holds the tables `data` names, and the commit's
    check reads those tables as validate does."""
    table = exp_table(grepo, "model,latency[ms]\nresnet,12.5\n")
    assert run(grepo, "set", "EXP-0001", "--status", "final", "--data", "runs.csv: one row per model") == 0
    assert set(git(grepo, "show", "--name-only", "--format=", "HEAD").split()) == {
        "experiments/EXP-0001.md", "experiments/attachments/EXP-0001/runs.csv"}
    table.write_text("model,latency\nresnet,12.5\n", encoding="utf-8")
    head = git(grepo, "rev-parse", "HEAD")
    assert run(grepo, "commit", "-m", "x", "EXP-0001") == 1
    assert "column `latency` holds numbers" in capsys.readouterr().err and git(grepo, "rev-parse", "HEAD") == head


def test_record_links_point_at_files_of_the_ledger(repo: Path, capsys):
    """A relative link in a record names a file or folder of the ledger: not an absolute path, not a file outside the
    ledger, not views/ (each clone renders its own), and never a file that is not there. URLs, anchors and code are not
    such links."""
    todo = todo_path(repo)
    todo.write_text(todo.read_text(encoding="utf-8") + "\n[a](/home/me/notes.md) [b](../../../outside.md) [c](../../views/index.md) "
                    "[d](missing.md)\n[record](../../experiments/EXP-0001.md) [folder](../../experiments) "
                    "[web](https://example.org/x.md) [here](#problem) `[code](nowhere.md)`\n\n```\n[fenced](nowhere.md)\n```\n",
                    encoding="utf-8")
    issue = repo / "areas" / "lab" / "issues" / "seed-issue-lab.md"
    issue.write_text(issue.read_text(encoding="utf-8") + "See [the log](../logs/run.txt).\n", encoding="utf-8")
    assert run(repo, "validate") == 1
    out = capsys.readouterr().out
    for problem in ("areas/lab/LAB-001.md: link `/home/me/notes.md` is an absolute path",
                    "areas/lab/LAB-001.md: link `../../../outside.md` leaves the ledger",
                    "areas/lab/LAB-001.md: link `../../views/index.md` points into views/",
                    "areas/lab/LAB-001.md: link `missing.md` points at areas/lab/missing.md, which is not in the ledger",
                    "areas/lab/issues/seed-issue-lab.md: link `../logs/run.txt` points at areas/lab/logs/run.txt"):
        assert problem in out, problem
    assert out.count(": link `") == 5, "a record, a folder, a URL, an anchor and code are fine"


def test_state_tables_hold_one_current_value_per_item(repo: Path, capsys):
    """state/*.csv holds current values: one row per item under its conditions (the columns beyond the fixed ones), each
    from a final experiment whose `state` names the table, with a unit, a kind, the code version and the day measured.
    render shows each table in views/state/."""
    (repo / "state").mkdir()
    table = repo / "state" / "latency.csv"
    head = "item,model,value,unit,kind,exp,commit,measured_at,note\n"
    row = "latency,resnet,12.5,ms,measured,EXP-0001,abc1234,2026-09-05,median of 3 runs\n"
    table.write_text(head + row, encoding="utf-8")
    assert run(repo, "validate") == 1 and "EXP-0001 is in_flight — a current value comes from a final experiment" in capsys.readouterr().out
    assert run(repo, "set", "EXP-0001", "--status", "final", "--data", "n/a (numbers in the body)") == 0
    assert run(repo, "validate") == 1 and "EXP-0001's `state` does not name latency.csv" in capsys.readouterr().out
    assert run(repo, "set", "EXP-0001", "--state", "latency.csv") == 0
    assert run(repo, "validate") == 0
    assert "| latency | resnet | 12.5 | ms | measured | EXP-0001 |" in (repo / "views" / "state" / "latency.md").read_text(encoding="utf-8")
    for rows, problem in ((row.replace("measured,", "guess,"), "kind `guess` is not measured or estimate"),
                          (row.replace("2026-09-05", "Sep 5"), "measured_at `Sep 5` is not YYYY-MM-DD"),
                          (row.replace("abc1234", ""), "commit is required"),
                          (row + row.replace("12.5", "11.0"), "state/latency.csv:3: latency under the same conditions is also state/latency.csv:2")):
        table.write_text(head + rows, encoding="utf-8")
        assert run(repo, "validate") == 1 and problem in capsys.readouterr().out, problem
    table.write_text(head + row + row.replace("resnet", "mobilenet"), encoding="utf-8")
    assert run(repo, "validate") == 0, "another model is another condition"
    capsys.readouterr()
    (repo / "state" / "copy.csv").write_text(head + row, encoding="utf-8")
    (repo / "state" / "loose.csv").write_text("item,value\nlatency,12.5\n", encoding="utf-8")
    assert run(repo, "set", "EXP-0001", "--state", "latency.csv copy.csv") == 0
    assert run(repo, "validate") == 1
    out = capsys.readouterr().out
    assert "state/latency.csv:2: latency under the same conditions is also state/copy.csv:2" in out
    assert "state/loose.csv: missing column(s) unit, kind, exp, commit, measured_at" in out
    (repo / "state" / "copy.csv").unlink()
    (repo / "state" / "loose.csv").unlink()
    assert run(repo, "set", "EXP-0001", "--state", "latency.csv") == 0 and run(repo, "validate") == 0
    table.unlink()
    assert run(repo, "set", "EXP-0001", "--state", "n/a") == 0
    assert not (repo / "views" / "state" / "latency.md").exists(), "render removes the view of a table that is gone"


def test_find_matches_titles_notes_issues_and_decisions(repo: Path, capsys):
    assert run(repo, "find", "first") == 0
    out = capsys.readouterr().out
    assert "LAB-001 · planned" in out and "EXP-0001 · in_flight" in out and "ARC-001 · active" in out
    assert run(repo, "find", "seed", "--kind", "issue", "dec") == 0
    out = capsys.readouterr().out
    assert "issue:seed-issue-lab · open" in out and "dec:2026-09-01-seed-decision" in out and "LAB-001 ·" not in out
    assert run(repo, "find", "seed", "--limit", "1") == 0 and " more (" in capsys.readouterr().out
    assert run(repo, "find", "zzz-none") == 1
    assert run(repo, "find", "done when") == 1, "bodies are not searched by default"
    assert run(repo, "find", "done when", "--body") == 0 and "LAB-001" in capsys.readouterr().out


def test_show_lists_relations_with_status(repo: Path, capsys):
    assert run(repo, "new", "todo", "lab", "second", "--depends-on", "LAB-001", "--from", "EXP-0001", "inv:review-2026-09-03") == 0
    assert run(repo, "show", "LAB-002") == 0
    out = capsys.readouterr().out
    assert "## Links" in out
    assert "- depends_on LAB-001 · planned · P1 · updated" in out
    assert "- from EXP-0001 · in_flight ·" in out and "- from inv:review-2026-09-03 · concluded · 2026-09-03 · review" in out



def test_write_commands_refuse_records_that_would_not_validate(repo: Path, capsys):
    snapshot = {p: p.read_bytes() for p in repo.rglob("*.md")}
    refused = [
        ("new", "todo", "lab", "x", "--prefix", "lab"),
        ("new", "todo", "lab", "x", "--from", "NOPE-001"),
        ("new", "todo", "lab", "x", "--from", "issue:no-such-issue"),
        ("new", "todo", "lab", "x", "--depends-on", "issue:seed-issue-lab"),
        ("new", "todo", "lab", "x", "--arc", "ARC-999"),
        ("new", "exp", "x", "--arc", "ARC-999"),
        ("new", "exp", "x", "--affects", "NOPE-001: note"),
        ("new", "arc", "x", "--members", "NOPE-001"),
        ("new", "arc", "x", "--areas", "nowhere"),
        ("new", "inv", "lab", "x", "--affects", "issue:no-such-issue: note"),
        ("set", "LAB-001", "--status", "bogus"),
        ("set", "LAB-001", "--status", "dropped"),
        ("set", "LAB-001", "--evidence", "NOPE-001"),
        ("set", "LAB-001", "--priority", "P1", "--status", "done", "--evidence", "EXP-0001", "--note", "x", "--summary", "s"),
        ("set", "EXP-0001", "--status", "superseded"),
        ("set", "EXP-0001", "--status", "superseded", "--superseded-by", "EXP-9999"),
        ("set", "EXP-0001", "--summary", "x" * (ll.SUMMARY_MAX + 1)),
        ("set", "EXP-0001", "--state", "somewhere"),
        ("set", "EXP-0001", "--host", "ml server"),
        ("set", "LAB-001", "--model", "model-a", "--commit", "a223f70"),
        ("new", "exp", "x", "--host", "a/b"),
        ("set", "ARC-001", "--status", "parked"),
        ("set", "inv:review-2026-09-03", "--affects", "NOPE-001: note"),
        ("set", "LAB-001", "--note", "x" * (ll.STATUS_NOTE_MAX + 1)),
        ("set", "EXP-0001", "--status", "final"),
        ("set", "issue:seed-issue-lab", "--note", "x"),
        ("set", "issue:seed-issue-lab", "--date", "2026-08-31"),
    ]
    for argv in refused:
        assert run(repo, *argv) == 2, argv
        capsys.readouterr()
    assert {p: p.read_bytes() for p in repo.rglob("*.md")} == snapshot, "a refused write must not touch any file"
    assert run(repo, "validate") == 0
    assert run(repo, "set", "EXP-0001", "--summary", "x" * ll.SUMMARY_MAX) == 0, "a summary may run longer than a status note"
    assert set(ll.SET_OPTIONS) == {"status", "note", "priority", "evidence", "affects", "summary", "state", "data", "superseded_by",
                                   "host", "model", "commit"}


def test_a_refusal_says_how_to_fix_it(repo: Path, capsys):
    """Agents fix what a message tells them, so each says what to write instead: the length against its limit, where an
    issue an experiment settled goes, what replaces an option the record does not take, what a misspelt address may
    have meant, the unit a column lacks and the line of a head that fmt rewrites."""
    for argv, hint in (
        (("set", "EXP-0001", "--summary", "x" * (ll.SUMMARY_MAX + 7)), f"summary is {ll.SUMMARY_MAX + 7} chars, over {ll.SUMMARY_MAX}"),
        (("set", "EXP-0001", "--affects", "issue:seed-issue-lab: settled"), "`resolve issue seed-issue-lab --by EXP-0001`"),
        (("set", "EXP-0001", "--note", "x"), "says what it found in --summary"),
        (("set", "issue:seed-issue-lab", "--status", "resolved"), "`resolve issue <slug> --by …`"),
        (("show", "inv:review"), "did you mean inv:review-2026-09-03?"),
        (("set", "LAB-01", "--note", "x"), "did you mean LAB-001"),
    ):
        assert run(repo, *argv) == 2, argv
        assert hint in capsys.readouterr().err, argv
    exp_table(repo, "model,latency\nresnet,12.5\n")
    assert run(repo, "set", "EXP-0001", "--data", "runs.csv: one row per model") == 2
    assert "like `latency[<unit>]`" in capsys.readouterr().err
    todo = todo_path(repo)
    todo.write_text(todo.read_text(encoding="utf-8").replace("priority: P1", "priority:  P1"), encoding="utf-8")
    assert run(repo, "validate") == 1
    assert "LAB-001.md: frontmatter/H1 is not canonical (line 7 is `priority:  P1`, fmt writes `priority: P1`)" in capsys.readouterr().out
    assert run(repo, "fmt", "--check") == 1 and "LAB-001.md: line 7 is `priority:  P1`" in capsys.readouterr().out


def test_check_node_is_the_rule_set_validate_uses(repo: Path):
    graph = ll.load_graph(repo)
    for kind in ll.NODE_TABLES:
        for node in getattr(graph, kind + "s").values():
            assert ll.check_node(graph, kind, node) == [] and ll.check_file(kind, node) == [], node.rel
    assert ll.validate_cross(graph, repo) == []
    todo = graph.todos["LAB-001"]
    bad = ll.replace(todo, fm={**todo.fm, "status": "dropped", "from": ["NOPE-001"], "priority": "P9"})
    problems = ll.check_node(graph, "todo", bad)
    assert any("dropped needs a status_note" in p for p in problems)
    assert any("`from` points at unknown NOPE-001" in p for p in problems)
    assert any("priority `P9`" in p for p in problems)


def test_resolve_address_covers_every_address_kind(repo: Path, capsys):
    graph = ll.load_graph(repo)
    expect = {"LAB-001": "todo", "lab-001": "todo", "EXP-0001": "exp", "ARC-001": "arc", "arc:ARC-001": "arc",
              "lab": "area", "inv:review-2026-09-03": "inv", "review-2026-09-03": "inv",
              "dec:2026-09-01-seed-decision": "dec", "dec:2026-09-02-seed-decision-lab": "dec",
              "issue:seed-issue-lab": "issue", "seed-issue-lab": "issue"}
    for address, kind in expect.items():
        found = ll.resolve_address(graph, address)
        assert found and found[0] == kind, (address, found)
        assert run(repo, "show", address) == 0, address
        capsys.readouterr()
    for address in ("NOPE-001", "inv:nope", "dec:nope", "issue:nope", "arc:ARC-999", "nowhere"):
        assert ll.resolve_address(graph, address) is None, address
        assert run(repo, "show", address) == 2
    assert run(repo, "set", "issue:seed-issue-lab", "--note", "x") == 2, "an issue takes no option"
    assert run(repo, "set", "lab", "--note", "x") == 2
    assert run(repo, "new", "todo", "lab", "x", "--from", "ARC-001") == 2
    assert "must be written `arc:ARC-001`" in capsys.readouterr().err



def record_bytes(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*.md") if "views" not in p.relative_to(root).parts}


def test_a_decision_or_issue_write_touches_its_own_file_only(repo: Path):
    before = record_bytes(repo)
    assert run(repo, "new", "issue", "lab", "first", "--symptom", "a") == 0
    assert run(repo, "new", "decision", "d1", "--area", "lab", "--text", "t") == 0
    assert run(repo, "new", "decision", "g1", "--text", "global") == 0
    after = record_bytes(repo)
    assert set(after) - set(before) == {"areas/lab/issues/first.md", f"areas/lab/decisions/{TODAY}-d1.md", f"decisions/{TODAY}-g1.md"}
    assert {k: v for k, v in after.items() if k in before} == before, "no other file changes"
    assert run(repo, "resolve", "issue", "first", "--by", "EXP-0001") == 0
    changed = {k: v for k, v in record_bytes(repo).items() if after.get(k) != v}
    assert changed == {"areas/lab/issues/first.md":
                       f"---\ndate: {TODAY}\nstatus: resolved\nupdated: {TODAY}\n---\n- Symptom: a\n- Resolved ({TODAY}): EXP-0001\n".encode()}
    assert run(repo, "validate") == 0


def test_decision_and_issue_files_are_checked(repo: Path, capsys):
    lab = repo / "areas" / "lab"
    entry(lab / "decisions" / "2026-09-03-wrong-name.md", "dec", {"date": "2026-09-03", "title": "right name"}, "x")
    entry(lab / "issues" / "odd.md", "issue", {"date": "2026-09-03", "status": "closed"}, "x")
    (lab / "issues" / "keys.md").write_text("---\ndate: 2026-09-03\nstatus: open\nowner: a\n---\nx\n", encoding="utf-8")
    entry(repo / "decisions" / "2026-09-02-seed-decision-lab.md", "dec", {"date": "2026-09-02", "title": "seed-decision-lab"}, "x")
    docs = repo / "areas" / "docs"
    docs.mkdir()
    (docs / "README.md").write_text(ll.canonical_head("area", {"id": "docs", "type": "area", "title": "d", "prefix": "DOC", "order": "2"})
                                    + AREA_README, encoding="utf-8")
    entry(docs / "issues" / "seed-issue-lab.md", "issue", {"date": "2026-09-03", "status": "open"}, "x")
    assert run(repo, "validate") == 1
    out = capsys.readouterr().out
    assert "2026-09-03-wrong-name.md: the file name must be `2026-09-03-right-name.md`" in out
    assert "odd.md: status must be open | promoted | resolved" in out and "keys.md: issue frontmatter is date · status (and updated)" in out
    assert "decision id `2026-09-02-seed-decision-lab` is defined in both" in out
    assert "issue slug `seed-issue-lab` is used by 2 files" in out
    for path in (lab / "decisions" / "2026-09-03-wrong-name.md", lab / "issues" / "odd.md",
                 lab / "issues" / "keys.md", repo / "decisions" / "2026-09-02-seed-decision-lab.md", docs / "issues" / "seed-issue-lab.md"):
        path.unlink()
    order = lab / "decisions" / "2026-09-04-order.md"
    order.write_text("---\ntitle: order\ndate: 2026-09-04\n---\n\nBody\n", encoding="utf-8")
    assert run(repo, "validate") == 1 and "2026-09-04-order.md: frontmatter is not canonical" in capsys.readouterr().out
    assert run(repo, "fmt") == 0 and order.read_text(encoding="utf-8") == "---\ndate: 2026-09-04\ntitle: order\n---\nBody\n"
    assert run(repo, "validate") == 0


def test_set_date_backfills_updated_and_the_close_decision(repo: Path, capsys):
    todo = todo_path(repo)
    todo.write_text(todo.read_text(encoding="utf-8").replace(f"created: {TODAY}", "created: 2020-01-01", 1), encoding="utf-8")
    assert run(repo, "render") == 0
    assert run(repo, "set", "LAB-001", "--status", "done", "--evidence", "EXP-0001", "--note", "long ago", "--date", "2026-03-01") == 0
    assert "updated: 2026-03-01" in todo.read_text(encoding="utf-8")
    assert (repo / "areas" / "lab" / "decisions" / "2026-03-01-close-LAB-001.md").is_file()
    assert run(repo, "set", "LAB-001", "--note", "x", "--date", "March") == 2
    assert run(repo, "set", "LAB-001", "--note", "x", "--date", "2019-01-01") == 2, "updated before created is refused"
    assert run(repo, "validate") == 0


@pytest.fixture
def utc_server(monkeypatch):
    """This process as a machine on UTC at 2026-09-28 20:00 UTC — already 2026-09-29 05:00 in Tokyo."""
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    instant = dt.datetime(2026, 9, 28, 20, tzinfo=dt.timezone.utc).timestamp()
    monkeypatch.setattr(time, "time", lambda: instant)
    yield
    monkeypatch.undo()
    time.tzset()


def test_every_stamped_date_is_in_the_ledger_s_time_zone(tmp_path: Path, utc_server, monkeypatch, capsys):
    """Machines writing to one ledger must stamp the same day: labledger.yml names the zone, else the machine's own is used."""
    assert dt.date.today().isoformat() == "2026-09-28", "the machine's own clock says the 28th"
    root = seed(tmp_path / "ledger")
    settings = root / "labledger.yml"
    settings.write_text("# the zone every date is stamped in\ntimezone: Asia/Tokyo\n", encoding="utf-8")
    for argv in (("new", "todo", "lab", "first TODO"),
                 ("new", "exp", "first experiment", "--affects", "LAB-001: progress", "--summary", "short summary"),
                 ("new", "arc", "an arc", "--members", "LAB-001"),
                 ("new", "issue", "lab", "hang", "--symptom", "hang"),
                 ("new", "decision", "d1", "--text", "t", "--area", "lab"),
                 ("new", "inv", "lab", "audit", "--slug", "audit"),
                 ("promote", "issue", "hang", "--to", "LAB-001"),
                 ("resolve", "issue", "hang", "--by", "EXP-0001"),
                 ("set", "LAB-001", "--status", "done", "--evidence", "EXP-0001", "--note", "done"),
                 ("review",)):
        assert run(root, *argv) == 0, argv
    assert "created: 2026-09-29\nupdated: 2026-09-29\n" in todo_path(root).read_text(encoding="utf-8")
    assert "date: 2026-09-29\n" in (root / "experiments" / "EXP-0001.md").read_text(encoding="utf-8")
    decisions = root / "areas" / "lab" / "decisions"
    assert (decisions / "2026-09-29-d1.md").is_file() and (decisions / "2026-09-29-close-LAB-001.md").is_file()
    assert "- Resolved (2026-09-29): EXP-0001" in (root / "areas" / "lab" / "issues" / "hang.md").read_text(encoding="utf-8")
    assert (root / "areas" / "lab" / "audit-2026-09-29.md").is_file()
    assert (root / "views" / "review.md").read_text(encoding="utf-8").startswith("# Review queue — 2026-09-29")
    assert not [p for p in root.rglob("*.md") if "2026-09-28" in p.name + p.read_text(encoding="utf-8")], "no machine date anywhere"
    assert run(root, "validate") == 0
    settings.unlink()
    assert run(root, "new", "todo", "lab", "second") == 0
    assert "created: 2026-09-28\n" in todo_path(root, "LAB-002").read_text(encoding="utf-8"), "without a zone, the machine's"
    settings.write_text("timezone: Mars/Olympus\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="Mars/Olympus"):
        run(root, "new", "todo", "lab", "third")
    assert not todo_path(root, "LAB-003").exists()
    capsys.readouterr()
    assert run(root, "validate") == 1 and "timezone `Mars/Olympus` is not an IANA time zone" in capsys.readouterr().out


def test_an_exp_names_the_server_its_paths_are_on(repo: Path, monkeypatch, capsys):
    """Paths in an EXP body (/data/…, work/…) are one machine's; `host` says which. It may be left out."""
    def exp(n: int) -> str:
        return (repo / "experiments" / f"EXP-{n:04d}.md").read_text(encoding="utf-8")

    assert "\nhost: test-server\nstatus: in_flight\n" in exp(1), "LABLEDGER_HOST names this server"
    monkeypatch.delenv("LABLEDGER_HOST")
    monkeypatch.setattr(ll.socket, "gethostname", lambda: "box.example.org")
    assert run(repo, "new", "exp", "this machine") == 0 and "\nhost: box\n" in exp(2), "else the host name up to its first dot"
    monkeypatch.setattr(ll.socket, "gethostname", lambda: "localhost.localdomain")
    with pytest.raises(SystemExit, match="LABLEDGER_HOST"):
        run(repo, "new", "exp", "a machine without a name")
    assert not (repo / "experiments" / "EXP-0003.md").exists(), "localhost names no server"
    monkeypatch.setenv("LABLEDGER_HOST", "node-2")
    assert run(repo, "new", "exp", "ran on the lab server", "--host", "lab-server") == 0 and "\nhost: lab-server\n" in exp(3)
    assert run(repo, "set", "EXP-0003", "--host", "node-2") == 0 and "\nhost: node-2\n" in exp(3)
    monkeypatch.setenv("LABLEDGER_HOST", "no such host")
    with pytest.raises(SystemExit, match="LABLEDGER_HOST"):
        run(repo, "new", "exp", "x")
    assert not (repo / "experiments" / "EXP-0004.md").exists()
    (repo / "experiments" / "EXP-0001.md").write_text(exp(1).replace("host: test-server\n", ""), encoding="utf-8")
    assert run(repo, "validate") == 0, "host is optional: a one-machine ledger does not need it"


def test_set_writes_an_exp_s_model_and_commit_as_new_exp_does(repo: Path):
    """Learned after `new exp`, they are set like the other fields — a hand edit of the frontmatter leaves views/ behind."""
    def fields(n: int) -> list[str]:
        text = (repo / "experiments" / f"EXP-{n:04d}.md").read_text(encoding="utf-8")
        return [line for line in text.split("\n") if line.startswith(("model:", "commit:"))]

    assert fields(1) == ['model: ""', 'commit: ""']
    assert run(repo, "new", "exp", "from the start", "--model", "model-a (v2), model-b", "--commit", "a223f70") == 0
    assert run(repo, "set", "EXP-0001", "--model", "model-a (v2), model-b", "--commit", "a223f70") == 0
    assert fields(1) == fields(2) == ['model: "model-a (v2), model-b"', "commit: a223f70"]
    assert run(repo, "render", "--check") == 0 and run(repo, "validate") == 0


def test_a_server_without_a_name_reserves_no_number(grepo: Path, origin: Path, monkeypatch):
    monkeypatch.delenv("LABLEDGER_HOST")
    monkeypatch.setattr(ll.socket, "gethostname", lambda: "localhost.localdomain")
    for argv in (("new", "todo", "lab", "x"), ("new", "arc", "x")):
        with pytest.raises(SystemExit, match="LABLEDGER_HOST"):
            run(grepo, *argv)
    assert git(origin, "for-each-ref", "refs/labledger") == "" and not todo_path(grepo, "LAB-002").exists()


def test_promote_only_from_open_and_resolve_from_open_or_promoted(repo: Path, capsys):
    assert run(repo, "new", "issue", "lab", "a", "--symptom", "a") == 0
    assert run(repo, "new", "issue", "lab", "b", "--symptom", "b") == 0
    assert run(repo, "resolve", "issue", "a", "--by", "EXP-0001") == 0
    assert run(repo, "promote", "issue", "a", "--to", "LAB-001") == 1, "a resolved issue is not promoted"
    assert "only open issues can be promoted" in capsys.readouterr().err
    assert run(repo, "promote", "issue", "b", "--to", "LAB-001") == 0
    assert run(repo, "promote", "issue", "b", "--to", "LAB-001") == 1, "already promoted"
    assert run(repo, "resolve", "issue", "b", "--by", "EXP-0001") == 0, "a promoted issue can be resolved"
    assert run(repo, "resolve", "issue", "b", "--by", "EXP-0001") == 1, "already resolved"
    assert run(repo, "validate") == 0


def test_a_refused_promote_leaves_the_issue_and_the_todo_as_they_were(repo: Path, capsys):
    """promote checks the TODO it links back before it writes either file."""
    t = ll.load_graph(repo).todos["LAB-001"]
    t.path.write_text(ll.formatted_text("todo", {**t.fm, "depends_on": ["LAB-404"]}, t.raw), encoding="utf-8")
    assert run(repo, "new", "issue", "lab", "hang", "--symptom", "it hangs") == 0
    before = record_bytes(repo)
    capsys.readouterr()
    assert run(repo, "promote", "issue", "hang", "--to", "LAB-001") == 2
    assert "`depends_on` points at unknown LAB-404" in capsys.readouterr().err
    assert record_bytes(repo) == before, "the issue stays open when its TODO cannot take the link"


def git(root: Path, *argv: str) -> str:
    return subprocess.run(["git", "-C", str(root), *argv], capture_output=True, text=True, check=True).stdout


@pytest.fixture
def grepo(repo: Path) -> Path:
    """The minimal repo under git with everything committed; from here on the write commands commit."""
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.name", "t")
    git(repo, "config", "user.email", "t@example.org")
    (repo / ".gitignore").write_text("views/\n__pycache__/\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "seed")
    return repo


def test_write_commands_commit_what_they_wrote(grepo: Path, capsys):
    assert run(grepo, "new", "exp", "second experiment", "--summary", "short summary") == 0
    assert git(grepo, "log", "-1", "--format=%s") == "labledger(exp): open EXP-0002 — second experiment\n"
    assert run(grepo, "new", "issue", "lab", "board-hangs", "--symptom", "it hangs") == 0
    assert run(grepo, "resolve", "issue", "board-hangs", "--by", "EXP-0002") == 0
    assert run(grepo, "set", "LAB-001", "--status", "done", "--evidence", "EXP-0002", "--note", "done") == 0
    assert git(grepo, "log", "-1", "--format=%s") == "labledger(lab): LAB-001 done\n"
    assert f"areas/lab/decisions/{TODAY}-close-LAB-001.md" in git(grepo, "show", "--name-only", "--format=", "HEAD"), "the close decision rides along"
    assert git(grepo, "rev-list", "--count", "HEAD") == "5\n"
    assert git(grepo, "status", "--porcelain") == "", "nothing is left behind"
    assert run(grepo, "--no-commit", "set", "LAB-001", "--note", "later") == 0
    assert git(grepo, "rev-list", "--count", "HEAD") == "5\n" and git(grepo, "status", "--porcelain")


def test_a_ledger_sets_its_own_words_and_commit_prefix(grepo: Path, capsys):
    """labledger.yml and templates/ make the records speak the ledger's language: new records start from its skeletons,
    the commands write its labels, its commits carry its prefix — and validate checks both files."""
    (grepo / "labledger.yml").write_text("commit_prefix: notes\n", encoding="utf-8")
    templates = grepo / "templates"
    templates.mkdir()
    (templates / "exp.md").write_text("## Question\n\n## Data\n", encoding="utf-8")
    (templates / "labels.yml").write_text('symptom: Symptôme\nresolved: Résolu\nclosed_by: "terminé par {evidence}"\nevidence: preuve\n',
                                          encoding="utf-8")
    git(grepo, "add", "labledger.yml", "templates")
    git(grepo, "commit", "-q", "-m", "settings")
    assert run(grepo, "new", "exp", "second experiment") == 0
    assert (grepo / "experiments" / "EXP-0002.md").read_text(encoding="utf-8").endswith("# EXP-0002 — second experiment\n\n## Question\n\n## Data\n")
    assert git(grepo, "log", "-1", "--format=%s") == "notes(exp): open EXP-0002 — second experiment\n"
    assert run(grepo, "new", "issue", "lab", "hang", "--symptom", "it hangs") == 0
    assert run(grepo, "resolve", "issue", "hang", "--by", "EXP-0002") == 0
    assert (grepo / "areas" / "lab" / "issues" / "hang.md").read_text(encoding="utf-8").endswith(
        f"- Symptôme: it hangs\n- Résolu ({TODAY}): EXP-0002\n")
    assert run(grepo, "set", "LAB-001", "--status", "done", "--evidence", "EXP-0002") == 0
    assert (grepo / "areas" / "lab" / "decisions" / f"{TODAY}-close-LAB-001.md").read_text(encoding="utf-8").endswith(
        "- done: terminé par EXP-0002 (preuve EXP-0002)\n")
    assert run(grepo, "validate") == 0
    (grepo / "labledger.yml").write_text("commit_prefix: two words\ncolour: blue\n", encoding="utf-8")
    (templates / "labels.yml").write_text("symtom: typo\nclosed_by: fini\n", encoding="utf-8")
    (templates / "issue.md").write_text("x\n", encoding="utf-8")
    capsys.readouterr()
    assert run(grepo, "validate") == 1
    out = capsys.readouterr().out
    for problem in ("labledger.yml: unknown key `colour`", "commit_prefix `two words` must be one word",
                    "templates/labels.yml: unknown label `symtom`", "closed_by must hold {evidence}", "templates/issue.md: not a template"):
        assert problem in out, problem


def test_an_unfinished_file_of_another_session_does_not_hold_the_commit_back(grepo: Path, capsys):
    """Sessions share one working tree. A note another session left in areas/ without frontmatter stops the render of
    views/, but a write command still commits its own record, which validates on its own tree, and says it worked."""
    (grepo / "areas" / "lab" / "notes-in-progress.md").write_text("notes — no frontmatter yet\n", encoding="utf-8")
    assert run(grepo, "new", "exp", "second experiment") == 0
    assert "notes-in-progress.md" in capsys.readouterr().out, "the render problem is still reported"
    assert git(grepo, "log", "-1", "--format=%s") == "labledger(exp): open EXP-0002 — second experiment\n"
    assert git(grepo, "status", "--porcelain") == "?? areas/lab/notes-in-progress.md\n", "the note is left to its session"


def test_commit_carries_only_the_named_records(grepo: Path, tmp_path: Path, capsys):
    """Two sessions share one working tree: committing one record leaves the other's half-written record out."""
    assert run(grepo, "--no-commit", "new", "exp", "session A") == 0
    assert run(grepo, "--no-commit", "new", "exp", "session B") == 0
    exp_b = grepo / "experiments" / "EXP-0003.md"
    exp_b.write_text(exp_b.read_text(encoding="utf-8") + "\nBody edited by hand.\n", encoding="utf-8")
    (grepo / "experiments" / "attachments" / "EXP-0003").mkdir(parents=True)
    (grepo / "experiments" / "attachments" / "EXP-0003" / "data.json").write_text("{}\n", encoding="utf-8")
    assert run(grepo, "commit", "-m", "ledger(exp): session B", "EXP-0003") == 0
    committed = set(git(grepo, "show", "--name-only", "--format=", "HEAD").split())
    assert committed == {"experiments/EXP-0003.md", "experiments/attachments/EXP-0003/data.json"}, "the named record and nothing else"
    assert git(grepo, "status", "--porcelain") == "?? experiments/EXP-0002.md\n"
    head = tmp_path / "head"
    git(grepo, "worktree", "add", "-q", "--detach", str(head), "HEAD")
    assert run(head, "validate") == 0, "the commit validates on its own"
    assert run(grepo, "commit", "-m", "again", "EXP-0003") == 0
    assert "nothing to commit" in capsys.readouterr().out


def test_commit_refuses_a_record_whose_links_stay_behind(grepo: Path, capsys):
    assert run(grepo, "--no-commit", "new", "todo", "lab", "second") == 0
    assert run(grepo, "--no-commit", "new", "exp", "x", "--affects", "LAB-002: progress") == 0
    head = git(grepo, "rev-parse", "HEAD")
    assert run(grepo, "commit", "-m", "x", "EXP-0002") == 1
    assert "affects unknown LAB-002" in capsys.readouterr().err
    assert git(grepo, "rev-parse", "HEAD") == head, "a refused commit moves nothing"
    assert run(grepo, "set", "EXP-0002", "--summary", "short summary") == ll.UNFINISHED, "written, not committed: not to be run again"
    assert "not committed" in capsys.readouterr().err and git(grepo, "rev-parse", "HEAD") == head, "nor does a refused auto-commit"
    assert run(grepo, "commit", "-m", "x", "EXP-0002", "LAB-002") == 0
    assert git(grepo, "status", "--porcelain") == ""
    assert run(grepo, "commit", "-m", "x", "NOPE-404") == 2


def test_writes_outside_git_do_not_try_to_commit(repo: Path, capsys):
    assert run(repo, "new", "exp", "x") == 0
    assert "commit" not in capsys.readouterr().out
    assert run(repo, "commit", "-m", "x", "EXP-0001") == 2
    assert "not a git work tree" in capsys.readouterr().err


@pytest.fixture
def origin(grepo: Path, tmp_path: Path) -> Path:
    """A bare remote that `grepo`'s main tracks, as GitHub does for this repository."""
    bare = tmp_path / "origin.git"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(bare))
    git(grepo, "remote", "add", "origin", str(bare))
    git(grepo, "push", "-q", "-u", "origin", "main")
    return bare


def another_server_pushes(origin: Path, tmp_path: Path) -> None:
    other = tmp_path / "other-server"
    git(tmp_path, "clone", "-q", str(origin), str(other))
    git(other, "config", "user.name", "o")
    git(other, "config", "user.email", "o@example.org")
    (other / "note.txt").write_text("x\n", encoding="utf-8")
    git(other, "add", "note.txt")
    git(other, "commit", "-q", "-m", "another server")
    git(other, "push", "-q", "origin", "main")


def test_every_commit_is_pushed_to_the_upstream(grepo: Path, origin: Path, capsys):
    assert run(grepo, "new", "exp", "remote") == 0
    assert "pushed" in capsys.readouterr().out
    assert git(origin, "rev-parse", "main") == git(grepo, "rev-parse", "HEAD")
    assert run(grepo, "--no-push", "set", "EXP-0002", "--summary", "short summary") == 0
    assert git(origin, "rev-parse", "main") != git(grepo, "rev-parse", "HEAD"), "--no-push keeps the commit local"
    capsys.readouterr()
    assert run(grepo, "commit", "-m", "x", "EXP-0002") == 0
    out = capsys.readouterr().out
    assert "nothing to commit" in out and "pushed" in out, "a commit that adds nothing still pushes what waits"
    assert git(origin, "rev-parse", "main") == git(grepo, "rev-parse", "HEAD")


def test_a_rejected_push_brings_the_other_servers_commits_in_and_pushes_on_top(grepo: Path, origin: Path, tmp_path: Path, capsys):
    another_server_pushes(origin, tmp_path)
    assert run(grepo, "new", "exp", "local") == 0
    out = capsys.readouterr().out
    assert "synced — 1 commit(s) from origin, 1 local commit(s) replayed on top" in out and "pushed" in out
    assert git(origin, "rev-parse", "main") == git(grepo, "rev-parse", "HEAD")
    assert git(grepo, "log", "--format=%s") == "labledger(exp): open EXP-0002 — local\nanother server\nseed\n", "a straight line"
    assert (grepo / "note.txt").read_text(encoding="utf-8") == "x\n", "the other server's file arrives in the working tree"
    assert git(grepo, "status", "--porcelain") == ""


def test_a_push_that_lost_a_race_to_a_descendant_is_quiet(grepo: Path, origin: Path, tmp_path: Path, capsys):
    """Another session sharing this clone pushed first and more: the rejected push finds its commit already there."""
    assert run(grepo, "--no-push", "new", "exp", "first") == 0
    git(grepo, "push", "-q", str(origin), "HEAD:main")  # by path, so this clone's origin/main does not see it
    another_server_pushes(origin, tmp_path)
    capsys.readouterr()
    assert run(grepo, "commit", "-m", "x", "EXP-0002") == 0
    assert "not pushed" not in capsys.readouterr().out


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


@pytest.fixture
def hooked(grepo: Path, capsys) -> Path:
    """`grepo` carrying this tool and its hooks; the first write command points the clone at them."""
    shutil.copytree(SCRIPTS / "hooks", grepo / "scripts" / "hooks")
    shutil.copy2(SCRIPTS / "labledger.py", grepo / "scripts" / "labledger.py")
    git(grepo, "add", "scripts")
    git(grepo, "commit", "-q", "-m", "tool")
    assert run(grepo, "new", "exp", "turn the hooks on") == 0
    assert "hooks: core.hooksPath → scripts/hooks" in capsys.readouterr().out
    assert git(grepo, "config", "core.hooksPath") == "scripts/hooks\n"
    return grepo


def unknown_affects_exp(root: Path) -> Path:
    """A new EXP whose affects names a TODO that does not exist — a tree with it does not validate."""
    assert run(root, "--no-commit", "new", "exp", "a broken record") == 0
    exp = sorted((root / "experiments").glob("EXP-*.md"))[-1]
    exp.write_text(exp.read_text(encoding="utf-8").replace("affects: []", 'affects:\n  - "NOPE-001: x"', 1), encoding="utf-8")
    return exp


def test_hand_commits_go_through_the_pre_commit_hook(hooked: Path):
    exp = hooked / "experiments" / "EXP-0001.md"
    exp.write_text(exp.read_text(encoding="utf-8") + "\nBody added by hand.\n", encoding="utf-8")
    git(hooked, "commit", "-q", "-am", "body only")
    bad = unknown_affects_exp(hooked)
    head = git(hooked, "rev-parse", "HEAD")
    git(hooked, "add", str(bad))
    r = subprocess.run(["git", "-C", str(hooked), "commit", "-q", "-m", "broken"], capture_output=True, text=True)
    assert r.returncode != 0 and "affects unknown NOPE-001" in r.stderr and "labledger.py commit" in r.stderr
    assert git(hooked, "rev-parse", "HEAD") == head


def test_a_clone_with_its_own_hooks_path_keeps_it(grepo: Path):
    shutil.copytree(SCRIPTS / "hooks", grepo / "scripts" / "hooks")
    git(grepo, "config", "core.hooksPath", "elsewhere")
    assert run(grepo, "new", "exp", "x") == 0
    assert git(grepo, "config", "core.hooksPath") == "elsewhere\n"


def test_the_pre_push_hook_refuses_a_tip_that_does_not_validate(hooked: Path, origin: Path):
    git(hooked, "push", "-q", "origin", "main")
    bad = unknown_affects_exp(hooked)
    git(hooked, "add", str(bad))
    git(hooked, "commit", "-q", "--no-verify", "-m", "broken, past the pre-commit hook")
    r = subprocess.run(["git", "-C", str(hooked), "push", "origin", "main"], capture_output=True, text=True)
    assert r.returncode != 0 and "would not validate" in r.stderr and "NOPE-001" in r.stderr
    assert git(origin, "rev-parse", "main") != git(hooked, "rev-parse", "HEAD")
    pushed, report = ll.push_upstream(hooked)
    assert not pushed and report.startswith("not pushed") and "NOPE-001" in report and "lacks" not in report


def test_the_pre_push_hook_runs_the_tool_tests_when_the_tool_changed(hooked: Path, origin: Path):
    git(hooked, "push", "-q", "origin", "main")
    (hooked / "tests").mkdir()
    (hooked / "tests" / "test_tool.py").write_text("def test_tool():\n    assert False\n", encoding="utf-8")
    git(hooked, "add", "tests")
    git(hooked, "commit", "-q", "-m", "a failing tool test")
    r = subprocess.run(["git", "-C", str(hooked), "push", "origin", "main"], capture_output=True, text=True)
    assert r.returncode != 0 and "tool tests fail" in r.stderr
    (hooked / "tests" / "test_tool.py").write_text(  # the tests must not inherit the hook's GIT_* and run git on this repo
        "import os\n\ndef test_tool():\n    assert not [k for k in os.environ if k.startswith('GIT_')]\n", encoding="utf-8")
    git(hooked, "commit", "-q", "-am", "fixed")
    git(hooked, "push", "-q", "origin", "main")
    assert git(origin, "rev-parse", "main") == git(hooked, "rev-parse", "HEAD")


def test_a_push_that_another_servers_push_will_refuse_skips_the_hook(hooked: Path, origin: Path, tmp_path: Path, capsys):
    """git runs pre-push even when the upstream holds a commit this clone lacks, and the push is then refused (fetch
    first) whatever the hook says. The hook must not run the tool tests on it: the push after the sync is checked."""
    (hooked / "tests").mkdir()
    (hooked / "tests" / "test_tool.py").write_text("def test_tool():\n    assert False\n", encoding="utf-8")
    git(hooked, "add", "tests")
    git(hooked, "commit", "-q", "--no-verify", "-m", "a failing tool test, already upstream")
    git(hooked, "push", "-q", "--no-verify", "origin", "main")
    another_server_pushes(origin, tmp_path)
    capsys.readouterr()
    assert run(hooked, "set", "EXP-0001", "--summary", "summary after another machine pushed first") == 0
    out = capsys.readouterr().out
    assert "synced — 1 commit(s) from origin, 1 local commit(s) replayed on top" in out and "pushed" in out
    assert "tool tests fail" not in out and git(origin, "rev-parse", "main") == git(hooked, "rev-parse", "HEAD")


def test_an_unreachable_remote_is_not_reported_as_another_servers_push(grepo: Path, origin: Path, tmp_path: Path, capsys):
    git(grepo, "remote", "set-url", "origin", str(tmp_path / "gone.git"))
    assert run(grepo, "--offline", "new", "exp", "x") == ll.UNFINISHED, "committed, not pushed"
    out, err = capsys.readouterr()
    assert "committed" in out and "not pushed" in err and "lacks" not in err


def server(origin: Path, path: Path) -> Path:
    """Another server's clone of `origin`."""
    git(path.parent, "clone", "-q", str(origin), str(path))
    git(path, "config", "user.name", path.name)
    git(path, "config", "user.email", f"{path.name}@example.org")
    return path


def test_two_servers_write_unrelated_records_and_sync(grepo: Path, origin: Path, tmp_path: Path, capsys):
    """Nothing generated is committed, so two servers writing different records meet no conflict; sync joins them."""
    other = server(origin, tmp_path / "server-b")
    data = "experiments/attachments/EXP-0001/data.json"
    (grepo / data).parent.mkdir(parents=True)
    (grepo / data).write_text("{}\n", encoding="utf-8")
    assert run(grepo, "commit", "-m", "ledger(exp): EXP-0001 data", "EXP-0001") == 0
    assert run(grepo, "new", "todo", "lab", "machine A's TODO") == 0 and "pushed" in capsys.readouterr().out
    assert run(other, "set", "EXP-0001", "--summary", "summary machine B changed") == 0
    out = capsys.readouterr().out
    assert "synced — 2 commit(s) from origin, 1 local commit(s) replayed on top" in out and "pushed" in out
    assert (other / "areas" / "lab" / "LAB-002.md").is_file() and (other / data).is_file()
    assert git(other, "status", "--porcelain") == ""
    (grepo / data).unlink()
    assert run(grepo, "commit", "-m", "ledger(exp): drop EXP-0001 data", data) == 0
    assert "synced — 1 commit(s) from origin, 1 local commit(s) replayed on top" in capsys.readouterr().out
    assert "summary machine B changed" in (grepo / "experiments" / "EXP-0001.md").read_text(encoding="utf-8")
    assert git(grepo, "status", "--porcelain") == "" and git(grepo, "rev-parse", "HEAD") == git(origin, "rev-parse", "main")
    assert run(other, "sync") == 0 and "synced — 1 commit(s) from origin; 1 file(s) updated here" in capsys.readouterr().out
    assert not (other / "experiments" / "attachments").exists(), "a deletion arrives, and the emptied directories go"
    assert git(other, "status", "--porcelain") == "" and git(other, "rev-parse", "HEAD") == git(origin, "rev-parse", "main")
    assert git(other, "rev-list", "--merges", "HEAD") == "", "a straight line of commits"
    assert run(grepo, "validate") == 0 and run(other, "validate") == 0
    assert run(grepo, "sync") == 0 and "in sync with the upstream" in capsys.readouterr().out


def test_sync_stops_where_both_sides_changed_the_same_lines_and_resumes_after_the_merge(grepo: Path, origin: Path, tmp_path: Path, capsys):
    attachment = "experiments/attachments/EXP-0001/data.json"
    (grepo / attachment).parent.mkdir(parents=True)
    (grepo / attachment).write_text("{}\n", encoding="utf-8")
    assert run(grepo, "commit", "-m", "ledger(exp): EXP-0001 data", "EXP-0001") == 0
    other = server(origin, tmp_path / "server-b")
    config = (grepo / ".git" / "config").read_bytes()
    exp = "experiments/EXP-0001.md"
    for root, line, rc in ((other, "B's result\n", 0), (grepo, "A's result\n", ll.UNFINISHED)):  # B pushes first; A's push syncs and stops
        (root / exp).write_text((root / exp).read_text(encoding="utf-8") + line, encoding="utf-8")
        assert run(root, "commit", "-m", f"ledger(exp): {line.strip()}", "EXP-0001") == rc
    out = capsys.readouterr().err
    wt = grepo / ".git" / "labledger-sync"
    assert "changed the same lines" in out and f"  - {exp}\n" in out and str(wt) in out
    local = git(grepo, "rev-parse", "HEAD")
    assert git(grepo, "log", "-1", "--format=%s") == "ledger(exp): A's result\n", "the branch did not move"
    assert (grepo / exp).read_text(encoding="utf-8").endswith("A's result\n"), "nor did the working tree"
    assert run(grepo, "sync") == 1 and "still carry conflict markers" in capsys.readouterr().out
    assert run(grepo, "sync", "--abort") == 0 and not wt.exists() and git(grepo, "rev-parse", "HEAD") == local
    assert run(grepo, "sync", "--abort") == 0 and "no stopped replay" in capsys.readouterr().out
    assert run(grepo, "sync") == 1 and "changed the same lines" in capsys.readouterr().out, "a fresh sync stops at the same place"
    assert not (wt / attachment).exists() and attachment in git(wt, "ls-files"), "the replay leaves the attachments off disk"
    merged = (grepo / exp).read_text(encoding="utf-8").removesuffix("A's result\n") + "B's result\nA's result\n"
    (wt / exp).write_text(merged, encoding="utf-8")
    assert run(grepo, "sync") == 0
    out = capsys.readouterr().out
    assert "synced — 1 commit(s) from origin, 1 local commit(s) replayed on top" in out and "pushed" in out
    assert (grepo / exp).read_text(encoding="utf-8") == merged and git(grepo, "status", "--porcelain") == ""
    assert git(grepo, "log", "-1", "--format=%B") == "ledger(exp): A's result\n\n", "the merge leaves no trace in the message"
    assert git(origin, "rev-parse", "main") == git(grepo, "rev-parse", "HEAD") and not wt.exists()
    assert git(grepo, "worktree", "list", "--porcelain").count("worktree ") == 1
    assert (grepo / ".git" / "config").read_bytes() == config, "the private worktree leaves the clone's config alone"
    assert run(other, "sync") == 0 and (other / exp).read_text(encoding="utf-8") == merged


def test_sync_keeps_uncommitted_work_and_stops_where_it_would_overwrite_it(grepo: Path, origin: Path, tmp_path: Path, capsys):
    other = server(origin, tmp_path / "server-b")
    assert run(other, "set", "EXP-0001", "--summary", "B's summary") == 0
    assert run(grepo, "--no-commit", "new", "exp", "another session of A writing") == 0
    exp = grepo / "experiments" / "EXP-0001.md"
    exp.write_text(exp.read_text(encoding="utf-8") + "\nBody A is editing.\n", encoding="utf-8")
    head, before = git(grepo, "rev-parse", "HEAD"), exp.read_text(encoding="utf-8")
    capsys.readouterr()
    assert run(grepo, "sync") == 1
    out = capsys.readouterr().out
    assert "hold uncommitted work" in out and "  - experiments/EXP-0001.md\n" in out
    assert git(grepo, "rev-parse", "HEAD") == head and exp.read_text(encoding="utf-8") == before, "nothing moved"
    assert run(grepo, "commit", "-m", "ledger(exp): A's body", "EXP-0001") == 0
    out = capsys.readouterr().out
    assert "synced" in out and "pushed" in out, "once committed, the body edit and B's summary merge"
    text = exp.read_text(encoding="utf-8")
    assert "B's summary" in text and "Body A is editing." in text
    assert git(grepo, "status", "--porcelain") == "?? experiments/EXP-0002.md\n", "the other session's record stays, untracked"


def test_a_replayed_commit_that_does_not_validate_stops_the_sync_until_fixed(grepo: Path, origin: Path, tmp_path: Path, capsys):
    other = server(origin, tmp_path / "server-b")
    assert run(other, "new", "decision", "same-slug", "--text", "B's global decision") == 0
    assert run(grepo, "new", "decision", "same-slug", "--area", "lab", "--text", "A's area decision") == ll.UNFINISHED
    out = capsys.readouterr().err
    assert "does not validate" in out and "is defined in both" in out, "the texts merge; the two addresses collide"
    wt = grepo / ".git" / "labledger-sync"
    dec = wt / "areas" / "lab" / "decisions" / f"{TODAY}-same-slug.md"
    entry(dec.with_name(f"{TODAY}-same-slug-lab.md"), "dec", {"date": TODAY, "title": "same-slug-lab"}, "- Decision: A's area decision")
    dec.unlink()
    assert run(grepo, "sync") == 0 and "pushed" in capsys.readouterr().out
    decisions = grepo / "areas" / "lab" / "decisions"
    assert (decisions / f"{TODAY}-same-slug-lab.md").is_file() and not (decisions / f"{TODAY}-same-slug.md").exists()
    assert run(grepo, "validate") == 0 and git(grepo, "status", "--porcelain") == ""


def test_sync_replays_a_commit_that_lands_while_it_runs(grepo: Path, origin: Path, tmp_path: Path, monkeypatch, capsys):
    another_server_pushes(origin, tmp_path)
    assert run(grepo, "--no-push", "new", "exp", "first one") == 0
    overlap = ll._overlap

    def a_session_commits_meanwhile(root: Path, old: str, new: str) -> list[str]:
        monkeypatch.setattr(ll, "_overlap", overlap)
        assert run(grepo, "--no-push", "new", "exp", "during the sync") == 0
        return overlap(root, old, new)

    monkeypatch.setattr(ll, "_overlap", a_session_commits_meanwhile)
    assert run(grepo, "sync") == 0
    assert git(grepo, "log", "--format=%s") == ("labledger(exp): open EXP-0003 — during the sync\nlabledger(exp): open EXP-0002 — first one\n"
                                                "another server\nseed\n")
    assert git(origin, "rev-parse", "main") == git(grepo, "rev-parse", "HEAD") and git(grepo, "status", "--porcelain") == ""


def test_a_second_sync_of_the_clone_waits_for_the_first(grepo: Path, origin: Path, monkeypatch):
    monkeypatch.setattr(ll, "SYNC_WAIT", 0.3)
    held = ll._lock_sync(ll._sync_paths(grepo)[2])
    try:
        done, report = ll.sync(grepo)
    finally:
        held.close()
    assert not done and "another sync" in report
    assert ll.sync(grepo) == (True, "")


def test_sync_runs_under_the_hooks(hooked: Path, origin: Path, tmp_path: Path, capsys):
    """The clone's hooks also run in the private worktree (finishing a merged pick commits there)."""
    other = server(origin, tmp_path / "server-b")
    exp = "experiments/EXP-0001.md"
    for root, line, rc in ((other, "B's result\n", 0), (hooked, "A's result\n", ll.UNFINISHED)):
        (root / exp).write_text((root / exp).read_text(encoding="utf-8") + line, encoding="utf-8")
        assert run(root, "commit", "-m", f"ledger(exp): {line.strip()}", "EXP-0001") == rc
    assert "changed the same lines" in capsys.readouterr().err
    wt = hooked / ".git" / "labledger-sync"
    merged = (hooked / exp).read_text(encoding="utf-8").removesuffix("A's result\n") + "B's result\nA's result\n"
    (wt / exp).write_text(merged, encoding="utf-8")
    assert run(hooked, "sync") == 0 and "pushed" in capsys.readouterr().out
    assert git(origin, "rev-parse", "main") == git(hooked, "rev-parse", "HEAD")
    assert (hooked / exp).read_text(encoding="utf-8") == merged


def test_a_number_reserved_on_one_server_is_not_issued_on_another(grepo: Path, origin: Path, tmp_path: Path, capsys):
    """A reserves numbers and keeps the records to itself (--no-commit); B, which never saw them, still skips them."""
    other = server(origin, tmp_path / "server-b")
    for argv in (("new", "exp", "A"), ("new", "todo", "lab", "A"), ("new", "arc", "A")):
        assert run(grepo, "--no-commit", *argv) == 0
        assert run(other, "--no-commit", *argv) == 0
    assert sorted(p.name for p in (grepo / "experiments").glob("EXP-*.md")) == ["EXP-0001.md", "EXP-0002.md"]
    assert sorted(p.name for p in (other / "experiments").glob("EXP-*.md")) == ["EXP-0001.md", "EXP-0003.md"]
    assert (grepo / "areas" / "lab" / "LAB-002.md").is_file() and (other / "areas" / "lab" / "LAB-003.md").is_file()
    assert (grepo / "arcs" / "ARC-002.md").is_file() and (other / "arcs" / "ARC-003.md").is_file()
    assert json.loads(git(origin, "show", "refs/labledger/ids:ids.json")) == {"ARC": 3, "EXP": 3, "LAB": 3}
    assert git(origin, "log", "-1", "--format=%s", "refs/labledger/ids").startswith("reserve ARC-003 (")
    assert git(origin, "rev-parse", "main") == git(grepo, "rev-parse", "HEAD"), "reserving touches no branch"


def test_a_reservation_that_loses_the_race_reads_the_counter_again(grepo: Path, origin: Path, tmp_path: Path, monkeypatch):
    """Two sessions of one machine race: one git identity, one host name, one second — so the two counter commits
    differ only in what makes each reservation its own, and the second push cannot pass as the first one's."""
    other = server(origin, tmp_path / "server-b")
    for who in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{who}_NAME", "t")
        monkeypatch.setenv(f"GIT_{who}_EMAIL", "t@example.org")
        monkeypatch.setenv(f"GIT_{who}_DATE", "2026-10-16T12:00:00+09:00")
    tree_max = ll._tree_max

    def b_reserves_meanwhile(root: Path, rev: str, key: str) -> int:  # A has read the counter, not yet pushed it
        monkeypatch.setattr(ll, "_tree_max", tree_max)
        assert run(other, "--no-commit", "new", "exp", "B") == 0
        return tree_max(root, rev, key)

    monkeypatch.setattr(ll, "_tree_max", b_reserves_meanwhile)
    assert run(grepo, "--no-commit", "new", "exp", "A") == 0
    assert (other / "experiments" / "EXP-0002.md").is_file() and (grepo / "experiments" / "EXP-0003.md").is_file()
    assert not (grepo / "experiments" / "EXP-0002.md").exists()


def test_a_remote_that_refuses_the_counter_is_not_taken_for_a_race(grepo: Path, origin: Path, capsys):
    """A remote that refuses the counter's push (a hook, permissions) says why at once, instead of being retried as a
    race lost to another server and reported as a counter that kept moving."""
    hook = origin / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\necho 'counter pushes are not allowed here' >&2\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)
    assert run(grepo, "new", "exp", "x") == 1
    err = capsys.readouterr().err
    assert "counter pushes are not allowed here" in err and "kept moving" not in err


def test_numbers_come_only_from_new_and_a_refused_record_takes_none(grepo: Path, origin: Path, capsys):
    """A record is checked before its number is reserved, so a refused one leaves no gap. A record added under a number
    no `new` reserved is refused at commit; a number taken with --offline is noted in the clone's copy of the counter."""
    assert run(grepo, "new", "exp", "refused", "--affects", "NOPE-001: progress") == 2
    assert run(grepo, "new", "exp", "kept") == 0 and (grepo / "experiments" / "EXP-0002.md").is_file(), "no number was spent"
    exp = grepo / "experiments" / "EXP-0002.md"
    hand = exp.with_name("EXP-0003.md")
    hand.write_text(exp.read_text(encoding="utf-8").replace("EXP-0002", "EXP-0003"), encoding="utf-8")
    capsys.readouterr()
    assert run(grepo, "commit", "-m", "x", "EXP-0003") == 1
    assert "EXP-0003 is past the last number reserved for EXP (2)" in capsys.readouterr().err
    hand.unlink()
    assert run(grepo, "--offline", "new", "exp", "away") == 0 and git(grepo, "log", "-1", "--format=%s").startswith("labledger(exp): open EXP-0003")


def test_no_number_without_the_upstream_unless_offline(grepo: Path, origin: Path, tmp_path: Path, capsys):
    git(grepo, "remote", "set-url", "origin", str(tmp_path / "gone.git"))
    assert run(grepo, "new", "exp", "x") == 1
    err = capsys.readouterr().err
    assert "not created — no number could be reserved" in err and "--offline" in err
    assert not (grepo / "experiments" / "EXP-0002.md").exists() and git(grepo, "status", "--porcelain") == "", "nothing was written"
    assert run(grepo, "--offline", "--no-push", "new", "exp", "x") == 0 and (grepo / "experiments" / "EXP-0002.md").is_file()


def test_global_options_may_follow_the_command(grepo: Path, origin: Path, capsys):
    """`set LAB-001 --note … --no-commit` is `--no-commit set LAB-001 --note …`, and so for --root, --no-push, --offline."""
    assert ll.main(["show", "LAB-001", "--root", str(grepo)]) == 0 and "first TODO" in capsys.readouterr().out
    base = git(grepo, "rev-parse", "HEAD")
    assert run(grepo, "set", "LAB-001", "--note", "later", "--no-commit") == 0 and git(grepo, "rev-parse", "HEAD") == base
    assert run(grepo, "new", "exp", "x", "--offline", "--no-push") == 0 and git(grepo, "rev-parse", "HEAD") != base
    assert git(origin, "rev-parse", "main") == base and git(origin, "for-each-ref", "refs/labledger") == "", "neither pushed nor reserved"


def test_the_first_reservation_starts_past_the_upstream_branch(grepo: Path, origin: Path, tmp_path: Path, capsys):
    """Records made before the counter existed (or with --offline) are on the branch only; a stale clone skips them."""
    other = server(origin, tmp_path / "server-b")
    assert run(grepo, "--offline", "new", "exp", "before the counter") == 0 and "pushed" in capsys.readouterr().out
    assert run(other, "--no-commit", "new", "exp", "B") == 0
    assert (other / "experiments" / "EXP-0003.md").is_file() and not (other / "experiments" / "EXP-0002.md").exists()


def test_two_servers_write_one_area_s_decisions_and_issues_without_a_conflict(grepo: Path, origin: Path, tmp_path: Path, capsys):
    """Each decision and issue is its own file, so two servers adding to one area never touch the same lines."""
    assert run(grepo, "new", "todo", "lab", "second") == 0
    other = server(origin, tmp_path / "server-b")
    for root, who, todo in ((grepo, "a", "LAB-001"), (other, "b", "LAB-002")):
        assert run(root, "--no-push", "new", "decision", f"rule-{who}", "--area", "lab", "--text", who) == 0
        assert run(root, "--no-push", "new", "issue", "lab", f"hang-{who}", "--symptom", who) == 0
        assert run(root, "--no-push", "set", todo, "--status", "done", "--note", who) == 0
    assert run(grepo, "sync") == 0
    capsys.readouterr()
    assert run(other, "sync") == 0
    out = capsys.readouterr().out
    assert "synced — 3 commit(s) from origin, 3 local commit(s) replayed on top" in out and "pushed" in out
    assert run(grepo, "sync") == 0
    for root in (grepo, other):
        assert sorted(p.name for p in (root / "areas" / "lab" / "decisions").iterdir()) == sorted([
            "2026-09-02-seed-decision-lab.md", f"{TODAY}-rule-a.md", f"{TODAY}-rule-b.md",
            f"{TODAY}-close-LAB-001.md", f"{TODAY}-close-LAB-002.md"])
        assert sorted(p.name for p in (root / "areas" / "lab" / "issues").iterdir()) == ["hang-a.md", "hang-b.md", "seed-issue-lab.md"]
        assert run(root, "validate") == 0 and git(root, "status", "--porcelain") == ""


def test_a_commit_that_renames_a_record_leaves_the_index_clean(grepo: Path):
    """git pairs a deletion and a similar addition as a rename; the index refresh must still see the deleted path."""
    old = grepo / "areas" / "lab" / "review-2026-09-03.md"
    new = old.with_name("review-audit-2026-09-03.md")
    new.write_text(old.read_text(encoding="utf-8").replace("id: review-2026-09-03", "id: review-audit-2026-09-03"), encoding="utf-8")
    old.unlink()
    assert run(grepo, "commit", "-m", "rename", "areas/lab/review-2026-09-03.md", "areas/lab/review-audit-2026-09-03.md") == 0
    assert git(grepo, "status", "--porcelain") == ""


def partial_server(origin: Path, path: Path) -> Path:
    """Another server's clone as AGENTS.md (Other machines) makes it: no blob over 1 MB fetched, no attachment on disk."""
    git(origin, "config", "uploadpack.allowFilter", "true")  # GitHub serves partial clones; a bare repo must be told to
    git(origin, "config", "uploadpack.allowAnySHA1InWant", "true")
    git(path.parent, "clone", "-q", "--filter=blob:limit=1m", "--no-checkout", f"file://{origin}", str(path))
    git(path, "config", "core.sparseCheckout", "true")
    git(path, "config", "core.sparseCheckoutCone", "false")
    (path / ".git" / "info" / "sparse-checkout").write_text("/*\n!/experiments/attachments/\n", encoding="utf-8")
    git(path, "checkout", "-q", "main")
    git(path, "config", "user.name", path.name)
    git(path, "config", "user.email", f"{path.name}@example.org")
    return path


def missing(root: Path) -> set[str]:
    """The objects this clone has left on the upstream (never fetches one)."""
    return {line[1:] for line in git(root, "rev-list", "--objects", "--all", "--missing=print").splitlines() if line.startswith("?")}


def big(tag: str) -> bytes:
    return (tag.encode() * 1_100_000)[:1_100_000]  # over the partial clone's 1 MB blob filter


def test_a_partial_clone_leaves_the_attachments_upstream_until_asked(hooked: Path, origin: Path, tmp_path: Path, capsys):
    """Commit, sync (fast-forward and replay), the pre-push hook and `attachments` on a server that cloned without the
    attachments: none of them downloads or deletes one it was not asked for."""
    a = hooked
    att = a / "experiments" / "attachments" / "EXP-0001"
    att.mkdir(parents=True)
    (att / "big.bin").write_bytes(big("a"))
    (att / "small.txt").write_text("small\n", encoding="utf-8")
    exp_table(a, "run[id],cycles[1]\n1,100\n")
    assert run(a, "set", "EXP-0001", "--status", "final", "--data", "runs.csv: one row per run") == 0

    def blob(path: str) -> str:
        return git(origin, "rev-parse", f"main:{path}").strip()

    b = partial_server(origin, tmp_path / "server-b")
    b_att = b / "experiments" / "attachments"
    big1 = blob("experiments/attachments/EXP-0001/big.bin")
    assert missing(b) == {big1} and not b_att.exists() and git(b, "status", "--porcelain") == ""
    assert run(b, "validate") == 0 and run(b, "show", "EXP-0001") == 0 and missing(b) == {big1}, "the table and the link are in git's index"

    assert run(b, "new", "exp", "B's experiment") == 0 and "pushed" in capsys.readouterr().out  # EXP-0003, through the pre-push hook
    config = (b / ".git" / "config").read_bytes()
    (b_att / "EXP-0003").mkdir(parents=True)
    (b_att / "EXP-0003" / "out.csv").write_text("a,b\n", encoding="utf-8")
    assert run(b, "commit", "-m", "ledger(exp): EXP-0003 data", "EXP-0003") == 0, "a server keeps the attachments of the EXPs it makes"
    assert "experiments/attachments/EXP-0003/out.csv" in git(origin, "ls-tree", "-r", "--name-only", "main")
    assert git(b, "config", "core.hooksPath") == "scripts/hooks\n" and missing(b) == {big1}, "committing and pushing fetch nothing"

    assert run(a, "--no-commit", "new", "exp", "A's experiment") == 0  # EXP-0004
    (a / "experiments" / "attachments" / "EXP-0004").mkdir()
    (a / "experiments" / "attachments" / "EXP-0004" / "big.bin").write_bytes(big("c"))
    (att / "small.txt").write_text("changed\n", encoding="utf-8")
    assert run(a, "commit", "-m", "ledger(exp): EXP-0004 data, EXP-0001 small", "EXP-0004", "EXP-0001") == 0
    capsys.readouterr()
    assert run(b, "sync") == 0 and "synced — " in capsys.readouterr().out
    big4 = blob("experiments/attachments/EXP-0004/big.bin")
    assert (b / "experiments" / "EXP-0004.md").is_file() and not (b_att / "EXP-0004").exists() and not (b_att / "EXP-0001").exists()
    assert missing(b) == {big1, big4}, "the other server's new attachment stays upstream"
    assert git(b, "status", "--porcelain") == "", "an attachment left off disk is not a deletion"

    assert run(b, "--no-push", "set", "EXP-0003", "--summary", "B's summary") == 0
    assert run(a, "--no-commit", "new", "exp", "A's second") == 0  # EXP-0005
    (a / "experiments" / "attachments" / "EXP-0005").mkdir()
    (a / "experiments" / "attachments" / "EXP-0005" / "big.bin").write_bytes(big("d"))
    assert run(a, "commit", "-m", "ledger(exp): EXP-0005 data", "EXP-0005") == 0
    capsys.readouterr()
    assert run(b, "sync") == 0 and "1 local commit(s) replayed on top" in capsys.readouterr().out
    big5 = blob("experiments/attachments/EXP-0005/big.bin")
    assert missing(b) == {big1, big4, big5} and git(b, "status", "--porcelain") == ""

    (b_att / "EXP-0001").mkdir()
    (b_att / "EXP-0001" / "note.txt").write_text("B's note\n", encoding="utf-8")
    assert run(b, "commit", "-m", "x", "EXP-0001") == 1, "its other files are not here: the commit would delete them"
    assert "`python3 scripts/labledger.py attachments EXP-0001` brings them in" in capsys.readouterr().err
    assert run(b, "commit", "-m", "x", "experiments") == 1 and "leaves off disk" in capsys.readouterr().err
    assert run(b, "attachments", "EXP-0001") == 0
    assert "experiments/attachments/EXP-0001/: 4 file(s)" in capsys.readouterr().out
    assert (b_att / "EXP-0001" / "big.bin").read_bytes() == big("a") and (b_att / "EXP-0001" / "small.txt").read_text(encoding="utf-8") == "changed\n"
    assert missing(b) == {big4, big5} and git(b, "status", "--porcelain") == "?? experiments/attachments/EXP-0001/note.txt\n"
    assert run(b, "commit", "-m", "ledger(exp): EXP-0001 B's note", "EXP-0001") == 0 and "pushed" in capsys.readouterr().out

    assert run(a, "sync") == 0 and (att / "note.txt").is_file()
    (att / "small.txt").write_text("again\n", encoding="utf-8")
    assert run(a, "commit", "-m", "ledger(exp): EXP-0001 small again", "EXP-0001") == 0
    assert run(b, "sync") == 0 and (b_att / "EXP-0001" / "small.txt").read_text(encoding="utf-8") == "again\n", "a brought-in directory follows"
    assert missing(b) == {big4, big5} and git(b, "status", "--porcelain") == ""
    assert (b / ".git" / "config").read_bytes() == config, "nothing rewrote the clone's config"
    assert run(b, "validate") == 0 and run(a, "validate") == 0
    assert git(a, "rev-parse", "HEAD") == git(b, "rev-parse", "HEAD") == git(origin, "rev-parse", "main")


def test_a_sparse_checkout_the_tool_does_not_know_is_refused(grepo: Path, origin: Path, tmp_path: Path, capsys):
    assert run(grepo, "attachments", "EXP-0001") == 0 and "every attachment" in capsys.readouterr().out
    assert run(grepo, "attachments", "EXP-0999") == 2
    b = partial_server(origin, tmp_path / "server-b")
    sparse = b / ".git" / "info" / "sparse-checkout"
    sparse.write_text(sparse.read_text(encoding="utf-8") + "!/state/\n", encoding="utf-8")
    capsys.readouterr()
    assert run(b, "new", "exp", "x") == 2 and "sparse checkout is not the one labledger.py knows" in capsys.readouterr().err
    assert not (b / "experiments" / "EXP-0002.md").exists()
    assert run(b, "sync") == 1 and "not synced — this clone's sparse checkout" in capsys.readouterr().out
    assert run(b, "commit", "-m", "x", "EXP-0001") == 1 and "not the one labledger.py knows" in capsys.readouterr().err
    assert run(b, "attachments", "EXP-0001") == 2


def test_ssh_targets_of_remote_urls():
    assert ll._ssh_target("git@github.com:someone/ledger.git") == ["git@github.com"]
    assert ll._ssh_target("ssh://git@example.org:2222/team/ledger.git") == ["-p", "2222", "git@example.org"]
    assert ll._ssh_target("ssh://example.org/ledger.git") == ["example.org"]
    for url in ("/srv/origin.git", "../origin.git", "file:///srv/origin.git", "https://github.com/a/b.git", ""):
        assert ll._ssh_target(url) is None, url


FAKE_SSH = """#!/bin/sh
stdio="$(readlink /proc/$$/fd/0) $(readlink /proc/$$/fd/1) $(readlink /proc/$$/fd/2)"
echo "$*" >> "$SSH_LOG"
case "$*" in
  *"-O check"*) test -e "$SSH_MASTER" ;;
  *" -N "*) echo "$stdio" > "$SSH_LOG.stdio"; test -z "$SSH_DOWN" || exit 255; : > "$SSH_MASTER" ;;
esac
"""


@pytest.fixture
def fake_ssh(tmp_path: Path, monkeypatch):
    """An `ssh` that logs its arguments and plays a ControlMaster: `-N` starts one (unless SSH_DOWN), `-O check` finds it.
    The socket directory stands in for $XDG_RUNTIME_DIR — short, as a real one is."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "ssh").write_text(FAKE_SSH, encoding="utf-8")
    (bin_dir / "ssh").chmod(0o755)
    run_dir = Path(tempfile.mkdtemp(prefix="ll", dir="/tmp"))
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(run_dir))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("SSH_LOG", str(tmp_path / "ssh.log"))
    monkeypatch.setenv("SSH_MASTER", str(tmp_path / "master"))
    monkeypatch.delenv("GIT_SSH_COMMAND", raising=False)
    monkeypatch.setenv("LABLEDGER_SSH_SHARE", "1")
    monkeypatch.setattr(ll, "_SSH_SHARED", {})
    yield run_dir
    shutil.rmtree(run_dir, ignore_errors=True)


def test_fetches_and_pushes_over_ssh_share_one_connection(grepo: Path, tmp_path: Path, fake_ssh: Path, monkeypatch):
    """The tool starts the shared connection itself (detached from every pipe) and git's ssh only joins it."""
    log = tmp_path / "ssh.log"

    def calls() -> list[str]:
        return log.read_text(encoding="utf-8").splitlines() if log.exists() else []

    git(grepo, "remote", "add", "origin", "git@example.invalid:team/ledger.git")
    monkeypatch.delenv("LABLEDGER_SSH_SHARE")
    assert ll._network_env(grepo, "origin")["GIT_SSH_COMMAND"] == "ssh -o BatchMode=yes -o ConnectTimeout=15" and not calls(), \
        "only a machine that asks for it gets a shared connection"
    monkeypatch.setenv("LABLEDGER_SSH_SHARE", "1")
    shared = f"ControlPath={fake_ssh}/labledger-%C"
    env = ll._network_env(grepo, "origin")
    assert env["GIT_SSH_COMMAND"] == f"ssh -o BatchMode=yes -o ConnectTimeout=15 -o ControlMaster=no -o {shared}"
    check, start, recheck = calls()
    assert check.endswith("-O check git@example.invalid") and shared in check and recheck == check
    assert " -N " in start and "ControlMaster=auto" in start and f"ControlPersist={ll.SSH_PERSIST}" in start
    assert (tmp_path / "ssh.log.stdio").read_text(encoding="utf-8").split() == ["/dev/null"] * 3, \
        "the connection outlives the command: it must not hold the caller's pipes"
    ll._network_env(grepo, "origin")
    assert len(calls()) == 3, "looked up once per command"
    monkeypatch.setattr(ll, "_SSH_SHARED", {})
    ll._network_env(grepo, "origin")
    assert calls()[3:] == [check], "the next command finds the connection alive and does not start another"

    (tmp_path / "master").unlink()
    monkeypatch.setenv("SSH_DOWN", "1")
    monkeypatch.setattr(ll, "_SSH_SHARED", {})
    assert ll._network_env(grepo, "origin")["GIT_SSH_COMMAND"] == "ssh -o BatchMode=yes -o ConnectTimeout=15", \
        "no shared connection: git connects on its own"
    before = len(calls())
    git(grepo, "config", "core.sshCommand", "ssh -i /elsewhere/key")
    assert "GIT_SSH_COMMAND" not in ll._network_env(grepo, "origin"), "the clone's own ssh stays"
    git(grepo, "config", "--unset", "core.sshCommand")
    git(grepo, "remote", "set-url", "origin", str(tmp_path / "origin.git"))
    ll._network_env(grepo, "origin")
    assert len(calls()) == before, "no ssh for a remote that is not reached over SSH, nor for a clone with its own ssh"


@pytest.mark.skipif(ll.NAME != ll.SOURCE_NAME, reason="new ledgers are made in the tool's source repository")
def test_setup_asks_first_then_makes_a_ledger_under_the_chosen_name_with_a_skill_that_answers_to_it(tmp_path: Path, monkeypatch, capsys):
    """Without its answers setup only prints the questions, recommending a name nothing on the machine answers to yet and
    keeping the answers given. It refuses what would break later (a taken name, a remote that is not empty) before it makes
    anything. Then it makes the ledger under the chosen name, every file carrying it, writes the skill of that name once
    per skill folder (Claude Code's is a link to the shared one here), and leaves English skeletons to translate for a
    ledger in another language. The new ledger's own tool runs, reserves its numbers on the remote, and puts its skill
    on a machine again; a ledger kept on one machine has nothing to sync."""
    home, shared = tmp_path / "home", tmp_path / "home" / ".agents" / "skills"
    (shared / "ledger").mkdir(parents=True)
    (shared / "ledger" / "SKILL.md").write_text("---\nname: ledger\ndescription: another tool\n---\n", encoding="utf-8")
    (home / ".claude").mkdir()
    (home / ".claude" / "skills").symlink_to(shared)
    (home / ".codex").mkdir()
    monkeypatch.setenv("HOME", str(home))
    for var in ("LABNOTES_HOST", "LABSOLO_HOST"):
        monkeypatch.setenv(var, "test-server")
    for who in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{who}_NAME", "t")
        monkeypatch.setenv(f"GIT_{who}_EMAIL", "t@example.org")
    remote, used, path = tmp_path / "remote.git", tmp_path / "used", tmp_path / "labnotes"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(remote))
    git(tmp_path, "init", "-q", "-b", "main", str(used))
    git(used, "commit", "-q", "--allow-empty", "-m", "someone's work")
    answers = ["--path", str(path), "--timezone", "Asia/Seoul", "--language", "Korean", "--agents", "claude", "codex"]
    assert ll.main(["setup"]) == 2
    out = capsys.readouterr().out
    assert "recommended: labbook" in out and "taken here: ledger — a skill" in out and "--agents claude codex" in out
    assert ll.main(["setup", "--name", "labnotes", "--timezone", "Europe/Berlin"]) == 2
    out = capsys.readouterr().out
    assert "--name:" not in out and "--name labnotes --path ~/labnotes --remote none --timezone Europe/Berlin" in out, "answers given stay"
    for name, why in (("ledger", "a skill"), ("json", "a Python module"), ("git", "a command"), ("Notes", "lowercase"),
                      ("notes", "git's own refs")):
        assert ll.main(["setup", "--name", name, "--remote", str(remote), *answers]) == 2
        assert why in capsys.readouterr().err, name
    assert ll.main(["setup", "--name", "labnotes", "--remote", str(used), *answers]) == 2
    assert "is not empty" in capsys.readouterr().err
    assert not path.exists(), "a refused setup makes nothing"
    refusing = tmp_path / "refusing.git"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(refusing))
    (refusing / "hooks" / "pre-receive").write_text("#!/bin/sh\necho 'push refused here' >&2\nexit 1\n", encoding="utf-8")
    (refusing / "hooks" / "pre-receive").chmod(0o755)
    assert ll.main(["setup", "--name", "labnotes", "--remote", str(refusing), *answers]) == 1
    assert "push refused here" in capsys.readouterr().err, "git's own reason is shown"
    assert not any(path.iterdir()) and not (shared / "labnotes").exists(), "a ledger that could not be pushed is not left half made"

    assert ll.main(["setup", "--name", "labnotes", "--remote", str(remote), *answers]) == 0
    assert "Translate each templates/<kind>.md" in capsys.readouterr().out
    assert subprocess.run(["git", "-C", str(path), "grep", "-qi", "labledger"]).returncode == 1, "every file carries the new name"
    assert git(path, "log", "--format=%s") == "labnotes: start the ledger\n" and git(remote, "rev-parse", "main") == git(path, "rev-parse", "HEAD")
    tool = path / "scripts" / "labnotes.py"
    skill = (shared / "labnotes" / "SKILL.md").read_text(encoding="utf-8")
    assert skill.startswith("---\nname: labnotes\n") and f"python3 {tool} *" in skill and "Write the records in Korean." in skill
    assert sorted(p.name for p in shared.iterdir()) == ["labnotes", "ledger"], "one skill folder for both agents"

    for argv in (["validate"], ["new", "exp", "first run"]):
        r = subprocess.run([sys.executable, str(tool), *argv], capture_output=True, text=True)
        assert r.returncode == 0, r.stdout + r.stderr
    assert git(path, "log", "-1", "--format=%s") == "labnotes(exp): open EXP-0001 — first run\n"
    assert git(remote, "for-each-ref", "--format=%(refname)") == "refs/heads/main\nrefs/labnotes/ids\n", "numbers are reserved on the remote"
    assert subprocess.run([sys.executable, str(tool), "setup"], capture_output=True).returncode == 2, "it asks for the agents first"
    (shared / "labnotes").rename(tmp_path / "removed")
    r = subprocess.run([sys.executable, str(tool), "setup", "--agents", "codex"], capture_output=True, text=True)
    assert r.returncode == 0 and (shared / "labnotes" / "SKILL.md").read_text(encoding="utf-8") == skill, r.stderr

    solo = tmp_path / "labsolo"
    assert ll.main(["setup", "--name", "labsolo", "--path", str(solo), "--remote", "none", "--timezone", "UTC", "--language", "English",
                    "--agents", "claude"]) == 0
    assert not (solo / "templates").exists()
    r = subprocess.run([sys.executable, str(solo / "scripts" / "labsolo.py"), "sync"], capture_output=True, text=True)
    assert r.returncode == 0 and "nothing to sync" in r.stdout, r.stdout + r.stderr
