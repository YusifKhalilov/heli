#!/usr/bin/env python3
import json
import subprocess
from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Center, Middle
from textual.reactive import reactive
from textual.widgets import Footer, Header, Label, Static

CONFIG = Path.home() / ".config" / "brightness" / "gamma.json"
GAMMA_MIN = 100
GAMMA_MAX = 200
GAMMA_STEP = 5


def get_gamma() -> int:
    try:
        return int(json.loads(CONFIG.read_text())["gamma"])
    except Exception:
        return 100


def _ensure_daemon(gamma: int = 100) -> None:
    """Start hyprsunset daemon if not running."""
    sock_glob = Path(f"/run/user/{Path('/proc/self/loginuid').read_text().strip()}/hypr")
    import glob
    sockets = glob.glob(str(sock_glob / "*" / ".hyprsunset.sock"))
    if not sockets:
        subprocess.Popen(
            ["hyprsunset", "-g", str(gamma), "--gamma_max", "200"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        import time; time.sleep(0.4)


def apply_gamma(val: int) -> None:
    _ensure_daemon(val)
    subprocess.run(["hyprctl", "hyprsunset", "gamma", str(val)], capture_output=True)
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps({"gamma": val}))


def reset_gamma() -> None:
    _ensure_daemon()
    subprocess.run(["hyprctl", "hyprsunset", "identity"], capture_output=True)
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps({"gamma": 100}))


def describe(val: int) -> str:
    if val <= 100:
        return "Normal"
    if val <= 130:
        return "Comfortable ·  colors slightly lighter"
    if val <= 170:
        return "Bright ·  noticeable boost"
    return "Maximum ·  full gamma push"


class GammaBar(Static):
    """Visual gamma bar widget."""

    gamma: reactive[int] = reactive(100)

    def render(self) -> str:
        ratio = (self.gamma - GAMMA_MIN) / (GAMMA_MAX - GAMMA_MIN)
        width = 40
        filled = round(ratio * width)
        bar = "█" * filled + "░" * (width - filled)
        return f"[dim]▕[/dim]{bar}[dim]▏[/dim]"


class BrightnessApp(App):
    CSS = """
    Screen { background: $surface; align: center middle; }

    #title {
        text-align: center;
        padding: 1 0 0 0;
        text-style: bold;
        color: $accent;
        width: 100%;
    }
    #pct {
        text-align: center;
        padding: 1 0 0 0;
        text-style: bold;
        color: $text;
        width: 100%;
    }
    #desc {
        text-align: center;
        color: $text-muted;
        padding: 0 0 1 0;
        width: 100%;
    }
    GammaBar {
        text-align: center;
        width: 100%;
        padding: 0 0 1 0;
    }
    #hint {
        text-align: center;
        color: $text-disabled;
        padding: 1 0 0 0;
        width: 100%;
    }
    """

    BINDINGS = [
        Binding("r", "reset", "Reset"),
        Binding("q", "quit", "Quit"),
        Binding("left", "decrease", "−", show=False),
        Binding("right", "increase", "+", show=False),
        Binding("h", "decrease", "−", show=False),
        Binding("l", "increase", "+", show=False),
    ]

    gamma: reactive[int] = reactive(100)

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield Static("☀  Gamma Brightness", id="title")
        yield Label("", id="pct")
        yield Label("", id="desc")
        yield GammaBar()
        yield Static("← → to adjust  ·  r reset  ·  q quit", id="hint")
        yield Footer()

    def on_mount(self) -> None:
        self.gamma = get_gamma()
        apply_gamma(self.gamma)
        self._refresh_ui()

    def watch_gamma(self, val: int) -> None:
        apply_gamma(val)
        self._refresh_ui()

    def _refresh_ui(self) -> None:
        val = self.gamma
        self.query_one("#pct", Label).update(f"{val}%")
        self.query_one("#desc", Label).update(describe(val))
        self.query_one(GammaBar).gamma = val

    def action_decrease(self) -> None:
        self.gamma = max(GAMMA_MIN, self.gamma - GAMMA_STEP)

    def action_increase(self) -> None:
        self.gamma = min(GAMMA_MAX, self.gamma + GAMMA_STEP)

    def action_reset(self) -> None:
        reset_gamma()
        self.gamma = 100


if __name__ == "__main__":
    BrightnessApp().run()
