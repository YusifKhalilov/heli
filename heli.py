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
from textual.widgets import Footer, Header, Input, Label, ListItem, ListView, Static
from textual.widgets import TabbedContent, TabPane
from textual.containers import Horizontal, Vertical
from textual import work
from textual.worker import get_current_worker

# ── CONSTANTS ─────────────────────────────────────────────────────────────────
CONFIG = Path.home() / ".config" / "brightness" / "settings.json"

GAMMA_MIN,    GAMMA_MAX,    GAMMA_STEP    = 100, 200,   5
TEMP_MIN,     TEMP_MAX,     TEMP_STEP     = 1000, 20000, 100
SAT_MIN,      SAT_MAX,      SAT_STEP      = 0,   200,   5
CONTRAST_MIN, CONTRAST_MAX, CONTRAST_STEP = -50, 50,    5

DEFAULTS: dict = {
    "gamma": 100,
    "temperature": 6000,
    "saturation": 100,
    "contrast": 0,
    "city": "",
    "lat": None,
    "lon": None,
    "presets": {
        "day":   {"gamma": 100, "temperature": 6500, "saturation": 100, "contrast": 0},
        "night": {"gamma": 110, "temperature": 3200, "saturation": 80,  "contrast": -10},
    },
}

TAB_COLORS = {
    "brightness":  "#f5c542",
    "temperature": "#e8813a",
    "saturation":  "#b04dc8",
    "contrast":    "#4d9de0",
    "presets":     "#3db87a",
}

_TAB_KEYS = {
    "brightness":  ("gamma",       GAMMA_MIN,    GAMMA_MAX,    GAMMA_STEP),
    "temperature": ("temperature", TEMP_MIN,     TEMP_MAX,     TEMP_STEP),
    "saturation":  ("saturation",  SAT_MIN,      SAT_MAX,      SAT_STEP),
    "contrast":    ("contrast",    CONTRAST_MIN, CONTRAST_MAX, CONTRAST_STEP),
    "presets":     None,
}

CHEATSHEET = (
    "1-5 tabs  ·  ←/→ or h/l adjust  ·  r reset  ·  R reset all  ·  Tab next  ·  q quit"
)

# ── PERSISTENCE ───────────────────────────────────────────────────────────────
def load_settings() -> dict:
    try:
        raw = json.loads(CONFIG.read_text())
        merged = copy.deepcopy(DEFAULTS)
        for k in ("gamma", "temperature", "saturation", "contrast", "city", "lat", "lon"):
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
        for name, group in db._db.items():
            entries = group if isinstance(group, list) else [group]
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


# ── WIDGETS ───────────────────────────────────────────────────────────────────
class SliderBar(Static):
    value   : reactive[float] = reactive(0.0)
    min_val : reactive[float] = reactive(0.0)
    max_val : reactive[float] = reactive(100.0)
    accent  : reactive[str]   = reactive("#ffffff")

    def render(self) -> str:
        span = max(self.max_val - self.min_val, 1)
        ratio = (self.value - self.min_val) / span
        filled = round(max(0.0, min(1.0, ratio)) * 40)
        bar = "█" * filled + "░" * (40 - filled)
        return f"[dim]▕[/dim][{self.accent}]{bar}[/{self.accent}][dim]▏[/dim]"


