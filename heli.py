#!/usr/bin/env python3
# ── IMPORTS ──────────────────────────────────────────────────────────────────
import copy
import datetime
import glob
import json
import subprocess
import time
import zoneinfo
from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.reactive import reactive
from textual.widgets import Input, Label, ListItem, ListView, Static
from textual.containers import Horizontal, Vertical
from textual import work
from textual.worker import get_current_worker

# ── CONSTANTS ─────────────────────────────────────────────────────────────────
CONFIG = Path.home() / ".config" / "brightness" / "settings.json"

GAMMA_MIN,    GAMMA_MAX,    GAMMA_STEP    = 100, 200,   5
TEMP_MIN,     TEMP_MAX,     TEMP_STEP     = 1000, 20000, 100
SAT_MIN,      SAT_MAX,      SAT_STEP      = 0,   200,   5
CONTRAST_MIN, CONTRAST_MAX, CONTRAST_STEP = -50, 50,    5

SLIDER_DEFS = [
    ("gamma",       "Brightness",  GAMMA_MIN,    GAMMA_MAX,    GAMMA_STEP,    "#f5c542"),
    ("temperature", "Temperature", TEMP_MIN,     TEMP_MAX,     TEMP_STEP,     "#e8813a"),
    ("saturation",  "Saturation",  SAT_MIN,      SAT_MAX,      SAT_STEP,      "#b04dc8"),
    ("contrast",    "Contrast",    CONTRAST_MIN, CONTRAST_MAX, CONTRAST_STEP, "#4d9de0"),
]

DEFAULTS: dict = {
    "gamma":        100,
    "temperature":  6000,
    "saturation":   100,
    "contrast":     0,
    "city":         "",
    "lat":          None,
    "lon":          None,
    "auto_switch":  True,
    "presets": {
        "day":   {"gamma": 100, "temperature": 6500, "saturation": 100, "contrast": 0},
        "night": {"gamma": 110, "temperature": 3200, "saturation": 80,  "contrast": -10},
    },
}

CHEATSHEET = (
    "j/k focus  h/l adjust  r reset  R reset all  d/n save preset  p apply  a toggle auto (restores on off)  q quit"
)


# ── PERSISTENCE ───────────────────────────────────────────────────────────────
def load_settings() -> dict:
    try:
        raw = json.loads(CONFIG.read_text())
        merged = copy.deepcopy(DEFAULTS)
        for k in ("gamma", "temperature", "saturation", "contrast",
                  "city", "lat", "lon", "auto_switch"):
            if k in raw:
                merged[k] = raw[k]
        if "presets" in raw and isinstance(raw["presets"], dict):
            for which in ("day", "night"):
                if which in raw["presets"]:
                    merged["presets"][which].update(raw["presets"][which])
        return merged
    except Exception:
        return copy.deepcopy(DEFAULTS)


def save_settings(settings: dict) -> None:
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps(settings, indent=2))


