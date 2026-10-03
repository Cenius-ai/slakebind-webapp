#!/usr/bin/env bash
# Non-interactive end-to-end demo of Slakebind.
#
# It writes to a throwaway ledger under a temp directory (never to your real
# ~/.slakebind), so it is completely safe and repeatable. Every command here
# runs to completion on its own: nothing waits for input.
set -euo pipefail

cd "$(dirname "$0")"
PYTHON="${PYTHON:-python3}"
SB=("$PYTHON" slakebind.py)

DEMO_HOME="$(mktemp -d)"
BAD_HOME="$(mktemp -d)"
trap 'rm -rf "$DEMO_HOME" "$BAD_HOME"' EXIT
export SLAKEBIND_HOME="$DEMO_HOME"

banner() { printf '\n=== %s ===\n' "$1"; }
show() { printf '\n$ %s\n' "$*"; "$@"; }
show_fail() {
  printf '\n$ %s\n' "$*"
  set +e
  "$@"
  local code=$?
  set -e
  printf '[exit %s]\n' "$code"
}

banner "Slakebind ${SB[*]} --version"
"${SB[@]}" --version

banner "1. What the tool does (no arguments = the help-first screen)"
show "${SB[@]}"

banner "2. Tannin-to-hide ratio, metric by default"
show "${SB[@]}" ratio --hide 8.5kg --tannin 340g

banner "3. The same maths in imperial; a unitless weight reads as pounds"
show "${SB[@]}" ratio --hide 18.7 --tannin 12oz --unit imperial

banner "4. Bad input gets one plain sentence, a non-zero exit and no traceback"
show_fail "${SB[@]}" ratio --hide 0kg --tannin 340g
show_fail "${SB[@]}" ratio --hide abc --tannin 340g

banner "5. Lime bath soak: the accepted durations"
"${SB[@]}" soak --help

banner "6. Soak with --no-wait prints the pull time and returns at once"
show "${SB[@]}" soak 1h30m --no-wait

banner "7. Piped output degrades to one target-time line (no carriage returns)"
show bash -c "$PYTHON slakebind.py soak 90m | cat"

banner "8. Bark source ledger: add entries (the ledger is created on first write)"
show "${SB[@]}" ledger add --species oak --source "Hill Farm" --qty 25kg --date 2025-04-01 --note "spring bark"
show "${SB[@]}" ledger add --species birch --source "Ridge Stand" --qty 12.5kg --date 2025-03-14 --note "peeled by hand"
show "${SB[@]}" ledger add --species oak --source "Mill Creek" --qty 8lb --date 2025-05-02
LATEST="$("${SB[@]}" ledger add --species hemlock --source "North Slope" --qty 18kg --note "strong tanning bark" | head -n 1)"
NEW_ID="$(printf '%s' "$LATEST" | awk '{print $2}')"

banner "9. List entries newest-first"
show "${SB[@]}" ledger list

banner "10. Filter by species and an inclusive date window"
show "${SB[@]}" ledger list --species oak --from 2025-01-01 --to 2025-06-30

banner "11. Quantity on hand per species"
show "${SB[@]}" ledger list --totals

banner "12. Filters that match nothing still exit 0 with a way out"
show "${SB[@]}" ledger list --species elm

banner "13. The ledger on disk is one plain JSON file"
show cat "$DEMO_HOME/ledger.json"

banner "14. Remove one entry ($NEW_ID) without a prompt"
show "${SB[@]}" ledger remove "$NEW_ID" --yes
show "${SB[@]}" ledger list --totals

banner "15. A damaged ledger is refused, not overwritten"
printf 'this is not json\n' > "$BAD_HOME/ledger.json"
show_fail env SLAKEBIND_HOME="$BAD_HOME" "${SB[@]}" ledger list
show cat "$BAD_HOME/ledger.json"
show env SLAKEBIND_HOME="$BAD_HOME" "${SB[@]}" ledger list --reset

banner "Demo complete"
printf 'Every screen above came from seeded, real data; nothing was stubbed.\n'
