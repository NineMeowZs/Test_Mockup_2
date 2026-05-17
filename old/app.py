"""app.py – Minimalist Project Launcher"""

import customtkinter as ctk
from tkinter import filedialog
import os

# ── Configuration ─────────────────────────────────────────────────────────────
ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

BG_DARK      = "#0b0b0b"
PANEL_DARK   = "#161616"
ACCENT_BLUE  = "#3a86ff"
TEXT_WHITE   = "#ffffff"
TEXT_GRAY    = "#a0a0a0"

class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        
        self.title("VideoAI Pro")
        self.geometry("1100x700")
        self.minsize(900, 600)
        self.configure(fg_color=BG_DARK)
        
        self._page = None
        self._show_home()

    def _show_home(self):
        self._swap(HomePage, on_start=self._on_start)

    def _on_start(self, initial_video):
        from editor_page import EditorPage
        self.geometry("1600x900")
        # Now we go straight to editor without model selection
        self._swap(EditorPage, 
                   initial_video=initial_video, 
                   on_back=self._show_home)

    def _swap(self, PageClass, **kw):
        if self._page:
            self._page.destroy()
        self._page = PageClass(self, **kw)
        self._page.pack(fill="both", expand=True)

class HomePage(ctk.CTkFrame):
    def __init__(self, master, on_start):
        super().__init__(master, fg_color=BG_DARK, corner_radius=0)
        self._on_start = on_start
        self._build_ui()

    def _build_ui(self):
        # Sidebar
        sidebar = ctk.CTkFrame(self, width=240, fg_color=PANEL_DARK, corner_radius=0)
        sidebar.pack(side="left", fill="y")
        
        logo = ctk.CTkLabel(sidebar, text="VideoAI Pro", font=ctk.CTkFont(size=26, weight="bold"), text_color=ACCENT_BLUE)
        logo.pack(pady=40)
        
        # Main area
        main = ctk.CTkFrame(self, fg_color="transparent")
        main.pack(side="left", fill="both", expand=True, padx=60, pady=60)
        
        ctk.CTkLabel(main, text="Start Your Next Masterpiece", font=ctk.CTkFont(size=36, weight="bold")).pack(anchor="w")
        ctk.CTkLabel(main, text="Create a new project or continue where you left off.", 
                      font=ctk.CTkFont(size=16), text_color=TEXT_GRAY).pack(anchor="w", pady=(5, 40))

        # Big Action Card
        self.card = ctk.CTkFrame(main, fg_color=PANEL_DARK, corner_radius=20, border_width=2, border_color="#222222")
        self.card.pack(fill="both", expand=True)
        
        inner = ctk.CTkFrame(self.card, fg_color="transparent")
        inner.place(relx=0.5, rely=0.5, anchor="center")
        
        ctk.CTkLabel(inner, text="🎬", font=ctk.CTkFont(size=80)).pack(pady=10)
        ctk.CTkLabel(inner, text="New Project", font=ctk.CTkFont(size=24, weight="bold")).pack()
        ctk.CTkLabel(inner, text="Import a video to begin editing", text_color=TEXT_GRAY).pack(pady=(0, 30))
        
        # THIS IS THE BUTTON - MAKE IT HUGE
        self.btn = ctk.CTkButton(inner, text="START EDITING", font=ctk.CTkFont(size=20, weight="bold"),
                                 height=80, width=400, corner_radius=15, fg_color=ACCENT_BLUE,
                                 hover_color="#2563eb", command=self._browse)
        self.btn.pack()

    def _browse(self):
        p = filedialog.askopenfilename(filetypes=[("Video Files", "*.mp4 *.mov *.avi *.mkv *.webm"), ("All Files", "*.*")])
        if p:
            self._on_start(p)

if __name__ == "__main__":
    app = App()
    app.mainloop()
