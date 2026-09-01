#!/usr/bin/env bash
# install_hooks.sh — install the repo's git hooks (hooks aren't cloned, so this
# must be run once per clone). Safe to re-run (idempotent).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOOK="$ROOT/.git/hooks/pre-commit"

cat > "$HOOK" <<'EOF'
#!/usr/bin/env bash
# Auto-installed by scripts/install_hooks.sh — runs the lint safety gate.
exec python3 "$(git rev-parse --show-toplevel)/scripts/pre_commit_lint.py"
EOF

chmod +x "$HOOK"
echo "Installed pre-commit hook -> $HOOK"
echo "It blocks commits with syntax errors / undefined names. Bypass: git commit --no-verify"
