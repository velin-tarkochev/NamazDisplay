"""Pygame fullscreen display — renders the Islamic prayer times table to the TV.

Nine selectable layouts (config.display.layout):

  standard   — 3-column table: Prayer | Adhan | Iqamah  (original)
  minimal    — same data, no vertical grid lines, bold typography, accent bar
  transposed — prayers as columns, rows are Adhan / Iqamah
  split      — left panel: clock + info; right panel: prayer table
  cards      — 6 prayers as large rounded cards in a 3+3 grid
  timeline   — horizontal day ruler with staggered prayer labels above/below
  ambient    — enormous clock, next prayer info, clean breathing room
  sunclock   — circular 24-hour day/night clock face with left info panel
  terminal   — green phosphor CRT terminal with scanlines (ignores theme)
"""

import logging
import math
import time as _time
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
FPS = 10  # 10 frames/sec keeps the countdown smooth without heavy CPU use

PRAYER_ORDER = ("fajr", "sunrise", "dhuhr", "asr", "maghrib", "isha", "tahajjud")
FARD_ORDER   = ("fajr", "dhuhr", "asr", "maghrib", "isha")
CARD_ORDER   = ("fajr", "sunrise", "dhuhr", "asr", "maghrib", "isha")

PRAYER_LABELS: dict[str, str] = {
    "fajr":     "Fajr",
    "sunrise":  "Sunrise",
    "dhuhr":    "Dhuhr",
    "asr":      "Asr",
    "maghrib":  "Maghrib",
    "isha":     "Isha",
    "tahajjud": "Tahajjud",
}

HIJRI_MONTHS = (
    "Muharram", "Safar", "Rabi' al-Awwal", "Rabi' al-Thani",
    "Jumada al-Awwal", "Jumada al-Thani", "Rajab", "Sha'ban",
    "Ramadan", "Shawwal", "Dhu al-Qi'dah", "Dhu al-Hijjah",
)

Color = tuple[int, int, int]

# ---------------------------------------------------------------------------
# Layout constants (fractions of screen H or W) — shared by standard / minimal
# ---------------------------------------------------------------------------
_CLOCK_CY        = 0.090
_DATE_CY         = 0.170
_HIJRI_CY        = 0.212
_TABLE_TOP       = 0.248
_TABLE_BOTTOM    = 0.800
_HEADER_FRAC     = 0.115
_COL_DIVIDER_1   = 0.370
_COL_DIVIDER_2   = 0.640
_COL_NAME_PAD    = 0.045
_FOOTER_TOP      = 0.812


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
# Blit helpers (module-level — no self needed)
# ---------------------------------------------------------------------------

def _blit_center(
    screen: pygame.Surface,
    font: pygame.font.Font,
    text: str,
    color: Color,
    cx: int,
    cy: int,
) -> None:
    surf = font.render(text, True, color)
    screen.blit(surf, surf.get_rect(centerx=cx, centery=cy))


def _blit_left(
    screen: pygame.Surface,
    font: pygame.font.Font,
    text: str,
    color: Color,
    x: int,
    cy: int,
) -> None:
    surf = font.render(text, True, color)
    screen.blit(surf, surf.get_rect(left=x, centery=cy))


def _blit_right(
    screen: pygame.Surface,
    font: pygame.font.Font,
    text: str,
    color: Color,
    x: int,
    cy: int,
) -> None:
    surf = font.render(text, True, color)
    screen.blit(surf, surf.get_rect(right=x, centery=cy))


