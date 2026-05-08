"""editor_page.py – The main editor interface (Video, Settings, Timeline)"""

import tkinter as tk
from tkinter import ttk, messagebox, colorchooser, filedialog
import threading
import queue
import cv2
import os
import subprocess
from PIL import Image, ImageTk
import pygame
import tempfile

import imageio_ffmpeg
from subtitle_config import SubtitleStyle, FONT_CHOICES, ANIMATION_CHOICES, DECORATION_CHOICES, POSITION_CHOICES, PRESETS
from subtitle_renderer import draw_subtitles_on_frame
from transcriber import transcribe_video
from video_exporter import export_video_with_subtitles

BG       = "#0a0a0a"
PANEL    = "#111111"
ACCENT   = "#ffffff"
ACCENT2  = "#cccccc"
TEXT     = "#f0f0f0"
SUBTEXT  = "#666666"
ENTRY_BG = "#1a1a1a"
DIVIDER  = "#222222"
GREEN    = "#22c55e"


class EditorPage(tk.Frame):
    def __init__(self, master, video_path, model, on_back):
        super().__init__(master, bg=BG)
        self.master = master
        self._video_path = video_path
        self._on_back = on_back

        preset = PRESETS[0]
        self.style = SubtitleStyle(
            font_name=preset["font"],
            font_size=preset["size"],
            font_color=preset["color"],
            decoration=preset["deco"],
            animation=preset["anim"],
        )
        self.segments = []
        
        self._active_row = -1
        self.timeline_rows = []

        # Playback state
        self.cap = None
        self.fps_video = 25
        self.total_frames = 1
        self.frame_index = 0
        self.is_playing = False
        self._play_start_t = 0.0
        self._audio_path = None

        pygame.mixer.init()

        # Style UI vars
        self.v_font       = tk.StringVar(value=self.style.font_name)
        self.v_size       = tk.IntVar(value=self.style.font_size)
        self.v_color      = tk.StringVar(value=self.style.font_color)
        self.v_deco_color = tk.StringVar(value=self.style.decoration_color)
        self.v_bold       = tk.BooleanVar(value=self.style.bold)
        self.v_italic     = tk.BooleanVar(value=self.style.italic)
        self.v_deco       = tk.StringVar(value=self.style.decoration)
        self.v_anim       = tk.StringVar(value=self.style.animation)
        self.v_position   = tk.StringVar(value=self.style.position)
        self.v_wpl        = tk.IntVar(value=0)

        self._build()
        self._start_transcribe(model)

    # ── BUILD UI ─────────────────────────────────────────────────────────────
    def _build(self):
        # Header
        hdr = tk.Frame(self, bg=PANEL, height=48)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        self._btn(hdr, "← กลับ", self._back, PANEL).pack(side="left", padx=8)
        tk.Label(hdr, text="Auto Subtitle Editor", font=("Segoe UI", 12, "bold"),
                 bg=PANEL, fg=TEXT).pack(side="left", padx=10)
        self._btn(hdr, "💾 Export วิดีโอ", self._export, ACCENT, fg=BG).pack(side="right", padx=12)
        
        # Body
        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True, padx=8, pady=8)

        # Left panel: Settings (CapCut style)
        left = tk.Frame(body, bg=PANEL, width=320)
        left.pack(side="left", fill="y")
        left.pack_propagate(False)

        # Center panel: Video preview
        center = tk.Frame(body, bg=BG)
        center.pack(side="left", fill="both", expand=True, padx=8)

        # Right panel: Timeline List
        right = tk.Frame(body, bg=PANEL, width=360)
        right.pack(side="left", fill="y")
        right.pack_propagate(False)

        self._build_left(left)
        self._build_center(center)
        self._build_right(right)
        
        # Loading overlay
        self.loading_frm = tk.Frame(self, bg=BG)
        self.loading_frm.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.loading_lbl = tk.Label(self.loading_frm, text="กำลังเตรียมไฟล์...",
                                    font=("Segoe UI", 16), bg=BG, fg=TEXT)
        self.loading_lbl.pack(expand=True)
        
    def _build_left(self, parent):
        tk.Label(parent, text="การตั้งค่าซับไตเติล", font=("Segoe UI", 11, "bold"),
                 bg=PANEL, fg=TEXT).pack(anchor="w", padx=16, pady=16)

        cv = tk.Canvas(parent, bg=PANEL, highlightthickness=0)
        sb = ttk.Scrollbar(parent, orient="vertical", command=cv.yview)
        frm = tk.Frame(cv, bg=PANEL)
        frm.bind("<Configure>", lambda e: cv.configure(scrollregion=cv.bbox("all")))
        cv.create_window((0, 0), window=frm, anchor="nw", width=300)
        cv.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        cv.pack(fill="both", expand=True)

        P = dict(padx=16, pady=4, sticky="w")

        # --- Preset Grid ---
        tk.Label(frm, text="สไตล์สำเร็จรูป", font=("Segoe UI", 9, "bold"),
                 bg=PANEL, fg=TEXT).grid(row=0, column=0, columnspan=2, sticky="w", padx=16, pady=(0, 6))
        
        grid = tk.Frame(frm, bg=PANEL)
        grid.grid(row=1, column=0, columnspan=2, padx=16, pady=(0, 16), sticky="w")
        
        self._pcvs = []
        for i, p in enumerate(PRESETS):
            c = tk.Canvas(grid, width=125, height=60, bg="#161616",
                          highlightthickness=1,
                          highlightbackground=ACCENT if i == 0 else DIVIDER,
                          cursor="hand2")
            c.grid(row=i // 2, column=i % 2, padx=3, pady=3)
            c.create_text(62, 24, text="ตัวอย่าง", font=("Tahoma", 11, "bold"),
                          fill=p["color"])
            c.create_text(62, 48, text=p["name"], font=("Segoe UI", 8),
                          fill=SUBTEXT)
            c.bind("<Button-1>", lambda e, idx=i: self._sel_preset(idx))
            self._pcvs.append(c)

        # --- Custom Settings ---
        tk.Label(frm, text="ฟอนต์", bg=PANEL, fg=SUBTEXT, font=("Segoe UI", 9)).grid(row=2, column=0, **P)
        ttk.Combobox(frm, textvariable=self.v_font, values=FONT_CHOICES, state="readonly", width=18).grid(row=2, column=1, **P)

        tk.Label(frm, text="ขนาด", bg=PANEL, fg=SUBTEXT, font=("Segoe UI", 9)).grid(row=3, column=0, **P)
        tk.Spinbox(frm, from_=10, to=120, textvariable=self.v_size, width=8, bg=ENTRY_BG, fg=TEXT, relief="flat").grid(row=3, column=1, **P)

        tk.Label(frm, text="สีข้อความ", bg=PANEL, fg=SUBTEXT, font=("Segoe UI", 9)).grid(row=4, column=0, **P)
        self._color_row(frm, 4, 1, self.v_color)

        tk.Label(frm, text="สีตกแต่ง", bg=PANEL, fg=SUBTEXT, font=("Segoe UI", 9)).grid(row=5, column=0, **P)
        self._color_row(frm, 5, 1, self.v_deco_color)

        tk.Label(frm, text="รูปแบบ", bg=PANEL, fg=SUBTEXT, font=("Segoe UI", 9)).grid(row=6, column=0, **P)
        f_style = tk.Frame(frm, bg=PANEL)
        f_style.grid(row=6, column=1, **P)
        tk.Checkbutton(f_style, text="B", variable=self.v_bold, bg=PANEL, fg=TEXT, selectcolor=ENTRY_BG, font=("Segoe UI", 9, "bold")).pack(side="left")
        tk.Checkbutton(f_style, text="I", variable=self.v_italic, bg=PANEL, fg=TEXT, selectcolor=ENTRY_BG, font=("Segoe UI", 9, "italic")).pack(side="left")

        tk.Label(frm, text="การตกแต่ง", bg=PANEL, fg=SUBTEXT, font=("Segoe UI", 9)).grid(row=7, column=0, **P)
        ttk.Combobox(frm, textvariable=self.v_deco, values=DECORATION_CHOICES, state="readonly", width=18).grid(row=7, column=1, **P)

        tk.Label(frm, text="แอนิเมชัน", bg=PANEL, fg=SUBTEXT, font=("Segoe UI", 9)).grid(row=8, column=0, **P)
        ttk.Combobox(frm, textvariable=self.v_anim, values=ANIMATION_CHOICES, state="readonly", width=18).grid(row=8, column=1, **P)

        tk.Label(frm, text="ตำแหน่ง", bg=PANEL, fg=SUBTEXT, font=("Segoe UI", 9)).grid(row=9, column=0, **P)
        ttk.Combobox(frm, textvariable=self.v_position, values=POSITION_CHOICES, state="readonly", width=18).grid(row=9, column=1, **P)

        tk.Label(frm, text="จำนวนซับต่อหน้า", bg=PANEL, fg=SUBTEXT, font=("Segoe UI", 9)).grid(row=10, column=0, **P)
        wf = tk.Frame(frm, bg=PANEL)
        wf.grid(row=10, column=1, **P)
        for lbl, val in [("ไม่แบ่ง", 0), ("1 คำ", 1), ("2 คำ", 2), ("3 คำ", 3), ("4 คำ", 4)]:
            tk.Radiobutton(wf, text=lbl, variable=self.v_wpl, value=val,
                           bg=PANEL, fg=TEXT, selectcolor=ENTRY_BG,
                           activebackground=PANEL, activeforeground=TEXT,
                           font=("Segoe UI", 8)).pack(side="left")

        for var in [self.v_font, self.v_size, self.v_color, self.v_deco_color,
                    self.v_bold, self.v_italic, self.v_deco, self.v_anim, self.v_position]:
            var.trace_add("write", lambda *_: self._sync_and_render())
            
        self.v_wpl.trace_add("write", lambda *_: self._apply_wpl())

    def _sel_preset(self, idx):
        for i, c in enumerate(self._pcvs):
            c.configure(highlightbackground=ACCENT if i == idx else DIVIDER)
        
        p = PRESETS[idx]
        self.v_color.set(p["color"])
        self.v_deco.set(p["deco"])
        self.v_anim.set(p["anim"])

    def _color_row(self, parent, row, col, var):
        f = tk.Frame(parent, bg=PANEL)
        f.grid(row=row, column=col, sticky="w", pady=4, padx=16)
        swatch = tk.Label(f, bg=var.get(), width=2, relief="flat")
        swatch.pack(side="left", padx=(0, 4))
        def pick():
            c = colorchooser.askcolor(color=var.get(), title="เลือกสี")[1]
            if c:
                var.set(c)
                swatch.configure(bg=c)
        tk.Button(f, text="🎨", command=pick, bg=PANEL, fg=TEXT, relief="flat", cursor="hand2").pack(side="left")

    def _build_center(self, parent):
        # Video Canvas
        self.canvas = tk.Canvas(parent, bg="#000000", highlightthickness=0, cursor="hand2")
        self.canvas.pack(fill="both", expand=True)
        
        # Drag to reposition
        self.canvas.bind("<B1-Motion>", self._on_canvas_drag)

        # Controls
        ctrl = tk.Frame(parent, bg=BG)
        ctrl.pack(fill="x", pady=(8, 4))
        self._btn(ctrl, "⏮", self._seek_start, PANEL).pack(side="left", padx=2)
        self.play_btn = self._btn(ctrl, "▶ เล่น", self._toggle_play, ACCENT, fg=BG)
        self.play_btn.pack(side="left", padx=2)

        self.time_label = tk.Label(ctrl, text="0:00 / 0:00", bg=BG, fg=SUBTEXT, font=("Segoe UI", 9))
        self.time_label.pack(side="right", padx=4)

        # Horizontal Timeline Canvas
        self.tl_canvas = tk.Canvas(parent, bg=PANEL, height=120, highlightthickness=0, cursor="hand2")
        self.tl_canvas.pack(fill="x", pady=(0, 8))
        self.tl_canvas.bind("<Button-1>", self._on_tl_click)
        self.tl_canvas.bind("<B1-Motion>", self._on_tl_drag)
        self.tl_canvas.bind("<Configure>", lambda e: self._draw_timeline())

    def _build_right(self, parent):
        hdr = tk.Frame(parent, bg=PANEL)
        hdr.pack(fill="x", padx=16, pady=(16, 8))
        tk.Label(hdr, text="Timeline", bg=PANEL, fg=TEXT, font=("Segoe UI", 11, "bold")).pack(side="left")
        self._btn(hdr, "+ Add", self._add_segment, "#1a1a1a").pack(side="right")

        cv = tk.Canvas(parent, bg=PANEL, highlightthickness=0)
        sb = ttk.Scrollbar(parent, orient="vertical", command=cv.yview)
        self.timeline_frame = tk.Frame(cv, bg=PANEL)
        self.timeline_frame.bind("<Configure>", lambda e: cv.configure(scrollregion=cv.bbox("all")))
        cv.create_window((0, 0), window=self.timeline_frame, anchor="nw")
        cv.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        cv.pack(fill="both", expand=True, padx=4)
        
        def _on_wheel(e):
            cv.yview_scroll(-1 * (e.delta // 120), "units")
        cv.bind("<MouseWheel>", _on_wheel)
        self.timeline_frame.bind("<MouseWheel>", _on_wheel)

    def _btn(self, parent, text, cmd, bg, fg=TEXT, font=("Segoe UI", 9)):
        b = tk.Button(parent, text=text, command=cmd, bg=bg, fg=fg,
                      activebackground="#333333", activeforeground=TEXT,
                      relief="flat", cursor="hand2", font=font, padx=12, pady=4)
        return b

    # ── TRANSCRIBE ───────────────────────────────────────────────────────────
    def _start_transcribe(self, model):
        threading.Thread(target=self._run_transcribe_bg, args=(model,), daemon=True).start()

    def _run_transcribe_bg(self, model):
        def _prog(msg):
            self.after(0, lambda: self.loading_lbl.configure(text=msg))
        
        try:
            # 1. extract audio to wav for fast playback
            _prog("กำลังเตรียมเสียง (Audio extraction)...")
            fd, tmp_wav = tempfile.mkstemp(suffix=".wav")
            os.close(fd)
            self._audio_path = tmp_wav
            ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
            subprocess.run([ffmpeg_exe, "-y", "-i", self._video_path, "-vn", "-ar", "44100", "-ac", "2", tmp_wav],
                           capture_output=True, check=True)

            # 2. Transcribe
            self.segments = transcribe_video(self._video_path, model_size=model, progress_cb=_prog)
            
            # Done
            self.after(0, self._on_transcribe_done)
        except Exception as e:
            self.after(0, lambda: messagebox.showerror("Error", str(e)))
            self.after(0, self._back)

    def _on_transcribe_done(self):
        self.loading_frm.destroy()
        import copy
        self.original_segments = copy.deepcopy(self.segments)
        self._apply_wpl()
        self._load_video()

    # ── VIDEO PLAYBACK & DRAG ────────────────────────────────────────────────
    def _load_video(self):
        self.cap = cv2.VideoCapture(self._video_path)
        self.total_frames = int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT))
        self.fps_video = self.cap.get(cv2.CAP_PROP_FPS) or 25
        pygame.mixer.music.load(self._audio_path)
        self._render_frame(0)

    def _on_canvas_drag(self, event):
        """ลากเพื่อปรับตำแหน่ง custom"""
        cw = self.canvas.winfo_width()
        ch = self.canvas.winfo_height()
        if cw == 0 or ch == 0: return
        self.v_position.set("custom")
        self.style.position = "custom"
        self.style.custom_x = max(0.0, min(1.0, event.x / cw))
        self.style.custom_y = max(0.0, min(1.0, event.y / ch))
        if not self.is_playing:
            self._render_frame(self.frame_index)

    def _sync_and_render(self):
        s = self.style
        s.font_name = self.v_font.get()
        try:
            s.font_size = self.v_size.get()
        except tk.TclError:
            pass # ignore empty spinbox
        s.font_color = self.v_color.get()
        s.decoration_color = self.v_deco_color.get()
        s.bold = self.v_bold.get()
        s.italic = self.v_italic.get()
        s.decoration = self.v_deco.get()
        s.animation = self.v_anim.get()
        s.position = self.v_position.get()
        if not self.is_playing:
            self._render_frame(self.frame_index)

    def _render_frame(self, idx):
        if not self.cap: return
        self.cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ok, frame = self.cap.read()
        if not ok: return

        t = idx / self.fps_video
        text, progress = self._find_seg(t)
        frame = draw_subtitles_on_frame(frame, text, self.style, progress)

        cw = self.canvas.winfo_width() or 640
        ch = self.canvas.winfo_height() or 360
        h, w = frame.shape[:2]
        scale = min(cw / w, ch / h)
        nw, nh = int(w * scale), int(h * scale)
        small = cv2.resize(frame, (nw, nh), interpolation=cv2.INTER_LINEAR)
        rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
        
        tk_img = ImageTk.PhotoImage(Image.fromarray(rgb))
        self.canvas.delete("all")
        self.canvas.create_image(cw // 2, ch // 2, anchor="center", image=tk_img)
        self.canvas._img = tk_img

        self._update_time_lbl(idx)
        self._draw_timeline()
        
        act = self._seg_index_at(t)
        if act != self._active_row:
            self._highlight_row(act)

    def _update_time_lbl(self, idx):
        dur = self.total_frames / self.fps_video
        cur_t = idx / self.fps_video
        self.time_label.configure(text=f"{int(cur_t//60)}:{int(cur_t%60):02} / {int(dur//60)}:{int(dur%60):02}")

    def _toggle_play(self):
        self.is_playing = not self.is_playing
        if self.is_playing:
            self.play_btn.configure(text="⏸ หยุด", bg="#333333", fg=TEXT)
            self._play_start_t = self.frame_index / self.fps_video
            try:
                pygame.mixer.music.play(start=self._play_start_t)
            except:
                pass
            self._play_loop()
        else:
            self.play_btn.configure(text="▶ เล่น", bg=ACCENT, fg=BG)
            try: pygame.mixer.music.pause()
            except: pass

    def _play_loop(self):
        if not self.is_playing: return
        if not pygame.mixer.music.get_busy():
            self._toggle_play()
            return
            
        audio_pos = pygame.mixer.music.get_pos() / 1000.0
        target_t = self._play_start_t + audio_pos
        target_idx = int(target_t * self.fps_video)
        
        if target_idx >= self.total_frames:
            self._toggle_play()
            return
            
        # Read frames up to target_idx
        if target_idx > self.frame_index:
            skip = target_idx - self.frame_index
            if skip > 15: # If too far behind, jump
                self.cap.set(cv2.CAP_PROP_POS_FRAMES, target_idx)
                self.frame_index = target_idx
                ok, frame = self.cap.read()
                if ok: 
                    self._draw_and_show(frame, target_idx)
            else:
                ok = True
                frame = None
                for _ in range(skip):
                    ok, frame = self.cap.read()
                    if not ok: break
                    self.frame_index += 1
                if ok and frame is not None:
                    self._draw_and_show(frame, self.frame_index)
                    
        self.after(20, self._play_loop)

    def _draw_and_show(self, frame, idx):
        # Extract drawing logic to keep loop fast
        t = idx / self.fps_video
        text, progress = self._find_seg(t)
        frame = draw_subtitles_on_frame(frame, text, self.style, progress)

        cw = self.canvas.winfo_width() or 640
        ch = self.canvas.winfo_height() or 360
        h, w = frame.shape[:2]
        scale = min(cw / w, ch / h)
        nw, nh = int(w * scale), int(h * scale)
        small = cv2.resize(frame, (nw, nh), interpolation=cv2.INTER_LINEAR)
        rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
        
        tk_img = ImageTk.PhotoImage(Image.fromarray(rgb))
        self.canvas.delete("all")
        self.canvas.create_image(cw // 2, ch // 2, anchor="center", image=tk_img)
        self.canvas._img = tk_img

        self._update_time_lbl(idx)
        self._draw_timeline()
        
        act = self._seg_index_at(t)
        if act != self._active_row:
            self._highlight_row(act)

    def _seek_start(self):
        self._seek_to(0)

    def _seek_to(self, idx):
        was_playing = self.is_playing
        if was_playing:
            self._toggle_play()
        
        self.frame_index = max(0, min(idx, self.total_frames - 1))
        self._render_frame(self.frame_index)
        
        if was_playing:
            self._toggle_play()

    # ── TIMELINE ─────────────────────────────────────────────────────────────
    def _apply_wpl(self):
        wpl = self.v_wpl.get()
        if wpl <= 0:
            if hasattr(self, 'original_segments'):
                import copy
                self.segments = copy.deepcopy(self.original_segments)
            self._populate_timeline()
            if hasattr(self, 'cap') and self.cap and not self.is_playing:
                self._render_frame(self.frame_index)
            return

        if not hasattr(self, 'original_segments'):
            import copy
            self.original_segments = copy.deepcopy(self.segments)

        # Try importing pythainlp
        has_pythainlp = False
        try:
            from pythainlp.tokenize import word_tokenize
            has_pythainlp = True
        except ImportError:
            pass

        new_segments = []
        for seg in self.original_segments:
            if "words" in seg and seg["words"]:
                # Use word-level timestamps from Whisper
                words = seg["words"]
                for i in range(0, len(words), wpl):
                    chunk = words[i:i+wpl]
                    if not chunk: continue
                    text = "".join([w["word"] for w in chunk]).strip()
                    if not text: continue
                    new_segments.append({
                        "start": chunk[0]["start"],
                        "end": chunk[-1]["end"],
                        "text": text,
                        "words": chunk
                    })
            else:
                # Fallback for custom model (no word timestamps)
                text = seg["text"].strip()
                if has_pythainlp:
                    # Use PyThaiNLP for accurate Thai tokenization
                    words = word_tokenize(text, engine="newmm")
                    # Filter out whitespace-only tokens
                    words = [w for w in words if w.strip()]
                    
                    if len(words) == 0: continue
                    
                    # Split into chunks of `wpl` words
                    for i in range(0, len(words), wpl):
                        chunk = words[i:i+wpl]
                        chunk_text = "".join(chunk)
                        start = seg["start"] + (i / len(words)) * (seg["end"] - seg["start"])
                        end = seg["start"] + (min(len(words), i+wpl) / len(words)) * (seg["end"] - seg["start"])
                        new_segments.append({"start": start, "end": end, "text": chunk_text})
                else:
                    words = text.split()
                    if len(words) == 1 and len(text) > 15:
                        chars_per_chunk = max(5, 8 * wpl)
                        for i in range(0, len(text), chars_per_chunk):
                            chunk_text = text[i:i+chars_per_chunk]
                            start = seg["start"] + (i / len(text)) * (seg["end"] - seg["start"])
                            end = seg["start"] + (min(len(text), i+chars_per_chunk) / len(text)) * (seg["end"] - seg["start"])
                            new_segments.append({"start": start, "end": end, "text": chunk_text})
                    else:
                        # Space-separated fallback
                        if len(words) == 0: continue
                        for i in range(0, len(words), wpl):
                            chunk = words[i:i+wpl]
                            chunk_text = " ".join(chunk)
                            start = seg["start"] + (i / len(words)) * (seg["end"] - seg["start"])
                            end = seg["start"] + (min(len(words), i+wpl) / len(words)) * (seg["end"] - seg["start"])
                            new_segments.append({"start": start, "end": end, "text": chunk_text})

        self.segments = new_segments
        self._populate_timeline()
        if hasattr(self, 'cap') and self.cap and not self.is_playing:
            self._render_frame(self.frame_index)

    def _fmt_ts(self, t):
        m, s = divmod(t, 60)
        return f"{int(m):02}:{s:05.2f}"

    def _populate_timeline(self):
        for w in self.timeline_frame.winfo_children(): w.destroy()
        self.timeline_rows = []
        self._active_row = -1
        for i, s in enumerate(self.segments):
            self._add_tl_row(i, s)

    def _add_tl_row(self, idx, seg):
        ROW_BG = "#111111"
        row = tk.Frame(self.timeline_frame, bg=ROW_BG, padx=8, pady=6)
        row.pack(fill="x", pady=1, padx=2)

        hdr = tk.Frame(row, bg=ROW_BG)
        hdr.pack(fill="x")

        ts = f"  {self._fmt_ts(seg['start'])} → {self._fmt_ts(seg['end'])}  "
        ts_lbl = tk.Label(hdr, text=ts, bg=DIVIDER, fg=ACCENT2, font=("Segoe UI", 8, "bold"), cursor="hand2")
        ts_lbl.pack(side="left")

        db = self._btn(hdr, "✕ ลบ", lambda i=idx: self._del_seg(i), ROW_BG, fg="#ff4444", font=("Segoe UI", 8, "bold"))
        db.pack(side="right")

        tw = tk.Text(row, height=2, bg=ROW_BG, fg=TEXT, insertbackground=TEXT, relief="flat", font=("Segoe UI", 9), wrap="word", bd=0)
        tw.insert("1.0", seg["text"].strip())
        tw.pack(fill="x", pady=(3, 0))

        def _click(e, i=idx):
            self._seek_to(int(self.segments[i]["start"] * self.fps_video))
            
        def _save(e, i=idx):
            if i < len(self.segments):
                self.segments[i]["text"] = tw.get("1.0", "end-1c")
                if not self.is_playing: 
                    self._render_frame(self.frame_index)

        for w in [row, hdr, ts_lbl]:
            w.bind("<Button-1>", _click, add="+")
            
        tw.bind("<KeyRelease>", _save)
        tw.bind("<FocusOut>", _save)
        
        self.timeline_rows.append((row, tw))

    def _highlight_row(self, idx):
        if 0 <= self._active_row < len(self.timeline_rows):
            r, t = self.timeline_rows[self._active_row]
            r.configure(bg="#111111"); t.configure(bg="#111111")
        self._active_row = idx
        if 0 <= idx < len(self.timeline_rows):
            r, t = self.timeline_rows[idx]
            r.configure(bg="#2a2a2a"); t.configure(bg="#2a2a2a")
            self.timeline_frame.update_idletasks()
            th = self.timeline_frame.winfo_height()
            if th > 0:
                self.timeline_frame.master.yview_moveto(max(0, (r.winfo_y() + r.winfo_height()/2) / th - 0.5))

    def _del_seg(self, i):
        if 0 <= i < len(self.segments):
            self.segments.pop(i)
            self._populate_timeline()
            if not self.is_playing:
                self._render_frame(self.frame_index)

    def _add_segment(self):
        t = self.frame_index / max(self.fps_video, 1)
        ins = len(self.segments)
        for i, s in enumerate(self.segments):
            if s["start"] > t: ins = i; break
        self.segments.insert(ins, {"start": t, "end": t+2.0, "text": "ใส่ข้อความ..."})
        self._populate_timeline()

    def _find_seg(self, t):
        for s in self.segments:
            if s["start"] <= t <= s["end"]:
                return s["text"], (t - s["start"]) / max(s["end"] - s["start"], 0.001)
        return "", 0.5

    def _seg_index_at(self, t):
        for i, s in enumerate(self.segments):
            if s["start"] <= t <= s["end"]: return i
        return -1

    def _on_tl_click(self, e):
        w = self.tl_canvas.winfo_width()
        if w < 10: return
        frac = max(0.0, min(1.0, e.x / w))
        self._seek_to(int(frac * self.total_frames))
        
    def _on_tl_drag(self, e):
        self._on_tl_click(e)

    def _draw_timeline(self):
        if not hasattr(self, 'tl_canvas') or self.total_frames <= 1: return
        self.tl_canvas.delete("all")
        w = self.tl_canvas.winfo_width()
        h = self.tl_canvas.winfo_height()
        if w < 10: return
        
        duration = self.total_frames / self.fps_video
        if duration <= 0: return

        # Draw subtitle blocks
        for i, s in enumerate(self.segments):
            x1 = (s["start"] / duration) * w
            x2 = (s["end"] / duration) * w
            bg = "#3a86ff" if i == self._active_row else "#1e3a5f"
            
            # Subtitle Box
            self.tl_canvas.create_rectangle(x1, 20, x2, h - 20, fill=bg, outline="#555555", width=1)
            # Text inside box
            text_disp = s["text"][:15] + "..." if len(s["text"]) > 15 else s["text"]
            self.tl_canvas.create_text(x1 + 4, h//2, text=text_disp, fill="#ffffff", font=("Segoe UI", 8), anchor="w")
            
        # Draw playhead
        cur_x = (self.frame_index / self.total_frames) * w
        self.tl_canvas.create_line(cur_x, 0, cur_x, h, fill="#ffffff", width=2)
        self.tl_canvas.create_polygon(cur_x-6, 0, cur_x+6, 0, cur_x, 12, fill="#ffffff")

    # ── EXPORT ───────────────────────────────────────────────────────────────
    def _export(self):
        if self.is_playing: self._toggle_play()
        out = filedialog.asksaveasfilename(defaultextension=".mp4", filetypes=[("MP4", "*.mp4")])
        if not out: return
        
        top = tk.Toplevel(self)
        top.title("Exporting...")
        top.geometry("400x100")
        top.configure(bg=PANEL)
        top.transient(self.master)
        top.grab_set()
        
        lbl = tk.Label(top, text="กำลัง Render...", bg=PANEL, fg=TEXT, font=("Segoe UI", 10))
        lbl.pack(pady=10)
        pb = ttk.Progressbar(top, orient="horizontal", mode="determinate", length=350)
        pb.pack(pady=10)

        def _prog(m):
            if "%" in m:
                try:
                    pct = float(m.split("%")[0].split()[-1])
                    self.after(0, lambda: pb.configure(value=pct))
                except: pass
            self.after(0, lambda: lbl.configure(text=m))

        def _run():
            try:
                export_video_with_subtitles(self._video_path, out, self.segments, self.style, progress_cb=_prog)
                self.after(0, top.destroy)
                self.after(0, lambda: messagebox.showinfo("สำเร็จ", f"บันทึกไฟล์: {out}"))
            except Exception as e:
                self.after(0, top.destroy)
                self.after(0, lambda: messagebox.showerror("ผิดพลาด", str(e)))

        threading.Thread(target=_run, daemon=True).start()

    def _back(self):
        if self.is_playing: self._toggle_play()
        if self.cap: self.cap.release()
        pygame.mixer.music.unload()
        if self._audio_path and os.path.exists(self._audio_path):
            try: os.remove(self._audio_path)
            except: pass
        self._on_back()
