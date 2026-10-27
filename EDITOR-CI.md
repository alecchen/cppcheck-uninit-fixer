# Editor and CI integration

How to get uninitialized-member findings in front of a developer without running the
fixer by hand. Pick the row that matches the team's existing workflow. An editor runs
cppcheck or clang-tidy in one build configuration; the CI and hook rows run the fixer's
`--fast` mode, which does not depend on configuration.

| Where the team works | Hook | Detection |
|---|---|---|
| vim / neovim | cppcheck in quickfix, or ALE | cppcheck, one configuration |
| VS Code | C/C++ extension with cppcheck as linter | cppcheck, one configuration |
| VS Code | clang-tidy via C/C++ extension | clang-tidy, needs compile_commands.json |
| GitHub | Actions workflow on push and pull request | fixer `--fast`, changed files |
| GitLab | `.gitlab-ci.yml`, merge requests | fixer `--fast`, changed files |
| Neither | git pre-commit hook | fixer `--fast`, staged files |
| Neither | release checklist | fixer `--fast`, whole tree |

Every editor option shares the same blind spot, and it is the reason this fixer exists. An
editor resolves a single set of `-D` flags, so a member behind `#ifdef FEATURE_X` is
visible only when the editor's configuration defines `FEATURE_X`. `--fast` parses the
source directly and ignores configuration. It is also the only mode that fits an editor or
CI budget: cppcheck mode runs one pass per extracted macro and needs a compilation
database.

## Gating on findings

`--report-only` exits `0` even when it finds problems, so pass `--fail-on-findings` to turn
it into a gate:

```bash
python3 cppcheck-fix-uninit.py --fast --report-only --fail-on-findings src/
```

Exit `1` means at least one finding. Findings print one per line on stdout as
`path:line: Class::member -> default`. The flag is off by default and only affects
`--report-only`, so existing scripts behave as before.

Changed files only, deliberately. A whole-tree gate on an existing recipe goes red for
reasons the current author did not cause, and someone disables it within a week. Scoped to
the diff, it stays green until a new uninitialized member shows up.

## GitHub Actions

`.github/workflows/uninit.yml`:

```yaml
name: uninit

on:
  push:
    paths: ['**/*.h', '**/*.hpp', '**/*.cpp', '**/*.cc']
  pull_request:
    paths: ['**/*.h', '**/*.hpp', '**/*.cpp', '**/*.cc']

jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - name: Fetch fixer
        run: git clone --depth 1 https://github.com/alecchen/cppcheck-uninit-fixer.git .fixer
      - name: Uninitialized members in changed files
        run: |
          base=${{ github.event.pull_request.base.sha || github.event.before }}
          [ -z "$base" ] || ! git cat-file -e "$base^{commit}" 2>/dev/null && exit 0
          files=$(git diff --name-only "$base" HEAD -- '*.h' '*.hpp' '*.cpp' '*.cc')
          if [ -z "$files" ]; then echo "no C++ changes"; exit 0; fi
          if ! python3 .fixer/cppcheck-fix-uninit.py --fast --report-only \
                 --fail-on-findings $files; then
            echo "::error::uninitialized members in this change"
            exit 1
          fi
```

`fetch-depth: 0` is required. The default shallow clone has no base commit, so `git diff`
fails and the job passes without checking anything.

## GitLab CI

`.gitlab-ci.yml`, works on self-hosted GitLab 19.x:

```yaml
uninit:
  image: python:3.11-slim
  stage: test
  variables:
    GIT_DEPTH: "0"
  rules:
    - if: $CI_PIPELINE_SOURCE == "merge_request_event"
    - if: $CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH
  before_script:
    - apt-get update && apt-get install -y --no-install-recommends git
    - git clone --depth 1 https://github.com/alecchen/cppcheck-uninit-fixer.git .fixer
  script:
    - |
      base=${CI_MERGE_REQUEST_DIFF_BASE_SHA:-$CI_COMMIT_BEFORE_SHA}
      git cat-file -e "$base^{commit}" 2>/dev/null || { echo "no base commit, skipping"; exit 0; }
      files=$(git diff --name-only "$base" HEAD -- '*.h' '*.hpp' '*.cpp' '*.cc')
      if [ -z "$files" ]; then echo "no C++ changes"; exit 0; fi
      python3 .fixer/cppcheck-fix-uninit.py --fast --report-only --fail-on-findings $files
```

`GIT_DEPTH: "0"` for the same reason: without full history, `git diff` against the merge
base fails.

## git pre-commit hook

For a team with no pipeline. Install as `.git/hooks/pre-commit`, `chmod +x`:

```bash
#!/bin/sh
command -v python3 >/dev/null || exit 0
files=$(git diff --cached --name-only --diff-filter=ACM -- '*.h' '*.hpp' '*.cpp' '*.cc')
[ -z "$files" ] && exit 0
if ! python3 .fixer/cppcheck-fix-uninit.py --fast --report-only --fail-on-findings $files; then
  echo "Fix in the editor, or re-run with --fast to apply defaults, then review the diff."
  exit 1
fi
```