def _blit_glow(
    screen: pygame.Surface,
    font: pygame.font.Font,
    text: str,
    color: Color,
    cx: int,
    cy: int,
    radius: int = 2,
) -> None:
    """Render text with a soft neon glow by blitting dim copies at offsets."""
    dim = (max(0, color[0] // 3), max(0, color[1] // 3), max(0, color[2] // 3))
    halo = font.render(text, True, dim)
    for dx in range(-radius, radius + 1):
        for dy in range(-radius, radius + 1):
            if dx == 0 and dy == 0:
                continue
            screen.blit(halo, halo.get_rect(centerx=cx + dx, centery=cy + dy))
    bright = font.render(text, True, color)
    screen.blit(bright, bright.get_rect(centerx=cx, centery=cy))


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

        self.clock        = _sys_font(family, fallback, sz(0.115), bold=True)
        self.date         = _sys_font(family, fallback, sz(0.040))
        self.hijri        = _sys_font(family, fallback, sz(0.036))
        self.col_header   = _sys_font(family, fallback, sz(0.036), bold=True)
        self.row_name     = _sys_font(family, fallback, sz(0.055), bold=True)
        self.row_time     = _sys_font(family, fallback, sz(0.055))
        self.footer_label = _sys_font(family, fallback, sz(0.036))
        self.footer_time  = _sys_font(family, fallback, sz(0.060), bold=True)
        # Larger variants for minimal layout
        self.row_name_lg  = _sys_font(family, fallback, sz(0.065), bold=True)
        self.row_time_lg  = _sys_font(family, fallback, sz(0.063))
        # Card layout
        self.card_label   = _sys_font(family, fallback, sz(0.058), bold=True)
        self.card_time    = _sys_font(family, fallback, sz(0.062), bold=True)
        self.card_iqamah  = _sys_font(family, fallback, sz(0.042))
        # Split-panel layout
        self.split_clock  = _sys_font(family, fallback, sz(0.090), bold=True)
        self.split_label  = _sys_font(family, fallback, sz(0.046))
        self.split_next   = _sys_font(family, fallback, sz(0.052), bold=True)
        self.split_cd     = _sys_font(family, fallback, sz(0.080), bold=True)
        # Ambient / billboard layouts — enormous clock digit
        self.ambient_clock = _sys_font(family, fallback, sz(0.240), bold=True)


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


def _label_for(name: str, state: DisplayState) -> str:
    """Return display label for a prayer, substituting Jumu'ah for Dhuhr on Fridays."""
    if name == "dhuhr" and state.jumuah_time is not None:
        return "Jumu'ah"
    return PRAYER_LABELS.get(name, name.title())


def _adhan_for(name: str, adhan_map: dict, state: DisplayState):
    """Return the adhan datetime for a prayer, using Jumu'ah time for Dhuhr on Fridays."""
    if name == "dhuhr" and state.jumuah_time is not None:
        return state.jumuah_time
    return adhan_map.get(name)


def _iqamah_for(name: str, state: DisplayState):
    """Return iqamah datetime, suppressed for Dhuhr on Fridays (Jumu'ah has no iqamah)."""
    if name == "dhuhr" and state.jumuah_time is not None:
        return None
    return state.iqamah_times.get(name)


def _hijri_str(state: DisplayState) -> Optional[str]:
    if state.hijri == (0, 0, 0):
        return None
    hy, hm, hd = state.hijri
    month = HIJRI_MONTHS[hm - 1] if 1 <= hm <= 12 else str(hm)
    return f"{hd} {month} {hy} AH"


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
        self._scanline_surface: Optional[pygame.Surface] = None

    # ------------------------------------------------------------------
    # DisplayEngine interface
    # ------------------------------------------------------------------

    def render(self, state: DisplayState) -> None:
        if self._screen is None or self._fonts is None:
            return
        W, H = self._screen.get_size()
        self._screen.fill(_c(self._theme, "background", (0, 0, 0)))

        layout = self._config.layout
        if layout == "minimal":
            self._layout_minimal(state, W, H)
        elif layout == "transposed":
            self._layout_transposed(state, W, H)
        elif layout == "split":
            self._layout_split(state, W, H)
        elif layout == "cards":
            self._layout_cards(state, W, H)
        elif layout == "timeline":
            self._layout_timeline(state, W, H)
        elif layout == "ambient":
            self._layout_ambient(state, W, H)
        elif layout == "sunclock":
            self._layout_sunclock(state, W, H)
        elif layout == "terminal":
            self._layout_terminal(state, W, H)
        else:
            self._layout_standard(state, W, H)

    def run(self, app_state: AppState) -> None:
        pygame.init()
        pygame.display.set_caption("Prayer Times")
        pygame.mouse.set_visible(False)

        if self._windowed:
            self._screen = pygame.display.set_mode((1280, 720))
        else:
            self._screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)

        _, H = self._screen.get_size()
        self._reload_theme_fonts(H)

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

    def update_config(self, config: DisplayConfig) -> None:
        """Hot-reload: swap config and rebuild theme + fonts immediately."""
        self._config = config
        if self._screen is not None:
            _, H = self._screen.get_size()
            self._reload_theme_fonts(H)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _reload_theme_fonts(self, H: int) -> None:
        theme_data = _load_theme(self._config.theme)
        self._theme = theme_data
        font_cfg = theme_data.get("fonts", {})
        self._fonts = _Fonts(
            family=font_cfg.get("family", "dejavusans"),
            fallback=font_cfg.get("fallback", "freesans"),
            H=H,
            scale=self._config.font_scale,
        )

    # ------------------------------------------------------------------
    # Shared sub-sections
    # ------------------------------------------------------------------

    def _draw_header(self, state: DisplayState, W: int, H: int) -> None:
        """Clock + Gregorian + Hijri dates centred at top of screen."""
        f, t, scr = self._fonts, self._theme, self._screen
        time_str = _fmt_clock(state.current_time, self._config.clock_format, self._config.show_seconds)
        _blit_center(scr, f.clock, time_str, _c(t, "clock_text"), W // 2, int(H * _CLOCK_CY))

        dt   = state.current_time
        greg = f"{dt.strftime('%A')}, {dt.day} {dt.strftime('%B %Y')}"
        _blit_center(scr, f.date, greg, _c(t, "date_text"), W // 2, int(H * _DATE_CY))

        hijri = _hijri_str(state)
        if hijri:
            _blit_center(scr, f.hijri, hijri, _c(t, "hijri_text"), W // 2, int(H * _HIJRI_CY))

    def _draw_footer(self, state: DisplayState, W: int, H: int) -> None:
        """Countdown footer with progress bar at its top edge.

        When between a prayer's adhan and iqamah the footer switches to an
        iqamah countdown bar instead of the normal next-adhan countdown.
        """
        if not state.next_prayer_name and not state.current_iqamah:
            return
        f, t, scr = self._fonts, self._theme, self._screen

        footer_y = int(H * _FOOTER_TOP)
        pygame.draw.rect(scr, _c(t, "countdown_bg"), (0, footer_y, W, H - footer_y))

        bar_h   = max(16, int(H * 0.040))
        text_top = footer_y + bar_h
        text_h   = H - text_top
        label_cy = text_top + int(text_h * 0.35)
        time_cy  = text_top + int(text_h * 0.78)

        if state.current_iqamah is not None:
            # ── Iqamah countdown ──────────────────────────────────────
            now_live      = datetime.now(tz=state.current_iqamah.tzinfo)
            remaining_secs = max(0.0, (state.current_iqamah - now_live).total_seconds())
            live_cd        = timedelta(seconds=remaining_secs)
            remaining_frac = 1.0 - state.current_iqamah_progress

            bar_w = int(W * remaining_frac)
            pygame.draw.rect(scr, _c(t, "row_bg_odd"), (0, footer_y, W, bar_h))
            if bar_w > 0:
                pygame.draw.rect(scr, _c(t, "iqamah_text", (220, 160, 50)), (W - bar_w, footer_y, bar_w, bar_h))

            prayer_label = _label_for(state.current_iqamah_name, state)
            _blit_center(scr, f.footer_label,
                         f"Iqamah: {prayer_label}",
                         _c(t, "iqamah_text"), W // 2, label_cy)
            pct    = int(remaining_frac * 100)
            cd_str = f"{_fmt_countdown(live_cd)}  ·  {pct}%"
            _blit_center(scr, f.footer_time, cd_str, _c(t, "countdown_text"), W // 2, time_cy)

        else:
            # ── Next-adhan countdown ──────────────────────────────────
            if state.next_prayer_adhan is not None and state.interval_progress < 1.0:
                now_live       = datetime.now(tz=state.next_prayer_adhan.tzinfo)
                remaining_secs = max(0.0, (state.next_prayer_adhan - now_live).total_seconds())
                live_cd        = timedelta(seconds=remaining_secs)
            else:
                remaining_secs = max(0.0, state.countdown.total_seconds())
                live_cd        = state.countdown
            remaining_frac = 1.0 - state.interval_progress

            bar_w = int(W * remaining_frac)
            pygame.draw.rect(scr, _c(t, "row_bg_odd"), (0, footer_y, W, bar_h))
            if bar_w > 0:
                pygame.draw.rect(scr, _c(t, "highlight_bg"), (W - bar_w, footer_y, bar_w, bar_h))

            prayer_label = _label_for(state.next_prayer_name, state)
            _blit_center(scr, f.footer_label,
                         f"Next: {prayer_label}",
                         _c(t, "countdown_label"), W // 2, label_cy)
            pct    = int(remaining_frac * 100)
            cd_str = f"{_fmt_countdown(live_cd)}  ·  {pct}%"
            _blit_center(scr, f.footer_time, cd_str, _c(t, "countdown_text"), W // 2, time_cy)

    # ------------------------------------------------------------------
    # Layout: standard
    # ------------------------------------------------------------------

    def _layout_standard(self, state: DisplayState, W: int, H: int) -> None:
        self._draw_header(state, W, H)
        self._draw_table_standard(state, W, H)
        self._draw_footer(state, W, H)

    def _draw_table_standard(self, state: DisplayState, W: int, H: int) -> None:
        f, t, scr = self._fonts, self._theme, self._screen

        table_top    = int(H * _TABLE_TOP)
        table_bottom = int(H * _TABLE_BOTTOM)
        table_h      = table_bottom - table_top

        div1_x       = int(W * _COL_DIVIDER_1)
        div2_x       = int(W * _COL_DIVIDER_2)
        col_name_x   = int(W * _COL_NAME_PAD)
        col_adhan_cx = (div1_x + div2_x) // 2
        col_iq_cx    = (div2_x + W) // 2

        hdr_h  = int(table_h * _HEADER_FRAC)
        pygame.draw.rect(scr, _c(t, "table_header_bg"), (0, table_top, W, hdr_h))
        hdr_cy = table_top + hdr_h // 2
        hc     = _c(t, "table_header_text")
        _blit_left(scr,   f.col_header, "Prayer", hc, col_name_x,   hdr_cy)
        _blit_center(scr, f.col_header, "Adhan",  hc, col_adhan_cx, hdr_cy)
        _blit_center(scr, f.col_header, "Iqamah", hc, col_iq_cx,    hdr_cy)

        div_col = _c(t, "divider")
        pygame.draw.line(scr, div_col, (0, table_top + hdr_h), (W, table_top + hdr_h), 1)

        rows_top = table_top + hdr_h
        row_h    = (table_bottom - rows_top) // len(PRAYER_ORDER)

        if state.prayer_times is None:
            return
        adhan_map = state.prayer_times.as_dict()

        for i, name in enumerate(PRAYER_ORDER):
            row_y   = rows_top + i * row_h
            is_next = name == state.next_prayer_name

            bg = (_c(t, "highlight_bg") if is_next
                  else _c(t, "row_bg_even") if i % 2 == 0
                  else _c(t, "row_bg_odd"))
            pygame.draw.rect(scr, bg, (0, row_y, W, row_h))

            if is_next:
                nc = ac = ic = _c(t, "highlight_text")
            else:
                nc, ac, ic = _c(t, "prayer_name"), _c(t, "time_text"), _c(t, "iqamah_text")

            cy = row_y + row_h // 2
            _blit_left(scr,   f.row_name, _label_for(name, state), nc, col_name_x, cy)
            adhan_dt = _adhan_for(name, adhan_map, state)
            if adhan_dt:
                _blit_center(scr, f.row_time, _fmt_time(adhan_dt, self._config.clock_format), ac, col_adhan_cx, cy)
            iq_dt = _iqamah_for(name, state)
            if iq_dt:
                _blit_center(scr, f.row_time, _fmt_time(iq_dt, self._config.clock_format), ic, col_iq_cx, cy)
            else:
                _blit_center(scr, f.row_time, "\u2014", _c(t, "no_iqamah"), col_iq_cx, cy)

            pygame.draw.line(scr, div_col, (0, row_y + row_h - 1), (W, row_y + row_h - 1), 1)

        pygame.draw.line(scr, div_col, (div1_x, table_top), (div1_x, table_bottom), 1)
        pygame.draw.line(scr, div_col, (div2_x, table_top), (div2_x, table_bottom), 1)

    # ------------------------------------------------------------------
    # Layout: minimal
    # ------------------------------------------------------------------

    def _layout_minimal(self, state: DisplayState, W: int, H: int) -> None:
        self._draw_header(state, W, H)
        self._draw_table_minimal(state, W, H)
        self._draw_footer(state, W, H)

    def _draw_table_minimal(self, state: DisplayState, W: int, H: int) -> None:
        f, t, scr = self._fonts, self._theme, self._screen

        table_top    = int(H * _TABLE_TOP)
        table_bottom = int(H * _TABLE_BOTTOM)
        table_h      = table_bottom - table_top

        col_name_x   = int(W * 0.055)
        col_adhan_cx = int(W * 0.500)
        col_iq_x     = int(W * 0.960)

        hdr_h  = int(table_h * _HEADER_FRAC)
        pygame.draw.rect(scr, _c(t, "table_header_bg"), (0, table_top, W, hdr_h))
        hdr_cy = table_top + hdr_h // 2
        hc     = _c(t, "table_header_text")
        _blit_left(scr,  f.col_header, "Prayer", hc, col_name_x,   hdr_cy)
        _blit_center(scr, f.col_header, "Adhan",  hc, col_adhan_cx, hdr_cy)
        _blit_right(scr,  f.col_header, "Iqamah", hc, col_iq_x,    hdr_cy)

        div_col  = _c(t, "divider")
        accent   = _c(t, "highlight_bg")
        rows_top = table_top + hdr_h
        row_h    = (table_bottom - rows_top) // len(PRAYER_ORDER)

        if state.prayer_times is None:
            return
        adhan_map = state.prayer_times.as_dict()

        for i, name in enumerate(PRAYER_ORDER):
            row_y   = rows_top + i * row_h
            is_next = name == state.next_prayer_name

            bg = (_c(t, "highlight_bg") if is_next
                  else _c(t, "row_bg_even") if i % 2 == 0
                  else _c(t, "row_bg_odd"))
            pygame.draw.rect(scr, bg, (0, row_y, W, row_h))

            if is_next:
                pygame.draw.rect(scr, accent, (0, row_y, 6, row_h))
                nc = ac = ic = _c(t, "highlight_text")
            else:
                nc, ac, ic = _c(t, "prayer_name"), _c(t, "time_text"), _c(t, "iqamah_text")

            cy = row_y + row_h // 2
            _blit_left(scr,  f.row_name_lg, _label_for(name, state), nc, col_name_x, cy)
            adhan_dt = _adhan_for(name, adhan_map, state)
            if adhan_dt:
                _blit_center(scr, f.row_time_lg, _fmt_time(adhan_dt, self._config.clock_format), ac, col_adhan_cx, cy)
            iq_dt = _iqamah_for(name, state)
            if iq_dt:
                _blit_right(scr, f.row_time_lg, _fmt_time(iq_dt, self._config.clock_format), ic, col_iq_x, cy)
            else:
                _blit_right(scr, f.row_time_lg, "\u2014", _c(t, "no_iqamah"), col_iq_x, cy)

            pygame.draw.line(scr, div_col, (0, row_y + row_h - 1), (W, row_y + row_h - 1), 2)

    # ------------------------------------------------------------------
    # Layout: transposed
    # ------------------------------------------------------------------

    def _layout_transposed(self, state: DisplayState, W: int, H: int) -> None:
        self._draw_header(state, W, H)
        self._draw_table_transposed(state, W, H)
        self._draw_footer(state, W, H)

    def _draw_table_transposed(self, state: DisplayState, W: int, H: int) -> None:
        f, t, scr = self._fonts, self._theme, self._screen

        table_top    = int(H * _TABLE_TOP)
        table_bottom = int(H * _TABLE_BOTTOM)
        table_h      = table_bottom - table_top

        label_w = int(W * 0.15)
        n_cols  = len(PRAYER_ORDER)
        col_w   = (W - label_w) // n_cols

        hdr_h  = int(table_h * 0.30)
        row_h  = (table_h - hdr_h) // 2

        div_col = _c(t, "divider")
        hc      = _c(t, "table_header_text")

        if state.prayer_times is None:
            return
        adhan_map = state.prayer_times.as_dict()

        # ── Row 0: prayer-name header ──────────────────────────────────
        hdr_y  = table_top
        hdr_cy = hdr_y + hdr_h // 2
        pygame.draw.rect(scr, _c(t, "table_header_bg"), (0, hdr_y, W, hdr_h))

        for ci, name in enumerate(PRAYER_ORDER):
            is_next = name == state.next_prayer_name
            col_x   = label_w + ci * col_w
            col_cx  = col_x + col_w // 2
            if is_next:
                pygame.draw.rect(scr, _c(t, "highlight_bg"), (col_x, hdr_y, col_w, hdr_h))
                tc = _c(t, "highlight_text")
            else:
                tc = hc
            _blit_center(scr, f.col_header, _label_for(name, state), tc, col_cx, hdr_cy)

        pygame.draw.line(scr, div_col, (0, hdr_y + hdr_h), (W, hdr_y + hdr_h), 1)

        # ── Rows 1 & 2: Adhan + Iqamah ────────────────────────────────
        row_defs = [
            ("Adhan",  adhan_map,          "time_text"),
            ("Iqamah", state.iqamah_times, "iqamah_text"),
        ]
        for ri, (row_label, data_map, color_key) in enumerate(row_defs):
            row_y  = table_top + hdr_h + ri * row_h
            row_cy = row_y + row_h // 2

            pygame.draw.rect(scr, _c(t, "table_header_bg"), (0, row_y, label_w, row_h))
            _blit_center(scr, f.row_name, row_label, _c(t, "table_header_text"), label_w // 2, row_cy)

            for ci, name in enumerate(PRAYER_ORDER):
                is_next = name == state.next_prayer_name
                col_x   = label_w + ci * col_w
                col_cx  = col_x + col_w // 2

                bg = (_c(t, "highlight_bg") if is_next
                      else _c(t, "row_bg_even") if ri % 2 == 0
                      else _c(t, "row_bg_odd"))
                pygame.draw.rect(scr, bg, (col_x, row_y, col_w, row_h))

                tc = _c(t, "highlight_text") if is_next else _c(t, color_key)
                dt = (_adhan_for(name, adhan_map, state) if ri == 0
                      else _iqamah_for(name, state))
                if dt:
                    time_str = _fmt_time(dt, self._config.clock_format)
                    parts = time_str.split(" ", 1)
                    if len(parts) == 2:
                        line_gap = int(row_h * 0.22)
                        _blit_center(scr, f.row_time, parts[0], tc, col_cx, row_cy - line_gap)
                        _blit_center(scr, f.col_header, parts[1], tc, col_cx, row_cy + line_gap)
                    else:
                        _blit_center(scr, f.row_time, time_str, tc, col_cx, row_cy)
                else:
                    _blit_center(scr, f.row_time, "\u2014", _c(t, "no_iqamah"), col_cx, row_cy)

                pygame.draw.line(scr, div_col, (col_x, table_top), (col_x, table_bottom), 1)

            pygame.draw.line(scr, div_col, (0, row_y + row_h - 1), (W, row_y + row_h - 1), 1)

        pygame.draw.line(scr, div_col, (label_w, table_top), (label_w, table_bottom), 2)

    # ------------------------------------------------------------------
    # Layout: split
    # ------------------------------------------------------------------

    def _layout_split(self, state: DisplayState, W: int, H: int) -> None:
        f, t, scr = self._fonts, self._theme, self._screen
        split_x = int(W * 0.38)

        # ── Left panel ─────────────────────────────────────────────────
        pygame.draw.rect(scr, _c(t, "table_header_bg"), (0, 0, split_x, H))

        time_str = _fmt_clock(state.current_time, self._config.clock_format, self._config.show_seconds)
        _blit_center(scr, f.split_clock, time_str, _c(t, "clock_text"), split_x // 2, int(H * 0.20))

        dt   = state.current_time
        greg = f"{dt.strftime('%A')}, {dt.day} {dt.strftime('%B %Y')}"
        _blit_center(scr, f.split_label, greg, _c(t, "date_text"), split_x // 2, int(H * 0.34))

        hijri = _hijri_str(state)
        if hijri:
            _blit_center(scr, f.split_label, hijri, _c(t, "hijri_text"), split_x // 2, int(H * 0.42))

        div_col = _c(t, "divider")
        pygame.draw.line(scr, div_col,
                         (int(split_x * 0.08), int(H * 0.50)),
                         (int(split_x * 0.92), int(H * 0.50)), 1)

        bar_y       = int(H * 0.89)
        bar_h       = max(16, int(H * 0.040))
        bar_total_w = int(split_x * 0.78)
        bar_x       = (split_x - bar_total_w) // 2

        if state.current_iqamah is not None:
            # ── Iqamah countdown ──────────────────────────────────────
            now_live       = datetime.now(tz=state.current_iqamah.tzinfo)
            remaining_secs = max(0.0, (state.current_iqamah - now_live).total_seconds())
            live_cd        = timedelta(seconds=remaining_secs)
            remaining_frac = 1.0 - state.current_iqamah_progress

            prayer_label = _label_for(state.current_iqamah_name, state)
            _blit_center(scr, f.split_label, "Iqamah",
                         _c(t, "iqamah_text"), split_x // 2, int(H * 0.57))
            _blit_center(scr, f.split_next, prayer_label,
                         _c(t, "highlight_text"), split_x // 2, int(H * 0.65))
            _blit_center(scr, f.split_cd, _fmt_countdown(live_cd),
                         _c(t, "countdown_text"), split_x // 2, int(H * 0.75))
            pct = int(remaining_frac * 100)
            _blit_center(scr, f.split_label, f"{pct}%",
                         _c(t, "iqamah_text"), split_x // 2, int(H * 0.85))
            pygame.draw.rect(scr, _c(t, "row_bg_odd"), (bar_x, bar_y, bar_total_w, bar_h))
            filled = int(bar_total_w * remaining_frac)
            if filled > 0:
                pygame.draw.rect(scr, _c(t, "iqamah_text", (220, 160, 50)),
                                 (bar_x + bar_total_w - filled, bar_y, filled, bar_h))

        elif state.next_prayer_name:
            # ── Next-adhan countdown ──────────────────────────────────
            if state.next_prayer_adhan is not None and state.interval_progress < 1.0:
                now_live       = datetime.now(tz=state.next_prayer_adhan.tzinfo)
                remaining_secs = max(0.0, (state.next_prayer_adhan - now_live).total_seconds())
                live_cd        = timedelta(seconds=remaining_secs)
            else:
                remaining_secs = max(0.0, state.countdown.total_seconds())
                live_cd        = state.countdown
            remaining_frac = 1.0 - state.interval_progress

            prayer_label = _label_for(state.next_prayer_name, state)
            _blit_center(scr, f.split_label, "Next Prayer",
                         _c(t, "countdown_label"), split_x // 2, int(H * 0.57))
            _blit_center(scr, f.split_next, prayer_label,
                         _c(t, "highlight_text"), split_x // 2, int(H * 0.65))
            _blit_center(scr, f.split_cd, _fmt_countdown(live_cd),
                         _c(t, "countdown_text"), split_x // 2, int(H * 0.75))
            pct = int(remaining_frac * 100)
            _blit_center(scr, f.split_label, f"{pct}%",
                         _c(t, "countdown_label"), split_x // 2, int(H * 0.85))
            pygame.draw.rect(scr, _c(t, "row_bg_odd"), (bar_x, bar_y, bar_total_w, bar_h))
            filled = int(bar_total_w * remaining_frac)
            if filled > 0:
                pygame.draw.rect(scr, _c(t, "highlight_bg"),
                                 (bar_x + bar_total_w - filled, bar_y, filled, bar_h))

        # ── Vertical separator ─────────────────────────────────────────
        pygame.draw.line(scr, div_col, (split_x, 0), (split_x, H), 2)

        # ── Right panel: prayer table ───────────────────────────────────
        self._draw_table_split_right(state, split_x, W, H)

    def _draw_table_split_right(self, state: DisplayState, split_x: int, W: int, H: int) -> None:
        f, t, scr = self._fonts, self._theme, self._screen

        rw      = W - split_x
        div1_rx = int(rw * 0.38)
        div2_rx = int(rw * 0.68)
        div1_x  = split_x + div1_rx
        div2_x  = split_x + div2_rx

        col_name_x   = split_x + int(rw * 0.04)
        col_adhan_cx = split_x + (div1_rx + div2_rx) // 2
        col_iq_cx    = split_x + (div2_rx + rw) // 2

        hdr_h  = int(H * 0.085)
        pygame.draw.rect(scr, _c(t, "table_header_bg"), (split_x, 0, rw, hdr_h))
        hdr_cy = hdr_h // 2
        hc     = _c(t, "table_header_text")
        _blit_left(scr,   f.col_header, "Prayer", hc, col_name_x,   hdr_cy)
        _blit_center(scr, f.col_header, "Adhan",  hc, col_adhan_cx, hdr_cy)
        _blit_center(scr, f.col_header, "Iqamah", hc, col_iq_cx,    hdr_cy)

        div_col = _c(t, "divider")
        pygame.draw.line(scr, div_col, (split_x, hdr_h), (W, hdr_h), 1)

        row_h = (H - hdr_h) // len(PRAYER_ORDER)

        if state.prayer_times is None:
            return
        adhan_map = state.prayer_times.as_dict()

        for i, name in enumerate(PRAYER_ORDER):
            row_y   = hdr_h + i * row_h
            is_next = name == state.next_prayer_name

            bg = (_c(t, "highlight_bg") if is_next
                  else _c(t, "row_bg_even") if i % 2 == 0
                  else _c(t, "row_bg_odd"))
            pygame.draw.rect(scr, bg, (split_x, row_y, rw, row_h))

            if is_next:
                nc = ac = ic = _c(t, "highlight_text")
            else:
                nc, ac, ic = _c(t, "prayer_name"), _c(t, "time_text"), _c(t, "iqamah_text")

            cy = row_y + row_h // 2
            _blit_left(scr,   f.row_name, _label_for(name, state), nc, col_name_x, cy)
            adhan_dt = _adhan_for(name, adhan_map, state)
            if adhan_dt:
                _blit_center(scr, f.row_time, _fmt_time(adhan_dt, self._config.clock_format), ac, col_adhan_cx, cy)
            iq_dt = _iqamah_for(name, state)
            if iq_dt:
                _blit_center(scr, f.row_time, _fmt_time(iq_dt, self._config.clock_format), ic, col_iq_cx, cy)
            else:
                _blit_center(scr, f.row_time, "\u2014", _c(t, "no_iqamah"), col_iq_cx, cy)

            pygame.draw.line(scr, div_col, (split_x, row_y + row_h - 1), (W, row_y + row_h - 1), 1)

        pygame.draw.line(scr, div_col, (div1_x, 0), (div1_x, H), 1)
        pygame.draw.line(scr, div_col, (div2_x, 0), (div2_x, H), 1)

    # ------------------------------------------------------------------
    # Layout: cards
    # ------------------------------------------------------------------

    def _layout_cards(self, state: DisplayState, W: int, H: int) -> None:
        f, t, scr = self._fonts, self._theme, self._screen

        # Compact header
        time_str = _fmt_clock(state.current_time, self._config.clock_format, self._config.show_seconds)
        _blit_center(scr, f.clock, time_str, _c(t, "clock_text"), W // 2, int(H * 0.07))
        dt   = state.current_time
        greg = f"{dt.strftime('%A')}, {dt.day} {dt.strftime('%B %Y')}"
        _blit_center(scr, f.date, greg, _c(t, "date_text"), W // 2, int(H * 0.14))
        hijri = _hijri_str(state)
        if hijri:
            _blit_center(scr, f.hijri, hijri, _c(t, "hijri_text"), W // 2, int(H * 0.19))

        if state.prayer_times is None:
            self._draw_footer(state, W, H)
            return

        adhan_map = state.prayer_times.as_dict()

        card_area_top    = int(H * 0.23)
        card_area_bottom = int(H * _FOOTER_TOP) - int(H * 0.015)
        card_area_h      = card_area_bottom - card_area_top

        gap     = int(W * 0.025)
        row_h   = (card_area_h - gap) // 2
        card_w  = (W - gap * 4) // 3

        row1_prayers = CARD_ORDER[:3]
        row2_prayers = CARD_ORDER[3:]

        def draw_card(name: str, cx: int, cy: int, cw: int, ch: int) -> None:
            is_next = name == state.next_prayer_name
            bg = _c(t, "highlight_bg") if is_next else _c(t, "row_bg_even")
            rx = cx - cw // 2
            ry = cy - ch // 2
            pygame.draw.rect(scr, bg, (rx, ry, cw, ch), border_radius=14)
            pygame.draw.rect(scr, _c(t, "divider"), (rx, ry, cw, ch), width=1, border_radius=14)

            lc = tc = ic = _c(t, "highlight_text") if is_next else None
            if not is_next:
                lc, tc, ic = _c(t, "prayer_name"), _c(t, "time_text"), _c(t, "iqamah_text")

            _blit_center(scr, f.card_label, _label_for(name, state), lc, cx, ry + int(ch * 0.22))

            adhan_dt = _adhan_for(name, adhan_map, state)
            if adhan_dt:
                _blit_center(scr, f.card_time, _fmt_time(adhan_dt, self._config.clock_format), tc, cx, ry + int(ch * 0.52))

            iq_dt = _iqamah_for(name, state)
            if iq_dt:
                _blit_center(scr, f.card_iqamah, _fmt_time(iq_dt, self._config.clock_format), ic, cx, ry + int(ch * 0.78))
            else:
                _blit_center(scr, f.card_iqamah, "\u2014", _c(t, "no_iqamah"), cx, ry + int(ch * 0.78))

        # Row 1: Fajr, Sunrise, Dhuhr
        row1_cy = card_area_top + row_h // 2
        for ci, name in enumerate(row1_prayers):
            cx = gap + card_w // 2 + ci * (card_w + gap)
            draw_card(name, cx, row1_cy, card_w, row_h)

        # Row 2: Asr, Maghrib, Isha
        row2_cy = card_area_top + row_h + gap + row_h // 2
        for ci, name in enumerate(row2_prayers):
            cx = gap + card_w // 2 + ci * (card_w + gap)
            draw_card(name, cx, row2_cy, card_w, row_h)

        self._draw_footer(state, W, H)

    # ------------------------------------------------------------------
    # Layout: timeline — horizontal 24-hour day ruler
    # ------------------------------------------------------------------

    def _layout_timeline(self, state: DisplayState, W: int, H: int) -> None:
        f, t, scr = self._fonts, self._theme, self._screen

        # Header strip
        self._draw_header(state, W, H)

        if state.prayer_times is None:
            self._draw_footer(state, W, H)
            return

        adhan_map = state.prayer_times.as_dict()
        div_col = _c(t, "divider")
        bar_y   = int(H * 0.400)
        bar_x0  = int(W * 0.04)
        bar_x1  = int(W * 0.96)
        bar_w   = bar_x1 - bar_x0
        dot_r   = int(H * 0.018)

        def _secs(dt: datetime) -> float:
            return dt.hour * 3600 + dt.minute * 60 + dt.second

        # Background band — tall enough for staggered labels above and below bar
        above_offset = int(H * 0.110)   # distance from bar_y to top label line
        below_offset = int(H * 0.110)   # distance from bar_y to bottom label line
        band_top    = bar_y - above_offset - int(H * 0.030)
        band_bottom = bar_y + below_offset + int(H * 0.030)
        pygame.draw.rect(scr, _c(t, "table_header_bg"), (0, band_top, W, band_bottom - band_top))

        # Highlight region: last past prayer → next prayer
        now_secs  = _secs(state.current_time)
        past_secs = [_secs(adhan_map[n]) for n in PRAYER_ORDER if n in adhan_map and _secs(adhan_map[n]) <= now_secs]
        next_secs = [_secs(adhan_map[n]) for n in PRAYER_ORDER if n in adhan_map and _secs(adhan_map[n]) > now_secs]
        if past_secs and next_secs:
            hx0 = bar_x0 + int(max(past_secs) / 86400 * bar_w)
            hx1 = bar_x0 + int(min(next_secs) / 86400 * bar_w)
            hl_surf = pygame.Surface((hx1 - hx0, band_bottom - band_top), pygame.SRCALPHA)
            hc = _c(t, "highlight_bg")
            hl_surf.fill((hc[0], hc[1], hc[2], 70))
            scr.blit(hl_surf, (hx0, band_top))

        # Main bar line
        pygame.draw.line(scr, div_col, (bar_x0, bar_y), (bar_x1, bar_y), 2)

        # Prayer dots + staggered labels (even index = above bar, odd = below)
        stem_col = _c(t, "divider")
        for ci, name in enumerate(PRAYER_ORDER):
            dt = _adhan_for(name, adhan_map, state)
            if dt is None:
                continue
            s   = _secs(dt)
            px  = bar_x0 + int(s / 86400 * bar_w)
            is_next = name == state.next_prayer_name
            color   = _c(t, "highlight_text") if is_next else _c(t, "time_text")
            r       = dot_r + 3 if is_next else dot_r
            pygame.draw.circle(scr, _c(t, "background"), (px, bar_y), r + 2)
            pygame.draw.circle(scr, color, (px, bar_y), r)

            above = (ci % 2 == 0)
            label_gap = int(H * 0.042)
            time_gap  = int(H * 0.082)
            if above:
                label_y = bar_y - r - label_gap
                time_y  = bar_y - r - time_gap
                stem_y0 = bar_y - r
                stem_y1 = label_y + int(H * 0.016)
            else:
                label_y = bar_y + r + label_gap
                time_y  = bar_y + r + time_gap
                stem_y0 = bar_y + r
                stem_y1 = label_y - int(H * 0.016)

            # Thin stem line connecting dot to label
            pygame.draw.line(scr, stem_col, (px, stem_y0), (px, stem_y1), 1)
            _blit_center(scr, f.col_header, _label_for(name, state), color, px, label_y)
            _blit_center(scr, f.col_header, _fmt_time(dt, self._config.clock_format), color, px, time_y)

        # Current-time tick
        now_px = bar_x0 + int(now_secs / 86400 * bar_w)
        pygame.draw.line(scr, _c(t, "countdown_text"), (now_px, bar_y - dot_r * 3), (now_px, bar_y + dot_r * 3), 3)

        # "Up next" info strip
        info_cy = int(H * 0.620)
        if state.next_prayer_name:
            label    = _label_for(state.next_prayer_name, state)
            adhan_dt = _adhan_for(state.next_prayer_name, adhan_map, state)
            _blit_center(scr, f.split_next, f"Up next: {label}", _c(t, "prayer_name"), W // 2, info_cy)
            if adhan_dt:
                _blit_center(scr, f.row_time, f"Adhan: {_fmt_time(adhan_dt, self._config.clock_format)}", _c(t, "time_text"), W // 3, info_cy + int(H * 0.065))
            iq = state.iqamah_times.get(state.next_prayer_name)
            if iq:
                _blit_center(scr, f.row_time, f"Iqamah: {_fmt_time(iq, self._config.clock_format)}", _c(t, "iqamah_text"), 2 * W // 3, info_cy + int(H * 0.065))

        self._draw_footer(state, W, H)

    # ------------------------------------------------------------------
    # Layout: ambient — giant clock, compact prayer strip
    # ------------------------------------------------------------------

    def _layout_ambient(self, state: DisplayState, W: int, H: int) -> None:
        f, t, scr = self._fonts, self._theme, self._screen

        # Tiny Hijri date at very top
        hijri = _hijri_str(state)
        if hijri:
            _blit_center(scr, f.hijri, hijri, _c(t, "hijri_text"), W // 2, int(H * 0.048))

        # Giant clock — centered in upper half
        time_str = _fmt_clock(state.current_time, self._config.clock_format, self._config.show_seconds)
        _blit_center(scr, f.ambient_clock, time_str, _c(t, "clock_text"), W // 2, int(H * 0.300))

        # Gregorian date
        dt   = state.current_time
        greg = f"{dt.strftime('%A')}, {dt.day} {dt.strftime('%B %Y')}"
        _blit_center(scr, f.date, greg, _c(t, "date_text"), W // 2, int(H * 0.555))

        # Thin decorative divider
        pygame.draw.line(scr, _c(t, "divider"),
                         (int(W * 0.25), int(H * 0.608)),
                         (int(W * 0.75), int(H * 0.608)), 1)

        # Next prayer name + adhan + iqamah
        if state.next_prayer_name and state.prayer_times is not None:
            adhan_map = state.prayer_times.as_dict()
            label     = _label_for(state.next_prayer_name, state)
            adhan_dt  = _adhan_for(state.next_prayer_name, adhan_map, state)
            iq_dt     = _iqamah_for(state.next_prayer_name, state)

            _blit_center(scr, f.split_next, f"Next: {label}", _c(t, "prayer_name"), W // 2, int(H * 0.655))

            time_parts = []
            if adhan_dt:
                time_parts.append(f"Adhan {_fmt_time(adhan_dt, self._config.clock_format)}")
            if iq_dt:
                time_parts.append(f"Iqamah {_fmt_time(iq_dt, self._config.clock_format)}")
            if time_parts:
                _blit_center(scr, f.row_time, "   ·   ".join(time_parts), _c(t, "time_text"), W // 2, int(H * 0.715))

            # Countdown
            if state.next_prayer_adhan is not None:
                now_live       = datetime.now(tz=state.next_prayer_adhan.tzinfo)
                remaining_secs = max(0.0, (state.next_prayer_adhan - now_live).total_seconds())
                live_cd        = timedelta(seconds=remaining_secs)
            else:
                live_cd = state.countdown
            remaining_frac = 1.0 - state.interval_progress
            pct    = int(remaining_frac * 100)
            cd_str = f"{_fmt_countdown(live_cd)}  ·  {pct}%"
            _blit_center(scr, f.split_cd, cd_str, _c(t, "countdown_text"), W // 2, int(H * 0.775))

        # Footer: progress bar only (countdown text shown above)
        footer_y = int(H * _FOOTER_TOP)
        amb_frac = 1.0 - state.interval_progress
        amb_bar_h = max(16, int(H * 0.040))
        amb_bar_w = int(W * amb_frac)
        pygame.draw.rect(scr, _c(t, "countdown_bg"), (0, footer_y, W, H - footer_y))
        pygame.draw.rect(scr, _c(t, "row_bg_odd"),   (0, footer_y, W, amb_bar_h))
        if amb_bar_w > 0:
            pygame.draw.rect(scr, _c(t, "highlight_bg"), (W - amb_bar_w, footer_y, amb_bar_w, amb_bar_h))

    # ------------------------------------------------------------------
    # Layout: sunclock — circular 24-hour day/night clock with info panel
    # ------------------------------------------------------------------

    def _layout_sunclock(self, state: DisplayState, W: int, H: int) -> None:
        """Sun clock: left info panel + right circular 24-hour clock face.

        Day sector (sunrise → maghrib) rendered as sky blue; rest is dark navy.
        Left panel shows current time, dates, next prayer, and countdown.
        """
        f, t, scr = self._fonts, self._theme, self._screen

        _NIGHT_SKY = (15, 20, 50)
        _DAY_SKY   = (100, 160, 220)

        dt = state.current_time

        # ── Left info panel ───────────────────────────────────────────────
        lp_cx = int(W * 0.21)

        time_str = _fmt_clock(dt, self._config.clock_format, self._config.show_seconds)
        _blit_center(scr, f.split_clock, time_str, _c(t, "clock_text"), lp_cx, int(H * 0.20))

        greg = f"{dt.strftime('%A')}, {dt.day} {dt.strftime('%B %Y')}"
        _blit_center(scr, f.date, greg, _c(t, "date_text"), lp_cx, int(H * 0.36))

        hijri = _hijri_str(state)
        if hijri:
            _blit_center(scr, f.hijri, hijri, _c(t, "hijri_text"), lp_cx, int(H * 0.41))

        pygame.draw.line(scr, _c(t, "divider"),
                         (int(W * 0.04), int(H * 0.465)),
                         (int(W * 0.38), int(H * 0.465)), 1)

        if state.next_prayer_name and state.prayer_times is not None:
            adhan_map_info = state.prayer_times.as_dict()
            label          = _label_for(state.next_prayer_name, state)
            adhan_dt_info  = _adhan_for(state.next_prayer_name, adhan_map_info, state)
            iq_dt_info     = _iqamah_for(state.next_prayer_name, state)

            _blit_center(scr, f.split_next, f"Next: {label}",
                         _c(t, "prayer_name"), lp_cx, int(H * 0.53))

            time_parts: list[str] = []
            if adhan_dt_info:
                time_parts.append(_fmt_time(adhan_dt_info, self._config.clock_format))
            if iq_dt_info:
                time_parts.append(f"Iqm {_fmt_time(iq_dt_info, self._config.clock_format)}")
            if time_parts:
                _blit_center(scr, f.row_time, "  ·  ".join(time_parts),
                             _c(t, "time_text"), lp_cx, int(H * 0.61))

            if state.next_prayer_adhan is not None:
                now_live       = datetime.now(tz=state.next_prayer_adhan.tzinfo)
                remaining_secs = max(0.0, (state.next_prayer_adhan - now_live).total_seconds())
                live_cd        = timedelta(seconds=remaining_secs)
            else:
                live_cd = state.countdown
            remaining_frac = 1.0 - state.interval_progress
            pct    = int(remaining_frac * 100)
            cd_str = f"{_fmt_countdown(live_cd)}  ·  {pct}%"
            _blit_center(scr, f.split_cd, cd_str, _c(t, "countdown_text"), lp_cx, int(H * 0.73))

        # Thin vertical divider between panels
        pygame.draw.line(scr, _c(t, "divider"),
                         (int(W * 0.42), int(H * 0.05)),
                         (int(W * 0.42), int(H * 0.80)), 1)

        # ── Right panel: circular clock ───────────────────────────────────
        cx    = int(W * 0.72)
        cy    = int(H * 0.43)
        r     = int(min(W * 0.23, H * 0.30))
        dot_r = int(H * 0.016)

        def _pray_angle(dt2: datetime) -> float:
            secs = dt2.hour * 3600 + dt2.minute * 60
            return (secs / 86400) * 2 * math.pi - math.pi / 2

        # Night fill (full circle)
        pygame.draw.circle(scr, _NIGHT_SKY, (cx, cy), r)

        if state.prayer_times is not None:
            adhan_map = state.prayer_times.as_dict()

            # Day sector: sunrise → maghrib (sky blue wedge)
            srise_dt = adhan_map.get("sunrise")
            sset_dt  = adhan_map.get("maghrib")
            if srise_dt and sset_dt:
                a0 = _pray_angle(srise_dt)
                a1 = _pray_angle(sset_dt)
                if a1 < a0:
                    a1 += 2 * math.pi
                n_steps = 80
                day_pts: list[tuple[int, int]] = [(cx, cy)]
                for si in range(n_steps + 1):
                    ang = a0 + (a1 - a0) * si / n_steps
                    day_pts.append((cx + int(r * math.cos(ang)),
                                    cy + int(r * math.sin(ang))))
                if len(day_pts) >= 3:
                    pygame.draw.polygon(scr, _DAY_SKY, day_pts)

            # Hour tick marks (inside rim)
            for h in range(0, 24, 2):
                ang   = (h / 24) * 2 * math.pi - math.pi / 2
                inner = r - int(H * 0.022)
                outer = r - int(H * 0.004)
                x0 = cx + int(inner * math.cos(ang))
                y0 = cy + int(inner * math.sin(ang))
                x1 = cx + int(outer * math.cos(ang))
                y1 = cy + int(outer * math.sin(ang))
                pygame.draw.line(scr, (200, 200, 200), (x0, y0), (x1, y1), 1)

            # Highlighted wedge: last past prayer → next prayer
            now_secs  = dt.hour * 3600 + dt.minute * 60
            past_list = [(n, adhan_map[n]) for n in PRAYER_ORDER
                         if n in adhan_map
                         and adhan_map[n].hour * 3600 + adhan_map[n].minute * 60 <= now_secs]
            next_list = [(n, adhan_map[n]) for n in PRAYER_ORDER
                         if n in adhan_map
                         and adhan_map[n].hour * 3600 + adhan_map[n].minute * 60 > now_secs]
            if past_list and next_list:
                a0 = _pray_angle(past_list[-1][1])
                a1 = _pray_angle(next_list[0][1])
                n_steps = max(12, int(abs(a1 - a0) * 30))
                if n_steps > 0:
                    hl_surf = pygame.Surface((r * 2 + 4, r * 2 + 4), pygame.SRCALPHA)
                    hc      = _c(t, "highlight_bg")
                    hl_pts  = [(r + 2, r + 2)]
                    for si in range(n_steps + 1):
                        ang = a0 + (a1 - a0) * si / n_steps
                        hl_pts.append((r + 2 + int(r * math.cos(ang)),
                                       r + 2 + int(r * math.sin(ang))))
                    if len(hl_pts) >= 3:
                        pygame.draw.polygon(hl_surf, (hc[0], hc[1], hc[2], 60), hl_pts)
                    scr.blit(hl_surf, (cx - r - 2, cy - r - 2))

            # Current-time spoke
            now_ang = (now_secs / 86400) * 2 * math.pi - math.pi / 2
            spoke_x = cx + int(r * math.cos(now_ang))
            spoke_y = cy + int(r * math.sin(now_ang))
            pygame.draw.line(scr, _c(t, "countdown_text"), (cx, cy), (spoke_x, spoke_y), 3)
            pygame.draw.circle(scr, _c(t, "countdown_text"), (cx, cy), int(H * 0.012))

            # Prayer dots + labels (outside rim)
            lbl_offset = r + int(H * 0.038)
            for name in PRAYER_ORDER:
                dt2 = _adhan_for(name, adhan_map, state)
                if dt2 is None:
                    continue
                ang     = _pray_angle(dt2)
                is_next = name == state.next_prayer_name
                dot_col = _c(t, "highlight_text") if is_next else _c(t, "time_text")
                dr      = dot_r + 3 if is_next else dot_r
                dpx     = cx + int(r * 0.87 * math.cos(ang))
                dpy     = cy + int(r * 0.87 * math.sin(ang))
                pygame.draw.circle(scr, (200, 200, 200), (dpx, dpy), dr + 2)
                pygame.draw.circle(scr, dot_col, (dpx, dpy), dr)
                lx = cx + int(lbl_offset * math.cos(ang))
                ly = cy + int(lbl_offset * math.sin(ang))
                _blit_center(scr, f.col_header, _label_for(name, state), dot_col, lx, ly)

        # Circle outline
        pygame.draw.circle(scr, _c(t, "divider"), (cx, cy), r, 3)

        # ── Footer: progress bar only (countdown shown in left panel) ─────
        footer_y  = int(H * _FOOTER_TOP)
        bar_frac  = 1.0 - state.interval_progress
        bar_h     = max(16, int(H * 0.040))
        bar_w     = int(W * bar_frac)
        pygame.draw.rect(scr, _c(t, "countdown_bg"), (0, footer_y, W, H - footer_y))
        pygame.draw.rect(scr, _c(t, "row_bg_odd"),   (0, footer_y, W, bar_h))
        if bar_w > 0:
            pygame.draw.rect(scr, _c(t, "highlight_bg"), (W - bar_w, footer_y, bar_w, bar_h))

    # ------------------------------------------------------------------
    # Layout: terminal — green phosphor CRT hacker display
    # ------------------------------------------------------------------

    def _layout_terminal(self, state: DisplayState, W: int, H: int) -> None:
        scr = self._screen
        f   = self._fonts

        _BG      = (6, 14, 6)
        _GREEN   = (0, 230, 70)
        _DIM     = (0, 100, 35)
        _HL_BG   = (0, 40, 0)
        _HL_TEXT = (180, 255, 180)

        scr.fill(_BG)

        dt = state.current_time

        def _line(text: str, color: Color, cy: int, left_pad: int = int(W * 0.025)) -> None:
            surf = f.date.render(text, True, color)
            scr.blit(surf, surf.get_rect(left=left_pad, centery=cy))

        # Header lines
        loc_str  = f"$ prayer_times --today --method ISNA"
        date_str = f"[{dt.strftime('%A %d %B %Y')}]"
        hijri    = _hijri_str(state)
        hijri_str = f"  {hijri}" if hijri else ""

        _line(loc_str,  _DIM,   int(H * 0.058))
        clock_str = _fmt_clock(dt, self._config.clock_format, self._config.show_seconds)
        _blit_right(scr, f.date, clock_str, _GREEN, int(W * 0.975), int(H * 0.058))
        _line(date_str + hijri_str, _DIM, int(H * 0.100))
        pygame.draw.line(scr, _DIM, (int(W * 0.025), int(H * 0.130)), (int(W * 0.975), int(H * 0.130)), 1)

        # Column headers
        col_name_x  = int(W * 0.035)
        col_adhan_x = int(W * 0.470)
        col_iq_x    = int(W * 0.720)
        _blit_left(scr, f.date, "PRAYER",  _GREEN, col_name_x,  int(H * 0.158))
        _blit_left(scr, f.date, "ADHAN",   _GREEN, col_adhan_x, int(H * 0.158))
        _blit_left(scr, f.date, "IQAMAH",  _GREEN, col_iq_x,    int(H * 0.158))
        pygame.draw.line(scr, _DIM, (int(W * 0.025), int(H * 0.180)), (int(W * 0.975), int(H * 0.180)), 1)

        if state.prayer_times is not None:
            adhan_map    = state.prayer_times.as_dict()
            row_area_top = int(H * 0.195)
            row_h        = int((H * 0.730 - H * 0.195) / len(PRAYER_ORDER))

            for i, name in enumerate(PRAYER_ORDER):
                row_y   = row_area_top + i * row_h
                row_cy  = row_y + row_h // 2
                is_next = name == state.next_prayer_name
                label   = f"[{_label_for(name, state).upper()}]"
                adhan_dt = _adhan_for(name, adhan_map, state)
                iq_dt    = _iqamah_for(name, state)
                adhan_s  = _fmt_time(adhan_dt, self._config.clock_format) if adhan_dt else "-----"
                iq_s     = _fmt_time(iq_dt,    self._config.clock_format) if iq_dt    else "-----"

                if is_next:
                    pygame.draw.rect(scr, _HL_BG, (int(W * 0.018), row_y + 2, int(W * 0.964), row_h - 4))
                    tc = _HL_TEXT
                else:
                    tc = _GREEN

                _blit_left(scr, f.date, label,   tc, col_name_x,  row_cy)
                _blit_left(scr, f.date, adhan_s, tc, col_adhan_x, row_cy)
                _blit_left(scr, f.date, iq_s,    tc, col_iq_x,    row_cy)

        # Separator + prompt
        sep_y   = int(H * 0.745)
        pygame.draw.line(scr, _DIM, (int(W * 0.025), sep_y), (int(W * 0.975), sep_y), 1)

        prompt_y = int(H * 0.788)
        if state.next_prayer_name:
            label   = _label_for(state.next_prayer_name, state)
            if state.next_prayer_adhan is not None:
                now_live       = datetime.now(tz=state.next_prayer_adhan.tzinfo)
                remaining_secs = max(0.0, (state.next_prayer_adhan - now_live).total_seconds())
                live_cd        = timedelta(seconds=remaining_secs)
            else:
                live_cd = state.countdown
            cursor  = "\u258c" if int(_time.time() * 2) % 2 == 0 else " "
            prompt  = f"> NEXT: {label.upper()} IN {_fmt_countdown(live_cd).upper()} {cursor}"
            _line(prompt, _GREEN, prompt_y)

        # Linux-style text progress bar: [████████░░░░░░░░] 47%
        footer_y       = int(H * _FOOTER_TOP)
        remaining_frac = 1.0 - state.interval_progress
        pct            = int(remaining_frac * 100)
        bar_chars      = 44
        filled_chars   = int(remaining_frac * bar_chars)
        empty_chars    = bar_chars - filled_chars
        bar_str = f"[{'\u2591' * empty_chars}{'\u2588' * filled_chars}] {pct}%"
        pygame.draw.rect(scr, (4, 10, 4), (0, footer_y, W, H - footer_y))
        bar_cy = footer_y + (H - footer_y) // 2
        _line(bar_str, _GREEN, bar_cy, int(W * 0.025))

        # CRT scanline overlay
        if self._scanline_surface is None or self._scanline_surface.get_size() != (W, H):
            sl = pygame.Surface((W, H), pygame.SRCALPHA)
            for y in range(0, H, 4):
                pygame.draw.line(sl, (0, 0, 0, 55), (0, y), (W, y), 1)
            self._scanline_surface = sl
        scr.blit(self._scanline_surface, (0, 0))
