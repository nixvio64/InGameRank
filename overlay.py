from PySide6.QtWidgets import QApplication, QWidget
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPainter, QColor, QFont, QPen

from utils import (
    FONT_NAME, FONT_SIZE, BASE_SCREEN_W, BASE_SCREEN_H,
    OVERLAY_WIDTH_PCT, MIN_HEIGHT_PCT, HEADER_TOP_PCT, HEADER_ICON_SIZE_PCT,
    DIVIDER_Y_PCT, ROW_START_Y_PCT, ROW_HEIGHT_PCT, INNER_BOTTOM_PADDING_PCT,
    RANK_ICON_SIZE_PCT, DIVISION_HEIGHT_PCT, DIVISION_GAP_PCT,
    MAX_VISIBLE_PLAYERS, FADE_STEP_IN, FADE_STEP_OUT,
    state, tracker_cache,
    PLAYLIST_IMAGE_MAP,
    get_pixmap, get_platform_tag, get_div_color_id, shorten_rank, get_total_matches,
)
from config import config
from controllers import get_rl_window_rect, is_rl_focused, is_hotkey_pressed


class Overlay(QWidget):
    def __init__(self):
        super().__init__()
        self.screen_geo = self._get_geometry()
        self.metrics = self._build_metrics()
        self.W = self.metrics["overlay_w"]
        self.H = self.metrics["min_h"]
        self.x_pos = self._center_x()

        self._opacity: float = 0.0
        self._fade_target: float = 0.0

        self.setWindowOpacity(0.0)
        self.setGeometry(self.x_pos, self._bottom_y(self.H), self.W, self.H)

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.WindowTransparentForInput |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._check_visibility)
        self.timer.start(50)

    # layout math

    def _screen_w(self, pct: float) -> int:
        return max(1, round(self.screen_geo.width() * (pct / 100.0)))

    def _screen_h(self, pct: float) -> int:
        return max(1, round(self.screen_geo.height() * (pct / 100.0)))

    def _window_w(self, overlay_w: int, pct: float) -> int:
        return max(1, round(overlay_w * (pct / 100.0)))

    def _build_metrics(self) -> dict:
        overlay_w = self._screen_w(OVERLAY_WIDTH_PCT)
        scale = min(self.screen_geo.width() / BASE_SCREEN_W, self.screen_geo.height() / BASE_SCREEN_H)

        metrics = {
            "overlay_w": overlay_w,
            "min_h": self._screen_h(MIN_HEIGHT_PCT),
            "header_top": self._screen_h(HEADER_TOP_PCT),
            "header_icon": self._screen_h(HEADER_ICON_SIZE_PCT),
            "divider_y": self._screen_h(DIVIDER_Y_PCT),
            "divider_margin_x": self._window_w(overlay_w, 1.79),
            "row_start": self._screen_h(ROW_START_Y_PCT),
            "row_h": self._screen_h(ROW_HEIGHT_PCT),
            "text_baseline": round(self._screen_h(ROW_HEIGHT_PCT) * 0.67),
            "inner_bottom_pad": self._screen_h(INNER_BOTTOM_PADDING_PCT),
            "corner_radius": self._screen_h(0.74),
            "best_col":    self._window_w(overlay_w, 2.3),
            "div1_x":      self._window_w(overlay_w, 16.0),
            "rank_col":    self._window_w(overlay_w, 17.5),
            "div2_x":      self._window_w(overlay_w, 32.0),
            "casual_col":  self._window_w(overlay_w, 33.5),
            "div3_x":      self._window_w(overlay_w, 41.5),
            "matches_col": self._window_w(overlay_w, 43.0),
            "div4_x":      self._window_w(overlay_w, 51.5),
            "name_col":    self._window_w(overlay_w, 53.0),

            "rank_icon": self._screen_h(RANK_ICON_SIZE_PCT),
            "rank_icon_gap": self._window_w(overlay_w, 1.05),
            "rank_fallback_w": self._window_w(overlay_w, 6.00),
            "unranked_text_offset": self._window_w(overlay_w, 6.00),
            "division_h": self._screen_h(DIVISION_HEIGHT_PCT),
            "division_gap": self._screen_h(DIVISION_GAP_PCT),
            "division_next_pad": self._window_w(overlay_w, 1.05),
            "font_size": max(8, round(FONT_SIZE * scale)),
        }
        return metrics

    def _get_geometry(self):
        from PySide6.QtCore import QRect
        rect = get_rl_window_rect()
        if rect:
            l, t, r, b = rect
            return QRect(l, t, r - l, b - t)
        return QApplication.primaryScreen().geometry()

    def _refresh_display_metrics(self):
        current_geo = self._get_geometry()
        if current_geo != self.screen_geo:
            self.screen_geo = current_geo
            self.metrics = self._build_metrics()
            self.W = self.metrics["overlay_w"]
            self.x_pos = self._center_x()

    def _center_x(self) -> int:
        return self.screen_geo.x() + ((self.screen_geo.width() - self.W) // 2)

    def _bottom_y(self, height: int) -> int:
        return self.screen_geo.y() + self.screen_geo.height() - height - 20

    # visibility

    def _check_visibility(self):
        self._refresh_display_metrics()

        with state["lock"]:
            in_match = state["in_match"]
            num_players = len(state["players"])

        visible_players = min(num_players, MAX_VISIBLE_PLAYERS)
        target_h = max(
            self.metrics["min_h"],
            self.metrics["row_start"] + (visible_players * self.metrics["row_h"]) + self.metrics["inner_bottom_pad"],
        )

        if self.width() != self.W or self.height() != target_h:
            self.setGeometry(self.x_pos, self._bottom_y(target_h), self.W, target_h)

        should_show = is_rl_focused() and is_hotkey_pressed() and in_match and num_players > 0

        if should_show:
            self._fade_target = 1.0
            if self._opacity < self._fade_target:
                self._opacity = min(1.0, self._opacity + FADE_STEP_IN)
        else:
            self._fade_target = 0.0
            if self._opacity > self._fade_target:
                self._opacity = max(0.0, self._opacity - FADE_STEP_OUT)

        if self._opacity > 0.0 and not self.isVisible():
            self.show()

        self.setWindowOpacity(self._opacity)

        if self._opacity <= 0.0 and self.isVisible():
            self.hide()

        if self._opacity > 0.0:
            self.update()

    # division drawing

    def division_stack_height(self, tier_id: int) -> int:
        if tier_id >= 22 or tier_id <= 0:
            return 0
        color_id = get_div_color_id(tier_id)
        pm_filled = get_pixmap("Divisions", f"{color_id}.png", None, self.metrics["division_h"])
        pm_blank  = get_pixmap("Divisions", "0.png",            None, self.metrics["division_h"])
        sample_h = max(
            pm_filled.height() if pm_filled else self.metrics["division_h"],
            pm_blank.height()  if pm_blank  else self.metrics["division_h"],
        )
        return (sample_h * 4) + (self.metrics["division_gap"] * 3)

    def draw_stacked_divisions(self, painter, x, y, tier_id, div_level):
        if div_level <= 0 or tier_id >= 22 or tier_id <= 0:
            return 0
        color_id = get_div_color_id(tier_id)
        pm_filled = get_pixmap("Divisions", f"{color_id}.png", None, self.metrics["division_h"])
        pm_blank  = get_pixmap("Divisions", "0.png",            None, self.metrics["division_h"])

        if not pm_filled or not pm_blank:
            painter.setPen(QColor(255, 255, 255))
            painter.drawText(x, y + self.metrics["text_baseline"], f"D{div_level}")
            return self.metrics["division_h"] * 4 + self.metrics["division_next_pad"]

        current_y = y
        max_w = 0
        for i in range(4, 0, -1):
            pm = pm_filled if i <= div_level else pm_blank
            painter.drawPixmap(x, current_y, pm)
            current_y += pm.height() + self.metrics["division_gap"]
            max_w = max(max_w, pm.width())

        return max_w + self.metrics["division_next_pad"]

    # paint

    def paintEvent(self, event):
        with state["lock"]:
            players = sorted(list(state["players"]), key=lambda p: p.get("TeamNum", -1), reverse=True)[:MAX_VISIBLE_PLAYERS]

        team_counts = {0: 0, 1: 0}
        for p in players:
            t = p.get("TeamNum")
            if t in team_counts:
                team_counts[t] += 1

        max_t = max(team_counts.values()) if team_counts else 0
        if max_t <= 1:
            playlist_id = 10
        elif max_t == 2:
            playlist_id = 11
        elif max_t >= 3:
            playlist_id = 13
        else:
            playlist_id = -1

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        # background
        painter.setBrush(QColor(17, 24, 39, 216))
        painter.setPen(QPen(QColor(45, 55, 72), 1))
        painter.drawRoundedRect(
            0, 0,
            self.width() - 1, self.height() - 1,
            self.metrics["corner_radius"], self.metrics["corner_radius"],
        )

        # column labels
        header_text_y = self.metrics["divider_y"] // 2 + self.metrics["font_size"] // 2
        label_font = QFont(FONT_NAME, max(7, self.metrics["font_size"] - 1))
        painter.setFont(label_font)
        painter.setPen(QColor(100, 116, 139))
        painter.drawText(self.metrics["best_col"], header_text_y, "Best Rank")

        ranked_text = "Ranked "
        painter.drawText(self.metrics["rank_col"], header_text_y, ranked_text)

        pl_target = int(self.metrics["header_icon"] * 0.8)
        pl_pm = get_pixmap("Playlists", PLAYLIST_IMAGE_MAP.get(playlist_id, "0.png"), pl_target, pl_target)
        if pl_pm:
            fm = painter.fontMetrics()
            ranked_text_w = fm.horizontalAdvance(ranked_text)
            px = self.metrics["rank_col"] + ranked_text_w
            py = self.metrics["divider_y"] // 2 - pl_pm.height() // 2
            painter.drawPixmap(px, py, pl_pm)

        painter.drawText(self.metrics["casual_col"], header_text_y, "Casual")
        painter.drawText(self.metrics["matches_col"], header_text_y, "Matches")

        # horizontal divider
        painter.setPen(QPen(QColor(45, 55, 72), 1))
        painter.drawLine(
            self.metrics["divider_margin_x"], self.metrics["divider_y"],
            self.width() - self.metrics["divider_margin_x"], self.metrics["divider_y"],
        )

        # vertical dividers
        div_top = self.metrics["divider_y"]
        div_bottom = self.height()
        painter.drawLine(self.metrics["div1_x"], div_top, self.metrics["div1_x"], div_bottom)
        painter.drawLine(self.metrics["div2_x"], div_top, self.metrics["div2_x"], div_bottom)
        painter.drawLine(self.metrics["div3_x"], div_top, self.metrics["div3_x"], div_bottom)
        painter.drawLine(self.metrics["div4_x"], div_top, self.metrics["div4_x"], div_bottom)

        col_best    = self.metrics["best_col"]
        col_ranked  = self.metrics["rank_col"]
        col_casual  = self.metrics["casual_col"]
        col_matches = self.metrics["matches_col"]
        col_name    = self.metrics["name_col"]
        font_regular = QFont(FONT_NAME, self.metrics["font_size"])

        for i, p in enumerate(players):
            y = self.metrics["row_start"] + (i * self.metrics["row_h"])
            if y + self.metrics["row_h"] > self.height():
                break

            text_y = y + self.metrics["text_baseline"]
            team = p.get("TeamNum", -1)
            color = QColor(255, 160, 64) if team == 1 else QColor(79, 195, 247)
            pid = p.get("PrimaryId", "")
            platform_tag = get_platform_tag(pid)

            painter.setFont(font_regular)

            cache_entry = tracker_cache.get(pid, {})
            stats = cache_entry.get("stats", {})

            if not pid:
                pass
            elif cache_entry.get("fetching") and not stats:
                painter.setPen(QColor(209, 213, 219))
                painter.drawText(col_ranked, text_y, "Loading...")
            elif cache_entry.get("error") and not stats:
                painter.setPen(QColor(209, 213, 219))
                painter.drawText(col_ranked, text_y, "API Error")
            elif stats:
                painter.setPen(QColor(209, 213, 219))

                # best rank
                best_playlist = None
                best_tier = -1
                best_div = -1

                for p_id in [10, 11, 13]:
                    rnk = stats.get(p_id)
                    if rnk:
                        t = rnk["tier_id"]
                        d = rnk["div_id"]
                        if t > best_tier or (t == best_tier and d > best_div):
                            best_tier = t
                            best_div = d
                            best_playlist = p_id

                if best_tier >= 0:
                    bx = col_best

                    m_size = int(self.metrics["rank_icon"] * 0.75)
                    if best_playlist is not None:
                        m_pm = get_pixmap("Playlists", PLAYLIST_IMAGE_MAP.get(best_playlist, "0.png"), m_size, m_size)
                        if m_pm:
                            icon_y = y + ((self.metrics["row_h"] - m_pm.height()) // 2)
                            painter.drawPixmap(bx, icon_y, m_pm)
                        bx += m_size + self.metrics["rank_icon_gap"]

                    t_size = self.metrics["rank_icon"]
                    t_pm = get_pixmap("Tiers", f"{best_tier}.png", t_size, t_size)
                    if t_pm:
                        icon_y = y + ((self.metrics["row_h"] - t_pm.height()) // 2)
                        painter.drawPixmap(bx, icon_y, t_pm)
                        bx += t_size + self.metrics["rank_icon_gap"]
                    else:
                        painter.drawText(bx, text_y, "Unranked")
                        bx += self.metrics["rank_fallback_w"]

                    div_stack_h = self.division_stack_height(best_tier)
                    div_y = y + ((self.metrics["row_h"] - div_stack_h) // 2)
                    bx += self.draw_stacked_divisions(painter, bx, div_y, best_tier, best_div)

                # current playlist ranked
                rnk_data = stats.get(playlist_id)
                if rnk_data:
                    tier_id = rnk_data["tier_id"]
                    div_id  = rnk_data["div_id"]
                    mmr     = rnk_data["mmr"]

                    rx = col_ranked
                    t_size = self.metrics["rank_icon"]
                    t_pm = get_pixmap("Tiers", f"{tier_id}.png", t_size, t_size)
                    if t_pm:
                        icon_y = y + ((self.metrics["row_h"] - t_pm.height()) // 2)
                        painter.drawPixmap(rx + ((t_size - t_pm.width()) // 2), icon_y, t_pm)
                        rx += t_size + self.metrics["rank_icon_gap"]
                    else:
                        painter.drawText(rx, text_y, shorten_rank(rnk_data["tier_name"]))
                        rx += self.metrics["rank_fallback_w"]

                    div_stack_h = self.division_stack_height(tier_id)
                    div_y = y + ((self.metrics["row_h"] - div_stack_h) // 2)
                    rx += self.draw_stacked_divisions(painter, rx, div_y, tier_id, div_id)
                    painter.drawText(rx, text_y, f"{mmr}")
                else:
                    t_size = self.metrics["rank_icon"]
                    t_pm = get_pixmap("Tiers", "0.png", t_size, t_size)
                    if t_pm:
                        icon_y = y + ((self.metrics["row_h"] - t_pm.height()) // 2)
                        painter.drawPixmap(col_ranked + ((t_size - t_pm.width()) // 2), icon_y, t_pm)
                        painter.drawText(col_ranked + self.metrics["unranked_text_offset"], text_y, "Unranked")

                # casual MMR
                painter.setPen(QColor(160, 160, 160))
                cas_data = stats.get(0)
                if cas_data:
                    painter.drawText(col_casual, text_y, f"{cas_data['mmr']}")

                # total matches
                total_matches = get_total_matches(stats)
                if total_matches > 0:
                    painter.setPen(QColor(160, 160, 160))
                    painter.drawText(col_matches, text_y, f"{total_matches:,}")

            painter.setPen(color)
            painter.drawText(col_name, text_y, f"{platform_tag} {p['Name']}")
