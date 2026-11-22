# Working on photo-backer-upper

Guidance for coding agents. The README describes what pback does and how it is set up; this file
covers how to change it.

## Checks

```sh
uv run pytest
uv run ruff format --check .
uv run ruff check .
```

Enable the pre-commit hook once per clone: `git config core.hooksPath .githooks`. It runs the three
checks and refuses the commit if any fails. Never bypass it with `--no-verify`.

Open work is tracked in GitHub issues (`gh issue list`), not in a file in the repository.

## Safety

pback acts on a real family photo archive, and a scheduled launchd job runs it every few hours.

- Never run `pback fix-dates --apply`, `pback fix-export-names --apply`, `export`, `mirror`, `run`,
  `reconcile` or `install-agent` against the real archive without asking first. `--help` and the
  report-only forms of `fix-dates` and `fix-export-names` are safe: they rename nothing.
- Agent sessions can't read the Photos library, so any command that calls `osxphotos` fails there
  with "Operation not permitted". When a change needs checking against the real library, give the
  owner a command to run in Terminal.
- The scheduled job runs the installed copy, not this checkout. After a change lands on `main`,
  `uv tool install . --reinstall` puts it into use.
- The repository is public. Never put real file names, dates or UUIDs from the archive or library
  into tests, commits, issues or comments.

## Behavior is an interface

Treat all of these as stable unless the task is to change them:

- command-line flags, help text and exit codes
- the `osxphotos`, `rsync` and `exiftool` command lines, including argument order
- every file pback writes to the state folder or the archive: its name, format and row order
- printed output, which the owner reads in logs
- the undo-log format (`old<TAB>new`, one line per rename). `pback fix-dates --undo` has to keep
  reading logs written by earlier versions.

A refactor changes none of these. Prove it with tests, not by reading the code.

## Code style

Ruff settles formatting and lint (line length 100; rules in `pyproject.toml`, including the
complexity limit of 10). Beyond that:

- **One module per topic.** `cli.py` holds only the argument parser, `main()` and thin `cmd_*`
  wrappers. The README's "The pieces" table lists every module; update it when you add one.
- **One home for each shared thing.** The capture-time format and its regex fragment are in
  `naming.py`. Export-database reads are in `exportdb.py`, the `osxphotos query` call is in
  `library.py`, and file extensions are in `media.py`. Use them; don't redefine them.
- **`naming.py` imports only the standard library.** osxphotos loads it by file path, outside the
  package.
- **Commands that change files are split into plan, report and apply.** Planning decides and touches
  nothing, reporting prints and writes the report, and applying acts. Plans are dataclasses (such as
  `Rename` in `renames.py`), not tuples read by position.
- **Names say what they hold.** No one- or two-letter names except `i` and `n` as counters, `e` for
  a caught exception, `f` for an open file, `m` for a regex match, `rc` for a return code, `_`, and
  variables inside comprehensions and lambdas.
- **Write state files with `atomic.write_text_atomic`**, so a crash never leaves half a file. Undo
  logs are the exception: they are appended and flushed one line at a time.
- **No `# noqa`** unless there is no clean alternative. Explain it in a comment. The one in
  `lock.py` is the example.
- **Comments explain why, not what.** Keep docstrings short and plain.
- **Keep commits small and single-purpose**, with the tests passing after each one.

## Tests

- Test through public entry points: `fix_dates`, `fix_export_names`, `cmd_export` and the like,
  never private helpers. Assert on what a user would see: printed text, report files, files on
  disk, and the command line passed to `run_cmd`.
- Use real files under `tmp_path` and a real SQLite file for the export database.
- Fake only calls to outside programs: `fixdates.exiftool_json`, `subprocess.run` as used by
  `library.py`, and `run_cmd`. Gather the patches in a fixture at the top of the test file, so a
  later move only has to retarget them there.
- Build command arguments with `cli.build_parser().parse_args([...])`, not a hand-made namespace.
- When a test pins behavior that looks wrong, keep it as it is today and file an issue instead of
  fixing it in the same change.
