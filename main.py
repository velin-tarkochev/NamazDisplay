"""Entry point for the Prayer Times Display application.

Wires together all components via dependency injection and starts the three
daemon threads (scheduler, web server) before handing the main thread to
Pygame (display).

Usage:
    python main.py              # fullscreen display + web server
    python main.py --windowed   # windowed mode (development)
    python main.py --web-only   # web server only (no display)
"""

import argparse
import logging
import threading
from pathlib import Path

from app_state import AppState
from config.loader import ConfigLoader
from config.models import AppConfig
from prayer.calculator import AdhanCalculator
from prayer.iqamah import build_iqamah_engine
from prayer.scheduler import Scheduler

CONFIG_PATH = Path(__file__).parent / "config" / "settings.yaml"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
logger = logging.getLogger(__name__)


def _build_scheduler(config: AppConfig, state: AppState) -> Scheduler:
    calculator = AdhanCalculator(config.calculation.method, config.calculation.asr_madhab)
    iqamah_engine = build_iqamah_engine(config.iqamah_rules)
    return Scheduler(calculator, iqamah_engine, state, config)


def main() -> None:
    parser = argparse.ArgumentParser(description="Islamic Prayer Times Display")
    parser.add_argument("--windowed", action="store_true", help="Windowed mode (dev)")
    parser.add_argument("--web-only", action="store_true", help="Web server only, no display")
    args = parser.parse_args()

    # ------------------------------------------------------------------ boot
    loader = ConfigLoader(CONFIG_PATH)
    state = AppState()
    scheduler = _build_scheduler(loader.config, state)

    # Mutable ref so the hot-reload callback can reach the display object,
    # which is created later (after the callback is registered).
    _display_ref: list = [None]

    def on_config_change(new_config: AppConfig) -> None:
        logger.info("Config changed — updating scheduler components")
        from prayer.calculator import AdhanCalculator
        from prayer.iqamah import build_iqamah_engine

        calculator = AdhanCalculator(new_config.calculation.method, new_config.calculation.asr_madhab)
        iqamah_engine = build_iqamah_engine(new_config.iqamah_rules)
        scheduler.update_components(calculator, iqamah_engine, new_config)

        d = _display_ref[0]
        if d is not None:
            d.update_config(new_config.display)

    loader.on_change(on_config_change)
    scheduler.start()
    logger.info("Scheduler started")

    # ------------------------------------------------------------------ web
    from web.app import create_app

    flask_app = create_app(loader, state)
    cfg = loader.config

    def run_web() -> None:
        try:
            from waitress import serve

            logger.info("Web UI listening on http://%s:%d", cfg.web.host, cfg.web.port)
            serve(flask_app, host=cfg.web.host, port=cfg.web.port)
        except ImportError:
            logger.warning("waitress not installed — using Flask dev server")
            flask_app.run(host=cfg.web.host, port=cfg.web.port, use_reloader=False)

    web_thread = threading.Thread(target=run_web, daemon=True, name="web")
    web_thread.start()

    # ------------------------------------------------------------------ display
    if args.web_only:
        logger.info("Running in web-only mode. Press Ctrl+C to quit.")
        try:
            web_thread.join()
        except KeyboardInterrupt:
            pass
    else:
        from display.pygame_display import PyGameDisplay

        display = PyGameDisplay(loader.config.display, windowed=args.windowed)
        _display_ref[0] = display
        logger.info("Starting display (windowed=%s)", args.windowed)
        display.run(state)  # blocks until window is closed or ESC pressed

    loader.stop()
    scheduler.stop()
    logger.info("Shutdown complete")


if __name__ == "__main__":
    main()
