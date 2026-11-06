#!/usr/bin/env bash
#
# Claude Code PostToolUse hook: lint Python files with ruff after Claude edits them.
#
# Wired up in .claude/settings.json for the Write, Edit and MultiEdit tools.
#
# How it works:
#   - Claude Code pipes the tool call as JSON on stdin; we read the edited file path from it.
#   - Non-Python files are ignored.
#   - `ruff check` runs from the project root, so any [tool.ruff] config in pyproject.toml applies.
#   - Clean file  -> exit 0, nothing shown.
#   - Lint errors -> ruff's report goes to stderr and we exit 2, which Claude Code feeds
#                    back to Claude so it fixes the problems. The edit itself is not undone.

set -euo pipefail

# Path of the file that was just written/edited.
file_path=$(jq -r '.tool_input.file_path // .tool_response.filePath // empty')

# Only lint Python files.
if [[ "$file_path" != *.py ]]; then
  exit 0
fi

cd "$CLAUDE_PROJECT_DIR"

if ! ruff_output=$(uv run ruff check "$file_path" 2>&1); then
  echo "$ruff_output" >&2
  exit 2
fi
