# heli ☀

Display control for Hyprland — brightness, color temperature, saturation, contrast, and Day/Night presets with auto-switching based on local sunrise/sunset.

## install

```bash
curl -fsSL https://raw.githubusercontent.com/YusifKhalilov/heli/master/install.sh | bash
```

That's it. Type `heli` to open.

## controls

| key | action |
|-----|--------|
| `1` `2` `3` `4` `5` | jump to tab (Brightness / Temperature / Saturation / Contrast / Presets) |
| `Tab` | cycle to next tab |
| `←` `→` or `h` `l` | adjust active slider |
| `r` | reset current slider |
| `R` | reset all sliders |
| `d` *(Presets tab)* | save current values as Day preset |
| `n` *(Presets tab)* | save current values as Night preset |
| `q` | quit |

## features

- **Brightness** — gamma boost (100–200%)
- **Color temperature** — 1000–20000 K (warm → cool)
- **Saturation** — simulated via gamma + temperature blend
- **Contrast** — simulated via gamma offset
- **Day / Night presets** — one key to save, auto-switches at local sunrise/sunset
- **City search** — type a city in the Presets tab to set your location (offline, no API needed)
- Persists all settings across reboots

## requirements

- Hyprland
- `hyprsunset`
- Python 3.9+ + `textual` + `astral` (installed automatically)
