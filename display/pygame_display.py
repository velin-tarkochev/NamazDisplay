"""Pygame fullscreen display — renders the Islamic prayer times table to the TV.

Layout (proportional to screen height H and width W):

  ┌────────────────────────────────────────────────────────────┐  0
  │              12:34:56  (clock, H*0.09 centre)              │
  │         Monday, 9 March 2026  (date, H*0.175 centre)       │
  │            8 Ramadan 1447 AH  (hijri, H*0.215 centre)      │
  ├────────────────────────────────────────────────────────────┤  H*0.245
  │  Prayer          │       Adhan       │      Iqamah         │  (header row)
  ├──────────────────┼───────────────────┼─────────────────────┤
  │  Fajr            │      5:12 AM      │      5:32 AM        │
  │  Sunrise         │      6:48 AM      │         —           │
  │  Dhuhr    ◄      │     12:30 PM      │     12:45 PM        │  (highlighted)
  │  Asr             │      4:15 PM      │      4:30 PM        │
  │  Maghrib         │      6:52 PM      │      6:57 PM        │
  │  Isha            │      8:10 PM      │      8:30 PM        │
  │  Tahajjud        │     12:01 AM      │         —           │
  ├────────────────────────────────────────────────────────────┤  H*0.88
  │                  Next: Dhuhr                               │
  │                  2h 15m 32s                                │
  └────────────────────────────────────────────────────────────┘  H
"""

import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pygame
import yaml

from app_state import AppState
from config.models import DisplayConfig
from display.engine import DisplayEngine, DisplayState

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

THEMES_DIR = Path(__file__).parent / "themes"
FPS = 2  # 2 frames/sec is smooth for a clock display and CPU-friendly

PRAYER_ORDER = ("fajr", "sunrise", "dhuhr", "asr", "maghrib", "isha", "tahajjud")

PRAYER_LABELS: dict[str, str] = {
    "fajr": "Fajr",
    "sunrise": "Sunrise",
    "dhuhr": "Dhuhr",
    "asr": "Asr",
    "maghrib": "Maghrib",
    "isha": "Isha",
    "tahajjud": "Tahajjud",
}

HIJRI_MONTHS = (
    "Muharram", "Safar", "Rabi' al-Awwal", "Rabi' al-Thani",
    "Jumada al-Awwal", "Jumada al-Thani", "Rajab", "Sha'ban",
    "Ramadan", "Shawwal", "Dhu al-Qi'dah", "Dhu al-Hijjah",
)

Color = tuple[int, int, int]

# ---------------------------------------------------------------------------
# Layout constants (fractions of screen H or W)
# ---------------------------------------------------------------------------
_CLOCK_CY      = 0.090  # clock centre-y
_DATE_CY       = 0.170  # gregorian date centre-y
_HIJRI_CY      = 0.212  # hijri date centre-y
_TABLE_TOP     = 0.248  # top of prayer table
_TABLE_BOTTOM  = 0.878  # bottom of prayer table
_HEADER_FRAC   = 0.115  # fraction of table height used by the column-header row
_COL_DIVIDER_1 = 0.370  # x-fraction for first vertical divider
_COL_DIVIDER_2 = 0.640  # x-fraction for second vertical divider
_COL_NAME_PAD  = 0.045  # left padding for prayer name text
_FOOTER_TOP    = 0.893  # start of countdown footer
_FOOTER_LABEL_CY = 0.924
_FOOTER_TIME_CY  = 0.967


# ---------------------------------------------------------------------------
# Theme helpers
# ---------------------------------------------------------------------------

def _load_theme(name: str) -> dict:
    path = THEMES_DIR / f"{name}.yaml"
    if not path.exists():
        logger.warning("Theme %r not found, falling back to 'dark'", name)
        path = THEMES_DIR / "dark.yaml"
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _c(theme: dict, key: str, default: Color = (255, 255, 255)) -> Color:
    """Look up a colour from the theme dict."""
    raw = theme.get("colors", {}).get(key)
    if isinstance(raw, (list, tuple)) and len(raw) == 3:
        return (int(raw[0]), int(raw[1]), int(raw[2]))
    return default


# ---------------------------------------------------------------------------
# Font loader
# ---------------------------------------------------------------------------

def _sys_font(family: str, fallback: str, size: int, bold: bool = False) -> pygame.font.Font:
    for name in (family, fallback, "freesans", "dejavusans", "sans", ""):
        try:
            f = pygame.font.SysFont(name or None, size, bold=bold)
            if f is not None:
                return f
        except Exception:
            continue
    return pygame.font.Font(None, size)


