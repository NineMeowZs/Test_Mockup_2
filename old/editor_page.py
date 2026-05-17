"""editor_page.py – Stable Unified Multi-track Video Editor"""

import customtkinter as ctk
import tkinter as tk
from tkinter import messagebox, colorchooser, filedialog
import threading
import cv2
import os
import subprocess
from PIL import Image, ImageTk
import pygame
import tempfile
import json
import time

import imageio_ffmpeg
from subtitle_config import SubtitleStyle, FONT_CHOICES, ANIMATION_CHOICES, DECORATION_CHOICES, POSITION_CHOICES, PRESETS
from subtitle_renderer import draw_subtitles_on_frame
from transcriber import transcribe_video
from video_exporter import export_video_with_subtitles

# ── Design Tokens ─────────────────────────────────────────────────────────────
BG_BLACK     = "#000000"
BG_DARK      = "#0b0b0b"
PANEL_DARK   = "#161616"
ACCENT_BLUE  = "#3a86ff"
ACCENT_RED   = "#ff4d4d"
ACCENT_GRAY  = "#2a2a2a"
TEXT_WHITE   = "#ffffff"
TEXT_GRAY    = "#a0a0a0"
BORDER_COLOR = "#222222"

class EditorPage(ctk.CTkFrame):
    def __init__(self, master, initial_video, on_back):
        super().__init__(master, fg_color=BG_DARK, corner_radius=0)
        self.master = master
        self._on_back = on_back
        
        # ── State Initialization ──────────────────────────────────────────────
        self.tracks = {
            "video": [],  # List of {path, name, start, end, speed, volume, timeline_start}
            "audio": [],
            "text": []
        }
        self.assets = []
        self.segments = []
        self.style = SubtitleStyle()
        self.timeline_zoom = 1.0
        self.aspect_ratio = "16:9"
        self.selected_item = {"track": "video", "index": 0}

        # Playback Logic
        self.cap = None
        self._current_cap_path = None
        self.frame_index = 0
        self.is_playing = False
        self._original_audio = None
        
        pygame.mixer.init()

        # UI Control Variables
        self.v_font     = tk.StringVar(value="Tahoma")
        self.v_size     = tk.IntVar(value=32)
        self.v_color    = tk.StringVar(value="#FFFFFF")
        self.v_ratio    = tk.StringVar(value="16:9")
        self.v_zoom     = tk.DoubleVar(value=1.0)
        self.v_clip_speed  = tk.DoubleVar(value=1.0)
        self.v_clip_volume = tk.DoubleVar(value=1.0)

        # First load the initial video into tracks
        self._load_initial_video(initial_video)
        
        # Build UI (Callbacks now should find the methods)
        self._build_ui()
        self._setup_initial_audio(initial_video)
        self._start_autosave_timer()

    def _load_initial_video(self, path):
        cap = cv2.VideoCapture(path)
        fps = cap.get(cv2.CAP_PROP_FPS) or 25
        count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        duration = count / fps
        cap.release()
        
        asset = {"path": path, "name": os.path.basename(path), "type": "video"}
        self.assets.append(asset)
        self.tracks["video"].append({
            "path": path, "name": asset["name"], "start": 0.0, "end": duration,
            "speed": 1.0, "volume": 1.0, "timeline_start": 0.0
        })

    def _setup_initial_audio(self, path):
        def run():
            try:
                self.after(0, lambda: self.loading_lbl.configure(text="Initializing Engine..."))
                fd, tmp = tempfile.mkstemp(suffix=".wav")
                os.close(fd)
                self._original_audio = tmp
                ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
                subprocess.run([ffmpeg, "-y", "-i", path, "-vn", "-ar", "44100", "-ac", "2", tmp], capture_output=True)
                pygame.mixer.music.load(tmp)
                self.after(0, self._finish_loading)
            except Exception as e: 
                print(f"Audio error: {e}")
                self.after(0, self._finish_loading)
        threading.Thread(target=run, daemon=True).start()

    def _finish_loading(self):
        if hasattr(self, 'loading_frm') and self.loading_frm.winfo_exists():
            self.loading_frm.destroy()
        self._render_frame(0)
        self._switch_tab("Assets")

    def _build_ui(self):
        # 1. Header
        top = ctk.CTkFrame(self, height=45, fg_color=PANEL_DARK, corner_radius=0)
        top.pack(side="top", fill="x")
        ctk.CTkButton(top, text="←", width=35, fg_color="transparent", command=self._back).pack(side="left", padx=10)
        ctk.CTkLabel(top, text="VideoAI Pro Editor", font=ctk.CTkFont(weight="bold")).pack(side="left", padx=10)
        ctk.CTkButton(top, text="Export", fg_color=ACCENT_BLUE, height=28, command=self._export).pack(side="right", padx=15)
        ctk.CTkOptionMenu(top, values=["16:9", "9:16", "1:1", "4:3"], variable=self.v_ratio, width=80, height=25, command=lambda v: self._render_frame(self.frame_index)).pack(side="right", padx=10)

        # 2. Main Area
        main = ctk.CTkFrame(self, fg_color="transparent")
        main.pack(fill="both", expand=True)

        # Side Tools
        tools = ctk.CTkFrame(main, width=60, fg_color=BG_DARK, corner_radius=0)
        tools.pack(side="left", fill="y")
        for i, n in [("📁", "Assets"), ("💬", "Text"), ("🎨", "Filters")]:
            ctk.CTkButton(tools, text=i, width=40, height=40, fg_color="transparent", command=lambda x=n: self._switch_tab(x)).pack(pady=10)

        # Assets/Resource Panel
        self.res_panel = ctk.CTkFrame(main, width=300, fg_color=PANEL_DARK, corner_radius=0)
        self.res_panel.pack(side="left", fill="y")
        self.res_container = ctk.CTkScrollableFrame(self.res_panel, fg_color="transparent")
        self.res_container.pack(fill="both", expand=True, padx=5, pady=5)

        # Preview Area
        preview = ctk.CTkFrame(main, fg_color="transparent")
        preview.pack(side="left", fill="both", expand=True)
        self.canvas = tk.Canvas(preview, bg=BG_BLACK, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True, padx=15, pady=15)
        
        pctrl = ctk.CTkFrame(preview, height=60, fg_color=BG_DARK)
        pctrl.pack(fill="x")
        ictrl = ctk.CTkFrame(pctrl, fg_color="transparent")
        ictrl.place(relx=0.5, rely=0.5, anchor="center")
        self.play_btn = ctk.CTkButton(ictrl, text="▶", width=44, height=44, corner_radius=22, fg_color=TEXT_WHITE, text_color=BG_BLACK, command=self._toggle_play)
        self.play_btn.pack(side="left", padx=10)
        ctk.CTkButton(ictrl, text="✂️", width=36, height=36, fg_color=ACCENT_GRAY, command=self._split_clip).pack(side="left", padx=5)
        
        self.time_label = ctk.CTkLabel(pctrl, text="00:00 / 00:00", text_color=TEXT_GRAY)
        self.time_label.pack(side="right", padx=15)

        # 3. Timeline Section
        self.timeline_area = ctk.CTkFrame(self, height=240, fg_color=PANEL_DARK, corner_radius=0)
        self.timeline_area.pack(side="bottom", fill="x")
        
        tl_head = ctk.CTkFrame(self.timeline_area, height=30, fg_color=PANEL_DARK)
        tl_head.pack(fill="x")
        ctk.CTkSlider(tl_head, from_=0.1, to=5.0, width=100, variable=self.v_zoom, command=lambda v: self._draw_timeline()).pack(side="right", padx=10)
        
        self.tl_canvas = tk.Canvas(self.timeline_area, bg="#111111", highlightthickness=0)
        self.tl_canvas.pack(fill="both", expand=True, padx=5, pady=5)
        self.tl_canvas.bind("<Button-1>", self._on_tl_click)
        self.tl_canvas.bind("<B1-Motion>", self._on_tl_drag)
        self.tl_canvas.bind("<Configure>", lambda e: self._draw_timeline())

        # Loading Overlay
        self.loading_frm = ctk.CTkFrame(self, fg_color=BG_DARK)
        self.loading_frm.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.loading_lbl = ctk.CTkLabel(self.loading_frm, text="Preparing Workspace...", font=ctk.CTkFont(size=18))
        self.loading_lbl.pack(expand=True)

    # ── Methods ───────────────────────────────────────────────────────────────
    def _render_frame(self, frame_idx):
        t = frame_idx / 25.0
        v_clip = self._get_item_at_time("video", t)
        if not v_clip: 
            self._draw_timeline()
            return
        
        source_t = (t - v_clip["timeline_start"]) * v_clip["speed"] + v_clip["start"]
        
        if self._current_cap_path != v_clip["path"]:
            if self.cap: self.cap.release()
            self.cap = cv2.VideoCapture(v_clip["path"])
            self._current_cap_path = v_clip["path"]

        self.cap.set(cv2.CAP_PROP_POS_MSEC, source_t * 1000.0)
        ok, frame = self.cap.read()
        if not ok: return

        # Apply Aspect Ratio
        frame = self._apply_ratio(frame)
        
        # Subtitles
        sub_text, prog = self._find_sub(t)
        frame = draw_subtitles_on_frame(frame, sub_text, self.style, prog)

        # Display on Canvas
        cw, ch = self.canvas.winfo_width() or 800, self.canvas.winfo_height() or 450
        h, w = frame.shape[:2]
        scale = min(cw/w, ch/h)
        frame = cv2.resize(frame, (int(w*scale), int(h*scale)))
        img = ImageTk.PhotoImage(Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)))
        self.canvas.delete("all")
        self.canvas.create_image(cw//2, ch//2, anchor="center", image=img)
        self.canvas._img = img
        
        total = self._get_total_duration()
        self.time_label.configure(text=f"{int(t//60):02}:{int(t%60):02} / {int(total//60):02}:{int(total%60):02}")
        self._draw_timeline()

    def _find_sub(self, t):
        for s in self.segments:
            if s["start"] <= t <= s["end"]:
                return s["text"], (t - s["start"]) / max(s["end"] - s["start"], 0.001)
        return "", 0.5

    def _toggle_play(self):
        self.is_playing = not self.is_playing
        self.play_btn.configure(text="⏸" if self.is_playing else "▶")
        if self.is_playing:
            pygame.mixer.music.play(start=self.frame_index/25.0)
            self._play_loop()
        else:
            pygame.mixer.music.pause()

    def _play_loop(self):
        if not self.is_playing: return
        if not pygame.mixer.music.get_busy(): 
            self._toggle_play()
            return
        
        pos = pygame.mixer.music.get_pos() / 1000.0
        # If frame_index was non-zero when we started playing, pos is relative to that
        # But pygame get_pos is a bit tricky. For now, simple increment or sync.
        self.frame_index = int((self.frame_index/25.0 + 0.015) * 25.0)
        self._render_frame(self.frame_index)
        self.after(15, self._play_loop)

    def _split_clip(self):
        t = self.frame_index / 25.0
        v_clip = self._get_item_at_time("video", t)
        if not v_clip: return
        idx = self.tracks["video"].index(v_clip)
        source_t = (t - v_clip["timeline_start"]) * v_clip["speed"] + v_clip["start"]
        
        new_clip = v_clip.copy()
        v_clip["end"] = source_t
        new_clip["start"] = source_t
        self.tracks["video"].insert(idx + 1, new_clip)
        self._draw_timeline()

    def _get_item_at_time(self, track, t):
        accum = 0.0
        for item in self.tracks[track]:
            dur = (item["end"] - item["start"]) / item["speed"]
            if accum <= t < accum + dur:
                item["timeline_start"] = accum
                return item
            accum += dur
        return None

    def _get_total_duration(self):
        return sum((c["end"] - c["start"]) / c["speed"] for c in self.tracks["video"]) or 0.1

    def _draw_timeline(self):
        if not hasattr(self, 'tl_canvas'): return
        self.tl_canvas.delete("all")
        w, h = self.tl_canvas.winfo_width(), self.tl_canvas.winfo_height()
        if w < 10: return
        
        total = max(60, self._get_total_duration())
        scale = (w - 70) / total * self.v_zoom.get()
        
        for idx, tr in enumerate(["text", "video", "audio"]):
            ty = 40 + idx * 55
            self.tl_canvas.create_text(5, ty + 25, text=tr.upper(), fill=TEXT_GRAY, anchor="w")
            self.tl_canvas.create_rectangle(60, ty, w, ty + 45, fill="#1a1a1a", outline="")
            
            accum = 0.0
            items = self.tracks[tr if tr != "text" else "video"]
            for i, item in enumerate(items):
                dur = (item["end"] - item["start"]) / item["speed"]
                x1, x2 = 60 + accum * scale, 60 + (accum + dur) * scale
                color = ACCENT_BLUE if tr == "video" else "#22c55e" if tr == "audio" else "#ff4444"
                self.tl_canvas.create_rectangle(x1, ty + 5, x2, ty + 40, fill=color, outline="#333333")
                accum += dur

        px = 60 + (self.frame_index / 25.0) * scale
        self.tl_canvas.create_line(px, 0, px, h, fill=ACCENT_RED, width=2)
        self.tl_canvas.create_polygon(px-8, 0, px+8, 0, px, 15, fill=ACCENT_RED)

    def _switch_tab(self, n):
        for w in self.res_container.winfo_children(): w.destroy()
        ctk.CTkLabel(self.res_container, text=n, font=ctk.CTkFont(weight="bold")).pack(pady=10)
        
        if n == "Assets":
            ctk.CTkButton(self.res_container, text="+ Import", command=self._import_asset).pack(fill="x", padx=10, pady=5)
            for a in self.assets:
                f = ctk.CTkFrame(self.res_container, fg_color="#1d1d1d")
                f.pack(fill="x", pady=2, padx=5)
                ctk.CTkLabel(f, text=f"📄 {a['name'][:20]}", font=ctk.CTkFont(size=11)).pack(side="left", padx=5)
                ctk.CTkButton(f, text="+", width=25, height=20, command=lambda x=a: self._add_to_timeline(x)).pack(side="right", padx=5)
        elif n == "Text":
            ctk.CTkButton(self.res_container, text="Generate Subtitles", command=self._run_auto_sub).pack(fill="x", padx=10)

    def _import_asset(self):
        p = filedialog.askopenfilename(filetypes=[("Media Files", "*.mp4 *.mov *.avi *.mkv *.wav *.mp3 *.jpg *.png")])
        if p:
            self.assets.append({"path": p, "name": os.path.basename(p), "type": "auto"})
            self._switch_tab("Assets")

    def _add_to_timeline(self, asset):
        cap = cv2.VideoCapture(asset["path"])
        fps = cap.get(cv2.CAP_PROP_FPS) or 25
        count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        duration = count / fps if count > 0 else 5.0
        cap.release()
        
        track = "video" if asset["path"].lower().endswith(('.mp4', '.mov', '.avi', '.jpg', '.png')) else "audio"
        self.tracks[track].append({
            "path": asset["path"], "name": asset["name"], "start": 0.0, "end": duration,
            "speed": 1.0, "volume": 1.0, "timeline_start": self._get_total_duration()
        })
        self._draw_timeline()

    def _on_tl_click(self, e):
        w = self.tl_canvas.winfo_width()
        total = max(60, self._get_total_duration())
        scale = (w - 70) / total * self.v_zoom.get()
        self.frame_index = int(max(0, (e.x - 60) / scale) * 25.0)
        self._render_frame(self.frame_index)

    def _on_tl_drag(self, e): self._on_tl_click(e)
    def _apply_ratio(self, frame):
        h, w = frame.shape[:2]
        r = {"16:9": 16/9, "9:16": 9/16, "1:1": 1.0, "4:3": 4/3}.get(self.v_ratio.get(), 16/9)
        cur = w/h
        if abs(cur - r) < 0.01: return frame
        if cur > r:
            nw = int(h * r)
            x = (w - nw) // 2
            return frame[:, x:x+nw]
        else:
            nh = int(w / r)
            y = (h - nh) // 2
            return frame[y:y+nh, :]

    def _run_auto_sub(self):
        threading.Thread(target=lambda: (setattr(self, 'segments', transcribe_video(self.tracks['video'][0]['path'])), self.after(0, lambda: messagebox.showinfo("Done", "Transcribed"))), daemon=True).start()

    def _start_autosave_timer(self):
        def save():
            while True:
                time.sleep(30)
                try: 
                    with open("project_save.json", "w") as f: json.dump({"tracks": self.tracks}, f)
                except: pass
        threading.Thread(target=save, daemon=True).start()

    def _export(self): messagebox.showinfo("Export", "Exporting...")
    def _back(self):
        if self.cap: self.cap.release()
        pygame.mixer.music.unload()
        self._on_back()
