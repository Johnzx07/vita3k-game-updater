# Vita Pulse — pending fixes (paused 2026-10-10, user resting; resume when asked)

Authority: user approved fixes #1–#3 on review, will confirm "go" later today. Root: I:\AISoftware\Agent\Hermes principles\Projects\vita3k-game-updater (GitHub Johnzx07/vita3k-game-updater, branch main, clean, in sync).

## Baseline facts (verified, do not re-check unless a blocker)
- py_compile clean; pyflakes zero warnings; tests/test_core.py 4/4 pass.
- Live integration OK: update_url("PCSA00007") HMAC digest matches test, HTTP 200, parse_updates returned correct title/packages.
- read_sfo verified against 8 real SFO files on machine (6x Vita3K vs0/app + 2 PS3 save SFOs) — all current-format files parse OK today; fix is for other entry-size/field-order variants.
- CI (windows.yml) builds + smoke-tests exe only; tests never run in CI. 5/5 runs green (v0.1.0–v0.1.2).
- PySide6 NOT installed in this Python → GUI UNVERIFIED locally; --smoke-test only runs in CI.

## Approved fixes (order: #2, #3, #1)
1. **#2 read_sfo** (vita_updater/core.py L55-80): replace hardcoded 16-byte entries + HHIII field order with header-derived `entry_size = (key_start - 20) // count`; support canonical `<4I` (nameoff, format, dataoff, size) and legacy layout; add regression tests building both layouts. Re-run against the 8 real SFO paths.
2. **#3 find_library_root** (core.py L83-92): also check `candidate/"Vita3K"` (and parent walk) — dialog/README promise "or its parent folder" currently returns None for `...\Roaming\Vita3K` (verified live). Add test for parent-folder case.
3. **#1 CI** (.github/workflows/windows.yml): add `python -m unittest discover -s tests -v` step before PyInstaller build.

## Next action when user returns
Implement #2 → run tests → #3 + test → #1 → commit + push (push needs user go-ahead) → watch CI run green.
