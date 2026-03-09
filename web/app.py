"""Flask web application — config editor UI and JSON API.

Routes:
    GET  /              Config form (mobile-friendly HTML)
    POST /save          Persist updated config, triggers hot-reload
    GET  /api/times     Current prayer times as JSON (for live status)
    GET  /api/config    Current config as JSON
"""

import logging
from datetime import datetime

from flask import Flask, jsonify, redirect, render_template, request, url_for
from pydantic import ValidationError

from app_state import AppState
from config.loader import ConfigLoader
from config.models import AppConfig

logger = logging.getLogger(__name__)


def create_app(loader: ConfigLoader, state: AppState) -> Flask:
    app = Flask(__name__, template_folder="templates", static_folder="static")

    # ------------------------------------------------------------------ views

    @app.get("/")
    def index():
        return render_template("index.html", config=loader.config)

    @app.post("/save")
    def save():
        try:
            # Build a new AppConfig from submitted form data.
            # render_template can't handle nested structures easily, so we
            # reconstruct the full config from the existing one and override
            # only what was submitted.
            data = _form_to_dict(request.form)
            new_config = AppConfig(**data)
            loader.save(new_config)
            logger.info("Config saved via web UI")
        except (ValidationError, ValueError) as exc:
            logger.warning("Config save rejected: %s", exc)
            # TODO: surface error back to the UI in a later polish task
        return redirect(url_for("index"))

    # ------------------------------------------------------------------ API

    @app.get("/api/times")
    def api_times():
        snap = state.snapshot()
        pt = snap.prayer_times

        def fmt(dt: datetime | None) -> str | None:
            return dt.strftime("%H:%M") if dt else None

        times = {}
        if pt:
            for name, adhan in pt.as_dict().items():
                times[name] = {
                    "adhan": fmt(adhan),
                    "iqamah": fmt(snap.iqamah_times.get(name)),
                }

        return jsonify(
            {
                "current_time": snap.current_time.strftime("%H:%M:%S"),
                "next_prayer": snap.next_prayer_name,
                "countdown": str(snap.countdown).split(".")[0],  # strip microseconds
                "hijri": {"year": snap.hijri[0], "month": snap.hijri[1], "day": snap.hijri[2]},
                "times": times,
            }
        )

    @app.get("/api/config")
    def api_config():
        return jsonify(loader.config.model_dump())

    return app


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _form_to_dict(form) -> dict:
    """Reconstruct a nested dict from flat dot-notation form fields.

    e.g. ``location.latitude=51.5`` → ``{"location": {"latitude": 51.5}}``
    """
    result: dict = {}
    for key, value in form.items():
        parts = key.split(".")
        node = result
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        # Attempt numeric coercion; fall back to string
        leaf = parts[-1]
        try:
            node[leaf] = int(value)
        except ValueError:
            try:
                node[leaf] = float(value)
            except ValueError:
                node[leaf] = value
    return result
