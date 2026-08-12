#!/usr/bin/env bash

set -euo pipefail

repository=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)

link_path() {
  local source=$1
  local target=$2

  mkdir -p -- "$(dirname -- "$target")"

  if [[ -L $target && $(readlink -f -- "$target") == $(readlink -f -- "$source") ]]; then
    printf 'Already linked: %s\n' "$target"
    return
  fi

  if [[ -e $target || -L $target ]]; then
    printf 'Refusing to replace an existing path: %s\n' "$target" >&2
    exit 1
  fi

  ln -s -- "$source" "$target"
  printf 'Linked: %s -> %s\n' "$target" "$source"
}

link_path "$repository" "$HOME/.config/hypr/plugins/agent-desktops"
link_path "$repository/config/agent_desktops.lua" "$HOME/.config/hypr/modules/agent_desktops.lua"
link_path "$repository/bin/agent-desktops-start" "$HOME/.config/hypr/scripts/agent-desktops-start"
link_path "$repository/bin/omarchy-agent-desktop" "$HOME/.config/quickshell/scripts/omarchy-agent-desktop"
link_path "$repository/libexec/agent_desktops.py" "$HOME/.config/quickshell/libexec/agent_desktops.py"
link_path "$repository/omarchy/nullskies.agent-desktops" "$HOME/.config/omarchy/plugins/nullskies.agent-desktops"
link_path "$repository/skills/agent-desktops" "${CODEX_HOME:-$HOME/.codex}/skills/agent-desktops"

printf '\nPackage links are installed. Complete docs/integration.md before use.\n'