The hook must not run the fixer without `--report-only`; it would rewrite staged source in
the middle of a commit.

## vim and neovim

cppcheck in the quickfix list, no plugin. The custom template matters: the default one
appends the source line and a caret line, and the caret line matches nothing in
`errorformat`.

```vim
" .vimrc
set makeprg=cppcheck\ --enable=warning,style\ --inconclusive\ -q\ --template={file}:{line}:{column}:\ {severity}:\ {message}\ [{id}]\ %
set errorformat=%f:%l:%c:\ %t%*[^:]:\ %m
```

`:make` fills the quickfix list, `:copen` opens it, `:cc 3` jumps to the third finding.
cppcheck 2.22 accepts `-q` and `-q -q` but rejects `-qq`; keep the single `-q`, which
suppresses the `Checking file ...` progress lines.

With ALE (<https://github.com/dense-analysis/ale>):

```vim
let g:ale_linters = {'cpp': ['cppcheck', 'clangtidy']}
let g:ale_cpp_cppcheck_options = '--enable=warning --inconclusive'
```

Findings land in the location list on save. `clangtidy` is optional and needs a
`compile_commands.json` to resolve include paths.

## VS Code

The C/C++ extension (`ms-vscode.cpptools`) has no uninitialized-member check of its own.
IntelliSense parses for completion and does no dataflow analysis, so a checker has to be
wired in.

cppcheck, which needs no compile database:

```jsonc
// .vscode/settings.json
{
  "C_Cpp.intelliSenseEngine": "default",
  "cppcheck.cppcheckPath": "/usr/local/bin/cppcheck",
  "cppcheck.enable": true,
  "cppcheck.args": ["--enable=warning,style", "--inconclusive", "--quiet", "-q"]
}
```

Requires the third-party `cppcheck` extension by Joseph Benden, not just `cpptools`.

clang-tidy, which needs the build's include paths:

```jsonc
// .vscode/settings.json
{
  "C_Cpp.codeAnalysis.clangTidy.enabled": true,
  "C_Cpp.codeAnalysis.clangTidy.args": ["--checks=cppcoreguidelines-pro-type-member-init"],
  "C_Cpp.default.compileCommands": "${workspaceFolder}/compile_commands.json"
}
```

clang-tidy accepts `.clang-tidy` in the source tree instead of inline args:

```yaml
Checks: 'cppcoreguidelines-pro-type-member-init'
WarningsAsErrors: ''
```

Generate the database with `bear -- ./build.sh`, or with CMake's
`-DCMAKE_EXPORT_COMPILE_COMMANDS=ON`.

## Compiler flags

These cut exposure to uninitialized stack members without touching the source or changing
anyone's workflow, so they stack with every option above:

```bash
clang++ -ftrivial-auto-var-init=pattern   # fills stack objects with 0xAA
clang++ -ftrivial-auto-var-init=zero      # fills with 0, hides the bug
```

`pattern` surfaces a forgotten member as a large wrong value. `zero` makes the member
behave as if initialized, which hides the defect, so it is the wrong choice for this job.
The flag covers stack objects and their members before the constructor runs; it does not
cover `new`. Catching stack objects only still covers the common case for recipe-local
structs.

## Known gaps in the gate

The gate is shallow. It fails on findings, and a file that yields none passes. One form
yields none and therefore passes silently:

```cpp
class X { private: int bad_; };   // class on one line, member after the access specifier
```

`class X { int bad_; };` without a specifier is detected. A class body that opens on the
class-name line with ordinary one-member-per-line indentation is also detected. If the
first form is common in the codebase, add a full-tree `--fast --report-only` to the release
checklist instead of relying on the diff-scoped gate alone.

## What each checker misses

| Checker | Flags unset members | Guarded (`#ifdef`) members | Needs compile DB |
|---|---|---|---|
| cppcheck | yes | only the configurations it is given | no |
| clang-tidy | yes, with `cppcoreguidelines-pro-type-member-init` | only the configuration it is given | yes |
| clang `-Wall -Wextra` | no | no | no |
| clang static analyzer | only where the value is read in the same translation unit | no | no |
| this fixer, `--fast` | yes | yes, guard-independent | no |

The fixer is the only row that covers guards without configuration, which is why it is
worth a run once per release even when the editor and CI are already in place.

## Recommended adoption order

1. `-ftrivial-auto-var-init=pattern` in debug builds. No adoption cost.
2. cppcheck in the editor they already use. Catches the unguarded majority while typing.
3. A diff-scoped gate in whatever review system exists: CI job, pre-commit hook, or the
   review tool's diff check.
4. `--fast --report-only` once per release on the full tree, to cover guarded members and
   to give the tool author a bug report that does not include source.
