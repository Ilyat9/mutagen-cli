# Regenerate this GIF with: vhs assets/demo.tape
# (requires vhs + ffmpeg: brew install vhs ffmpeg)
#
# Prerequisites (one-time), so the recording plays from cache and needs no
# API key or network access:
#   1. cp -R tests/fixtures/victim_project ~/demo && cd ~/demo && git init -q && git add -A && git commit -q -m init
#   2. Add a one-line comment inside apply_discount's max_discount branch in
#      victim/pricing.py (any harmless edit works — it's just the "I changed
#      something" beat for `git diff --stat` in act 1).
#   3. uv venv .venv && uv pip install -q -p .venv pytest
#   4. OPENROUTER_API_KEY=... mutagen run --path victim/pricing.py --all --max-mutants 3
#      (run it twice; the second run should say "1 from cache" and finish in ~1s)
# If the UI or output format changes, redo step 4 to refresh the cache before recording.

Output assets/demo.gif

Set Shell "bash"
Set FontSize 15
Set Width 1200
Set Height 900
Set Theme "Catppuccin Mocha"
Set TypingSpeed 60ms
Set Padding 20

# Only needed if the cache from the prerequisites above is missing.
Env OPENROUTER_API_KEY "REPLACE_WITH_YOUR_KEY"

Hide
Type "cd ~/demo && clear"
Enter
Sleep 500ms
Show

Type "git diff --stat"
Enter
Sleep 2500ms

Type "mutagen run --path victim/pricing.py --all --max-mutants 3"
Sleep 500ms
Enter
Sleep 5000ms

Sleep 4000ms
