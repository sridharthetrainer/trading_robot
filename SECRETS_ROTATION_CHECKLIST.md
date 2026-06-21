# SECRETS_ROTATION_CHECKLIST.md

> **Status:** drafted 2026-06-20 for Sridhar to execute. **No secret values appear in this file.**
> **Why now:** the working tree and `origin` are clean (env templates untracked/removed; origin is a
> squashed clean snapshot), BUT the secrets still exist in the **local git history** (e.g. commit
> `5407640`). History exposure cannot be undone by deletion — **rotation is the only real fix**, and it
> makes any leaked copy worthless. Treat every credential below as **already compromised**.

---

## 1. What is exposed (rotate ALL of these — by name, not value)

Confirm exact variable names against your local `.env` before starting (do **not** paste values anywhere).

| Credential | Risk if leaked | Rotate via |
|---|---|---|
| **Angel `API_KEY`** | API access to the trading account | SmartAPI dashboard → regenerate app/API key |
| **Angel `CLIENT_ID`** | Login identifier (not secret alone, but pairs with the rest) | n/a (identifier) — rotate the secrets it pairs with |
| **Angel `PASSWORD` / M-PIN** | **Full login → can place real trades** | Angel app/web → change login PIN/password |
| **Angel `TOTP_SECRET`** | 2FA seed → defeats 2FA entirely | Angel → disable & re-enable TOTP to get a NEW seed; update `.env` |
| **`GITHUB_TOKEN`** | Repo read/write (code exfiltration, malicious commits) | GitHub → Settings → Developer settings → revoke & create new PAT (least scope) |
| **`GITHUB_BACKUP_TOKEN`** | Same as above (used for backup pushes) | Same; keep scope minimal (repo only) |
| **`TELEGRAM_BOT_TOKEN`** (+ `SCALPER_BOT_TOKEN`) | Hijack the alert bot / impersonate | @BotFather → `/revoke` → new token per bot |
| **`TWITTER_*` (4 keys)** | Post/read on the linked account | X developer portal → regenerate API key/secret + access token/secret |
| **`FYERS_TOKEN`** | Secondary broker access | Fyers app → regenerate API credentials |

> **Highest urgency = Angel `PASSWORD` + `TOTP_SECRET` + `API_KEY` together** — that triad is full
> trading authority, not just data access.

---

## 2. Rotation procedure (per credential)

1. **Generate the new value** at the provider (links/paths above).
2. **Update `.env`** (gitignored — never commit). Edit only the one line; don't reformat the file.
3. **Revoke the old value** at the provider (don't just overwrite — explicitly invalidate it).
4. **Restart the affected service** so it loads the new value:
   - Trading bot: `sudo systemctl restart trading-bot.service`
   - Manual tracker: `sudo systemctl restart manual-tracker.service`
   - (Both restarts are passwordless-enabled for your user.)
5. **Smoke-test**: confirm Angel login succeeds (check `journalctl -u trading-bot.service` for a session-refresh
   line and token count), Telegram alerts still arrive, and the bot is in **PAPER** mode.

Do these **one provider at a time** so a bad value is easy to isolate.

---

## 3. History remediation (after rotation)

Rotation makes the leaked values useless, which is 90% of the fix. The leaked *strings* still sit in the
local 176-commit history. Two options — **pick with Sridhar; do not run unilaterally**:

- **(A) Accept + rely on rotation (recommended default).** `origin/main` is already a clean squashed
  snapshot; the repo is private/local. Once every value is rotated, the historical strings are inert.
  Lowest risk; no history rewrite.
- **(B) Purge local history** with `git filter-repo` (or BFG) to scrub the strings from old commits.
  This **rewrites history** (all hashes change) and can desync `origin` and any clones — only do it
  deliberately, with a full backup first, and re-establish `origin` afterward via the existing
  clean-snapshot dance. Higher risk; unnecessary if (A) + rotation is accepted.

**Verification after either path:**
```bash
git ls-files | grep -iE '(^|/)\.env'        # expect: NOTHING tracked
```

---

## 4. Forward defense — pre-commit secret guard (additive; I can install on request)

Prevents this from recurring via an accidental `git add -A`:

- A `.git/hooks/pre-commit` that **rejects** any commit that stages `.env`, `.env.template`,
  `.env.example`, or that introduces high-signal secret patterns (Angel `TOTP_SECRET=`, `ghp_` /
  `github_pat_` tokens, Telegram `\d{8,10}:[A-Za-z0-9_-]{35}`, etc.).
- Mirror the script into a tracked `scripts/` path so it's recoverable on a fresh clone (hooks aren't
  cloned), with a one-line installer.
- Touches **no trading logic**.

---

## 5. Done-when

- [ ] All credentials in §1 regenerated **and** old ones revoked at the provider.
- [ ] `.env` updated; affected services restarted; PAPER mode confirmed.
- [ ] History remediation decision made (A or B) and executed.
- [ ] (Optional) pre-commit guard installed.
- [ ] `git ls-files | grep .env` returns nothing.
