#!/bin/bash
# PreToolUse[Bash] hook: blocks git operations that have caused real incidents
# in this fork (amend on a pushed commit, force-push in a multi-machine setup,
# and staging .env files containing secrets).
input=$(cat)
cmd=$(printf '%s' "$input" | jq -r '.tool_input.command // ""' 2>/dev/null) || exit 0

if printf '%s' "$cmd" | grep -qE 'git[^|;&]*commit[^|;&]*--amend'; then
  echo "Blocked: never amend in this repo — commits may already be pushed and the Windows box syncs via 'git pull origin master'. Make a new commit instead." >&2
  exit 2
fi

if printf '%s' "$cmd" | grep -qE 'git[^|;&]*push[^|;&]*(--force(-with-lease)?|[[:space:]]-f)([[:space:]]|$)'; then
  echo "Blocked: force-push is forbidden in this multi-machine setup (origin/master is the sync channel). Ask the user." >&2
  exit 2
fi

if printf '%s' "$cmd" | grep -qE 'git[^|;&]*commit'; then
  cd "${CLAUDE_PROJECT_DIR:-.}" || exit 0
  bad=$(git diff --cached --name-only | grep -E '(^|/)\.env[^/]*$' | grep -vi 'example' || true)
  if [ -n "$bad" ]; then
    echo "Blocked: .env-like file(s) staged for commit: $bad — secrets must never be committed (a real API key was caught by GitHub push protection once). Unstage them first (git restore --staged <file>)." >&2
    exit 2
  fi
fi

exit 0
