"""app.py – VideoAI Pro · Modern CapCut-Inspired Launcher"""

import customtkinter as ctk
from tkinter import filedialog
import os

# ── Design Tokens ──────────────────────────────────────────────────────────────
ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

BG_DEEP      = "#0a0a0c"
BG_DARK      = "#111114"
PANEL_DARK   = "#18181c"
PANEL_MID    = "#1e1e24"
ACCENT_BLUE  = "#3a86ff"
ACCENT_CYAN  = "#00d4ff"
TEXT_WHITE   = "#f0f0f5"
TEXT_GRAY    = "#7a7a8a"
BORDER_DARK  = "#2a2a35"


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("MediaPro")
        self.geometry("1100x720")
        self.minsize(900, 600)
        self.configure(fg_color=BG_DEEP)
        self._page = None
        self._show_home()

    def _show_home(self):
        self._swap(HomePage, on_start=self._on_start)

    def _on_start(self, initial_video):
        from editor_page import EditorPage
        self.geometry("1680x960")
        self.minsize(1200, 700)
        self._swap(EditorPage, initial_video=initial_video, on_back=self._show_home)

    def _swap(self, PageClass, **kw):
        if self._page:
            self._page.destroy()
        self._page = PageClass(self, **kw)
        self._page.pack(fill="both", expand=True)


class HomePage(ctk.CTkFrame):
    def __init__(self, master, on_start):
        super().__init__(master, fg_color=BG_DEEP, corner_radius=0)
        self._on_start = on_start
        self._build_ui()

    def _build_ui(self):
        # ── Left Sidebar ─────────────────────────────────────────────────────
        sidebar = ctk.CTkFrame(self, width=220, fg_color=PANEL_DARK, corner_radius=0)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)

        # Logo
        logo_frame = ctk.CTkFrame(sidebar, fg_color="transparent", height=80)
        logo_frame.pack(fill="x")
        ctk.CTkLabel(
            logo_frame,
            text="MediaPro",
            font=ctk.CTkFont(family="Helvetica", size=22, weight="bold"),
            text_color=ACCENT_BLUE,
        ).pack(side="left", padx=20, pady=20)
        ctk.CTkLabel(
            logo_frame,
            text="Pro",
            font=ctk.CTkFont(family="Helvetica", size=14),
            text_color=ACCENT_CYAN,
        ).pack(side="left", pady=20)

        # Divider
        ctk.CTkFrame(sidebar, height=1, fg_color=BORDER_DARK).pack(fill="x", padx=15)

        # Nav Items
        nav_items = [
            ("🏠", "Home", True),
            ("📁", "My Projects", False),
            ("⭐", "Templates", False),
            ("🎬", "Effects", False),
        ]
        nav_frame = ctk.CTkFrame(sidebar, fg_color="transparent")
        nav_frame.pack(fill="x", pady=20)

        for icon, label, active in nav_items:
            btn_fg = PANEL_MID if active else "transparent"
            text_col = TEXT_WHITE if active else TEXT_GRAY
            row = ctk.CTkFrame(nav_frame, fg_color=btn_fg, corner_radius=10, height=42)
            row.pack(fill="x", padx=10, pady=2)
            row.pack_propagate(False)
            ctk.CTkLabel(row, text=icon, width=30, font=ctk.CTkFont(size=15)).pack(side="left", padx=(12, 4), pady=10)
            ctk.CTkLabel(row, text=label, font=ctk.CTkFont(size=13), text_color=text_col).pack(side="left")

        # Spacer + bottom section
        ctk.CTkFrame(sidebar, fg_color="transparent").pack(fill="both", expand=True)
        ctk.CTkFrame(sidebar, height=1, fg_color=BORDER_DARK).pack(fill="x", padx=15)
        ctk.CTkLabel(sidebar, text="v2.0.0", font=ctk.CTkFont(size=11), text_color=TEXT_GRAY).pack(pady=12)

        # ── Main Area ─────────────────────────────────────────────────────────
        main = ctk.CTkFrame(self, fg_color="transparent")
        main.pack(side="left", fill="both", expand=True)

        # Top bar
        topbar = ctk.CTkFrame(main, height=60, fg_color="transparent")
        topbar.pack(fill="x", padx=35, pady=(25, 0))

        ctk.CTkLabel(
            topbar,
            text="Start Creating",
            font=ctk.CTkFont(family="Helvetica", size=28, weight="bold"),
            text_color=TEXT_WHITE,
        ).pack(side="left", anchor="s")

        # Content Area
        content = ctk.CTkFrame(main, fg_color="transparent")
        content.pack(fill="both", expand=True, padx=35, pady=20)

        # ── Hero Card (New Project) ───────────────────────────────────────────
        hero = ctk.CTkFrame(
            content,
            fg_color=PANEL_DARK,
            corner_radius=16,
            border_width=1,
            border_color=BORDER_DARK,
        )
        hero.pack(fill="both", expand=True)

        inner = ctk.CTkFrame(hero, fg_color="transparent")
        inner.place(relx=0.5, rely=0.45, anchor="center")

        # Icon ring
        icon_ring = ctk.CTkFrame(inner, width=100, height=100, corner_radius=50, fg_color=PANEL_MID, border_width=2, border_color=ACCENT_BLUE)
        icon_ring.pack(pady=(0, 20))
        icon_ring.pack_propagate(False)
        ctk.CTkLabel(icon_ring, text="🎬", font=ctk.CTkFont(size=42)).place(relx=0.5, rely=0.5, anchor="center")

        ctk.CTkLabel(
            inner,
            text="New Project",
            font=ctk.CTkFont(family="Helvetica", size=22, weight="bold"),
            text_color=TEXT_WHITE,
        ).pack()
        ctk.CTkLabel(
            inner,
            text="Import a video file to begin editing",
            font=ctk.CTkFont(size=13),
            text_color=TEXT_GRAY,
        ).pack(pady=(4, 28))

        # CTA Button
        self.start_btn = ctk.CTkButton(
            inner,
            text="  ▶  START EDITING",
            font=ctk.CTkFont(family="Helvetica", size=15, weight="bold"),
            height=54,
            width=340,
            corner_radius=12,
            fg_color=ACCENT_BLUE,
            hover_color="#2563eb",
            command=self._browse,
        )
        self.start_btn.pack()

        ctk.CTkLabel(
            inner,
            text="Supports MP4 · MOV · AVI · MKV · WebM",
            font=ctk.CTkFont(size=11),
            text_color=TEXT_GRAY,
        ).pack(pady=(12, 0))

    def _browse(self):
        path = filedialog.askopenfilename(
            title="Import Video",
            filetypes=[
                ("Video Files", "*.mp4 *.mov *.avi *.mkv *.webm"),
                ("All Files", "*.*"),
            ],
        )
        if path:
            self._on_start(path)


if __name__ == "__main__":
    app = App()
    app.mainloop()
