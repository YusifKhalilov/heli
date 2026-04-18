#!/bin/bash
# restore saved gamma on login — add to ~/.config/hypr/autostart.conf:
#   exec-once = uwsm-app -- /path/to/restore-gamma.sh
cfg="$HOME/.config/brightness/gamma.json"
if [[ -f "$cfg" ]]; then
    val=$(python3 -c "import json; print(json.load(open('$cfg'))['gamma'])" 2>/dev/null)
    if [[ "$val" =~ ^[0-9]+$ ]] && (( val > 100 )); then
        hyprsunset -g "$val" --gamma_max 200 &
    fi
fi
