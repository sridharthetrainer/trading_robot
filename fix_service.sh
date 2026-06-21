#!/bin/bash
# fix_service.sh — diagnose and fix service startup failure
cd ~/Desktop/trading_robot

echo "═══════════════════════════════════════"
echo "SERVICE FAILURE DIAGNOSIS"  
echo "═══════════════════════════════════════"

echo ""
echo "── Last 30 journal lines ───────────────"
journalctl -u trading-bot -n 30 --no-pager 2>/dev/null || echo "No journal"

echo ""
echo "── Service file ExecStart ──────────────"
cat /etc/systemd/system/trading-bot.service | grep -E "ExecStart|User|WorkingDir|Python|venv"

echo ""
echo "── Python path check ───────────────────"
which python3
source .venv/bin/activate 2>/dev/null || source venv/bin/activate 2>/dev/null || true
which python3
python3 --version

echo ""
echo "── yf_compat present ───────────────────"
ls -la yf_compat.py 2>/dev/null && echo "✅ present" || echo "❌ MISSING"

echo ""
echo "── Syntax check all .py ────────────────"
source .venv/bin/activate 2>/dev/null || source venv/bin/activate 2>/dev/null || true
python3 - << 'PYEOF'
import ast, os
errors = []
for f in sorted(os.listdir('.')):
    if f.endswith('.py'):
        try: ast.parse(open(f).read())
        except SyntaxError as e: errors.append(f"{f}:{e.lineno}: {e.msg}")
n = sum(1 for f in os.listdir('.') if f.endswith('.py'))
print(f"Files: {n}  |  Errors: {len(errors)}")
for e in errors: print(f"  ✗ {e}")
PYEOF

echo ""
echo "── Try manual start ────────────────────"
source .venv/bin/activate 2>/dev/null || source venv/bin/activate 2>/dev/null || true
python3 main_autonomous.py --test 2>&1 | head -20 || \
python3 -c "import main_autonomous; print('✅ main_autonomous imports OK')" 2>&1 | head -10
