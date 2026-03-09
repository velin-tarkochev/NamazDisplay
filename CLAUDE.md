# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A fullscreen Python/Pygame application for Raspberry Pi (HDMI to TV) displaying Islamic prayer times for a mosque. Shows a full table of adhan and iqamah times for all 7 daily times (Fajr, Sunrise, Dhuhr, Asr, Maghrib, Isha, Midnight), both Gregorian and Hijri dates, and a live countdown to the next prayer. Configured remotely from any phone or laptop via a mobile-friendly web UI (Flask + Tailscale — no port forwarding, no public IP needed). Autostarted on boot via systemd.

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run the display app (fullscreen) + web UI
python main.py

# Run in windowed mode for development
python main.py --windowed

# Run web UI only (no display, for testing)
python main.py --web-only

# Run tests
pytest

# Run a single test file
pytest tests/test_prayer.py -v

# Run tests with coverage
pytest --cov=. --cov-report=term-missing

# Lint and format
ruff check .
ruff format .

# Type check
mypy .

# Deploy systemd service on Raspberry Pi
sudo cp deploy/prayer-display.service /etc/systemd/system/
sudo systemctl enable --now prayer-display

# Tail live logs on Pi
journalctl -u prayer-display -f
```

## Project Structure

```
prayer_times_display/
├── main.py                    # Entry point; DI wiring; starts display + web server threads
├── config/
│   ├── models.py              # Pydantic models: AppConfig, LocationConfig, DisplayConfig, IqamahConfig
│   ├── loader.py              # Load/save/watch settings.yaml; fires callbacks on hot-reload
│   └── settings.yaml          # User-editable config (GPS, calc method, theme, iqamah rules)
├── prayer/
│   ├── calculator.py          # Abstract PrayerCalculator + AdhanCalculator (wraps adhan-python)
│   ├── iqamah.py              # IqamahEngine: applies pluggable rules to adhan times
│   ├── rules.py               # IqamahRule protocol + built-in rules (OffsetRule, RoundUpRule, FixedTimeRule)
│   └── scheduler.py           # Background thread; daily recalculation + next-prayer countdown polling
├── display/
│   ├── engine.py              # Abstract DisplayEngine interface
│   ├── pygame_display.py      # Pygame fullscreen renderer (prayer table, Hijri/Gregorian dates, countdown)
│   └── themes/                # YAML theme files (colors, fonts, layout proportions)
│       ├── dark.yaml
│       └── light.yaml
├── web/
│   ├── app.py                 # Flask app; GET / (config form), POST /save, GET /api/times (JSON)
│   ├── templates/
│   │   └── index.html         # Mobile-friendly config UI (plain HTML form, no JS framework)
│   └── static/
│       └── style.css          # Minimal responsive CSS
├── tests/
│   ├── test_prayer.py         # Calculation tests (mock date/location → assert known times)
│   ├── test_iqamah.py         # Rule engine unit tests
│   ├── test_config.py         # Pydantic validation tests (valid + invalid configs)
│   └── test_web.py            # Flask route tests (Flask test client)
└── deploy/
    ├── prayer-display.service  # systemd unit (sets SDL_FBDEV, runs as pi user)
    └── install.sh              # Pi setup: apt deps, pip install, Tailscale install, service enable
