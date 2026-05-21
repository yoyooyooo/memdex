# NotebookLM CLI Reference

Load this reference when a task needs concrete NotebookLM CLI syntax,
generation/download behavior, JSON parsing, or failure handling.

## Install

```bash
uv tool install git+https://github.com/teng-lin/notebooklm-py.git
notebooklm login
notebooklm list --json
```

`uv tool install` keeps the executable under uv's tool dir and links it into
`~/.local/bin`, avoiding `/tmp` virtualenv loss. Use a PyPI release or a pinned
GitHub commit/tag when reproducibility matters. For a one-off test only:

```bash
uvx --from git+https://github.com/teng-lin/notebooklm-py.git notebooklm --help
```

## Context And Profiles

Notebook context is profile-local and can be overwritten by concurrent agents.
Automation should pass notebook IDs explicitly:

```bash
notebooklm ask "question" -n <notebook_id> --json
notebooklm source list -n <notebook_id> --json
notebooklm artifact wait <artifact_id> -n <notebook_id>
```

For isolation:

```bash
export NOTEBOOKLM_PROFILE=agent-$ID
export NOTEBOOKLM_HOME=/tmp/notebooklm-agent-$ID
```

## Notebook And Source Commands

```bash
notebooklm create "Title" --json
notebooklm list --json
notebooklm use <notebook_id>
notebooklm source add ./file.pdf -n <notebook_id> --json
notebooklm source add "https://example.com" -n <notebook_id> --json
notebooklm source list -n <notebook_id> --json
notebooklm source wait <source_id> -n <notebook_id> --timeout 600
notebooklm source fulltext <source_id> -n <notebook_id>
notebooklm source guide <source_id> -n <notebook_id>
```

Deletion is destructive:

```bash
notebooklm source delete <source_id> -n <notebook_id>
notebooklm source delete-by-title "Exact Title" -n <notebook_id>
notebooklm notebook delete <notebook_id>
```

## Chat

```bash
notebooklm ask "Summarize this corpus" -n <notebook_id>
notebooklm ask "Return cited files and keywords" -n <notebook_id> --json
notebooklm ask "Question" -s <source_id> -s <source_id> -n <notebook_id> --json
notebooklm ask "Question" -c <conversation_id> -n <notebook_id>
```

Only save notes when requested:

```bash
notebooklm ask "Question" -n <notebook_id> --save-as-note --note-title "Title"
notebooklm history -n <notebook_id> --save --note-title "History"
```

## Generation

All generation commands can use `--json`, source filters with `-s`, and
language override with `--language`.

```bash
notebooklm generate audio "Focus on tradeoffs" -n <notebook_id> --json
notebooklm generate video "Explain architecture" -n <notebook_id> --json
notebooklm generate slide-deck --format detailed -n <notebook_id> --json
notebooklm generate report --format briefing-doc -n <notebook_id> --json
notebooklm generate report "Custom prompt" --format custom -n <notebook_id> --json
notebooklm generate mind-map -n <notebook_id> --json
notebooklm generate data-table "Compare decisions" -n <notebook_id> --json
notebooklm generate quiz --difficulty medium -n <notebook_id> --json
notebooklm generate flashcards --difficulty medium -n <notebook_id> --json
```

Long generation can take minutes. In the main conversation, start the job,
return artifact ID, and avoid tight polling. In a background agent, wait:

```bash
notebooklm artifact wait <artifact_id> -n <notebook_id> --timeout 1200
```

## Downloads

```bash
notebooklm download audio ./output.mp3 -n <notebook_id> -a <artifact_id>
notebooklm download video ./output.mp4 -n <notebook_id> -a <artifact_id>
notebooklm download slide-deck ./slides.pptx --format pptx -n <notebook_id> -a <artifact_id>
notebooklm download report ./report.md -n <notebook_id> -a <artifact_id>
notebooklm download mind-map ./map.json -n <notebook_id> -a <artifact_id>
notebooklm download data-table ./table.csv -n <notebook_id> -a <artifact_id>
notebooklm download quiz ./quiz.md --format markdown -n <notebook_id> -a <artifact_id>
notebooklm download flashcards ./cards.md --format markdown -n <notebook_id> -a <artifact_id>
```

## Web Research

```bash
notebooklm source add-research "topic" --mode fast -n <notebook_id> --import-all
notebooklm source add-research "topic" --mode deep -n <notebook_id> --no-wait
notebooklm research status -n <notebook_id> --json
notebooklm research wait -n <notebook_id> --import-all --timeout 1800
```

## JSON Fields

Common extraction targets:

- `create --json`: `.notebook.id`
- `source add --json`: `.source.id`
- `generate * --json`: `.artifact.id` or `.task_id`, depending on CLI version
- `ask --json`: `.references[].source_id`
- `list --json`: `.notebooks[]`
- `source list --json`: `.sources[]`
- `artifact list --json`: `.artifacts[]`

Use `jq` or the host language JSON parser instead of brittle text matching.

## Failure Handling

| Symptom | Likely Cause | Action |
|---|---|---|
| auth/cookie error | expired session | `notebooklm auth check --test`, then `notebooklm login` |
| no notebook context | implicit context absent | pass `-n` / `--notebook` |
| invalid ID | wrong notebook/source/artifact | `notebooklm list --json`, `source list --json`, `artifact list --json` |
| timeout exit code `2` | wait exceeded timeout | check status, extend timeout |
| generation failed/rate limited | provider limit | retry later or use web UI fallback |
| source not ready | indexing still running | `source wait` before chat/generation |

Reliable operations: notebook/source CRUD, chat, source fulltext, reports,
mind maps, data tables. Rate-limited operations: audio, video, quizzes,
flashcards, infographics, and slide decks.
