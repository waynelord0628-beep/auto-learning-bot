import json
import os
import sys
import re
import threading
import random
import math
from app import AdminEfficiencyPilot
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFormLayout,
    QFrame,
    QGraphicsBlurEffect,
    QGraphicsDropShadowEffect,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedLayout,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QComboBox,
)
from PySide6.QtCore import (
    Qt,
    QEasingCurve,
    QPoint,
    QPropertyAnimation,
    QSize,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QIcon,
    QPainter,
    QPalette,
    QPixmap,
)
from utils.helpers import get_logger


BASE_DIR = os.path.dirname(__file__)

logger = get_logger()


def icon(name):
    return QIcon(resource_path(f"icons/{name}"))


def resource_path(relative_path):
    if getattr(sys, "frozen", False):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.abspath("."), relative_path)


# =========================
# 粒子轉場效果
# =========================
class ParticleEffect(QWidget):
    """前往宇宙的粒子轉場效果"""

    finished = Signal()  # ⭐ 動畫完成信號

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("background-color: rgba(0, 0, 0, 0.9);")
        self.particles = []
        self.elapsed_time = 0
        self.duration = 800  # 0.8秒

    def showEvent(self, event):
        self.create_particles()
        super().showEvent(event)

        # 定時器更新動畫
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_particles)
        self.timer.start(16)  # ~60fps

    def create_particles(self):
        """創建隨機粒子"""
        num_particles = 150
        center_x = self.width() // 2
        center_y = self.height() // 2

        for _ in range(num_particles):
            # 隨機角度和速度
            angle = random.uniform(0, 2 * math.pi)
            speed = random.uniform(2, 8)

            # 起點在屏幕邊緣
            distance = random.uniform(300, 600)
            start_x = center_x + distance * math.cos(angle)
            start_y = center_y + distance * math.sin(angle)

            # 粒子大小和透明度
            size = random.uniform(2, 6)
            color = random.choice(
                [
                    QColor(100, 200, 255),  # 藍色
                    QColor(150, 220, 255),  # 淡藍
                    QColor(200, 240, 255),  # 淡白藍
                    QColor(255, 255, 255),  # 白色
                ]
            )

            self.particles.append(
                {
                    "x": start_x,
                    "y": start_y,
                    "vx": -math.cos(angle) * speed,
                    "vy": -math.sin(angle) * speed,
                    "size": size,
                    "color": color,
                    "opacity": 1.0,
                }
            )

    def update_particles(self):
        """更新粒子位置和動畫"""
        self.elapsed_time += 16
        progress = min(self.elapsed_time / self.duration, 1.0)

        center_x = self.width() // 2
        center_y = self.height() // 2

        for particle in self.particles:
            # 移動粒子
            particle["x"] += particle["vx"]
            particle["y"] += particle["vy"]

            # 淡出效果
            particle["opacity"] = 1.0 - progress

        self.update()  # 重繪

        # 動畫完成
        if progress >= 1.0:
            self.timer.stop()
            self.finished.emit()

    def paintEvent(self, event):
        """繪製粒子"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        for particle in self.particles:
            color = particle["color"]
            color.setAlpha(int(255 * particle["opacity"]))

            painter.setBrush(color)
            painter.setPen(Qt.NoPen)

            x = particle["x"]
            y = particle["y"]
            size = particle["size"]

            painter.drawEllipse(
                int(x - size / 2), int(y - size / 2), int(size), int(size)
            )

        painter.end()

    def resizeEvent(self, event):
        """視窗大小改變時重新創建粒子"""
        if not self.particles:
            self.create_particles()
        super().resizeEvent(event)


# =========================
# 共用樣式（簡約大氣）
# =========================
GLOBAL_QSS = """
QWidget {
    background-color: transparent;
    color: #111827;
}
#card {
    background-color: rgba(255, 255, 255, 0.1);  /* 半透明 */
    border-radius: 20px;

    border: 1px solid rgba(255, 255, 255, 0.2);  /* 淡白邊 */

    padding: 16px;
}
QPushButton {
    background-color: rgba(255,255,255,0.8);
    color: #111827;
    border-radius: 16px;
    padding: 16px;

    font-size: 16px;
    text-align: left;

    border: none;
}

/* ⭐ hover：不變色，只做浮動感 */
QPushButton:hover {
    background-color: #fef3c7;

    border: 1px solid rgba(0,0,0,0.15);
}

/* 點擊 */
QPushButton:pressed {
    background-color: #fef3c7;

    border: 1px solid rgba(0,0,0,0.25);

    padding-top: 18px;
    padding-bottom: 14px;  /* 反向 → 壓下去 */
}

QPushButton#ghost {
    background-color: transparent;
    border: 1px solid #E5E7EB;
    color: #6B7280;
    border-radius: 8px;
    padding: 6px 12px;
}
QPushButton#ghost:hover {
    border: 1px solid #D1D5DB;
    color: #111827;
}

