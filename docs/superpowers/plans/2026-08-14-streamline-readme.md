# Streamlined README Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the root README with a concise, onboarding-first guide that gets new users from project overview to working embedded and Python examples quickly.

**Architecture:** Keep the README as the repository's entry point and move implementation depth behind a link to `docs/noxydb-como-funciona.md`. Preserve only the operational constraints that readers need for safe local use, then verify the document mechanically and with the available project tests.

**Tech Stack:** Markdown, Mermaid, Noxy examples, Python 3.10+, PowerShell verification commands

## Global Constraints

- Target roughly 120 to 160 lines.
- Prefer short paragraphs, bullets, and runnable code blocks.
- Keep one compact Mermaid diagram without implementation-level nodes.
- Avoid duplicating details already covered by the deep-dive document.
- Use relative Markdown links so documentation works on GitHub and locally.
- Keep the README in English.
- Preserve the local-only binding, lack of authentication, one-process-per-file constraint, logical Python close behavior, and durability boundary.

---

### Task 1: Rewrite the onboarding README

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: public embedded API from `noxydb/noxydb.nx`, server CLI from `server/noxydb_server.nx`, and Python API from `python/src/noxydb/client.py`
- Produces: a concise repository entry point linking to the examples and architecture deep dive

- [x] **Step 1: Record the baseline size and duplicated-detail problem**

Run:

```powershell
$readme = Get-Content -Raw README.md
$lines = (Get-Content README.md).Count
"lines=$lines"
@("read-idle deadline", "net_select", "Physical format and replay") | ForEach-Object { "$_=$($readme.Contains($_))" }
```

Expected: more than 160 lines and all three implementation-detail checks report `True`.

- [x] **Step 2: Replace the README with the approved onboarding structure**

Write `README.md` with these exact sections and responsibilities:

```text
# NoxyDB
  Two-sentence project description.
  Six bullets: document key-value model, append-only persistence, strict JSON,
  embedded Noxy API, local server, dependency-free Python client.

## Architecture
  Compact Mermaid flowchart containing only Noxy application, Python
  application/client, local HTTP server, database API, document codec, and
  append-only log.

## Quick start: embedded Noxy
  Complete open/put/get/close example and command using NOXY_EXE.

## Quick start: local server and Python
  Server launch, editable client install, and context-managed Python example.

## Data model and API
  JSON root/object constraints and a compact mapping table for open, put, get,
  exists, remove, and close in both APIs.

## Examples and internals
  Relative links to examples/documents.nx, examples/cadastro_usuarios.nx,
  examples/cadastro_usuarios.py, and docs/noxydb-como-funciona.md.

## Operational notes
  Local-only/no-auth warning, isolated database files, one process per file,
  logical Python close versus physical embedded close, append-before-memory
  behavior, no fsync/crash durability, and out-of-scope feature summary.

## Tests
  Python-only, complete, and integration PowerShell commands.
```

Use the current working examples from the existing README, shorten prose, and
do not describe transport deadlines, polling, replay grammar, or failure-state
internals.

- [x] **Step 3: Check size, required content, and removed duplication**

Run:

```powershell
$readme = Get-Content -Raw README.md
$lines = (Get-Content README.md).Count
if ($lines -lt 120 -or $lines -gt 160) { throw "README line count outside 120-160: $lines" }
@("## Architecture", "## Quick start: embedded Noxy", "## Quick start: local server and Python", "## Data model and API", "## Examples and internals", "## Operational notes", "## Tests") | ForEach-Object { if (-not $readme.Contains($_)) { throw "Missing section: $_" } }
@("read-idle deadline", "net_select", "P<TAB>", "poll-and-receive") | ForEach-Object { if ($readme.Contains($_)) { throw "Duplicated internal detail: $_" } }
```

Expected: exit code 0.

- [x] **Step 4: Check every local Markdown link**

Run:

```powershell
$readme = Get-Content -Raw README.md
$matches = [regex]::Matches($readme, '\[[^\]]+\]\((?!https?://|#)([^)#]+)(?:#[^)]+)?\)')
foreach ($match in $matches) { $path = $match.Groups[1].Value; if (-not (Test-Path -LiteralPath $path)) { throw "Broken local link: $path" } }
"Validated $($matches.Count) local links."
```

Expected: all four example/document paths exist and the command exits 0.

- [x] **Step 5: Review and commit the README**

Run:

```powershell
git diff --check
git diff -- README.md
git add README.md
git commit -m "docs: streamline README onboarding"
```

Expected: a focused README commit with no whitespace errors.

---

### Task 2: Add the release note and run repository verification

**Files:**
- Modify: `CHANGELOG.md`
- Verify: `README.md`
- Verify: `python/tests/test_client.py`
- Verify: `tests/*.nx`

**Interfaces:**
- Consumes: the README result from Task 1 and the existing PowerShell test runner
- Produces: a documented change with fresh test evidence suitable for a pull request

- [x] **Step 1: Add a changelog entry**

Insert before version `0.2.0`:

```markdown
## [0.2.1] - 2026-08-14

### Changed

- Streamlined the README around project onboarding, quickstarts, examples, and
  essential operational guidance, with implementation details linked from the
  existing deep-dive document. `#docs` @estevaofon
```

- [x] **Step 2: Run the dependency-free Python client tests**

Run:

```powershell
pwsh -File tests/run_tests.ps1 -Group python
```

Expected: `All Python client tests passed.` and exit code 0.

- [x] **Step 3: Run Noxy tests when the runtime is available**

Run:

```powershell
if ($env:NOXY_EXE -and (Test-Path -LiteralPath $env:NOXY_EXE)) {
    pwsh -File tests/run_tests.ps1
    pwsh -File tests/run_tests.ps1 -Group integration
} else {
    Write-Warning "NOXY_EXE is unavailable; Noxy and integration suites were not run."
}
```

Expected: both suites pass when `NOXY_EXE` is configured; otherwise the output explicitly records the missing prerequisite.

- [x] **Step 4: Perform the final documentation checks**

Run:

```powershell
git diff --check
git status --short
git diff --stat develop...HEAD
```

Expected: no whitespace errors; only the approved spec, plan, README, and changelog are included.

- [x] **Step 5: Commit the changelog and plan**

Run:

```powershell
git add CHANGELOG.md docs/superpowers/plans/2026-08-14-streamline-readme.md
git commit -m "docs: record README refresh"
```

Expected: the branch is clean and ready for the `open-pr` workflow targeting `develop`.