# ── APP ───────────────────────────────────────────────────────────────────────
class HeliApp(App):
    CSS = """
    Screen { background: $surface; }

    TabbedContent { height: 1fr; }

    /* Per-tab accent on tab labels */
    TabbedContent Tab#--content-tab-brightness  { color: #f5c542; }
    TabbedContent Tab#--content-tab-temperature { color: #e8813a; }
    TabbedContent Tab#--content-tab-saturation  { color: #b04dc8; }
    TabbedContent Tab#--content-tab-contrast    { color: #4d9de0; }
    TabbedContent Tab#--content-tab-presets     { color: #3db87a; }
    TabbedContent Tab.-active { text-style: bold underline; }

    /* Slider pane layout */
    .slider-pane {
        align: center middle;
        padding: 1 4;
    }
    .val-label {
        text-align: center;
        text-style: bold;
        width: 100%;
        padding: 0 0 1 0;
    }
    .note-label {
        text-align: center;
        color: $text-muted;
        width: 100%;
        padding: 1 0 0 0;
    }
    SliderBar {
        text-align: center;
        width: 100%;
        padding: 0 0 1 0;
    }

    /* Presets tab */
    .presets-pane {
        padding: 1 4;
    }
    .preset-row {
        height: 3;
        margin-bottom: 1;
    }
    .preset-label {
        width: 16;
        color: $text-muted;
        padding: 1 0 0 0;
    }
    .preset-val {
        color: $text;
        padding: 1 0 0 0;
    }
    .preset-btn {
        width: 14;
        margin-left: 2;
    }
    #city-input {
        width: 50%;
        margin-top: 1;
        border: solid $accent;
    }
    #city-results {
        height: 8;
        overflow-y: auto;
        margin-top: 1;
        width: 60%;
        border: solid $surface-lighten-2;
    }
    #location-status {
        color: $text-muted;
        margin-top: 1;
    }
    #astral-missing {
        color: $error;
        margin-top: 1;
    }

    /* Cheatsheet footer */
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
        Binding("tab",   "next_tab",              "Next tab",    show=False, priority=True),
        Binding("1",     "goto_tab('brightness')",  "Brightness"),
        Binding("2",     "goto_tab('temperature')", "Temperature"),
        Binding("3",     "goto_tab('saturation')",  "Saturation"),
        Binding("4",     "goto_tab('contrast')",    "Contrast"),
        Binding("5",     "goto_tab('presets')",     "Presets"),
        Binding("left",  "decrease",               "−",          show=False),
        Binding("right", "increase",               "+",          show=False),
        Binding("h",     "decrease",               "−",          show=False),
        Binding("l",     "increase",               "+",          show=False),
        Binding("r",     "reset_current",          "Reset"),
        Binding("R",     "reset_all",              "Reset All"),
        Binding("q",     "quit",                   "Quit"),
    ]

    settings   : reactive[dict] = reactive({}, init=False)
    active_tab : reactive[str]  = reactive("brightness")

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with TabbedContent(initial="brightness"):
            with TabPane("☀  Brightness", id="brightness"):
                with Vertical(classes="slider-pane"):
                    yield Label("", id="val-brightness", classes="val-label")
                    yield SliderBar(id="bar-brightness")
                    yield Label("", id="desc-brightness", classes="note-label")

            with TabPane("🌡  Temperature", id="temperature"):
                with Vertical(classes="slider-pane"):
                    yield Label("", id="val-temperature", classes="val-label")
                    yield SliderBar(id="bar-temperature")
                    yield Label("Kelvin — lower = warmer, higher = cooler", classes="note-label")

            with TabPane("🎨  Saturation", id="saturation"):
                with Vertical(classes="slider-pane"):
                    yield Label("", id="val-saturation", classes="val-label")
                    yield SliderBar(id="bar-saturation")
                    yield Label(
                        "Simulated via gamma + temperature blend", classes="note-label"
                    )

            with TabPane("◑  Contrast", id="contrast"):
                with Vertical(classes="slider-pane"):
                    yield Label("", id="val-contrast", classes="val-label")
                    yield SliderBar(id="bar-contrast")
                    yield Label("Simulated via gamma offset", classes="note-label")

            with TabPane("⚙  Presets", id="presets"):
                with Vertical(classes="presets-pane"):
                    with Horizontal(classes="preset-row"):
                        yield Label("Day preset:", classes="preset-label")
                        yield Label("", id="day-preview", classes="preset-val")
                    with Horizontal(classes="preset-row"):
                        yield Label("Night preset:", classes="preset-label")
                        yield Label("", id="night-preview", classes="preset-val")
                    with Horizontal(classes="preset-row"):
                        yield Static("Save current as:", classes="preset-label")
                        yield Static(
                            "[ d ] Day   [ n ] Night", classes="preset-val"
                        )
                    yield Label("City / location for auto Day↔Night switching:", classes="note-label")
                    yield Input(placeholder="Type city name…", id="city-input")
                    yield ListView(id="city-results")
                    yield Label("", id="location-status")
                    if not _ASTRAL:
                        yield Label(
                            "⚠  Install astral for city search: pip install astral",
                            id="astral-missing",
                        )

        yield Static(CHEATSHEET, id="cheatsheet")

    def on_mount(self) -> None:
        self.settings = load_settings()
        apply_all(self.settings)
        self._refresh_all_bars(self.settings)
        self._refresh_presets_tab(self.settings)
        self._start_auto_switch()

    # ── reactive watcher ───────────────────────────────────────────────────────
    def watch_settings(self, s: dict) -> None:
        self._refresh_all_bars(s)
        self._refresh_presets_tab(s)

    def _refresh_all_bars(self, s: dict) -> None:
        _bar_cfg = [
            ("bar-brightness",  "val-brightness",  s["gamma"],
             GAMMA_MIN, GAMMA_MAX, TAB_COLORS["brightness"],
             f"{s['gamma']}%  —  {_describe_gamma(s['gamma'])}"),
            ("bar-temperature", "val-temperature",  s["temperature"],
             TEMP_MIN, TEMP_MAX, TAB_COLORS["temperature"],
             f"{s['temperature']} K"),
            ("bar-saturation",  "val-saturation",  s["saturation"],
             SAT_MIN, SAT_MAX, TAB_COLORS["saturation"],
             f"{s['saturation']}%"),
            ("bar-contrast",    "val-contrast",    s["contrast"],
             CONTRAST_MIN, CONTRAST_MAX, TAB_COLORS["contrast"],
             f"{s['contrast']:+d}"),
        ]
        for bar_id, lbl_id, val, mn, mx, color, text in _bar_cfg:
            try:
                bar = self.query_one(f"#{bar_id}", SliderBar)
                bar.min_val = float(mn)
                bar.max_val = float(mx)
                bar.value   = float(val)
                bar.accent  = color
                self.query_one(f"#{lbl_id}", Label).update(text)
            except Exception:
                pass

    def _refresh_presets_tab(self, s: dict) -> None:
        def _fmt(p: dict) -> str:
            return (
                f"γ {p['gamma']}  {p['temperature']}K  "
                f"sat {p['saturation']}%  con {p['contrast']:+d}"
            )
        try:
            self.query_one("#day-preview",   Label).update(_fmt(s["presets"]["day"]))
            self.query_one("#night-preview", Label).update(_fmt(s["presets"]["night"]))
        except Exception:
            pass
        loc = make_location(s)
        try:
            status_lbl = self.query_one("#location-status", Label)
            if loc:
                try:
                    sr, ss = get_sun_times(loc)
                    day = is_daytime(loc)
                    phase = "Day" if day else "Night"
                    status_lbl.update(
                        f"📍 {s.get('city','custom')} — {phase}  "
                        f"(↑ {sr.strftime('%H:%M')}  ↓ {ss.strftime('%H:%M')})"
                    )
                except Exception:
                    status_lbl.update(f"📍 {s.get('city','custom')} — sun times unavailable")
            elif s.get("city") or s.get("lat") is not None:
                status_lbl.update("⚠  Location not found in database")
            else:
                status_lbl.update("No location set — auto-switch disabled")
        except Exception:
            pass

    # ── settings mutation ──────────────────────────────────────────────────────
    def _update_setting(self, key: str, value) -> None:
        new = {**self.settings, key: value}
        self.settings = new
        save_settings(new)
        apply_all(new)

    # ── tab navigation ─────────────────────────────────────────────────────────
    def action_next_tab(self) -> None:
        order = ["brightness", "temperature", "saturation", "contrast", "presets"]
        try:
            tc = self.query_one(TabbedContent)
            idx = (order.index(tc.active) + 1) % len(order)
            tc.active = order[idx]
        except Exception:
            pass

    def action_goto_tab(self, tab: str) -> None:
        try:
            self.query_one(TabbedContent).active = tab
        except Exception:
            pass

    def on_tabbed_content_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        if event.pane:
            self.active_tab = event.pane.id

    # ── slider actions ─────────────────────────────────────────────────────────
    def action_increase(self) -> None:
        cfg = _TAB_KEYS.get(self.active_tab)
        if cfg is None:
            return
        key, mn, mx, step = cfg
        self._update_setting(key, min(mx, self.settings[key] + step))

    def action_decrease(self) -> None:
        cfg = _TAB_KEYS.get(self.active_tab)
        if cfg is None:
            return
        key, mn, mx, step = cfg
        self._update_setting(key, max(mn, self.settings[key] - step))

    def action_reset_current(self) -> None:
        cfg = _TAB_KEYS.get(self.active_tab)
        if cfg is None:
            return
        key = cfg[0]
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

    # ── preset key bindings (d / n to save) ───────────────────────────────────
    def on_key(self, event) -> None:
        if self.active_tab != "presets":
            return
        if event.key == "d":
            self.settings = save_current_as_preset(self.settings, "day")
            save_settings(self.settings)
            self._refresh_presets_tab(self.settings)
        elif event.key == "n":
            self.settings = save_current_as_preset(self.settings, "night")
            save_settings(self.settings)
            self._refresh_presets_tab(self.settings)
        elif event.key == "p":
            loc = make_location(self.settings)
            if loc:
                which = "day" if is_daytime(loc) else "night"
                new = apply_preset(self.settings, which)
                self.settings = new
                save_settings(new)
                apply_all(new)

    # ── city search ────────────────────────────────────────────────────────────
    _city_candidates: list = []

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "city-input":
            return
        results = city_search(event.value)
        self._city_candidates = results
        try:
            lv = self.query_one("#city-results", ListView)
            lv.clear()
            for name, region, lat, lon in results:
                lv.append(ListItem(Label(f"{name}, {region}  ({lat:.2f}°, {lon:.2f}°)")))
        except Exception:
            pass

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        try:
            idx = list(self.query_one("#city-results", ListView).children).index(event.item)
            name, region, lat, lon = self._city_candidates[idx]
            new = {**self.settings, "city": name, "lat": lat, "lon": lon}
            self.settings = new
            save_settings(new)
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
        loc = make_location(self.settings)
        if loc is None:
            return
        try:
            day = is_daytime(loc)
        except Exception:
            return
        which = "day" if day else "night"
        new = apply_preset(copy.deepcopy(self.settings), which)
        self.settings = new
        save_settings(new)
        apply_all(new)


# ── HELPERS ───────────────────────────────────────────────────────────────────
def _describe_gamma(val: int) -> str:
    if val <= 100:
        return "Normal"
    if val <= 130:
        return "Comfortable"
    if val <= 170:
        return "Bright"
    return "Maximum"


# ── ENTRY POINT ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    HeliApp().run()