QComboBox {
    background-color: #F9FAFB;
    border: 1px solid #E5E7EB;
    border-radius: 8px;
    padding: 8px 10px;
    color: #111827;
}
"""


def style_btn(btn):
    btn.setStyleSheet("""
        background-color: rgba(255,255,255,0.25);
        border-radius: 14px;
        padding: 14px;
        font-size: 15px;
        font-family: "Noto Sans TC Rounded", "Microsoft JhengHei";
    """)


def add_hover_effect(btn):
    # ===== 陰影1（貼近，柔）
    shadow1 = QGraphicsDropShadowEffect(btn)
    shadow1.setBlurRadius(25)
    shadow1.setOffset(0, 6)
    shadow1.setColor(QColor(0, 0, 0, 30))

    # ===== 陰影2（遠距，懸浮感）
    shadow2 = QGraphicsDropShadowEffect(btn)
    shadow2.setBlurRadius(60)
    shadow2.setOffset(0, 20)
    shadow2.setColor(QColor(0, 0, 0, 20))

    # ⚠️ Qt 只能套一個 effect → 用 shadow1 當主體
    btn.shadow = shadow1
    btn.setGraphicsEffect(shadow1)

    def enterEvent(event):
        # 👉 浮起來
        btn.move(btn.x(), btn.y() - 4)

        # 👉 陰影拉開（模擬高度）
        btn.shadow.setBlurRadius(45)
        btn.shadow.setOffset(0, 18)
        btn.shadow.setColor(QColor(0, 0, 0, 80))

    def leaveEvent(event):
        # 👉 回來
        btn.move(btn.x(), btn.y() + 4)

        # 👉 回到貼近狀態
        btn.shadow.setBlurRadius(25)
        btn.shadow.setOffset(0, 6)
        btn.shadow.setColor(QColor(0, 0, 0, 30))

    btn.enterEvent = enterEvent
    btn.leaveEvent = leaveEvent


# =========================
# 入口頁
# =========================
class EntryPage(QWidget):
    _ai_verify_signal = Signal(bool, str)

    def __init__(self, on_start):
        super().__init__()
        self._ai_verify_signal.connect(self._on_ai_verify_done)

        self.is_updating = False

        self.on_start = on_start

        self.bg_label = QLabel(self)
        self.bg_label.setPixmap(QPixmap(resource_path("login.png")))
        self.bg_label.setScaledContents(True)
        self.bg_label.lower()  # ⭐ 放到最底層

        # ⭐ 手機螢幕位置（先用這組，之後可微調）
        self.screen_x = 421
        self.screen_y = 132
        self.screen_w = 263
        self.screen_h = 473

        self.account_container = QFrame(self)
        self.account_container.setObjectName("card")
        self.account_container.setGeometry(
            self.screen_x, self.screen_y, self.screen_w, self.screen_h
        )

        # ⭐ 模擬手機內 UI（圓角 + 微透明）
        self.account_container.setStyleSheet("""
            background-color: rgba(255,255,255,0.06);
            border-radius: 24px;
        """)

        # ⭐ 手機內 layout（這是關鍵）
        self.inner_layout = QVBoxLayout(self.account_container)
        self.inner_layout.setContentsMargins(20, 8, 20, 20)
        self.inner_layout.setSpacing(24)
        self.inner_layout.setAlignment(Qt.AlignTop)

        # 加標題（像 App）
        title = QLabel("行政程序領航員")
        title.setStyleSheet("""
            font-family: "Noto Sans TC Rounded";
            color: rgba(0,0,0,0.6);
            font-size: 16px;
            font-weight: 600;
            margin-left: 24px;
            margin-top: -4px;
            letter-spacing: 1px;
        """)

        self.inner_layout.addWidget(title)

        # 做「卡片式按鈕」（核心）
        self.combo = QComboBox()
        self.combo.addItem("請選擇人員")  # ⭐ Step1：預設提示
        font = QFont()
        if font.pointSize() <= 0:
            font.setPointSize(10)  # 🔥 固定字體大小
        self.combo.setFont(font)
        self.combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.combo.setMinimumHeight(50)

        self.combo.setStyleSheet("""
        QComboBox {
            background-color: rgba(255,255,255,0.18);
            border-radius: 16px;
            border: 1px solid rgba(255,255,255,0.25);

            padding: 14px 16px;

            font-size: 15px;
            color: #111827;
        }

        QComboBox:hover {
            background-color: rgba(255,255,255,0.35);
        }

        QComboBox::drop-down {
            border: none;
            background: transparent;   /* 🔥 關鍵 */
            width: 32px;
        }

        /* 箭頭（關鍵） */
        QComboBox::down-arrow {
            image: url(icons/down.png);   /* 你可以先不放 */
            background: transparent;
            width: 16px;
            height: 16px;
        }

        /* 下拉選單 */
        QComboBox QAbstractItemView {
            background-color: rgba(255,255,255,0.95);
            border-radius: 10px;
            padding: 6px;
            selection-background-color: rgba(0,0,0,0.08);
        }
        """)
        self.combo.activated.connect(self._on_combo_activated)

        self.btn_add = QPushButton("   新增帳號")
        self.btn_edit = QPushButton("   編輯帳號")
        self.btn_delete = QPushButton("   刪除帳號")
        self.btn_setting = QPushButton("   設定執行方式")

        self.btn_add.setIcon(icon("add.png"))
        self.btn_edit.setIcon(icon("edit.png"))
        self.btn_delete.setIcon(icon("delete.png"))
        self.btn_setting.setIcon(icon("settings.png"))

        self.inner_layout.addWidget(self.combo)

        for btn in [self.btn_add, self.btn_edit, self.btn_delete, self.btn_setting]:
            btn.setMinimumHeight(56)
            btn.setFont(font)
            btn.setIconSize(QSize(24, 24))
            btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            add_hover_effect(btn)
            btn.setLayoutDirection(Qt.LeftToRight)
            btn.setStyleSheet("""
                background-color: rgba(255,255,255,0.25);
                border-radius: 16px;
                border: 1px solid rgba(255,255,255,0.25);
                padding: 14px 16px;
                font-size: 15px;
                text-align: left;
            """)
            self.inner_layout.addWidget(btn)

        self.btn_add.clicked.connect(self.add_account)
        self.btn_edit.clicked.connect(self.edit_account)
        self.btn_delete.clicked.connect(self.delete_account)
        self.btn_setting.clicked.connect(self.edit_settings)

        # ===== 操作按鈕 =====

        self.config = self.load_config()
        self.accounts = self.config.get("accounts", [])

        self.is_updating = True

        self.combo.blockSignals(True)
        self.refresh_combo()
        self.combo.setCurrentIndex(0)

        self.combo.blockSignals(False)

        # ===== 遮罩（放在 panel 前）=====

        self.overlay = QWidget(self)
        self.overlay.setStyleSheet("""
            background-color: rgba(0,0,0,0.25);
        """)
        self.overlay.hide()

        # ⭐ 改成自定義點擊事件
        def on_overlay_clicked(event):
            # 隱藏 panel 和 confirm_box
            if hasattr(self, "panel"):
                if self.panel.isVisible():
                    self.panel.hide()
                # ⭐ 確認框也隱藏
                if (
                    hasattr(self.panel, "confirm_box")
                    and self.panel.confirm_box.isVisible()
                ):
                    self.panel.confirm_box.hide()
            self.overlay.hide()

        self.overlay.mousePressEvent = on_overlay_clicked

        # 版本號（左下角）
        from app import AdminEfficiencyPilot as _AEP
        self._version_label = QLabel(_AEP.VERSION, self)
        self._version_label.setStyleSheet(
            "color: rgba(255,255,255,0.45); font-size: 11px; background: transparent;"
        )
        self._version_label.adjustSize()
        self._version_label.raise_()

        # 更新圖示（右下角）
        self._update_btn = QPushButton(self)
        self._update_btn.setFixedSize(52, 52)
        self._update_btn.setToolTip("檢查更新")
        self._update_btn.setCursor(Qt.PointingHandCursor)
        import os as _os, sys as _sys
        if getattr(_sys, "frozen", False):
            _base = _sys._MEIPASS
        else:
            _base = _os.path.dirname(_os.path.abspath(__file__))
        _icon_path = _os.path.join(_base, "icons", "settings.png")
        if _os.path.exists(_icon_path):
            self._update_btn.setIcon(QIcon(_icon_path))
            self._update_btn.setIconSize(QSize(34, 34))
        self._update_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: none;
                border-radius: 26px;
            }
            QPushButton:hover {
                background: rgba(0,0,0,0.12);
            }
        """)
        self._update_btn.clicked.connect(lambda: self._on_update_btn_clicked())
        self._update_btn.raise_()
        self._has_update = False
        self._latest_update_info = None  # (latest, changelog, url)
        # 初始定位
        QTimer.singleShot(0, lambda: self._update_btn.move(self.width() - self._update_btn.width() - 20, 6))

    def _on_update_btn_clicked(self):
        """手動點更新圖示：有新版直接跳視窗，沒有則重新觸發 MainWindow 檢查"""
        mw = self.window()
        if hasattr(mw, "_handle_update_btn"):
            mw._handle_update_btn()

    def _on_combo_activated(self):
        # ⭐ 選擇後立即隱藏下拉選單
        self.combo.hidePopup()
        # ⭐ 檢查是否有 panel 開啟
        if hasattr(self, "panel") and self.panel.isVisible():
            return  # 如果有 panel，不執行
        # 延遲執行 handle_start，避免卡頓
        QTimer.singleShot(100, self.handle_start)

    def add_account(self):
        panel = AddAccountPanel(self)
        panel.btn_ok.clicked.connect(self.save_account)
        panel.btn_cancel.clicked.connect(self.close_panel)
        self._show_panel(panel)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.bg_label.setGeometry(0, 0, self.width(), self.height())
        if hasattr(self, "_version_label"):
            lw = self._version_label.width()
            lh = self._version_label.height()
            self._version_label.move(8, self.height() - lh - 6)
        if hasattr(self, "_update_btn"):
            bw = self._update_btn.width()
            self._update_btn.move(self.width() - bw - 20, 6)

    def delete_account(self):
        if not self.accounts:
            return

        panel = DeleteAccountPanel(self)
        panel.selector.clear()
        for acc in self.accounts:
            login_display = "我的E政府" if acc["login_type"] == "egov" else "eCPA"
            panel.selector.addItem(f"{acc['name']}（{login_display}）")

        panel.btn_ok.clicked.connect(self.show_delete_confirm)
        panel.btn_cancel.clicked.connect(self.close_panel)
        self._show_panel(panel)

    def show_delete_confirm(self):
        selected = self.panel.selector.currentText()
        self.panel.confirm_label.setText(f"確定刪除 {selected}？")

        # ⭐ 設定確認框位置（panel 下方，貼近）
        panel_pos = self.panel.pos()
        confirm_x = panel_pos.x()
        confirm_y = panel_pos.y() + self.panel.height() + 8
        start_y = confirm_y + 40

        self.panel.confirm_box.move(confirm_x, start_y)
        self.panel.confirm_box.show()
        self.panel.confirm_box.raise_()

        # ⭐ 動畫滑入（從下方滑上來）
        confirm_anim = QPropertyAnimation(self.panel.confirm_box, b"pos")
        confirm_anim.setDuration(300)
        confirm_anim.setStartValue(QPoint(confirm_x, start_y))
        confirm_anim.setEndValue(QPoint(confirm_x, confirm_y))
        confirm_anim.start()

        # ⭐ 改成這樣，檢查是否已連接後再斷開
        try:
            self.panel.confirm_yes.clicked.disconnect()
        except (TypeError, RuntimeError):
            pass

        try:
            self.panel.confirm_no.clicked.disconnect()
        except (TypeError, RuntimeError):
            pass

        # 綁新事件
        self.panel.confirm_yes.clicked.connect(self.confirm_delete)
        self.panel.confirm_no.clicked.connect(lambda: self.panel.confirm_box.hide())

    def confirm_delete(self):
        idx = self.panel.selector.currentIndex()
        if idx < 0:
            return

        self.accounts.pop(idx)
        self.config["accounts"] = self.accounts

        self._save_config()

        self.refresh_combo()

        # ⭐ 修正：完全隱藏，清空 combo 選擇
        self.combo.blockSignals(True)
        self.combo.setCurrentIndex(0)
        self.combo.blockSignals(False)

        self.panel.hide()
        self.panel.confirm_box.hide()
        self.overlay.hide()

    def handle_start(self):
        idx = self.combo.currentIndex()

        if idx == 0:
            return  # ⭐ 選到提示不做事

        # ⭐ 如果有 panel 開啟，則不執行
        if hasattr(self, "panel") and self.panel.isVisible():
            return

        account = self.accounts[idx - 1]  # ⭐ index 對齊
        self.on_start(account)

    def refresh_combo(self):
        self.combo.clear()

        self.combo.addItem("請選擇人員")  # ⭐ 一定要加

        for acc in self.accounts:
            # ⭐ 轉換登入方式的顯示文字
            login_display = "我的E政府" if acc["login_type"] == "egov" else "eCPA"
            self.combo.addItem(f"{acc['name']}（{login_display}）")

        self.combo.setCurrentIndex(0)

    def render_accounts(self, accounts):
        # ❌ 不要再動 layout
        pass

    def load_config(self):
        path = "config.json"

        if not os.path.exists(path):
            # 初始空設定
            data = {"accounts": [], "settings": {}}
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4, ensure_ascii=False)
            return data

        with open(path, "r", encoding="utf-8") as f:
            content = f.read().strip()
            if not content:
                return {"accounts": [], "settings": {}}
            return json.loads(content)

    def _save_config(self) -> bool:
        """統一的設定儲存方法，含錯誤處理"""
        try:
            with open("config.json", "w", encoding="utf-8") as f:
                json.dump(self.config, f, indent=4, ensure_ascii=False)
            return True
        except (OSError, IOError) as e:
            logger.error(f"設定儲存失敗: {e}")
            return False

    def _show_panel(self, panel) -> None:
        """通用：顯示遮罩並以動畫滑入側邊欄"""
        self.panel = panel
        self.overlay.setGeometry(0, 0, self.width(), self.height())
        self.overlay.show()
        self.overlay.raise_()

        self.panel.move(self.width(), 100)
        self.panel.show()
        self.panel.raise_()

        self.anim = QPropertyAnimation(self.panel, b"pos")
        self.anim.setDuration(300)
        self.anim.setStartValue(QPoint(self.width(), 100))
        self.anim.setEndValue(QPoint(self.width() - 360, 100))
        self.anim.start()

    def save_account(self):
        new_data = self.panel.get_data()

        # 簡單檢查
        if not new_data["name"] or not new_data["account"] or not new_data["password"]:
            return

        self.accounts.append(new_data)
        self.config["accounts"] = self.accounts

        self._save_config()

        self.is_updating = True

        self.combo.blockSignals(True)
        self.refresh_combo()
        self.combo.setCurrentIndex(0)
        self.combo.blockSignals(False)

        self.is_updating = False

        self.panel.hide()  # ⭐ 關閉側邊欄
        self.overlay.hide()

    def edit_account(self):
        if not self.accounts:
            return

        panel = AddAccountPanel(self, data=self.accounts[0] if self.accounts else None)
        panel.btn_ok.clicked.connect(self.save_edit)
        panel.btn_cancel.clicked.connect(self.close_panel)
        self._show_panel(panel)

    def save_edit(self):
        new_data = self.panel.get_data()

        if not new_data["name"] or not new_data["account"] or not new_data["password"]:
            return

        # 👉 先簡單：改第一筆
        idx = self.panel.selector.currentIndex()
        self.accounts[idx] = new_data
        self.config["accounts"] = self.accounts

        self._save_config()

        self.refresh_combo()
        self.panel.hide()
        self.overlay.hide()

    def edit_settings(self):
        panel = SettingsPanel(self, data=self.config)
        panel.btn_ok.clicked.connect(self.save_settings)
        panel.btn_cancel.clicked.connect(self.close_panel)
        self._show_panel(panel)

    def save_settings(self):
        settings_data = self.panel.get_data()
        self.config["settings"] = settings_data
        self._save_config()

        ai_key = settings_data.get("ai_api_key", "").strip()
        if ai_key:
            self.panel.show_ai_verifying()
            import threading, requests as _req
            def _verify():
                provider = settings_data.get("ai_provider", "OpenAI")
                base_url = settings_data.get("ai_base_url", "https://api.openai.com/v1").rstrip("/")
                model    = settings_data.get("ai_model", "gpt-4o-mini")
                ok, msg  = False, ""

                # Claude 用 x-api-key header，其他用 Bearer
                if provider == "Claude":
                    headers = {
                        "x-api-key":         ai_key,
                        "anthropic-version":  "2023-06-01",
                        "Content-Type":       "application/json",
                    }
                else:
                    headers = {"Authorization": f"Bearer {ai_key}"}

                try:
                    if provider == "自訂":
                        # 第一段：試打 /models
                        try:
                            r = _req.get(f"{base_url}/models", headers=headers, timeout=8, verify=False)
                            if r.status_code == 200:
                                ok, msg = True, "✅ API Key 驗證成功"
                            elif r.status_code == 401:
                                ok, msg = False, "❌ API Key 無效（401）"
                            else:
                                # 第二段：試打 chat/completions
                                r2 = _req.post(
                                    f"{base_url}/chat/completions",
                                    headers={**headers, "Content-Type": "application/json"},
                                    json={"model": model, "messages": [{"role": "user", "content": "hi"}], "max_tokens": 1},
                                    timeout=10, verify=False,
                                )
                                if r2.status_code == 200:
                                    ok, msg = True, "✅ 連線成功（已儲存）"
                                elif r2.status_code == 401:
                                    ok, msg = False, "❌ API Key 無效（401）"
                                else:
                                    ok, msg = True, f"⚠️ 無法自動驗證，已儲存（HTTP {r2.status_code}）"
                        except Exception:
                            ok, msg = True, "⚠️ 無法自動驗證，已儲存"
                    elif provider == "Claude":
                        # Claude 用 chat/completions 測試
                        r = _req.post(
                            f"{base_url}/messages",
                            headers=headers,
                            json={"model": model, "max_tokens": 1, "messages": [{"role": "user", "content": "hi"}]},
                            timeout=10, verify=False,
                        )
                        if r.status_code == 200:
                            ok, msg = True, "✅ API Key 驗證成功"
                        elif r.status_code == 401:
                            ok, msg = False, "❌ API Key 無效（401）"
                        else:
                            ok, msg = False, f"❌ 驗證失敗（HTTP {r.status_code}）"
                    else:
                        r = _req.get(f"{base_url}/models", headers=headers, timeout=8, verify=False)
                        if r.status_code == 200:
                            ok, msg = True, "✅ API Key 驗證成功"
                        elif r.status_code == 401:
                            ok, msg = False, "❌ API Key 無效（401）"
                        else:
                            ok, msg = False, f"❌ 驗證失敗（HTTP {r.status_code}）"
                except Exception as e:
                    ok, msg = False, f"❌ 無法連線：{e}"
                self._ai_verify_signal.emit(ok, msg)
            threading.Thread(target=_verify, daemon=True).start()
        else:
            self.panel.hide()
            self.overlay.hide()

    def close_panel(self):
        self.panel.hide()
        self.overlay.hide()

    def _on_ai_verify_done(self, ok: bool, msg: str):
        """AI key 驗證結果回到主執行緒"""
        if hasattr(self, "panel"):
            self.panel.show_ai_result(ok, msg)
            if ok:
                QTimer.singleShot(1500, lambda: (self.panel.hide(), self.overlay.hide()))