```

## Architecture

### Core Abstractions

**`PrayerCalculator` (prayer/calculator.py)** — Abstract base with one method: `get_times(date, location) -> PrayerTimes`. `AdhanCalculator` wraps the `adhan-python` library. Swap calculation backends by subclassing without touching display or scheduling code.

**`IqamahEngine` (prayer/iqamah.py)** — Takes a list of `IqamahRule` objects (from config) and applies them per prayer to produce iqamah times. Rules implement a simple protocol: `apply(adhan_time: datetime) -> datetime`. Built-in rules in `rules.py` — adding a new rule type requires only a new class, no changes to the engine (Open/Closed).

**`DisplayEngine` (display/engine.py)** — Abstract interface with `render(state: DisplayState)`. `DisplayState` is a dataclass holding computed times, Hijri date, countdown, and which prayer is active/next. The display knows nothing about config or calculation.

**`ConfigLoader` (config/loader.py)** — Loads and validates `settings.yaml` via Pydantic. Uses `watchdog` to detect file changes and fires `on_config_change` callbacks. The only component that reads or writes the config file. The Flask route `POST /save` calls `loader.save(new_config)` to persist changes.

### Data Flow

```
settings.yaml
    └─► ConfigLoader ──────────────────────────────────┐
             │                                          │ hot-reload (watchdog)
             ▼                                          ▼
      AdhanCalculator ──► PrayerTimes ──► IqamahEngine ──► ComputedTimes
                                                    │
                           Scheduler (daily + 1Hz) ─┘
                                    │
                   ┌────────────────┴────────────────┐
                   ▼                                 ▼
          PyGameDisplay                       Flask Web UI
         (fullscreen TV)               (http://pi:8080 via Tailscale)
                                        GET /  → config form
                                        POST /save → update settings.yaml
                                        GET /api/times → current times JSON
```

### Threading Model

`main.py` runs three threads:
1. **Main thread** — Pygame event loop and rendering (must own the display)
2. **Scheduler thread** — Daemon; recalculates prayer times at midnight, polls every second for countdown
3. **Web thread** — Daemon; `waitress` WSGI server serving the Flask app

Threads share an `AppState` dataclass protected by a `threading.Lock`. Display reads it each frame; scheduler writes to it; Flask `/api/times` reads it.

### SOLID Application

- **SRP**: Config, prayer calculation, iqamah rules, display, and web UI each have a single reason to change.
- **OCP**: New iqamah rules, calc backends, and display themes added without modifying existing classes.
- **LSP**: Any `PrayerCalculator` subclass is substitutable everywhere a calculator is expected.
- **ISP**: `DisplayEngine.render()` only takes `DisplayState` — no config objects or calculator references.
- **DIP**: `main.py` constructs and injects concrete implementations; all other modules depend on abstractions.

## Configuration (`settings.yaml`)

```yaml
location:
  latitude: 0.0
  longitude: 0.0
  timezone: "America/New_York"
  elevation: 0  # meters

calculation:
  method: "ISNA"  # ISNA | MWL | Egyptian | Karachi | UmmAlQura | ...
  asr_madhab: "Standard"  # Standard (Shafi) | Hanafi

iqamah_rules:
  # Each prayer accepts a list of rules applied in order
  fajr:
    - type: offset_minutes
      value: 20
  dhuhr:
    - type: round_up_to
      every_n_minutes: 15
  # Omit a prayer to default iqamah = adhan time

display:
  theme: "dark"           # filename in display/themes/ (without .yaml)
  font_scale: 1.0
  clock_format: 24        # 12 or 24
  show_seconds: true
  language: "en"

hijri:
  enabled: true
  adjustment: 0           # ±days for local moon sighting

web:
  port: 8080
  host: "0.0.0.0"        # listen on all interfaces for Tailscale access
```

## Key Libraries

- `adhanpy` — Prayer time calculation (Python port of batoulapps/adhan-java; Jean Meeus accuracy; 12 methods)
- `pygame` — Fullscreen display rendering
- `flask` — Web UI and config API
- `waitress` — Production WSGI server for Flask on Pi (replaces Flask dev server)
- `pydantic` — Config validation and parsing
- `watchdog` — Config file hot-reload via filesystem events
- `hijri-converter` — Gregorian ↔ Hijri date conversion
- `pytest` + `pytest-cov` — Testing
- `ruff` — Linting and formatting
- `mypy` — Type checking

## Remote Access Setup (Tailscale)

Tailscale provides a secure private network between the Pi and your phone — no port forwarding or public IP needed.

1. On the Pi: `curl -fsSL https://tailscale.com/install.sh | sh && sudo tailscale up`
2. On Android: install the **Tailscale** app (free) and sign in with the same account
3. Open Chrome on Android → `http://<pi-tailscale-name>:8080`

The web UI shows all settings as an editable form. Hit **Save** — the Pi hot-reloads instantly without restarting.

## Raspberry Pi Notes

- Target: **Raspberry Pi OS Lite** (no desktop). Pygame uses Linux framebuffer (`/dev/fb0`) via `SDL_FBDEV`.
- Systemd service sets `SDL_VIDEODRIVER=fbcon` and `SDL_FBDEV=/dev/fb0` for headless operation.
- Use `waitress` in production (not Flask dev server): started automatically by `main.py` in the web thread.
- For desktop development, run with `--windowed`; leave `SDL_VIDEODRIVER` unset.
