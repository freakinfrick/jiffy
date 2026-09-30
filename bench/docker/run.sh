#!/usr/bin/env bash
# Build a fresh-machine image for one distro and run a command in it.
#   bench/docker/run.sh ubuntu:24.04                      # default: install check + tests
#   bench/docker/run.sh all                               # the three supported distros
#   JEV=1 bench/docker/run.sh ubuntu:22.04                # + the held-out bench, with the Jev key (paid)
#   bench/docker/run.sh debian:12 jiffy bench --cases bench/cases/fresh_cases.json --dry
# The default indexes the container itself (downloading the pinned tldr release) and runs
# the tests. It needs no key; with JEV=1 it also runs the paid bench over the held-out cases.
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
DISTROS=(debian:12 ubuntu:22.04 ubuntu:24.04)

one() {
  local base="$1"; shift
  local tag="jiffy-dev:${base//:/-}"
  docker build -q --build-arg BASE="$base" -t "$tag" -f "$root/bench/docker/Dockerfile" "$root" >/dev/null
  local mounts=()
  [[ ${JEV:-} == 1 ]] && mounts+=(-v "$HOME/.config/typesafe/api_key:/home/tester/.config/typesafe/api_key:ro")
  local cmd=("$@")
  local bench='true'
  [[ ${JEV:-} == 1 ]] && bench='jiffy bench --cases bench/cases/fresh_cases.json --all 2>/dev/null | grep -m1 -i "top-1"'
  (( ${#cmd[@]} )) || cmd=(bash -c "jiffy index 2>/dev/null | tail -1 \
    && python3 -m pytest tests -q -p no:cacheprovider | tail -1 \
    && $bench")
  echo "== $base"
  docker run --rm ${mounts[@]+"${mounts[@]}"} "$tag" "${cmd[@]}"
}

if [[ ${1:-} == all ]]; then
  shift
  for d in "${DISTROS[@]}"; do one "$d" "$@"; done
else
  one "${1:?distro, e.g. ubuntu:24.04, or all}" "${@:2}"
fi