class AddAccountPanel(QFrame):
    def __init__(self, parent=None, data=None):
        super().__init__(parent)

        # ===== 基本尺寸 =====
        self.setFixedSize(300, 400)

        # ===== 外觀（卡片）=====
        self.setStyleSheet("""
        QFrame {
            background-color: rgba(255,255,255,0.96);
            border-radius: 16px;
        }

        QLabel {
            color: #111827;
            font-size: 14px;
            background: transparent;
        }

        QLineEdit {
            background-color: transparent;
            border: none;
            border-bottom: 1px solid #D1D5DB;
            padding: 6px 2px;
        }

        QComboBox {
            background-color: transparent;
            border: none;
            border-bottom: 1px solid #D1D5DB;
            padding: 6px 2px;
            color: #111827;
        }

        QComboBox QAbstractItemView {
            background-color: #ffffff;
            color: #111827;
            selection-background-color: #EFF6FF;
            selection-color: #1D4ED8;
            border: 1px solid #D1D5DB;
            border-radius: 8px;
            padding: 4px;
        }

        QPushButton {
            background-color: #F3F4F6;
            border-radius: 12px;
            padding: 10px;
        }

        QPushButton:hover {
            background-color: #E5E7EB;
        }
        """)

        # ===== 陰影（右側浮出感）=====
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(40)
        shadow.setOffset(-12, 0)
        shadow.setColor(QColor(0, 0, 0, 80))
        self.setGraphicsEffect(shadow)

        # ===== Layout =====
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        # ===== 標題 =====
        title = QLabel("新增帳號")

        # ⭐ 如果有 data → 代表是編輯
        if data:
            title.setText("編輯帳號")
        title.setAlignment(Qt.AlignCenter)  # ⭐ 置中
        title.setStyleSheet("""
            font-size:18px;
            font-weight:600;
            color:#111827;
            margin-bottom: 10px;
        """)
        layout.addWidget(title)
        # ===== 帳號選擇（編輯用）=====
        self.selector = QComboBox()
        self.selector.hide()  # 預設隱藏
        layout.addWidget(self.selector)

        layout.addSpacing(8)

        # ===== 表單 =====
        form = QFormLayout()
        form.setSpacing(10)

        self.name = QLineEdit()
        self.login_type = QComboBox()
        self.login_type.addItem("eCPA", "eCPA")
        self.login_type.addItem("我的E政府", "egov")
        self.account = QLineEdit()
        # ===== 密碼 + 眼睛 =====
        pw_container = QWidget()
        pw_layout = QHBoxLayout()
        pw_layout.setContentsMargins(0, 0, 0, 0)
        pw_layout.setSpacing(0)  # ⭐ 改成 0，移除間距

        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)

        self.eye_btn = QPushButton("👁")
        self.eye_btn.setFixedSize(28, 28)  # ⭐ 改小一點
        self.eye_btn.setStyleSheet("""
            background: transparent;
            border: none;
            padding: 0px;
            margin: 0px;
        """)

        pw_layout.addWidget(self.password)
        pw_layout.addWidget(
            self.eye_btn, 0, Qt.AlignRight | Qt.AlignVCenter
        )  # ⭐ 靠右對齐

        pw_container.setLayout(pw_layout)  # ⭐ 最後再設定 layout
        form.addRow("名稱", self.name)
        form.addRow("登入方式", self.login_type)
        form.addRow("帳號", self.account)
        form.addRow("密碼", pw_container)  # ⭐ 加入容器到 form

        self.password.setEchoMode(QLineEdit.Password)

        layout.addLayout(form)

        # ===== 按鈕區 =====
        btn_row = QHBoxLayout()

        self.btn_ok = QPushButton("確定")
        self.btn_cancel = QPushButton("取消")
        self.btn_ok.setStyleSheet("""
            background-color: #2563EB;
            color: white;
            border-radius: 12px;
            padding: 10px 16px;
            font-size: 15px;
        """)

        self.btn_cancel.setStyleSheet("""
            background-color: rgba(0,0,0,0.05);
            border-radius: 12px;
            padding: 10px 16px;
            font-size: 15px;
        """)

        btn_row.addStretch()  # ⭐ 左空

        btn_row.addWidget(self.btn_ok)
        btn_row.addSpacing(12)
        btn_row.addWidget(self.btn_cancel)

        btn_row.addStretch()  # ⭐ 右空

        layout.addLayout(btn_row)

        # ===== 預設資料（編輯用）=====
        if data:
            # ⭐ 改標題
            title.setText("編輯帳號")

            # ⭐ 顯示下拉
            self.selector.show()

            # ⭐ 從 parent 拿帳號
            parent = self.parent()
            if parent and hasattr(parent, "accounts"):
                self.selector.clear()

                for acc in parent.accounts:
                    # ⭐ 轉換登入方式顯示
                    login_display = (
                        "我的E政府" if acc["login_type"] == "egov" else "eCPA"
                    )
                    self.selector.addItem(f"{acc['name']}（{login_display}）", acc)

            # ⭐ 預設選第一個
            if self.selector.count() > 0:
                self.selector.setCurrentIndex(0)
                self.load_data(self.selector.itemData(0))

            # ⭐ 切換帳號 → 更新表單
            self.selector.currentIndexChanged.connect(self.on_select_changed)

            # ===== 事件（先簡單關閉）=====
            self.btn_cancel.clicked.connect(self.hide)

        def toggle_password():
            if self.password.echoMode() == QLineEdit.Password:
                self.password.setEchoMode(QLineEdit.Normal)
                self.eye_btn.setText("🙈")  # ⭐ 關閉狀態
            else:
                self.password.setEchoMode(QLineEdit.Password)
                self.eye_btn.setText("👁")  # ⭐ 開啟狀態

        self.eye_btn.clicked.connect(toggle_password)
        self.eye_btn.setText("🙈")

    def get_data(self):
        return {
            "name":       self.name.text().strip(),
            "login_type": self.login_type.currentData(),
            "account":    self.account.text().strip(),
            "password":   self.password.text(),
        }

    def show_ai_verifying(self):
        self.btn_ok.setEnabled(False)
        self.ai_status.setStyleSheet("font-size: 12px; color: #888; background: transparent;")
        self.ai_status.setText("⏳ 驗證 API Key 中...")
        self.ai_status.show()

    def show_ai_result(self, ok: bool, msg: str):
        self.btn_ok.setEnabled(True)
        color = "#16a34a" if ok else "#dc2626"
        self.ai_status.setStyleSheet(f"font-size: 12px; color: {color}; background: transparent;")
        self.ai_status.setText(msg)
        self.ai_status.show()

    def load_data(self, data):
        self.name.setText(data.get("name", ""))

        value = data.get("login_type", "eCPA")
        index = self.login_type.findData(value)
        if index >= 0:
            self.login_type.setCurrentIndex(index)

        self.account.setText(data.get("account", ""))
        self.password.setText(data.get("password", ""))

    def on_select_changed(self, idx):
        data = self.selector.itemData(idx)
        if data:
            self.load_data(data)


