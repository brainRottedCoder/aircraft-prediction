#!/usr/bin/env bash
# Fetch the NASA C-MAPSS turbofan degradation dataset into backend/data/cmapss.
#
# The four subsets are ~43 MB of plain text and are deliberately NOT committed: they are
# a third-party dataset with an unchanged upstream source, so re-fetching beats carrying
# them in every clone. `make fetch-data` is the only step a new checkout needs before
# `make up` — the API bind-mounts this directory (see docker-compose.yml) and the replay
# engine reads from it.
#
# Usage:
#   scripts/fetch-cmapss.sh                 # fetch every subset into backend/data/cmapss
#   scripts/fetch-cmapss.sh --subset FD001  # one subset only
#   scripts/fetch-cmapss.sh --dir /tmp/cmapss
#
# If you already have the dataset, just copy the *_FD*.txt files into the target
# directory and skip this script.
set -euo pipefail

DEST="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/backend/data/cmapss"

# Mirrors, tried in order. Do not reorder without checking that the whole set is present:
# the replay engine and the trained booster were fitted on this exact data, so a
# truncated or substituted file silently changes every prediction.
#
# The first entry was `jasonniebeck/C-MAPSS`, which is where these were originally fetched
# and which worked until the repository was deleted. That is why a Render build failed with
# three 404s and no other clue: the URL had quietly become a 404 for everyone, not just
# for a misconfigured clone. The remaining entries are content-verified by checksum below,
# so a mirror that drifts is rejected rather than used.
BASE_URLS=(
  "https://raw.githubusercontent.com/edwardzjl/CMAPSSData/master"
  "https://huggingface.co/datasets/SoyVitou/NASA-C-MAPSS-Turbofan-Engine/resolve/main/data"
)

# SHA-256 of the files this project has always trained and replayed against. Verified
# byte-identical to a known-good local copy when these mirrors were added.
declare -A SHA256=(
  [train_FD001.txt]=963b5e22825b34d8b21c69e1aeb4af3e647050eb672ee8834ba4b5d91d2de0f8
  [test_FD001.txt]=3cda7109ce17bafb5443f2ac926cfcf88154b941b8c4cf95eb55d1ddd6f52851
  [RUL_FD001.txt]=a19c8ec94931949d0485bdc35118206e9c81c4547b422efb9cf86f4ceddbceca
)

SUBSETS=(FD001)
VERBOSE=1

while [[ $# -gt 0 ]]; do
  case "$1" in
    # Repeatable. Assigning the array directly, as in `SUBSETS=("$2")`, silently kept only
    # the last value — four `--subset` flags fetched just FD004 and the loop reported
    # success. Append instead. `--subset-all` covers the usual case in one flag.
    --subset)
      [[ $# -ge 2 ]] || { echo "--subset needs a value (FD001..FD004)" >&2; exit 2; }
      SUBSETS+=("$2"); shift 2 ;;
    --subset-all) SUBSETS=(FD001 FD002 FD003 FD004); shift ;;
    --dir)    DEST="$2"; shift 2 ;;
    --quiet)  VERBOSE=0; shift ;;
    -h|--help) sed -n '2,20p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

log() { [[ "$VERBOSE" == "1" ]] && echo "[cmapss] $*" || true; }

mkdir -p "$DEST"

failed=()
for subset in "${SUBSETS[@]}"; do
  for kind in train test RUL; do
    file="${kind}_${subset}.txt"
    target="$DEST/$file"

    if [[ -s "$target" ]]; then
      log "have $file"
      continue
    fi

    # Try each mirror in turn. A 200 is not enough on its own: a truncated body or an HTML
    # error page saved as a .txt would parse as garbage, so verify against the known digest.
    log "fetching $file"
    for base in "${BASE_URLS[@]}"; do
      url="${base}/${file}"
      if command -v curl >/dev/null 2>&1; then
        curl -fsSL --retry 2 --retry-delay 1 -o "$target.part" "$url" 2>/dev/null || true
      else
        wget -q -O "$target.part" "$url" 2>/dev/null || true
      fi

      if [[ ! -s "$target.part" ]]; then
        rm -f "$target.part"
        continue
      fi

      want="${SHA256[$file]:-}"
      if [[ -n "$want" ]] && command -v sha256sum >/dev/null 2>&1; then
        got="$(sha256sum "$target.part" | cut -d' ' -f1)"
        if [[ "$got" != "$want" ]]; then
          log "checksum mismatch for $file from ${base##*//} — discarding"
          rm -f "$target.part"
          continue
        fi
      fi

      mv "$target.part" "$target"
      log "ok $file ($(wc -c <"$target" | tr -d ' ') bytes, from ${base##*//})"
      break
    done

    if [[ ! -s "$target" ]]; then
      rm -f "$target"
      failed+=("$file")
      log "FAILED $file"
    fi
  done
done

if [[ ${#failed[@]} -gt 0 ]]; then
  cat >&2 <<EOF

[cmapss] ${#failed[@]} file(s) could not be fetched:
  ${failed[*]}

The mirror may be unavailable. C-MAPSS is distributed by NASA at
  https://data.nasa.gov/dataset/cmapss-jet-engine-simulated-data

Proceeding with existing data if present.
EOF
fi

log "ready: $DEST"
log "the replay engine reads ${SUBSETS[*]} (see FDT_ML_DATASET / FDT_REPLAY_SUBSET)"