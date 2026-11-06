#!/usr/bin/env bash
#
# Claude Code PostToolUse hook: run the pytest suite after Claude edits a Python file.
#
# Wired up in .claude/settings.json for the Write, Edit and MultiEdit tools,
# right after the ruff hook (.claude/hooks/ruff-check.sh).
#
# How it works:
#   - Claude Code pipes the tool call as JSON on stdin; we read the edited file path from it.
#   - Non-Python files are ignored.
#   - The whole suite runs from the project root (`pytest -q -x`: quiet, stop at first failure).
#   - Tests pass            -> exit 0, nothing shown.
#   - No tests collected    -> pytest exits 5; we treat that as a pass so an empty
#                              suite can't trap Claude in a fix-the-tests loop.
#   - Any other failure     -> pytest's report goes to stderr and we exit 2, which
#                              Claude Code feeds back to Claude so it fixes the problem.
#                              The edit itself is not undone.

set -uo pipefail

# Path of the file that was just written/edited.
file_path=$(jq -r '.tool_input.file_path // .tool_response.filePath // empty')

# Only run tests when a Python file changed.
if [[ "$file_path" != *.py ]]; then
  exit 0
fi

cd "$CLAUDE_PROJECT_DIR" || exit 1

pytest_output=$(uv run pytest -q -x 2>&1)
pytest_status=$?

# 0 = all passed, 5 = no tests collected (loop guard).
if [[ $pytest_status -eq 0 || $pytest_status -eq 5 ]]; then
  exit 0
fi

echo "$pytest_output" >&2
exit 2