class DeleteAccountPanel(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)

        # ===== 基本尺寸 =====
        self.setFixedSize(300, 200)

        # ===== 外觀（卡片）=====
        self.setStyleSheet("""
        QFrame {
            background-color: rgba(255,255,255,0.96);
            border-radius: 16px;
        }

        QLabel {
            color: #111827;
            font-size: 14px;
            background: transparent;
        }

        QComboBox {
            background-color: transparent;
            border: none;
            border-bottom: 1px solid #D1D5DB;
            padding: 6px 2px;
        }

        QPushButton {
            background-color: #F3F4F6;
            border-radius: 12px;
            padding: 10px;
        }

        QPushButton:hover {
            background-color: #E5E7EB;
        }
        """)

        # ===== 陰影（右側浮出感）=====
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(40)
        shadow.setOffset(-12, 0)
        shadow.setColor(QColor(0, 0, 0, 80))
        self.setGraphicsEffect(shadow)

        # ===== Layout =====
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        # ===== 標題 =====
        title = QLabel("刪除帳號")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("""
            font-size:18px;
            font-weight:600;
            color:#111827;
            margin-bottom: 10px;
        """)
        layout.addWidget(title)

        # ===== 帳號選擇 =====
        self.selector = QComboBox()
        layout.addWidget(self.selector)

        # ===== 按鈕區 =====
        btn_row = QHBoxLayout()

        self.btn_ok = QPushButton("刪除")
        self.btn_cancel = QPushButton("取消")

        self.btn_ok.setMinimumHeight(40)  # ⭐ 改成跟刪除 Panel 一樣
        self.btn_cancel.setMinimumHeight(40)  # ⭐ 改成跟刪除 Panel 一樣

        self.btn_ok.setStyleSheet("""
            background-color: #EF4444;
            color: white;
            border-radius: 12px;
            padding: 10px 16px;
        """)

        self.btn_cancel.setStyleSheet("""
            background-color: rgba(0,0,0,0.05);
            border-radius: 12px;
            padding: 10px 16px;
        """)

        btn_row.addStretch()
        btn_row.addWidget(self.btn_ok)
        btn_row.addSpacing(12)
        btn_row.addWidget(self.btn_cancel)
        btn_row.addStretch()

        layout.addLayout(btn_row)

        # ⭐ 確認框（獨立建立，設為 parent（EntryPage）的子元件）
        self.confirm_box = QFrame(parent)
        self.confirm_box.setFixedSize(
            self.width(), 120
        )  # ⭐ 改成 300x120（寬度同 panel）
        self.confirm_box.setStyleSheet("""
            QFrame {
                background-color: rgba(255,255,255,0.96);
                border-radius: 16px;
            }
            QPushButton {
                background-color: #F3F4F6;
                border-radius: 10px;
                padding: 8px;
                font-size: 13px;
            }
            QPushButton:hover {
                background-color: #E5E7EB;
            }
        """)

        # 陰影
        confirm_shadow = QGraphicsDropShadowEffect(self.confirm_box)
        confirm_shadow.setBlurRadius(40)
        confirm_shadow.setOffset(-12, 0)
        confirm_shadow.setColor(QColor(0, 0, 0, 80))
        self.confirm_box.setGraphicsEffect(confirm_shadow)

        confirm_layout = QVBoxLayout(self.confirm_box)
        confirm_layout.setContentsMargins(20, 15, 20, 15)
        confirm_layout.setSpacing(12)
        confirm_layout.setAlignment(Qt.AlignCenter)  # ⭐ 加這行

        self.confirm_label = QLabel("確定刪除？")
        self.confirm_label.setAlignment(Qt.AlignCenter)
        self.confirm_label.setStyleSheet("""
            color:#111827;
            font-size:14px;
            padding: 10px 12px;
            font-weight: 600;
            background-color: rgba(0,0,0,0.04);
            border-radius: 10px;
        """)

        confirm_btn_layout = QHBoxLayout()

        self.confirm_yes = QPushButton("確定")
        self.confirm_no = QPushButton("取消")

        self.confirm_yes.setMinimumHeight(46)  # ⭐ 改成跟刪除 Panel 一樣
        self.confirm_no.setMinimumHeight(46)  # ⭐ 改成跟刪除 Panel 一樣

        self.confirm_yes.setStyleSheet("""
            background-color: #2563EB;
            color: white;
            border-radius: 12px;
            padding: 10px 16px;
            font-size: 14px;
        """)

        self.confirm_no.setStyleSheet("""
            background-color: rgba(0,0,0,0.05);
            border-radius: 12px;
            padding: 10px 16px;
            font-size: 14px;
        """)

        # ⭐ 直接套用上面刪除 Panel 的邏輯
        confirm_btn_layout.addStretch()
        confirm_btn_layout.addWidget(self.confirm_yes)
        confirm_btn_layout.addSpacing(12)
        confirm_btn_layout.addWidget(self.confirm_no)
        confirm_btn_layout.addStretch()

        confirm_layout.addWidget(self.confirm_label)
        confirm_layout.addLayout(confirm_btn_layout)

        self.confirm_box.hide()


