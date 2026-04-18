#!/bin/bash
# restore saved display settings on login — managed by heli installer
#   exec-once = uwsm-app -- ~/.local/share/heli/restore-gamma.sh
cfg="$HOME/.config/brightness/settings.json"
if [[ -f "$cfg" ]]; then
    gamma=$(python3 -c "import json; d=json.load(open('$cfg')); print(d.get('gamma',100))" 2>/dev/null)
    temp=$(python3  -c "import json; d=json.load(open('$cfg')); print(d.get('temperature',6000))" 2>/dev/null)
    [[ "$gamma" =~ ^[0-9]+$ ]] || gamma=100
    [[ "$temp"  =~ ^[0-9]+$ ]] || temp=6000
    if (( gamma > 100 || temp != 6000 )); then
        hyprsunset -g "$gamma" -t "$temp" --gamma_max 200 &
    fi
fi
