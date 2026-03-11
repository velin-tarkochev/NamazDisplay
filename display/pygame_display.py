"""Pygame fullscreen display — renders the Islamic prayer times table to the TV.

Five selectable layouts (config.display.layout):

  standard    — 3-column table: Prayer | Adhan | Iqamah  (original)
  minimal     — same data, no vertical grid lines, bold typography, accent bar
  transposed  — prayers as columns, rows are Adhan / Iqamah
  split       — left panel: clock + info; right panel: prayer table
  cards       — 5 fard prayers as large rounded cards in a 3+2 grid
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
FARD_ORDER   = ("fajr", "dhuhr", "asr", "maghrib", "isha")

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
_TABLE_BOTTOM    = 0.878
_HEADER_FRAC     = 0.115
_COL_DIVIDER_1   = 0.370
_COL_DIVIDER_2   = 0.640
_COL_NAME_PAD    = 0.045
_FOOTER_TOP      = 0.893
_FOOTER_LABEL_CY = 0.924
_FOOTER_TIME_CY  = 0.960


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
        self.split_clock  = _sys_font(family, fallback, sz(0.130), bold=True)
        self.split_label  = _sys_font(family, fallback, sz(0.046))
        self.split_next   = _sys_font(family, fallback, sz(0.052), bold=True)
        self.split_cd     = _sys_font(family, fallback, sz(0.080), bold=True)


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
        """Countdown footer with progress bar at its top edge."""
        if not state.next_prayer_name:
            return
        f, t, scr = self._fonts, self._theme, self._screen

        footer_y = int(H * _FOOTER_TOP)
        pygame.draw.rect(scr, _c(t, "countdown_bg"), (0, footer_y, W, H - footer_y))

        # Progress bar — thin strip at the very top of the footer
        bar_h = max(5, int(H * 0.011))
        bar_w = int(W * state.interval_progress)
        if bar_w > 0:
            pygame.draw.rect(scr, _c(t, "highlight_bg"), (0, footer_y, bar_w, bar_h))

        prayer_label = _label_for(state.next_prayer_name, state)
        _blit_center(scr, f.footer_label,
                     f"Next: {prayer_label}",
                     _c(t, "countdown_label"), W // 2, int(H * _FOOTER_LABEL_CY))

        pct    = int(state.interval_progress * 100)
        cd_str = f"{_fmt_countdown(state.countdown)}  ·  {pct}%"
        _blit_center(scr, f.footer_time, cd_str, _c(t, "countdown_text"), W // 2, int(H * _FOOTER_TIME_CY))

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

        label_w = int(W * 0.10)
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
                    _blit_center(scr, f.row_time, _fmt_time(dt, self._config.clock_format), tc, col_cx, row_cy)
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

        if state.next_prayer_name:
            prayer_label = _label_for(state.next_prayer_name, state)
            _blit_center(scr, f.split_label, "Next Prayer",
                         _c(t, "countdown_label"), split_x // 2, int(H * 0.57))
            _blit_center(scr, f.split_next, prayer_label,
                         _c(t, "highlight_text"), split_x // 2, int(H * 0.65))
            _blit_center(scr, f.split_cd, _fmt_countdown(state.countdown),
                         _c(t, "countdown_text"), split_x // 2, int(H * 0.75))

            # Progress bar inside left panel
            pct = int(state.interval_progress * 100)
            _blit_center(scr, f.split_label, f"{pct}%",
                         _c(t, "countdown_label"), split_x // 2, int(H * 0.85))
            bar_y        = int(H * 0.89)
            bar_h        = max(5, int(H * 0.012))
            bar_total_w  = int(split_x * 0.78)
            bar_x        = (split_x - bar_total_w) // 2
            pygame.draw.rect(scr, _c(t, "row_bg_odd"), (bar_x, bar_y, bar_total_w, bar_h))
            filled = int(bar_total_w * state.interval_progress)
            if filled > 0:
                pygame.draw.rect(scr, _c(t, "highlight_bg"), (bar_x, bar_y, filled, bar_h))

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
        card_area_bottom = int(H * 0.87)
        card_area_h      = card_area_bottom - card_area_top

        gap       = int(W * 0.025)
        card_h_r1 = int(card_area_h * 0.48)
        card_h_r2 = card_area_h - card_h_r1 - gap

        row1_prayers = FARD_ORDER[:3]
        row2_prayers = FARD_ORDER[3:]
        card_w_3 = (W - gap * 4) // 3
        card_w_2 = (W - gap * 3) // 2

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

        # Row 1: Fajr, Dhuhr, Asr
        row1_cy = card_area_top + card_h_r1 // 2
        for ci, name in enumerate(row1_prayers):
            cx = gap + card_w_3 // 2 + ci * (card_w_3 + gap)
            draw_card(name, cx, row1_cy, card_w_3, card_h_r1)

        # Row 2: Maghrib, Isha (centred)
        row2_cy      = card_area_top + card_h_r1 + gap + card_h_r2 // 2
        total_row2_w = len(row2_prayers) * card_w_2 + (len(row2_prayers) - 1) * gap
        row2_start_x = (W - total_row2_w) // 2
        for ci, name in enumerate(row2_prayers):
            cx = row2_start_x + card_w_2 // 2 + ci * (card_w_2 + gap)
            draw_card(name, cx, row2_cy, card_w_2, card_h_r2)

        self._draw_footer(state, W, H)
