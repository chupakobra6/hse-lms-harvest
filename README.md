<h1 align="center">hse-lms-harvest</h1>

<p align="center">
  Read-only Smart LMS course page and attachment harvester for local study automation.
</p>

<p align="center">
  <img alt="Python" src="https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white">
  <img alt="Playwright" src="https://img.shields.io/badge/Playwright-browser%20automation-2EAD33?logo=playwright&logoColor=white">
  <img alt="uv" src="https://img.shields.io/badge/uv-package%20manager-DE5FE9">
  <img alt="Read only" src="https://img.shields.io/badge/default-read--only-0E7C7B">
</p>

<p align="center">
  <a href="#why">Why</a> ·
  <a href="#quick-start">Quick start</a> ·
  <a href="#capture-output">Capture output</a> ·
  <a href="#safety">Safety</a> ·
  <a href="#repository-map">Repository map</a>
</p>

## Why

`hse-lms-harvest` opens Smart LMS pages through a local browser profile and turns course pages into
agent-friendly files: compact Markdown navigation, full JSON page captures, downloaded study
attachments, and structured diagnostics.

The default workflow is conservative. It reads visible course content, opens read-only assignment
detail pages when they contain task text, and avoids form submission or course-state mutation.

| Capability | What it gives |
| --- | --- |
| Browser-backed capture | Uses the same authenticated pages a student can already access. |
| Compact Markdown layer | `navigation.md`, `summary.md`, and `pages/*.md` are optimized for fast agent reads. |
| Full JSON layer | `manifest.json` and `pages/*.json` keep complete links, buttons, file metadata, and errors. |
| Attachment handling | Same-site study files, including Moodle `pluginfile.php`, can be downloaded with caching. |
| Privacy filters | Personal submission files, grades, messages, calendars, and report/detail noise are excluded. |
| Local diagnostics | Errors are written as structured bundles without saving full HTML by default. |

## Quick Start

```bash
cd hse-lms-harvest
uv sync --extra dev
make check
```

Set credentials without writing them to shell history:

```bash
uv run hse-lms-harvest credentials set \
  --username "student@example.edu" \
  --env-file ".env" \
  --password-stdin
```

Run a course capture:

```bash
uv run hse-lms-harvest harvest \
  --url "https://edu.hse.ru/my/courses.php" \
  --profile ".browser-profile" \
  --out "dumps" \
  --course-title "Course title" \
  --max-pages 260 \
  --download-files \
  --ensure-login \
  --auto-login \
  --headless
```

For command discovery:

```bash
make help
uv run hse-lms-harvest --help
```

## Capture Output

A run writes a new `dumps/<host>-YYYYMMDD-HHMMSS/` directory:

| Path | Purpose |
| --- | --- |
| `manifest.json` | Machine-readable source of truth after capture-level filters. |
| `navigation.md` | First file to give an agent: page tree plus local Markdown/JSON/file pointers. |
| `navigation.json` | Machine-readable navigation without LMS URLs. |
| `summary.md` | Short human/agent summary without internal indexes. |
| `pages/*.md` | Compact page text without repeated navigation lines, service URLs, hashes, or action URLs. |
| `pages/*.json` | Full page structure: text, links, buttons, downloaded file records, and errors. |
| `files/` | Downloaded attachments when `--download-files` is enabled. |
| `debug/errors.md` | Short error index with pointers to structured diagnostic bundles. |

If only the output format changed, reuse an existing dump instead of hitting LMS again:

```bash
uv run hse-lms-harvest migrate --out dumps/current-subjects
```

## Safety

- `.env`, `.browser-profile/`, `dumps/`, browser cookies, screenshots, and logs are ignored by git.
- The harvester does not click `Save`, `Submit`, `Delete`, or similar state-changing controls.
- `--allow-state-changes` only permits explicit completion toggles; save/submit/delete stays blocked.
- Audio/video and conference artifacts are skipped by default; use `--download-media` only when you intentionally need them.
- Personal submission files under `assignsubmission_file/submission_files` are not downloaded and are removed from compact capture text.
- Tests use local fixtures and `about:blank`; they do not call a live LMS.

## Testing

```bash
make check
make doctor
make smoke
```

`make smoke` runs against `about:blank` with temporary paths, so it does not require LMS access.

## Repository Map

| Path | Purpose |
| --- | --- |
| `src/hse_lms_harvest/cli.py` | CLI entrypoint, Playwright orchestration, and page queue. |
| `src/hse_lms_harvest/cli_args.py` | Command arguments, defaults, and help text. |
| `src/hse_lms_harvest/capture.py` | Page text, inline links, and read-only action/detail pages. |
| `src/hse_lms_harvest/classify.py` | URL/file/media classification and unsafe navigation filters. |
| `src/hse_lms_harvest/downloads.py` | Attachment downloads, HEAD checks, size limits, and file cache. |
| `src/hse_lms_harvest/manifest.py` | Manifest loading, migration, fingerprints, and page-cache helpers. |
| `src/hse_lms_harvest/render.py` | `manifest.json`, navigation files, page JSON, and compact Markdown. |
| `src/hse_lms_harvest/privacy.py` | URL and diagnostic redaction helpers. |
| `tests/` | Unit tests for public contracts without live LMS calls. |