class _Fonts:
    """All fonts needed for one frame, sized proportionally to screen height."""

    def __init__(self, family: str, fallback: str, H: int, scale: float) -> None:
        def sz(ratio: float) -> int:
            return max(8, int(H * ratio * scale))

        self.clock         = _sys_font(family, fallback, sz(0.115), bold=True)
        self.date          = _sys_font(family, fallback, sz(0.040))
        self.hijri         = _sys_font(family, fallback, sz(0.036))
        self.col_header    = _sys_font(family, fallback, sz(0.036), bold=True)
        self.row_name      = _sys_font(family, fallback, sz(0.055), bold=True)
        self.row_time      = _sys_font(family, fallback, sz(0.055))
        self.footer_label  = _sys_font(family, fallback, sz(0.036))
        self.footer_time   = _sys_font(family, fallback, sz(0.072), bold=True)


# ---------------------------------------------------------------------------
# Main display class
# ---------------------------------------------------------------------------

class PyGameDisplay(DisplayEngine):

    def __init__(self, config: DisplayConfig, windowed: bool = False) -> None:
        self._config = config
        self._windowed = windowed
        self._screen: Optional[pygame.Surface] = None
        self._theme: dict = {}
        self._fonts: Optional[_Fonts] = None

    # ------------------------------------------------------------------
    # DisplayEngine interface
    # ------------------------------------------------------------------

    def render(self, state: DisplayState) -> None:
        if self._screen is None or self._fonts is None:
            return
        W, H = self._screen.get_size()
        self._screen.fill(_c(self._theme, "background", (0, 0, 0)))
        self._draw_header(state, W, H)
        self._draw_table(state, W, H)
        self._draw_footer(state, W, H)

    def run(self, app_state: AppState) -> None:
        pygame.init()
        pygame.display.set_caption("Prayer Times")
        pygame.mouse.set_visible(False)

        if self._windowed:
            self._screen = pygame.display.set_mode((1280, 720))
        else:
            self._screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)

        W, H = self._screen.get_size()
        theme_data = _load_theme(self._config.theme)
        self._theme = theme_data
        font_cfg = theme_data.get("fonts", {})
        self._fonts = _Fonts(
            family=font_cfg.get("family", "dejavusans"),
            fallback=font_cfg.get("fallback", "freesans"),
            H=H,
            scale=self._config.font_scale,
        )

        clock = pygame.time.Clock()
        try:
            running = True
            while running:
                for event in pygame.event.get():
                    if event.type == pygame.QUIT:
                        running = False
                    elif event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                        running = False

                self.render(DisplayState.from_app_state(app_state))
                pygame.display.flip()
                clock.tick(FPS)
        finally:
            pygame.quit()
            logger.info("Display closed")

    # ------------------------------------------------------------------
    # Drawing: header (clock + dates)
    # ------------------------------------------------------------------

    def _draw_header(self, state: DisplayState, W: int, H: int) -> None:
        f = self._fonts

        # Current time
        time_str = _fmt_clock(state.current_time, self._config.clock_format, self._config.show_seconds)
        self._blit_center(f.clock, time_str, _c(self._theme, "clock_text"), W // 2, int(H * _CLOCK_CY))

        # Gregorian date
        dt = state.current_time
        greg = f"{dt.strftime('%A')}, {dt.day} {dt.strftime('%B %Y')}"
        self._blit_center(f.date, greg, _c(self._theme, "date_text"), W // 2, int(H * _DATE_CY))

        # Hijri date
        if state.hijri != (0, 0, 0):
            hy, hm, hd = state.hijri
            month = HIJRI_MONTHS[hm - 1] if 1 <= hm <= 12 else str(hm)
            hijri = f"{hd} {month} {hy} AH"
            self._blit_center(f.hijri, hijri, _c(self._theme, "hijri_text"), W // 2, int(H * _HIJRI_CY))

    # ------------------------------------------------------------------
    # Drawing: prayer table
    # ------------------------------------------------------------------

    def _draw_table(self, state: DisplayState, W: int, H: int) -> None:
        f = self._fonts
        t = self._theme

        table_top    = int(H * _TABLE_TOP)
        table_bottom = int(H * _TABLE_BOTTOM)
        table_h      = table_bottom - table_top

        div1_x = int(W * _COL_DIVIDER_1)
        div2_x = int(W * _COL_DIVIDER_2)
        col_name_x   = int(W * _COL_NAME_PAD)
        col_adhan_cx = (div1_x + div2_x) // 2
        col_iqamah_cx = (div2_x + W) // 2

        # --- Column header row ---
        hdr_h = int(table_h * _HEADER_FRAC)
        pygame.draw.rect(self._screen, _c(t, "table_header_bg"), (0, table_top, W, hdr_h))
        hdr_cy = table_top + hdr_h // 2
        hdr_col = _c(t, "table_header_text")
        self._blit_left(f.col_header,  "Prayer",  hdr_col, col_name_x,   hdr_cy)
        self._blit_center(f.col_header, "Adhan",   hdr_col, col_adhan_cx,  hdr_cy)
        self._blit_center(f.col_header, "Iqamah",  hdr_col, col_iqamah_cx, hdr_cy)

        # --- Divider below header ---
        div_col = _c(t, "divider")
        pygame.draw.line(self._screen, div_col, (0, table_top + hdr_h), (W, table_top + hdr_h), 1)

        # --- Prayer rows ---
        rows_top = table_top + hdr_h
        row_h = (table_bottom - rows_top) // len(PRAYER_ORDER)

        if state.prayer_times is None:
            return

        adhan_map = state.prayer_times.as_dict()

        for i, name in enumerate(PRAYER_ORDER):
            row_y  = rows_top + i * row_h
            is_next = (name == state.next_prayer_name)

            # Row background
            if is_next:
                bg = _c(t, "highlight_bg")
            elif i % 2 == 0:
                bg = _c(t, "row_bg_even")
            else:
                bg = _c(t, "row_bg_odd")
            pygame.draw.rect(self._screen, bg, (0, row_y, W, row_h))

            # Text colours
            if is_next:
                name_col   = _c(t, "highlight_text")
                adhan_col  = _c(t, "highlight_text")
                iqamah_col = _c(t, "highlight_text")
            else:
                name_col   = _c(t, "prayer_name")
                adhan_col  = _c(t, "time_text")
                iqamah_col = _c(t, "iqamah_text")

            text_cy = row_y + row_h // 2

            # Prayer name
            label = PRAYER_LABELS.get(name, name.title())
            self._blit_left(f.row_name, label, name_col, col_name_x, text_cy)

            # Adhan time
            adhan_dt = adhan_map.get(name)
            if adhan_dt:
                self._blit_center(f.row_time, _fmt_time(adhan_dt, self._config.clock_format), adhan_col, col_adhan_cx, text_cy)

            # Iqamah time
            iqamah_dt = state.iqamah_times.get(name)
            if iqamah_dt:
                self._blit_center(f.row_time, _fmt_time(iqamah_dt, self._config.clock_format), iqamah_col, col_iqamah_cx, text_cy)
            else:
                self._blit_center(f.row_time, "\u2014", _c(t, "no_iqamah"), col_iqamah_cx, text_cy)

            # Horizontal row divider
            pygame.draw.line(self._screen, div_col, (0, row_y + row_h - 1), (W, row_y + row_h - 1), 1)

        # Vertical column dividers (full table height including header)
        pygame.draw.line(self._screen, div_col, (div1_x, table_top), (div1_x, table_bottom), 1)
        pygame.draw.line(self._screen, div_col, (div2_x, table_top), (div2_x, table_bottom), 1)

    # ------------------------------------------------------------------
    # Drawing: countdown footer
    # ------------------------------------------------------------------

    def _draw_footer(self, state: DisplayState, W: int, H: int) -> None:
        if not state.next_prayer_name:
            return
        f = self._fonts
        t = self._theme

        footer_y = int(H * _FOOTER_TOP)
        pygame.draw.rect(self._screen, _c(t, "countdown_bg"), (0, footer_y, W, H - footer_y))

        prayer_label = PRAYER_LABELS.get(state.next_prayer_name, state.next_prayer_name.title())
        self._blit_center(
            f.footer_label,
            f"Next: {prayer_label}",
            _c(t, "countdown_label"),
            W // 2,
            int(H * _FOOTER_LABEL_CY),
        )
        self._blit_center(
            f.footer_time,
            _fmt_countdown(state.countdown),
            _c(t, "countdown_text"),
            W // 2,
            int(H * _FOOTER_TIME_CY),
        )

    # ------------------------------------------------------------------
    # Blit helpers
    # ------------------------------------------------------------------

    def _blit_center(
        self,
        font: pygame.font.Font,
        text: str,
        color: Color,
        cx: int,
        cy: int,
    ) -> None:
        surf = font.render(text, True, color)
        rect = surf.get_rect(centerx=cx, centery=cy)
        self._screen.blit(surf, rect)  # type: ignore[union-attr]

    def _blit_left(
        self,
        font: pygame.font.Font,
        text: str,
        color: Color,
        x: int,
        cy: int,
    ) -> None:
        surf = font.render(text, True, color)
        rect = surf.get_rect(left=x, centery=cy)
        self._screen.blit(surf, rect)  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# Formatting utilities
# ---------------------------------------------------------------------------

def _fmt_clock(dt: datetime, clock_format: int, show_seconds: bool) -> str:
    if clock_format == 12:
        fmt = "%I:%M:%S %p" if show_seconds else "%I:%M %p"
        return dt.strftime(fmt).lstrip("0")
    fmt = "%H:%M:%S" if show_seconds else "%H:%M"
    return dt.strftime(fmt)


def _fmt_time(dt: datetime, clock_format: int) -> str:
    if clock_format == 12:
        return dt.strftime("%I:%M %p").lstrip("0")
    return dt.strftime("%H:%M")


def _fmt_countdown(td: timedelta) -> str:
    total = max(0, int(td.total_seconds()))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h > 0:
        return f"{h}h {m:02d}m {s:02d}s"
    if m > 0:
        return f"{m}m {s:02d}s"
    return f"{s}s"