class SettingsPanel(QFrame):
    # 各服務預設值：(base_url, default_model, 申請連結)
    AI_PRESETS = {
        "OpenAI": ("https://api.openai.com/v1",                               "gpt-4o-mini",          "https://platform.openai.com/api-keys"),
        "Gemini": ("https://generativelanguage.googleapis.com/v1beta/openai", "gemini-1.5-flash",     "https://aistudio.google.com/app/apikey"),
        "Claude": ("https://api.anthropic.com/v1",                            "claude-3-haiku-20240307", "https://console.anthropic.com/settings/keys"),
        "Groq":   ("https://api.groq.com/openai/v1",                          "llama3-8b-8192",       "https://console.groq.com/keys"),
        "自訂":   ("", "", ""),
    }

    def __init__(self, parent=None, data=None):
        super().__init__(parent)

        # ===== 基本尺寸 =====
        self.setFixedSize(320, 480)

        # ===== 外觀（卡片）=====
        self.setStyleSheet("""
        QFrame {
            background-color: #ffffff;
            border-radius: 16px;
        }

        QLabel {
            color: #374151;
            font-size: 13px;
            background: transparent;
        }

        QLineEdit {
            background: transparent;
            border: none;
            border-bottom: 1px solid #E5E7EB;
            padding: 5px 2px;
            color: #111827;
            font-size: 13px;
        }
        QLineEdit:focus { border-bottom: 1px solid #2563EB; }

        QComboBox {
            background: transparent;
            border: none;
            border-bottom: 1px solid #E5E7EB;
            padding: 5px 2px;
            color: #111827;
            font-size: 13px;
        }
        QComboBox QAbstractItemView {
            background: white;
            color: #111827;
            selection-background-color: #EFF6FF;
            selection-color: #1D4ED8;
            border: 1px solid #E5E7EB;
            outline: none;
        }

        QPushButton {
            background-color: #F3F4F6;
            border-radius: 10px;
            padding: 8px 16px;
            font-size: 13px;
            color: #374151;
        }
        QPushButton:hover { background-color: #E5E7EB; }
        """)

        # ===== 陰影（右側浮出感）=====
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(40)
        shadow.setOffset(-12, 0)
        shadow.setColor(QColor(0, 0, 0, 60))
        self.setGraphicsEffect(shadow)

        # ===== 主內容 Layout =====
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 20)
        layout.setSpacing(0)

        # ===== 標題 =====
        title = QLabel("執行設定")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet(
            "font-size:17px; font-weight:700; color:#111827; margin-bottom:16px;"
        )
        layout.addWidget(title)

        # ===== 輔助：section 小標 =====
        def _section(text):
            lbl = QLabel(text)
            lbl.setStyleSheet(
                "font-size:13px; font-weight:700; color:#374151;"
                "letter-spacing:0.3px; margin-top:10px; margin-bottom:2px;"
            )
            layout.addWidget(lbl)

        # ===== 輔助：一列（標籤 + 欄位 [+ 額外]）=====
        def _row(label_text, widget, extra=None):
            row = QHBoxLayout()
            row.setSpacing(8)
            lbl = QLabel(label_text)
            lbl.setFixedWidth(64)
            lbl.setStyleSheet("font-size:13px; color:#6B7280;")
            row.addWidget(lbl)
            row.addWidget(widget, 1)
            if extra:
                row.addWidget(extra)
            layout.addLayout(row)
            layout.addSpacing(10)

        # ===== 執行設定 =====
        _section("執行設定")

        self.headless = QComboBox()
        self.headless.addItem("背景執行", True)
        self.headless.addItem("顯示視窗", False)
        _row("模式", self.headless)

        self.residence = QLineEdit()
        self.residence.setPlaceholderText("預設 75")
        _row("停留秒數", self.residence)

        self.target = QLineEdit()
        self.target.setPlaceholderText("預設 1.05")
        _row("完成率", self.target)

        # ===== 分隔線 =====
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet("color:#F3F4F6; margin:4px 0;")
        layout.addWidget(sep)

        # ===== AI 補答設定 =====
        _section("AI 補答設定")

        self.ai_provider = QComboBox()
        for name in self.AI_PRESETS:
            self.ai_provider.addItem(name)

        self.ai_link = QLabel()
        self.ai_link.setOpenExternalLinks(True)
        self.ai_link.setFixedWidth(20)
        self.ai_link.setStyleSheet("font-size:15px; background:transparent;")
        _row("服務", self.ai_provider, self.ai_link)

        self.ai_base_url = QLineEdit()
        self.ai_base_url.setPlaceholderText("API Base URL")
        _row("Base URL", self.ai_base_url)

        self.ai_model = QLineEdit()
        self.ai_model.setPlaceholderText("模型名稱")
        _row("模型", self.ai_model)

        self.ai_key = QLineEdit()
        self.ai_key.setPlaceholderText("貼上 API Key")
        self.ai_key.setEchoMode(QLineEdit.Password)

        eye_btn = QPushButton("🙈")
        eye_btn.setFixedSize(26, 26)
        eye_btn.setStyleSheet(
            "QPushButton { background:transparent; border:none; font-size:14px; padding:0; }"
            "QPushButton:hover { background:transparent; }"
        )
        def _toggle_key_visibility():
            if self.ai_key.echoMode() == QLineEdit.Password:
                self.ai_key.setEchoMode(QLineEdit.Normal)
                eye_btn.setText("👁")
            else:
                self.ai_key.setEchoMode(QLineEdit.Password)
                eye_btn.setText("🙈")
        eye_btn.clicked.connect(_toggle_key_visibility)
        _row("API Key", self.ai_key, eye_btn)

        # ===== AI 驗證狀態 =====
        self.ai_status = QLabel("")
        self.ai_status.setAlignment(Qt.AlignCenter)
        self.ai_status.setWordWrap(True)
        self.ai_status.setStyleSheet("font-size:12px; color:#555; background:transparent;")
        self.ai_status.hide()
        layout.addWidget(self.ai_status)

        layout.addSpacing(12)
        btn_row = QHBoxLayout()
        self.btn_cancel = QPushButton("取消")
        self.btn_ok = QPushButton("確定")
        self.btn_ok.setStyleSheet("""
            QPushButton { background:#2563EB; color:white; border-radius:10px;
                          padding:8px 16px; font-size:13px; font-weight:600; }
            QPushButton:hover { background:#1D4ED8; }
        """)
        btn_row.addStretch()
        btn_row.addWidget(self.btn_cancel)
        btn_row.addSpacing(8)
        btn_row.addWidget(self.btn_ok)
        layout.addLayout(btn_row)

        # ===== 選服務時自動填入 =====
        self._loading_settings = False
        self._ai_keys = {}

        def _on_provider_changed(idx):
            name = self.ai_provider.currentText()
            url, model, link = self.AI_PRESETS[name]
            self.ai_base_url.setReadOnly(name != "自訂")
            self.ai_model.setReadOnly(name != "自訂" and model != "")
            self.ai_key.setText(self._ai_keys.get(name, ""))
            if name == "Gemini":
                from PySide6.QtGui import QFontMetrics
                fm = QFontMetrics(self.ai_base_url.font())
                available = self.ai_base_url.width() - 12
                self.ai_base_url.setText(fm.elidedText(url, Qt.ElideRight, available))
            else:
                self.ai_base_url.setText(url)
            self.ai_model.setText(model)
            if link:
                self.ai_link.setText(
                    f'<a href="{link}" style="color:#2563EB;text-decoration:none;">🔗</a>'
                )
                self.ai_link.show()
            else:
                self.ai_link.hide()

        self.ai_provider.currentIndexChanged.connect(_on_provider_changed)
        _on_provider_changed(0)

        # ===== 預設值 =====
        if data:
            settings = data.get("settings", {})

            headless_value = settings.get("headless", True)
            self.headless.setCurrentIndex(0 if headless_value else 1)

            self.residence.setText(str(settings.get("residence_time", 75)))
            self.target.setText(str(settings.get("target_percentage", 1.05)))

            # 還原各服務 key（相容舊格式）
            self._ai_keys = settings.get("ai_keys", {})
            if not self._ai_keys and settings.get("ai_api_key"):
                saved_p = settings.get("ai_provider", "OpenAI")
                self._ai_keys = {saved_p: settings["ai_api_key"]}

            self._loading_settings = True
            saved_provider = settings.get("ai_provider", "OpenAI")
            idx = self.ai_provider.findText(saved_provider)
            if idx >= 0:
                self.ai_provider.setCurrentIndex(idx)
            _on_provider_changed(self.ai_provider.currentIndex())
            self._loading_settings = False

            if saved_provider == "自訂":
                self.ai_base_url.setText(settings.get("ai_base_url", ""))
                self.ai_model.setText(settings.get("ai_model", ""))

    def get_data(self):
        provider = self.ai_provider.currentText()
        url, model, _ = self.AI_PRESETS[provider]
        # 非自訂服務一律用預設完整 URL，避免存入截斷的顯示文字
        actual_url = self.ai_base_url.text().strip() if provider == "自訂" else url
        actual_model = self.ai_model.text().strip() if provider == "自訂" else model
        # 將目前 key 寫回 _ai_keys dict
        current_key = self.ai_key.text().strip()
        if current_key:
            self._ai_keys[provider] = current_key
        return {
            "headless":           self.headless.currentData(),
            "residence_time":     int(self.residence.text() or 75),
            "target_percentage":  float(self.target.text() or 1.05),
            "ai_provider":        provider,
            "ai_base_url":        actual_url,
            "ai_model":           actual_model,
            "ai_api_key":         current_key,   # 相容舊格式
            "ai_keys":            dict(self._ai_keys),  # 各服務 key
        }

    def show_ai_verifying(self):
        self.btn_ok.setEnabled(False)
        self.ai_status.setStyleSheet("font-size: 12px; color: #888; background: transparent;")
        self.ai_status.setText("⏳ 驗證 API Key 中...")
        self.ai_status.show()

    def show_ai_result(self, ok: bool, msg: str):
        self.btn_ok.setEnabled(True)
        color = "#16a34a" if ok else "#dc2626"
        self.ai_status.setStyleSheet(f"font-size: 12px; color: {color}; background: transparent;")
        self.ai_status.setText(msg)
        self.ai_status.show()


