---
name: literature-method-extraction
description: Runs a local, auditable pipeline that archives papers for a configured research topic and extracts data-analysis methods from open Europe PMC full text. Use for recurring method surveys, structured comparison of preprocessing, statistical analysis, software, and validation practices, or a local GUI for reviewing extracted methods.
license: MIT
compatibility: Requires Python 3.11+ on Linux or macOS and network access to OpenAlex and Europe PMC. Uses the literature-archive skill's configuration and local SQLite database. The GUI runs locally in a browser; optional PyInstaller packages it as a desktop launcher.
allowed-tools: Read Write Edit Bash
metadata:
  version: "1.0"
  skill-author: K-Dense Inc.
---

# Literature Method Extraction

## When to use

Use this skill for a recurring question such as “what data-analysis methods are
used in papers on this topic?” It composes `literature-archive` with a bounded,
deterministic extraction stage and a local review GUI. The result is a local
SQLite evidence table, not an unverified narrative review.

## Scope and limits

- The pipeline downloads only explicitly open-access PDFs through
  `literature-archive`; it never bypasses paywalls.
- Method extraction reads Europe PMC JATS full text only when a PMCID is
  available. A PDF, abstract, title, or metadata record is never treated as a
  substitute for a Methods section.
- The summary is lexical evidence extraction, not an LLM judgment. It stores
  source snippets and labels records `ready_for_review`; a reviewer must accept,
  revise, or reject the result before downstream use.

## Setup

Create an archive configuration using `literature-archive`:

```bash
mkdir -p research/method-survey
cp skills/literature-archive/assets/config.example.json \
  research/method-survey/archive.json
```

Set `database`, `pdf_directory`, and the topic query in `archive.json`. Keep
runtime data outside `skills/`.

## CLI

Run discovery, archive, JATS retrieval, extraction, and export in one command:

```bash
python skills/literature-method-extraction/scripts/literature_methods.py run \
  --archive-config research/method-survey/archive.json \
  --output research/method-survey/methods.csv
```

The output CSV has one row per paper and includes identifiers, extraction status,
structured method fields, provenance URL, reviewer status, and the Methods
excerpt. Limit a run during initial review:

```bash
python skills/literature-method-extraction/scripts/literature_methods.py run \
  --archive-config research/method-survey/archive.json \
  --limit 25 --output research/method-survey/methods.csv
```

`--skip-archive` extracts from an existing local archive without new network
discovery. `--refresh` fetches JATS again for records already extracted.

## GUI

Start the local review interface after a successful CLI run:

```bash
python skills/literature-method-extraction/scripts/literature_methods_gui.py \
  --archive-config research/method-survey/archive.json
```

Open the displayed loopback URL. The interface starts the same CLI, displays
paper-level status and method evidence, and records accept/revise/reject review
decisions in SQLite. It does not expose a network service outside the local
machine.

To package a launcher for colleagues, build in a dedicated environment:

```bash
uv tool run pyinstaller --onefile --name literature-methods \
  skills/literature-method-extraction/scripts/literature_methods_gui.py
```

The executable still needs an archive configuration and network access. Package
the example configuration separately, not a real configuration containing local
paths or credentials.

## Review protocol

1. Inspect `unavailable` records; they do not have accessible JATS Methods.
2. For `ready_for_review` records, compare each extracted field and excerpt to
   the source URL.
3. Mark the record accepted, revised, or rejected in the GUI. Export only
   accepted/revised records for a formal synthesis.

Run tests with:

```bash
python tests/run_all.py --isolated literature-method-extraction
```