# ── DAEMON + APPLY ────────────────────────────────────────────────────────────
def _ensure_daemon(gamma: int = 100, temperature: int = 6000) -> None:
    uid = Path("/proc/self/loginuid").read_text().strip()
    sockets = glob.glob(f"/run/user/{uid}/hypr/*/.hyprsunset.sock")
    if not sockets:
        subprocess.Popen(
            ["hyprsunset", "-g", str(gamma), "-t", str(temperature), "--gamma_max", "200"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(0.4)


def _effective_gamma(s: dict) -> int:
    sat_delta = round((s["saturation"] - 100) / 100 * 15)
    return max(0, min(200, s["gamma"] + s["contrast"] + sat_delta))


def _effective_temperature(s: dict) -> int:
    sat_delta = round((s["saturation"] - 100) / 100 * 1000)
    return max(TEMP_MIN, min(TEMP_MAX, s["temperature"] + sat_delta))


def apply_all(s: dict) -> None:
    g = _effective_gamma(s)
    t = _effective_temperature(s)
    _ensure_daemon(g, t)
    subprocess.run(["hyprctl", "hyprsunset", "gamma",       str(g)], capture_output=True)
    subprocess.run(["hyprctl", "hyprsunset", "temperature", str(t)], capture_output=True)


# ── GEO + ASTRAL ──────────────────────────────────────────────────────────────
try:
    from astral import LocationInfo
    from astral.sun import sun as astral_sun
    from astral.geocoder import database as astral_db, lookup as astral_lookup
    _ASTRAL = True
except ImportError:
    _ASTRAL = False


def city_search(query: str) -> list[tuple[str, str, float, float]]:
    if not _ASTRAL or not query.strip():
        return []
    q = query.strip().lower()
    results = []
    try:
        db = astral_db()
        for continent in db.values():
            for locs in continent.values():
                entries = locs if isinstance(locs, list) else [locs]
                for loc in entries:
                    if q in loc.name.lower() or q in loc.region.lower():
                        results.append((loc.name, loc.region, loc.latitude, loc.longitude))
    except Exception:
        pass
    return results[:10]


def make_location(s: dict):
    if not _ASTRAL:
        return None
    if s.get("city"):
        try:
            return astral_lookup(s["city"], astral_db())
        except Exception:
            pass
    if s.get("lat") is not None and s.get("lon") is not None:
        return LocationInfo("custom", "", "UTC", s["lat"], s["lon"])
    return None


def get_sun_times(loc) -> tuple:
    today = datetime.date.today()
    tz = zoneinfo.ZoneInfo(loc.timezone) if hasattr(loc, "timezone") else zoneinfo.ZoneInfo("UTC")
    s = astral_sun(loc.observer, date=today, tzinfo=tz)
    return s["sunrise"], s["sunset"]


def is_daytime(loc) -> bool:
    sunrise, sunset = get_sun_times(loc)
    now = datetime.datetime.now(tz=sunrise.tzinfo)
    return sunrise <= now <= sunset


# ── PRESETS ───────────────────────────────────────────────────────────────────
def preset_values(s: dict, which: str) -> dict:
    return s["presets"].get(which, copy.deepcopy(DEFAULTS["presets"][which])).copy()


def save_current_as_preset(s: dict, which: str) -> dict:
    s = {**s}
    s["presets"] = {**s["presets"], which: {
        "gamma": s["gamma"], "temperature": s["temperature"],
        "saturation": s["saturation"], "contrast": s["contrast"],
    }}
    return s


def apply_preset(s: dict, which: str) -> dict:
    p = preset_values(s, which)
    return {**s, **p}


def _fmt_preset(p: dict) -> str:
    return f"g{p['gamma']} {p['temperature']}K s{p['saturation']} c{p['contrast']:+d}"


# ── HELPERS ───────────────────────────────────────────────────────────────────
def _describe_gamma(val: int) -> str:
    if val <= 100: return "Normal"
    if val <= 130: return "Comfortable"
    if val <= 170: return "Bright"
    return "Maximum"


def _fmt_slider_val(key: str, val) -> str:
    if key == "gamma":       return f"{val}%  {_describe_gamma(val)}"
    if key == "temperature": return f"{val} K"
    if key == "saturation":  return f"{val}%"
    if key == "contrast":    return f"{val:+d}"
    return str(val)


# ── WIDGETS ───────────────────────────────────────────────────────────────────
class SliderBar(Static):
    value   : reactive[float] = reactive(0.0)
    min_val : reactive[float] = reactive(0.0)
    max_val : reactive[float] = reactive(100.0)
    accent  : reactive[str]   = reactive("#ffffff")

    def render(self) -> str:
        span = max(self.max_val - self.min_val, 1)
        ratio = (self.value - self.min_val) / span
        filled = round(max(0.0, min(1.0, ratio)) * 32)
        bar = "#" * filled + "." * (32 - filled)
        return f"[{self.accent}][{bar}][/{self.accent}]"


class SliderRow(Static, can_focus=True):
    COMPONENT_CLASSES = {"slider-row--focused"}

    key: str = ""
    accent: str = "#ffffff"

    def __init__(self, key: str, label: str, mn: float, mx: float,
                 step: float, accent: str, **kw):
        super().__init__(**kw)
        self.key = key
        self._label = label
        self._mn = mn
        self._mx = mx
        self._step = step
        self.accent = accent

    def compose(self) -> ComposeResult:
        yield Label(" ", id=f"focus-{self.key}", classes="focus-indicator")
        yield Label(self._label, classes="slider-name")
        yield SliderBar(id=f"bar-{self.key}")
        yield Label("", id=f"val-{self.key}", classes="slider-val")

    def on_focus(self) -> None:
        try:
            self.query_one(f"#focus-{self.key}", Label).update(">")
        except Exception:
            pass

    def on_blur(self) -> None:
        try:
            self.query_one(f"#focus-{self.key}", Label).update(" ")
        except Exception:
            pass

    def update_display(self, val: float, s: dict) -> None:
        try:
            bar = self.query_one(f"#bar-{self.key}", SliderBar)
            bar.min_val = float(self._mn)
            bar.max_val = float(self._mx)
            bar.value   = float(val)
            bar.accent  = self.accent
            self.query_one(f"#val-{self.key}", Label).update(_fmt_slider_val(self.key, val))
        except Exception:
            pass


# ── APP ───────────────────────────────────────────────────────────────────────
class HeliApp(App):
    CSS = """
    Screen {
        background: $surface;
    }

    #main {
        padding: 1 3;
    }

    #app-title {
        text-style: bold;
        color: $text;
        padding: 0 0 1 0;
    }

    /* Slider rows */
    SliderRow {
        height: 1;
        layout: horizontal;
        margin-bottom: 1;
    }

    SliderRow:focus {
        background: $surface-lighten-1;
    }

    .focus-indicator {
        width: 2;
        color: $accent;
        text-style: bold;
    }

    .slider-name {
        width: 14;
        color: $text-muted;
    }

    SliderBar {
        width: 1fr;
        padding: 0 1;
    }

    .slider-val {
        width: 22;
        text-align: right;
        color: $text;
    }

    /* Presets section */
    #presets-section {
        margin-top: 1;
        border-top: solid $surface-lighten-2;
        padding-top: 1;
    }

    #presets-row {
        height: auto;
        margin-bottom: 1;
    }

    .preset-item {
        margin-right: 3;
        color: $text-muted;
    }

    .preset-val {
        color: $text;
        margin-right: 3;
    }

    #preset-keys {
        color: $text-muted;
        margin-left: 2;
    }

    /* City section */
    #city-section {
        margin-top: 1;
        border-top: solid $surface-lighten-2;
        padding-top: 1;
    }

    #city-row {
        height: 3;
        align: left middle;
    }

    #city-label {
        width: 6;
        color: $text-muted;
        padding: 0 1 0 0;
    }

    #city-input {
        width: 40;
        border: solid $surface-lighten-2;
    }

    #city-input:focus {
        border: solid $accent;
    }

    #city-results {
        margin-top: 0;
        width: 60;
        max-height: 6;
        border: solid $surface-lighten-2;
        margin-left: 6;
    }

    #location-status {
        color: $text-muted;
        margin-top: 1;
    }

    #astral-missing {
        color: $error;
        margin-top: 1;
    }

    /* Cheatsheet */
    #cheatsheet {
        dock: bottom;
        height: 1;
        color: $text-muted;
        text-align: center;
        background: $surface-darken-1;
        padding: 0 1;
    }
    """

    BINDINGS = [
        Binding("j",     "focus_next_slider",  "Focus next",  show=False),
        Binding("k",     "focus_prev_slider",  "Focus prev",  show=False),
        Binding("left",  "decrease",           "-",           show=False),
        Binding("right", "increase",           "+",           show=False),
        Binding("h",     "decrease",           "-",           show=False),
        Binding("l",     "increase",           "+",           show=False),
        Binding("r",     "reset_current",      "Reset"),
        Binding("R",     "reset_all",          "Reset All"),
        Binding("d",     "save_day",           "Save Day"),
        Binding("n",     "save_night",         "Save Night"),
        Binding("p",     "apply_preset",       "Apply"),
        Binding("a",     "toggle_auto",        "Auto"),
        Binding("q",     "quit",               "Quit"),
    ]

    settings : reactive[dict] = reactive({}, init=False)
    _focused_slider_idx: int = 0
    _city_candidates: list = []
    _pre_auto_settings: dict | None = None

    def compose(self) -> ComposeResult:
        with Vertical(id="main"):
            yield Static("heli  --  display control", id="app-title")

            for key, label, mn, mx, step, accent in SLIDER_DEFS:
                yield SliderRow(key, label, mn, mx, step, accent, id=f"row-{key}")

            with Vertical(id="presets-section"):
                with Horizontal(id="presets-row"):
                    yield Label("Day:", classes="preset-item")
                    yield Label("", id="day-preview", classes="preset-val")
                    yield Label("Night:", classes="preset-item")
                    yield Label("", id="night-preview", classes="preset-val")
                    yield Label("[ d ] Day  [ n ] Night  [ p ] Apply", id="preset-keys")

            with Vertical(id="city-section"):
                with Horizontal(id="city-row"):
                    yield Label("City:", id="city-label")
                    yield Input(placeholder="type to search...", id="city-input")
                yield ListView(id="city-results")
                yield Label("", id="location-status")
                if not _ASTRAL:
                    yield Label(
                        "! astral not installed: pip install astral",
                        id="astral-missing",
                    )

        yield Static(CHEATSHEET, id="cheatsheet")

    def on_mount(self) -> None:
        self.settings = load_settings()
        apply_all(self.settings)
        self._refresh_ui(self.settings)
        # hide city list initially
        self.query_one("#city-results", ListView).display = False
        # focus first slider
        self._focus_slider(0)
        self._start_auto_switch()

    def watch_settings(self, s: dict) -> None:
        self._refresh_ui(s)

    def _refresh_ui(self, s: dict) -> None:
        for key, _label, mn, mx, step, accent in SLIDER_DEFS:
            try:
                row = self.query_one(f"#row-{key}", SliderRow)
                row.update_display(s[key], s)
            except Exception:
                pass
        self._refresh_presets(s)
        self._refresh_status(s)

    def _refresh_presets(self, s: dict) -> None:
        try:
            self.query_one("#day-preview",   Label).update(_fmt_preset(s["presets"]["day"]))
            self.query_one("#night-preview", Label).update(_fmt_preset(s["presets"]["night"]))
        except Exception:
            pass

    def _refresh_status(self, s: dict) -> None:
        try:
            lbl = self.query_one("#location-status", Label)
            auto = s.get("auto_switch", True)
            auto_str = "auto:ON" if auto else "auto:OFF"
            loc = make_location(s)
            if loc:
                try:
                    sr, ss = get_sun_times(loc)
                    phase = "Day" if is_daytime(loc) else "Night"
                    city = s.get("city", "custom")
                    lbl.update(
                        f"{auto_str}  {city} -- {phase}"
                        f"  (^ {sr.strftime('%H:%M')}  v {ss.strftime('%H:%M')})"
                    )
                except Exception:
                    lbl.update(f"{auto_str}  {s.get('city','custom')} -- sun times unavailable")
            elif s.get("city") or s.get("lat") is not None:
                lbl.update(f"{auto_str}  Location not found in database")
            else:
                lbl.update(f"{auto_str}  No location set")
        except Exception:
            pass

    # ── settings mutation ──────────────────────────────────────────────────────
    def _update_setting(self, key: str, value) -> None:
        new = {**self.settings, key: value}
        self.settings = new
        save_settings(new)
        apply_all(new)

    # ── slider focus ───────────────────────────────────────────────────────────
    def _slider_rows(self) -> list[SliderRow]:
        return [self.query_one(f"#row-{key}", SliderRow) for key, *_ in SLIDER_DEFS]

    def _focus_slider(self, idx: int) -> None:
        rows = self._slider_rows()
        if 0 <= idx < len(rows):
            self._focused_slider_idx = idx
            rows[idx].focus()

    def _input_focused(self) -> bool:
        return isinstance(self.focused, Input)

    def action_focus_next_slider(self) -> None:
        if self._input_focused():
            return
        rows = self._slider_rows()
        self._focus_slider((self._focused_slider_idx + 1) % len(rows))

    def action_focus_prev_slider(self) -> None:
        if self._input_focused():
            return
        rows = self._slider_rows()
        self._focus_slider((self._focused_slider_idx - 1) % len(rows))

    def on_slider_row_focus(self, event) -> None:
        rows = self._slider_rows()
        for i, row in enumerate(rows):
            if row is event.widget:
                self._focused_slider_idx = i
                break

    # ── slider actions ─────────────────────────────────────────────────────────
    def _current_slider_def(self):
        if self._input_focused():
            return None
        rows = self._slider_rows()
        if not (0 <= self._focused_slider_idx < len(rows)):
            return None
        return SLIDER_DEFS[self._focused_slider_idx]

    def action_increase(self) -> None:
        d = self._current_slider_def()
        if d is None:
            return
        key, _lbl, mn, mx, step, _c = d
        self._update_setting(key, min(mx, self.settings[key] + step))

    def action_decrease(self) -> None:
        d = self._current_slider_def()
        if d is None:
            return
        key, _lbl, mn, mx, step, _c = d
        self._update_setting(key, max(mn, self.settings[key] - step))

    def action_reset_current(self) -> None:
        d = self._current_slider_def()
        if d is None:
            return
        key = d[0]
        self._update_setting(key, DEFAULTS[key])

    def action_reset_all(self) -> None:
        new = {**self.settings,
               "gamma":       DEFAULTS["gamma"],
               "temperature": DEFAULTS["temperature"],
               "saturation":  DEFAULTS["saturation"],
               "contrast":    DEFAULTS["contrast"]}
        self.settings = new
        save_settings(new)
        apply_all(new)

    # ── preset actions ─────────────────────────────────────────────────────────
    def action_save_day(self) -> None:
        if self._input_focused():
            return
        self.settings = save_current_as_preset(self.settings, "day")
        save_settings(self.settings)

    def action_save_night(self) -> None:
        if self._input_focused():
            return
        self.settings = save_current_as_preset(self.settings, "night")
        save_settings(self.settings)

    def action_apply_preset(self) -> None:
        if self._input_focused():
            return
        loc = make_location(self.settings)
        if loc:
            which = "day" if is_daytime(loc) else "night"
            new = apply_preset(self.settings, which)
            self.settings = new
            save_settings(new)
            apply_all(new)

    def action_toggle_auto(self) -> None:
        if self._input_focused():
            return
        new_val = not self.settings.get("auto_switch", True)
        if not new_val and self._pre_auto_settings is not None:
            # restore state from before auto took over
            restored = {**self._pre_auto_settings, "auto_switch": False}
            self._pre_auto_settings = None
            self.settings = restored
            save_settings(restored)
            apply_all(restored)
        else:
            self._pre_auto_settings = None  # reset snapshot for new auto session
            self._update_setting("auto_switch", new_val)

    # ── city search ────────────────────────────────────────────────────────────
    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "city-input":
            return
        results = city_search(event.value)
        self._city_candidates = results
        try:
            lv = self.query_one("#city-results", ListView)
            lv.clear()
            if results:
                for name, region, lat, lon in results:
                    lv.append(ListItem(Label(f"{name}, {region}  ({lat:.2f}, {lon:.2f})")))
                lv.display = True
            else:
                lv.display = False
        except Exception:
            pass

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        try:
            idx = list(self.query_one("#city-results", ListView).children).index(event.item)
            name, _region, lat, lon = self._city_candidates[idx]
            new = {**self.settings, "city": name, "lat": lat, "lon": lon}
            self.settings = new
            save_settings(new)
            # hide list, clear input, refocus first slider
            lv = self.query_one("#city-results", ListView)
            lv.display = False
            self.query_one("#city-input", Input).value = ""
            self._focus_slider(self._focused_slider_idx)
            self._start_auto_switch()
        except Exception:
            pass

    # ── auto-switch background worker ──────────────────────────────────────────
    @work(exclusive=True, thread=True, name="auto_switch")
    def _start_auto_switch(self) -> None:
        worker = get_current_worker()
        while not worker.is_cancelled:
            self.call_from_thread(self._check_and_switch)
            for _ in range(60):
                if worker.is_cancelled:
                    return
                time.sleep(1)

    def _check_and_switch(self) -> None:
        if not self.settings.get("auto_switch", True):
            return
        loc = make_location(self.settings)
        if loc is None:
            return
        try:
            day = is_daytime(loc)
        except Exception:
            return
        which = "day" if day else "night"
        # snapshot before auto overwrites sliders (only once per auto session)
        if self._pre_auto_settings is None:
            self._pre_auto_settings = copy.deepcopy(self.settings)
        new = apply_preset(copy.deepcopy(self.settings), which)
        self.settings = new
        save_settings(new)
        apply_all(new)


# ── ENTRY POINT ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    HeliApp().run()
