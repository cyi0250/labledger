# A ledger on several machines

[AGENTS.md](AGENTS.md) holds the rules every session uses. This file holds what only a ledger kept on more than one
machine meets. Such a ledger shares one remote, a private GitHub repository for instance, and each machine keeps a clone
of it; a new machine clones the ledger and puts its skill on with `setup` ([SETUP.md](SETUP.md)).

## Numbers

- **Numbers are reserved on the remote.** When the branch tracks an upstream, `new todo`, `new exp` and `new arc` raise
  a counter on it (`refs/labledger/ids`), so two machines never issue the same number. They check the record first and
  reserve its number only when it passes, so a refused record takes none. When that remote cannot be reached, nothing is
  made; `--offline` takes the local maximum + 1 instead and notes it in the clone's copy of the counter. A branch
  without an upstream takes the local maximum + 1. A commit that adds a TODO, an experiment or an arc under a number
  past the clone's copy of the counter, which no `new` reserved, is refused; a clone has that copy from its first
  `new` on. *Guidance:* gaps are fine and no number is reused.
- **Two records under one number** come only from `--offline` (another machine took the same number meanwhile), and
  they are repaired by hand. sync stops on the record's file, and only one record keeps the number. Inside
  `.git/labledger-sync/`, put the other machine's file back (`git show <remote>/<branch>:<path> > <path>`; this
  machine's version stays in `git show CHERRY_PICK_HEAD:<path>`), write this machine's record under a new number
  (`new … --no-commit` reserves one: delete the file it writes), change its `id` and H1, then `sync`. That fixes the
  commit that made the record. The local commits after it are replayed as they are: whatever they put under the old
  number (a mention, a `state` row, an attachment, which the private worktree never holds) now belongs to the other
  machine's record and moves to the new number in a commit after sync. `git log -p @{u}..` in the clone lists those
  commits before sync resumes. *Guidance:* use `--offline` only when the remote cannot be reached.

## Machine names and dates

Each machine has a name: `LABLEDGER_HOST` when it is set (in the shell profile), else the host name up to its first
dot. `new exp` writes it into `host`, the machine whose local paths the body names (`--host` names another, for a run
made elsewhere), and the counter's log records it. On a machine called `localhost`, `new exp` without `--host` and
every number reservation refuse to run until `LABLEDGER_HOST` is set.

Write commands stamp today in `timezone` from `labledger.yml`, or in each machine's own time zone when that names none.
*Guidance:* a ledger written from several time zones names one.

## Sync

`sync` brings in what other machines pushed, and the tool runs it when a push is refused because another machine
pushed first. It fetches, replays this clone's own commits one by one on top of the remote's in a private worktree
(`.git/labledger-sync`, validating each), moves the branch, rewrites only the files that changed, and pushes.
Replayed commits get new ids.

When sync stops, it moves nothing and says why:

- both sides changed the same lines: merge the files it names inside `.git/labledger-sync/` (keep both records,
  delete the `<<<<<<<` `=======` `>>>>>>>` lines), then `sync`; when both added a record under one number, see
  [Numbers](#numbers);
- a replayed commit does not validate (two machines made the same `dec:` address): fix it there, then `sync`;
- an incoming file has uncommitted changes here: commit them, then `sync`;
- `sync --abort` drops the stopped replay; the branch and the local commits stay as they were.

## A shared SSH connection

A shared SSH connection is opt-in. With `LABLEDGER_SSH_SHARE=1`, the tool keeps one connection per remote host
open for 10 minutes after its last use (a background `ssh -N` with the socket `labledger-%C` in `$XDG_RUNTIME_DIR`
or `~/.ssh`), and the fetches and pushes of one command share one handshake. `ControlMaster` in `~/.ssh/config` does
the same for every program.

## A clone without the attachments

A machine that does not need the attachments clones without them:

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