# =========================
# 版本更新通知 Signal
# =========================
from PySide6.QtCore import QObject

class UpdateSignal(QObject):
    notify = Signal(str, str, str)  # (latest_version, changelog, download_url)
    up_to_date = Signal()           # 已是最新版

    def emit(self, version, changelog, url):
        self.notify.emit(version, changelog, url)


# =========================
# 主執行頁面
# =========================
class ImmersivePage(QWidget):
    log_signal = Signal(str)

    def __init__(self, on_stop):
        super().__init__()
        self.on_stop = on_stop

        root = QVBoxLayout(self)
        root.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 中央區
        container = QFrame()

        # 螢幕圖片
        self.screen_bg = QLabel()
        pixmap = QPixmap(resource_path("screen.png"))

        # ⭐ 加入強力安全檢查
        if pixmap.isNull() or pixmap.width() <= 0 or pixmap.height() <= 0:
            # 如果圖片不存在或尺寸為 0，使用預設值
            logger.warning("⚠️ screen.png 不存在或尺寸無效，使用預設尺寸")
            self.w = 1024
            self.h = 768
            # 創建一個空的 pixmap（不會顯示，但不會崩潰）
            pixmap = QPixmap(self.w, self.h)
            pixmap.fill(QColor(200, 200, 200))  # 灰色背景
        else:
            self.w = pixmap.width()
            self.h = pixmap.height()

        # === 螢幕比例（保護除以零）
        if self.w > 0 and self.h > 0:
            self.rx = 255 / self.w
            self.ry = 138 / self.h
            self.rw = 580 / self.w
            self.rh = 280 / self.h
        else:
            self.rx = 0.25
            self.ry = 0.18
            self.rw = 0.57
            self.rh = 0.36

        container.setMinimumSize(400, 300)

        self.screen_bg.setScaledContents(True)
        self.screen_bg.setPixmap(pixmap)
        self.screen_bg.setAlignment(Qt.AlignTop | Qt.AlignLeft)

        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.addWidget(self.screen_bg)

        root.addWidget(container)

        # ===== Mac 標題列（正確版本）=====
        self.mac_bar = QWidget(self.screen_bg)
        self.mac_bar.setStyleSheet("""
            background-color: rgba(255,255,255,0.05);
            border-top-left-radius: 6px;
            border-top-right-radius: 6px;
        """)

        mac_layout = QHBoxLayout(self.mac_bar)
        mac_layout.setContentsMargins(8, 0, 8, 0)
        mac_layout.setSpacing(6)

        blur = QGraphicsBlurEffect()
        blur.setBlurRadius(6)
        self.mac_bar.setGraphicsEffect(blur)

        def make_dot(color):
            dot = QWidget()
            dot.setFixedSize(10, 10)
            dot.setStyleSheet(f"""
                background-color: {color};
                border-radius: 5px;
            """)
            return dot

        mac_layout.addWidget(make_dot("#FF5F57"))
        mac_layout.addWidget(make_dot("#FEBC2E"))
        mac_layout.addWidget(make_dot("#28C840"))

        title = QLabel("行政效能領航員")
        title.setStyleSheet("""
            color: rgba(255,255,255,0.85);
            font-size: 11px;
            background: transparent;
            letter-spacing: 0.5px;
        """)
        title.setAlignment(Qt.AlignCenter)

        mac_layout.addSpacing(200)
        mac_layout.addWidget(title)
        mac_layout.addStretch()

        self.mac_bar.setGeometry(244, 128, 578, 24)

        # 🔴 stop 按鈕
        self.stop_btn = QPushButton("⏹ Stop", self.screen_bg)
        self.stop_btn.setObjectName("ghost")

        self.stop_btn.clicked.connect(self.on_stop)
        self.stop_btn.setStyleSheet("""
            background-color: rgba(255,255,255,0.2);
            color: white;
            border-radius: 6px;
        """)

        # 建立 info（一定要先建）
        self.info = QLabel(self.screen_bg)
        self.info.setStyleSheet("""
        color: white;
        font-size: 14px;
        font-weight: 600;
        background: transparent;
        """)
        self.info.hide()

        self.log_view = QTextEdit(self.screen_bg)
        self.log_view.setReadOnly(True)
        self.log_view.document().setMaximumBlockCount(200)
        self.log_view.setStyleSheet("""
        QTextEdit {
            background: transparent;
            border: none;
            color: #E5E7EB;
            font-size: 13px;
        }

        QScrollBar:vertical {
            background: transparent;
            width: 5px;
            margin: 4px 0px 4px 0px;
        }

        QScrollBar::handle:vertical {
            background: rgba(255, 255, 255, 0.7);
            border-radius: 2px;
            min-height: 40px;
        }

        QScrollBar::handle:vertical:hover {
            background: rgba(255, 255, 255, 0.6);
        }

        QScrollBar::add-line:vertical,
        QScrollBar::sub-line:vertical {
            height: 0px;
        }

        QScrollBar::add-page:vertical,
        QScrollBar::sub-page:vertical {
            background: transparent;
        }
        """)

        # 螢幕區域
        self.screen_x = 255
        self.screen_y = 138
        self.screen_w = 580
        self.screen_h = 280

        self.log_signal.connect(self._append_text_safe)

    def append_text(self, text):
        """接收外部的日志文本"""
        self.log_signal.emit(text)

    def start(self, account: str):
        """開始顯示學習页面"""
        self._init_position()
        self.info.setText(f"上課人員 {account}")
        self.info.show()
        self.log_view.clear()

    def resizeEvent(self, event):
        """窗口大小改变时的处理"""
        super().resizeEvent(event)

        w = self.width()

        screen_x = self.screen_x
        screen_y = self.screen_y
        screen_w = self.screen_w
        screen_h = self.screen_h

        pad_x = 70
        pad_top = 70
        pad_info = 30
        pad_bottom = 40

        # stop
        self.stop_btn.move(w - 120, 25)

        # log_view 區域
        self.log_view.move(screen_x + pad_x, screen_y + pad_top)
        self.log_view.resize(screen_w - pad_x * 2, screen_h - pad_top - pad_bottom)

        # account
        self.info.move(screen_x + pad_x, screen_y + pad_info)
        self.info.resize(screen_w - pad_x * 2, 25)

    def _init_position(self):
        """初始化位置"""
        # stop（右上）
        self.stop_btn.move(self.screen_bg.width() - 120, 25)

        # log_view（文字區）
        self.log_view.setGeometry(248, 195, 578, 220)

        # account（帳號）
        self.info.move(248, 162)

    def _append_text_safe(self, text):
        """添加日志文本（HTML 上色，直接 append 到 QTextEdit）"""
        text = re.sub(r"\x1b\[[0-9;]*m", "", text)

        m = re.match(r"(\d{2}:\d{2}:\d{2}) \[(.*?)\] (.*)", text)

        def esc(s):
            return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

        if m:
            time_part, level_part, msg_part = m.groups()

            level_colors = {
                "INFO": "#3B82F6",
                "WARNING": "#F59E0B",
                "WARN": "#F59E0B",
                "ERROR": "#EF4444",
                "CRITICAL": "#F97316",
                "DEBUG": "#9CA3AF",
            }
            level_color = level_colors.get(level_part, "#9CA3AF")

            html = (
                f'<span style="color:#9CA3AF;">{esc(time_part)}</span> '
                f'<span style="color:{level_color};">[{esc(level_part)}]</span> '
                f'<span style="color:#E5E7EB;">{esc(msg_part)}</span>'
            )
        else:
            html = f'<span style="color:#E5E7EB;">{esc(text)}</span>'

        self.log_view.append(html)
        bar = self.log_view.verticalScrollBar()
        bar.setValue(bar.maximum())


