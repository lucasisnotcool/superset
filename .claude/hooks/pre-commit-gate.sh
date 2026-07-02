#!/bin/bash
# PreToolUse[Bash] hook: run pre-commit on staged files before any `git commit`
# (informational — does not block; CI enforces). Uses the repo venv's
# pre-commit when available since it is often not on PATH.
input=$(cat)
cmd=$(printf '%s' "$input" | jq -r '.tool_input.command // ""' 2>/dev/null) || exit 0

case "$cmd" in
  *"git commit"*) ;;
  *) exit 0 ;;
esac

cd "${CLAUDE_PROJECT_DIR:-.}" || exit 0
PC="${CLAUDE_PROJECT_DIR:-.}/venv/bin/pre-commit"
[ -x "$PC" ] || PC=pre-commit
command -v "$PC" >/dev/null 2>&1 || exit 0

echo '🔍 Running pre-commit on staged files before commit...'
"$PC" run || true
exit 0
