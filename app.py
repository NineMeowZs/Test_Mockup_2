"""app.py – Auto Subtitle Tool  (controller + upload page)"""

import tkinter as tk
from tkinter import ttk, filedialog
import os

# ── colour palette ────────────────────────────────────────────────────────────
BG       = "#0a0a0a"
PANEL    = "#111111"
ACCENT   = "#ffffff"
ACCENT2  = "#cccccc"
TEXT     = "#f0f0f0"
SUBTEXT  = "#666666"
ENTRY_BG = "#1a1a1a"
DIVIDER  = "#222222"
GREEN    = "#22c55e"
# ── main controller ───────────────────────────────────────────────────────────
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Auto Subtitle")
        self.geometry("1100x700")
        self.minsize(900, 600)
        self.configure(bg=BG)
        self._page = None
        self._show_upload()

    def _show_upload(self):
        self._swap(UploadPage, on_start=self._on_start)
        self.geometry("1100x700")

    def _on_start(self, video_path, model):
        from editor_page import EditorPage
        self.geometry("1640x860")
        self._swap(EditorPage,
                   video_path=video_path,
                   model=model,
                   on_back=self._show_upload)

    def _swap(self, PageClass, **kw):
        if self._page:
            self._page.destroy()
        self._page = PageClass(self, **kw)
        self._page.pack(fill="both", expand=True)


# ── upload page ───────────────────────────────────────────────────────────────
class UploadPage(tk.Frame):
    def __init__(self, master, on_start):
        super().__init__(master, bg=BG)
        self._on_start = on_start
        self._video_path = None
        self._model_var = tk.StringVar(value="base")
        self._build()

    def _build(self):
        # header
        hdr = tk.Frame(self, bg=PANEL, height=60)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        tk.Label(hdr, text="Auto Subtitle ✨", font=("Segoe UI", 16, "bold"),
                 bg=PANEL, fg=TEXT).pack(side="left", padx=24, pady=14)

        # body
        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True)

        center = tk.Frame(body, bg=BG)
        center.place(relx=0.5, rely=0.45, anchor="center")

        # Title
        tk.Label(center, text="สร้างซับไตเติลอัตโนมัติด้วย AI", font=("Segoe UI", 22, "bold"),
                 bg=BG, fg=TEXT).pack(pady=(0, 4))
        tk.Label(center, text="อัปโหลดวิดีโอแล้วปล่อยให้ AI จัดการถอดเสียงและใส่ซับให้คุณทันที เร็วและแม่นยำ", 
                 font=("Segoe UI", 11), bg=BG, fg=SUBTEXT).pack(pady=(0, 32))

        # Drop zone
        self._drop = tk.Canvas(center, width=640, height=240, bg=ENTRY_BG,
                               highlightthickness=2, highlightbackground=DIVIDER,
                               cursor="hand2")
        self._drop.pack(pady=(0, 12))
        self._draw_drop(None)
        self._drop.bind("<Button-1>", lambda e: self._browse())

        self._file_lbl = tk.Label(center, text="", bg=BG, fg=GREEN, font=("Segoe UI", 10, "bold"))
        self._file_lbl.pack(pady=(0, 16))

        # Model Selection
        mfrm = tk.Frame(center, bg=BG)
        mfrm.pack(pady=(0, 24))
        tk.Label(mfrm, text="AI Model:", font=("Segoe UI", 10, "bold"),
                 bg=BG, fg=SUBTEXT).pack(side="left", padx=8)
        
        self._model_btns = {}
        def _sel_m(v):
            if v == "custom":
                d = filedialog.askdirectory(title="เลือกโฟลเดอร์โมเดล (Hugging Face)")
                if not d: return
                self._model_var.set(d)
                v = "custom"
            else:
                self._model_var.set(v)
            for k, b in self._model_btns.items():
                b.configure(bg=ACCENT if k == v else ENTRY_BG, fg=BG if k == v else TEXT)

        for m in ["tiny", "base", "small", "medium", "custom"]:
            b = tk.Button(mfrm, text=m.upper(), font=("Segoe UI", 9, "bold"),
                          bg=ACCENT if m == "base" else ENTRY_BG,
                          fg=BG if m == "base" else TEXT,
                          relief="flat", padx=12, pady=6, cursor="hand2",
                          command=lambda v=m: _sel_m(v))
            b.pack(side="left", padx=4)
            self._model_btns[m] = b

        # Start button
        self._start_btn = tk.Button(center, text="กรุณาอัปโหลดวิดีโอก่อน",
                                    bg=DIVIDER, fg=SUBTEXT,
                                    font=("Segoe UI", 12, "bold"),
                                    relief="flat", cursor="hand2", width=40, pady=12,
                                    state="disabled", command=self._start)
        self._start_btn.pack(pady=(0, 20))

    def _draw_drop(self, path):
        c = self._drop
        c.delete("all")
        w, h = 640, 240
        if path:
            name = os.path.basename(path)
            c.create_text(w // 2, h // 2 - 16, text="🎬",
                          fill=ACCENT, font=("Segoe UI", 36))
            c.create_text(w // 2, h // 2 + 24, text="คลิกที่นี่เพื่อเปลี่ยนไฟล์วิดีโอ",
                          fill=SUBTEXT, font=("Segoe UI", 10))
        else:
            c.create_text(w // 2, h // 2 - 20, text="☁️",
                          fill=SUBTEXT, font=("Segoe UI", 48))
            c.create_text(w // 2, h // 2 + 24, text="ลากไฟล์วิดีโอมาวาง หรือ คลิกเพื่อเลือกไฟล์",
                          fill=TEXT, font=("Segoe UI", 12, "bold"))
            c.create_text(w // 2, h // 2 + 50, text="รองรับ MP4, MOV, AVI, MKV",
                          fill=SUBTEXT, font=("Segoe UI", 9))

    def _browse(self):
        p = filedialog.askopenfilename(
            filetypes=[("Video", "*.mp4 *.avi *.mov *.mkv *.webm"), ("All", "*.*")])
        if p:
            self._video_path = p
            self._draw_drop(p)
            size_mb = os.path.getsize(p) / 1e6
            self._file_lbl.configure(text=f"✅ {os.path.basename(p)}  ({size_mb:.1f} MB)")
            self._start_btn.configure(text="✨ เริ่มถอดเสียงและใส่ซับไตเติล",
                                      bg=ACCENT, fg=BG, state="normal")

    def _start(self):
        if self._video_path:
            self._on_start(self._video_path, self._model_var.get())


# ── entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app = App()
    app.mainloop()