# =========================
# 主視窗（頁面切換）
# =========================
class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("行政效能領航員")

        self.stack = QStackedLayout(self)

        self.entry = EntryPage(self.go_immersive)
        self.immersive = ImmersivePage(self.go_entry)

        # ⭐ 重要：先檢查 w 和 h 是否有效
        if self.immersive.w > 0 and self.immersive.h > 0:
            self.resize(int(self.immersive.w * 0.7), int(self.immersive.h * 0.7))
        else:
            # 如果無法取得，使用預設值
            self.resize(900, 600)

        self.setMinimumSize(500, 400)

        self.stack.addWidget(self.entry)
        self.stack.addWidget(self.immersive)

        self.pilot = None
        self.particle_effect = None
        self.cleanup_thread = None

        # 啟動時立即在背景檢查更新
        self._run_startup_update_check()

    def _run_startup_update_check(self):
        """程式啟動時，背景 thread 檢查版本，有新版則跳提示"""
        from app import AdminEfficiencyPilot
        import threading, requests as _req

        VERSION_URL = "https://raw.githubusercontent.com/waynelord0628-beep/auto-learning-bot/main/version.txt"
        DOWNLOAD_URL = "https://drive.google.com/drive/u/0/folders/1Fm6CwmV2AsoWaUOGV0V5hZbgP_GJrU8g"
        current_version = AdminEfficiencyPilot.VERSION
        current_changelog = AdminEfficiencyPilot.CHANGELOG

        self._update_signal = UpdateSignal()
        self._update_signal.notify.connect(self._on_update_available)
        self._update_signal.up_to_date.connect(self._on_up_to_date)
        _update_signal = self._update_signal

        import sys as _sys, os as _os
        _log_path = _os.path.join(_os.path.dirname(_sys.executable if getattr(_sys, "frozen", False) else _os.path.abspath(__file__)), "update_debug.log")

        def _dbg(msg):
            try:
                with open(_log_path, "a", encoding="utf-8") as f:
                    f.write(msg + "\n")
            except Exception:
                pass

        def _check():
            _dbg("thread started")
            try:
                resp = _req.get(VERSION_URL, timeout=5)
                _dbg(f"status={resp.status_code} body={resp.text.strip()!r}")
                if resp.status_code != 200:
                    return
                latest = resp.text.strip()
                if not latest or not latest.startswith("V"):
                    _dbg(f"格式不符：{latest!r}")
                    return
                _dbg(f"latest={latest!r} current={current_version!r}")
                if latest != current_version:
                    _dbg("emitting signal")
                    _update_signal.emit(latest, current_changelog, DOWNLOAD_URL)
                else:
                    _dbg("already latest")
                    _update_signal.up_to_date.emit()
            except Exception as e:
                _dbg(f"例外：{e}")

        threading.Thread(target=_check, daemon=True).start()

    def go_immersive(self, account_data):
        """轉到沈浸頁面，帶粒子效果"""
        self.show_particle_transition(account_data)

    def show_particle_transition(self, account_data):
        """直接切換到學習頁面並啟動引擎"""
        self._start_pilot_background(account_data)
        self.start_learning(account_data)

    def _cleanup_particle(self):
        """移除粒子效果層"""
        if self.particle_effect:
            self.particle_effect.hide()
            self.particle_effect.deleteLater()
            self.particle_effect = None

    def _start_pilot_background(self, account_data):
        """在後臺啟動 pilot 程式"""
        if hasattr(self, "pilot") and self.pilot:
            self.pilot.running = False

        # ⭐ 從 entry 的配置中讀取完整配置
        config_from_entry = self.entry.load_config()

        # ⭐ 找到對應的賬戶，並添加 settings
        full_config = account_data.copy()
        full_config.update(config_from_entry.get("settings", {}))

        # ⭐ 調試（遮蔽敏感欄位）
        _safe = {k: ("***" if "key" in k.lower() or "password" in k.lower() else v) for k, v in full_config.items()}
        logger.info(f"DEBUG: 最終配置 = {_safe}")

        self.pilot = AdminEfficiencyPilot(
            config_override=full_config, log_callback=self.immersive.append_text
        )

        # 版本更新通知
        self.pilot.update_signal = UpdateSignal()
        self.pilot.update_signal.notify.connect(self._on_update_available)
        self.pilot.running = True

        self.thread = threading.Thread(target=self.pilot.run, daemon=True)
        self.thread.start()

    def start_learning(self, account_data):
        """動畫播到一半，切換到學習頁面"""
        self.stack.setCurrentWidget(self.immersive)
        self.immersive.start(account_data["name"])
        self.setFixedSize(self.size())
        self.immersive._init_position()

    def _handle_update_btn(self):
        """手動點更新圖示：一定跳視窗顯示版本資訊"""
        entry = self.entry
        if entry._has_update and entry._latest_update_info:
            # 有新版 → 跳更新視窗
            latest, changelog, url = entry._latest_update_info
            self._on_update_available(latest, changelog, url)
        else:
            # 沒有新版或尚未檢查 → 跳「目前版本」視窗
            self._show_version_dialog()

    def _show_version_dialog(self):
        """顯示目前版本視窗（尚未有新版資訊）"""
        from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QPushButton, QFrame
        from app import AdminEfficiencyPilot as _AEP
        cur_ver = _AEP.VERSION

        dialog = QDialog(self)
        dialog.setWindowTitle("版本資訊")
        dialog.setFixedWidth(360)
        dialog.setStyleSheet("QDialog { background: #f5f7fa; } QLabel { color: #2c3e50; background: transparent; }")

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QLabel()
        header.setFixedHeight(6)
        header.setStyleSheet("background: qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #4fc3f7,stop:1 #0288d1);")
        layout.addWidget(header)

        body = QVBoxLayout()
        body.setContentsMargins(28, 22, 28, 24)
        body.setSpacing(12)

        title = QLabel("版本資訊")
        title.setStyleSheet("font-size: 17px; font-weight: bold; color: #0277bd;")
        body.addWidget(title)

        cur_label = QLabel(f"目前版本：{cur_ver}")
        cur_label.setStyleSheet("font-size: 13px;")
        body.addWidget(cur_label)

        entry = self.entry
        if entry._has_update and entry._latest_update_info:
            latest_label = QLabel(f"最新版本：{entry._latest_update_info[0]}")
        else:
            latest_label = QLabel("最新版本：目前已是最新版")
        latest_label.setStyleSheet("font-size: 13px; color: #27ae60;")
        body.addWidget(latest_label)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet("color: #dce1e7;")
        body.addWidget(sep)

        close_btn = QPushButton("關閉")
        close_btn.setMinimumHeight(40)
        close_btn.setStyleSheet("""
            QPushButton {
                background: #e0e0e0; color: #2c3e50;
                border: none; border-radius: 6px; font-size: 13px;
                padding: 8px 0;
                text-align: center;
            }
            QPushButton:hover { background: #bdbdbd; }
        """)
        close_btn.clicked.connect(dialog.accept)
        body.addWidget(close_btn)

        layout.addLayout(body)

        # 置中於螢幕
        from PySide6.QtWidgets import QApplication
        def _center():
            screen = QApplication.primaryScreen().availableGeometry()
            x = screen.x() + (screen.width() - dialog.width()) // 2
            y = screen.y() + (screen.height() - dialog.height()) // 2
            dialog.move(x, y)
        QTimer.singleShot(0, _center)

        dialog.exec()

    def _on_up_to_date(self):
        """已是最新版，更新按鈕 tooltip"""
        self.entry._has_update = False
        btn = getattr(self.entry, "_update_btn", None)
        if btn:
            btn.setToolTip("目前已是最新版")
            btn.setStyleSheet("""
                QPushButton { background: transparent; border: none; }
                QPushButton:hover { background: transparent; }
            """)

    def _on_update_available(self, latest: str, changelog: str, url: str):
        """在主執行緒顯示更新提示視窗"""
        # 儲存更新資訊，讓按鈕可以重複觸發
        self.entry._has_update = True
        self.entry._latest_update_info = (latest, changelog, url)
        btn = getattr(self.entry, "_update_btn", None)
        if btn:
            btn.setToolTip(f"有新版本 {latest}！點此查看")
            btn.setStyleSheet("""
                QPushButton {
                    background: transparent;
                    border: none;
                    border-radius: 26px;
                }
                QPushButton:hover {
                    background: rgba(0,0,0,0.12);
                }
            """)

        from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QPushButton, QHBoxLayout
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtCore import QUrl

        dialog = QDialog(self)
        dialog.setWindowTitle("發現新版本！")
        dialog.setFixedWidth(440)
        dialog.setStyleSheet("""
            QDialog {
                background: #f5f7fa;
            }
            QLabel {
                color: #2c3e50;
                background: transparent;
            }
        """)

        layout = QVBoxLayout(dialog)
        layout.setSpacing(0)
        layout.setContentsMargins(0, 0, 0, 0)

        # 頂部色帶
        header = QLabel()
        header.setFixedHeight(6)
        header.setStyleSheet("background: qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #4fc3f7,stop:1 #0288d1);")
        layout.addWidget(header)

        # 主內容區
        content = QVBoxLayout()
        content.setSpacing(14)
        content.setContentsMargins(28, 24, 28, 20)

        # 標題
        title = QLabel(f"新版本 {latest} 已發布")
        title.setStyleSheet("font-size: 18px; font-weight: bold; color: #0277bd; letter-spacing: 0.5px;")
        content.addWidget(title)

        # 當前版本
        from app import AdminEfficiencyPilot as _AEP
        cur = getattr(self, "pilot", None)
        cur_ver = getattr(cur, "version", "") if cur else _AEP.VERSION
        if cur_ver:
            cur_label = QLabel(f"目前版本：{cur_ver}")
            cur_label.setStyleSheet("font-size: 12px; color: #7f8c8d; margin-top: 2px;")
            content.addWidget(cur_label)

        # 分隔線
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet("color: #dce1e7; margin-top: 4px; margin-bottom: 4px;")
        content.addWidget(sep)

        # Changelog
        if changelog:
            change_title = QLabel("本次更新內容")
            change_title.setStyleSheet("font-size: 13px; font-weight: bold; color: #34495e;")
            content.addWidget(change_title)

            change_body = QLabel(changelog)
            change_body.setStyleSheet("""
                font-size: 12px;
                color: #555f6e;
                line-height: 2;
                padding: 8px 12px;
                background: #eaf4fb;
                border-left: 3px solid #4fc3f7;
                border-radius: 4px;
            """)
            change_body.setWordWrap(True)
            content.addWidget(change_body)

        # 提示文字
        hint = QLabel("請前往 Google Drive 下載最新版本並替換舊的 .exe 檔案。")
        hint.setStyleSheet("font-size: 11px; color: #95a5a6;")
        hint.setWordWrap(True)
        content.addWidget(hint)

        # 按鈕列
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        btn_later = QPushButton("稍後再說")
        btn_later.setFixedHeight(36)
        btn_later.setStyleSheet("""
            QPushButton {
                background: #ecf0f1; color: #7f8c8d;
                border-radius: 6px; padding: 0 20px; font-size: 13px;
                border: 1px solid #dce1e7;
            }
            QPushButton:hover { background: #dde3e8; }
        """)
        btn_later.clicked.connect(dialog.reject)

        btn_download = QPushButton("前往下載")
        btn_download.setFixedHeight(36)
        btn_download.setStyleSheet("""
            QPushButton {
                background: #0288d1; color: #fff; font-weight: bold;
                border-radius: 6px; padding: 0 20px; font-size: 13px;
                border: none;
            }
            QPushButton:hover { background: #0277bd; }
        """)
        btn_download.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(url)))
        btn_download.clicked.connect(dialog.accept)

        btn_row.addStretch()
        btn_row.addWidget(btn_later)
        btn_row.addWidget(btn_download)
        content.addLayout(btn_row)

        layout.addLayout(content)

        dialog.exec()

    def go_entry(self):
        """⭐ 修改版：立即返回入口，後臺清理"""
        # Step 1️⃣：立即設置停止旗標
        if hasattr(self, "pilot") and self.pilot:
            self.pilot.running = False

        # Step 2️⃣：立即切換 UI 回到入���頁面（重點：不等待）
        self.stack.setCurrentWidget(self.entry)

        # Step 3️⃣：重置入口頁面的 combo
        self.entry.combo.blockSignals(True)
        self.entry.combo.setCurrentIndex(0)
        self.entry.combo.blockSignals(False)

        # Step 4️⃣：在後臺執行清理（非同步，不卡 UI）
        if self.cleanup_thread is None or not self.cleanup_thread.is_alive():
            self.cleanup_thread = threading.Thread(
                target=self._cleanup_pilot_async, daemon=True
            )
            self.cleanup_thread.start()

    def _cleanup_pilot_async(self):
        """⭐ 新增：在後臺安全清理 pilot，不阻塞 UI"""
        try:
            # 等待 pilot 執行緒結束（最多 5 秒）
            if hasattr(self, "thread") and self.thread and self.thread.is_alive():
                self.thread.join(timeout=5)

            # 強制清理 driver 和行程
            if hasattr(self, "pilot") and self.pilot:
                self.pilot._cleanup()

        except Exception as e:
            pass


# =========================
# Run
# =========================
if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyleSheet(GLOBAL_QSS)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())
