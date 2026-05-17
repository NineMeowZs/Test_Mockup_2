"""editor_page.py – VideoAI Pro · CapCut-Style Multi-Track Editor v5

Layer order (top → bottom in timeline, matches CapCut):
  OVERLAY VIDEO  – picture-in-picture / sticker clips  (purple)
  TEXT           – title / subtitle text clips           (pink)
  ── MAIN VIDEO  – primary video track (largest)         (blue)  ← anchor
  AUDIO 1        – background music                      (teal)
  AUDIO 2        – SFX / voice-over                      (green)

Key behaviours that match CapCut:
  • Main video is the "base" – everything is positioned relative to it
  • Overlay clips sit visually ABOVE main video but in shorter rows
  • Audio plays first before video decode starts (pygame pre-roll)
  • Clicking any track row selects it; property panel adapts
  • Timeline playhead is a thin red line with a diamond at top
  • Clips show waveform bars for audio, gradient fills for video
  • Scrubbing is instant (no background thread during manual seek)
"""

import customtkinter as ctk
import tkinter as tk
from tkinter import messagebox, filedialog
import threading, queue, math, random
import cv2, os, subprocess
from PIL import Image, ImageTk
import pygame
import tempfile, json, time, copy

import imageio_ffmpeg

# ── Optional subtitle modules ─────────────────────────────────────────────────
try:
    from subtitle_config import (SubtitleStyle, FONT_CHOICES,
                                  ANIMATION_CHOICES, POSITION_CHOICES,
                                  DECORATION_CHOICES, PRESETS)
    from subtitle_renderer import draw_subtitles_on_frame
    from transcriber import transcribe_video
    from video_exporter import export_video_with_subtitles
    HAS_SUBTITLES = True
except ImportError:
    HAS_SUBTITLES = False
    FONT_CHOICES = ANIMATION_CHOICES = POSITION_CHOICES = DECORATION_CHOICES = []
    PRESETS = []
    class SubtitleStyle:
        font_name="Arial"; font_size=32; font_color="#ffffff"
        decoration="outline"; animation="none"; position="bottom_center"
        margin_x=40; margin_y=40; custom_x=0.5; custom_y=0.85
        line_spacing=8; bg_opacity=0.5; max_chars_per_line=40; max_lines=2

# ── Design tokens ─────────────────────────────────────────────────────────────
BG_DEEP     = "#08080b"
BG_DARK     = "#0f0f13"
PANEL_DARK  = "#161619"
PANEL_MID   = "#1c1c22"
PANEL_LIGHT = "#242430"
PANEL_HOV   = "#2a2a38"

C_BLUE      = "#3a86ff"    # main video
C_PURPLE    = "#a855f7"    # overlay / text
C_PINK      = "#ec4899"    # text clips
C_TEAL      = "#14b8a6"    # audio 1
C_GREEN     = "#22c55e"    # audio 2
C_RED       = "#f43f5e"    # playhead / delete
C_AMBER     = "#f59e0b"    # warning / speed

TXT_W  = "#f1f1f6"
TXT_G  = "#6b6b80"
TXT_L  = "#a0a0b8"
BORD   = "#252530"

TL_BG      = "#0d0d11"
TL_ROW_BG  = "#121218"
TL_RULER   = "#0a0a0e"

# ── Track definitions (CapCut order: overlays top → audio bottom) ─────────────
#   rows draw bottom-up in the canvas (y increases downward), but LABEL list
#   is top-to-bottom so overlay appears above main video visually.
TRACKS = [
    # key            label          color      h   kind
    ("overlay",  "OVERLAY",   C_PURPLE,  30, "video"),
    ("text",     "TEXT",      C_PINK,    28, "text"),
    ("subtitle", "SUBTITLE",  C_AMBER,   28, "subtitle"),  # ← subtitle layer
    ("main",     "VIDEO",     C_BLUE,    56, "video"),   # ← anchor / largest
    ("audio1",   "MUSIC",     C_TEAL,    32, "audio"),
    ("audio2",   "SFX / VO",  C_GREEN,   28, "audio"),
]

TRACK_KEYS   = [t[0] for t in TRACKS]
TRACK_BY_KEY = {t[0]: {"label":t[1],"color":t[2],"height":t[3],"kind":t[4]} for t in TRACKS}

RATIO_OPT  = ["16:9", "9:16", "1:1", "4:3", "2.35:1"]
TARGET_FPS = 30
RULER_H    = 22
TGAP       = 3     # gap between track rows (px)
LABEL_W    = 82
EDGE_PX    = 9
MAX_UNDO   = 60
FRAME_BUF  = 6
SNAP_PX    = 12    # pixel distance for magnetic snap
FADE_ZONE  = 8     # px from corner → fade handle


def _ft(t: float) -> str:
    m=int(t//60); s=int(t%60); cs=int((t%1)*100)
    return f"{m:02}:{s:02}.{cs:02}"

def _bright(hx: str, d=40) -> str:
    try:
        r=min(255,int(hx[1:3],16)+d); g=min(255,int(hx[3:5],16)+d); b=min(255,int(hx[5:7],16)+d)
        return f"#{r:02x}{g:02x}{b:02x}"
    except: return hx

def _dark(hx: str, d=30) -> str:
    try:
        r=max(0,int(hx[1:3],16)-d); g=max(0,int(hx[3:5],16)-d); b=max(0,int(hx[5:7],16)-d)
        return f"#{r:02x}{g:02x}{b:02x}"
    except: return hx


# ═════════════════════════════════════════════════════════════════════════════
class EditorPage(ctk.CTkFrame):

    # ── init ──────────────────────────────────────────────────────────────────
    def __init__(self, master, initial_video, on_back):
        super().__init__(master, fg_color=BG_DEEP, corner_radius=0)
        self.master   = master
        self._on_back = on_back

        # Track data: {key: [clip_dict, ...]}
        self.tracks  = {k: [] for k in TRACK_KEYS}
        self.assets  = []
        self.segments: list[dict] = []
        self.style   = SubtitleStyle()

        # Selection
        self.sel_track = "main"
        self.sel_idx   = -1

        # Drag
        self._dm   = None   # drag mode: move/trim_l/trim_r/scrub
        self._dtk  = None
        self._di   = -1
        self._dx0  = 0.0
        self._tl0  = 0.0
        self._st0  = 0.0
        self._en0  = 0.0

        # Playback
        self.cap        = None
        self._cap_path  = None
        self.fi         = 0       # frame index at TARGET_FPS
        self.playing    = False
        self._pt0       = -1.0   # perf_counter at play start (-1 = not set)
        self._pfi0      = 0      # fi at play start
        self._audio_tmp = None

        # Frame buffer
        self._fbuf      = queue.Queue(maxsize=FRAME_BUF)
        self._dec_stop  = threading.Event()
        self._dec_th    = None
        self._disp_img  = None
        self._last_raw_bgr = None  # cache raw frame (before subtitle) for live preview refresh

        pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=512)

        # Undo stacks
        self._undo: list = []
        self._redo: list = []

        # Tk vars
        self.v_ratio  = tk.StringVar(value="16:9")
        self.v_zoom   = tk.DoubleVar(value=1.0)
        self.v_vol    = tk.DoubleVar(value=1.0)
        self.v_speed  = tk.DoubleVar(value=1.0)
        self.v_text   = tk.StringVar(value="")

        self._thumbs:    dict = {}
        self._waveforms: dict = {}   # path → list[float] 0..1 amplitude bars
        self._muted:     dict = {k: False for k in TRACK_KEYS}
        self._solo_key:  str  = ""   # "" = none soloed
        self._multi_sel: list = []   # [(track_key, idx), ...]
        self._jkl_speed: float = 1.0 # J=rev/K=pause/L=fwd speed multiplier

        self._load_video(initial_video)
        self._build_ui()
        # Remove the loading overlay immediately and show first frame —
        # audio will load in the background and won't block the UI.
        self.update_idletasks()
        if hasattr(self, "_overlay") and self._overlay.winfo_exists():
            self._overlay.destroy()
        self._render(0)
        self._tab("Media")
        # Defer a second render after layout settles so canvas has real size
        self.after(200, lambda: self._render(self.fi))
        self._setup_audio(initial_video)
        self._autosave_start()
        self._bind_keys()
        self._extract_waveforms_bg(initial_video)

    # ── load ──────────────────────────────────────────────────────────────────
    def _load_video(self, path):
        cap  = cv2.VideoCapture(path)
        fps  = cap.get(cv2.CAP_PROP_FPS) or 25
        cnt  = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        dur  = cnt / fps
        cap.release()
        asset = {"path":path,"name":os.path.basename(path),"type":"video"}
        self.assets.append(asset)
        self.tracks["main"].append(self._clip(path,asset["name"],0,dur,fps=fps))
        self._push_undo()

    def _clip(self, path, name, start, end, tl=0.0, fps=25.0):
        return {"path":path,"name":name,"start":start,"end":end,
                "speed":1.0,"volume":1.0,"tl":tl,"fps":fps,
                "fade_in":0.0,"fade_out":0.0}

    def _setup_audio(self, path):
        def run():
            self.after(0, lambda: self._status("Loading audio…"))
            try:
                fd, tmp = tempfile.mkstemp(suffix=".wav")
                os.close(fd)
                self._audio_tmp = tmp
                ff = imageio_ffmpeg.get_ffmpeg_exe()
                subprocess.run([ff,"-y","-i",path,"-vn",
                                "-ar","44100","-ac","2",tmp],
                               capture_output=True, timeout=120)
                pygame.mixer.music.load(tmp)
            except Exception as e:
                print(f"[Audio] {e}")
            self.after(0, self._finish_load)
        threading.Thread(target=run, daemon=True).start()

    def _finish_load(self):
        """Called when audio is ready – just update status."""
        self._status("Ready")

    # ── keys ──────────────────────────────────────────────────────────────────
    def _bind_keys(self):
        for seq, fn in [
            ("<space>",          lambda e: self._toggle_play()),
            ("<Delete>",         lambda e: self._del_sel()),
            ("<BackSpace>",      lambda e: self._del_sel()),
            ("<Control-z>",      lambda e: self._undo_do()),
            ("<Control-Z>",      lambda e: self._undo_do()),
            ("<Control-y>",      lambda e: self._redo_do()),
            ("<Control-Y>",      lambda e: self._redo_do()),
            ("<Control-s>",      lambda e: self._save()),
            ("<Control-o>",      lambda e: self._load_project()),
            ("<Control-i>",      lambda e: self._import()),
            ("<Left>",           lambda e: self._step(-1)),
            ("<Right>",          lambda e: self._step(1)),
            ("<Shift-Left>",     lambda e: self._step(-TARGET_FPS)),
            ("<Shift-Right>",    lambda e: self._step(TARGET_FPS)),
            ("<s>",              lambda e: self._split()),
            ("<g>",              lambda e: self._ripple_delete()),
            # J/K/L — CapCut / Premiere style
            ("<j>",              lambda e: self._jkl("J")),
            ("<k>",              lambda e: self._jkl("K")),
            ("<l>",              lambda e: self._jkl("L")),
            ("<m>",              lambda e: self._toggle_mute_sel()),
        ]:
            self.master.bind(seq, fn)

    # ── J/K/L playback ────────────────────────────────────────────────────────
    def _jkl(self, key):
        if key == "K":
            self._stop(); self._jkl_speed = 1.0; return
        if key == "L":
            if self.playing and self._jkl_speed > 0:
                self._jkl_speed = min(4.0, self._jkl_speed * 2)
                self._status(f"Speed ×{self._jkl_speed:.0f}")
            else:
                self._jkl_speed = 1.0; self._play()
        if key == "J":
            if self.playing and self._jkl_speed < 0:
                self._jkl_speed = max(-4.0, self._jkl_speed * 2)
                self._status(f"Reverse ×{abs(self._jkl_speed):.0f}")
            else:
                self._stop()
                self._jkl_speed = -1.0
                self._status("Reverse play (frame step)")

    # ── Real waveform extraction ───────────────────────────────────────────────
    def _extract_waveforms_bg(self, path):
        def run():
            try: self._extract_waveform(path)
            except Exception as e: print(f"[Waveform] {e}")
        threading.Thread(target=run, daemon=True).start()

    def _extract_waveform(self, path, n_bars=200):
        if path in self._waveforms: return
        try:
            import struct as _struct, math as _math
            ff  = imageio_ffmpeg.get_ffmpeg_exe()
            cmd = [ff,"-y","-i",path,"-vn","-ar","8000","-ac","1","-f","s16le","-"]
            proc = subprocess.run(cmd, capture_output=True, timeout=30)
            raw  = proc.stdout
            n    = len(raw) // 2
            if n < 2: raise RuntimeError("No audio")
            samps = _struct.unpack(f"<{n}h", raw[:n*2])
            chunk = max(1, n // n_bars)
            bars  = []
            for i in range(n_bars):
                sl = samps[i*chunk:(i+1)*chunk]
                if not sl: bars.append(0.05); continue
                rms = _math.sqrt(sum(x*x for x in sl) / len(sl))
                bars.append(min(1.0, rms / 6000.0))
            mx = max(bars) or 1.0
            self._waveforms[path] = [b/mx for b in bars]
            self.after(0, self._draw_tl)
        except Exception:
            random.seed(hash(path) % 9999)
            self._waveforms[path] = [0.15 + random.random()*0.85 for _ in range(n_bars)]

    # ── Project load ──────────────────────────────────────────────────────────
    def _load_project(self):
        path = filedialog.askopenfilename(
            title="Open Project",
            filetypes=[("VideoAI Project","*.json"),("All","*.*")])
        if not path: return
        try:
            with open(path) as f: data = json.load(f)
            self.tracks = {k: [] for k in TRACK_KEYS}
            for k in TRACK_KEYS:
                self.tracks[k] = data.get("tracks", {}).get(k, [])
            if data.get("assets"): self.assets = data["assets"]
            self._push_undo(); self._draw_tl(); self._render(0)
            self._tab("Media"); self._status(f"Loaded: {os.path.basename(path)}")
            for k in ("audio1","audio2"):
                for cl in self.tracks[k]:
                    if cl.get("path"): self._extract_waveforms_bg(cl["path"])
        except Exception as ex: messagebox.showerror("Load Error", str(ex))

    # ── Mute / Solo (real) ────────────────────────────────────────────────────
    def _toggle_mute_sel(self):
        k = self.sel_track
        self._muted[k] = not self._muted.get(k, False)
        self._status(f"{'Muted' if self._muted[k] else 'Unmuted'}: {TRACK_BY_KEY[k]['label']}")
        self._build_track_controls(); self._draw_tl()

    def _toggle_mute_track(self, key):
        self._muted[key] = not self._muted.get(key, False)
        self._status(f"{'Muted' if self._muted[key] else 'Unmuted'}: {TRACK_BY_KEY[key]['label']}")
        self._build_track_controls(); self._draw_tl()

    def _solo_track(self, key):
        self._solo_key = "" if self._solo_key == key else key
        self._status(f"Solo: {TRACK_BY_KEY[key]['label']}" if self._solo_key else "Solo off")
        self._build_track_controls(); self._draw_tl()

    def _is_active(self, key):
        if self._solo_key: return key == self._solo_key
        return not self._muted.get(key, False)

    # ── Ripple delete (G key) ─────────────────────────────────────────────────
    def _ripple_delete(self):
        items = self.tracks.get(self.sel_track, [])
        if not (0 <= self.sel_idx < len(items)):
            self._status("Nothing selected for ripple delete (G)"); return
        clip  = items[self.sel_idx]
        dur   = (clip["end"] - clip["start"]) / max(clip["speed"], 0.01)
        tl_rm = clip.get("tl", 0.0)
        items.pop(self.sel_idx)
        for c in items:
            if c.get("tl", 0.0) >= tl_rm + dur - 0.01:
                c["tl"] = max(0.0, c["tl"] - dur)
        self.sel_idx = max(0, self.sel_idx - 1)
        self._push_undo(); self._draw_tl()
        self._status(f"Ripple delete — pulled {_ft(dur)}")

    # ── Magnetic snap ─────────────────────────────────────────────────────────
    def _snap(self, tl: float, excl_k: str, excl_i: int) -> float:
        thresh = SNAP_PX / self._scale()
        best = tl; best_d = thresh
        candidates = [0.0]
        for k, clips in self.tracks.items():
            for i, c in enumerate(clips):
                if k == excl_k and i == excl_i: continue
                dur = (c["end"]-c["start"])/max(c["speed"],0.01)
                candidates += [c.get("tl",0.0), c.get("tl",0.0)+dur]
        for sp in candidates:
            d = abs(sp - tl)
            if d < best_d: best_d = d; best = sp
        if best != tl: self._status(f"Snapped to {_ft(best)}")
        return best

    # ─────────────────────────────────────────────────────────────────────────
    # UI BUILD
    # ─────────────────────────────────────────────────────────────────────────
    def _build_ui(self):
        self._build_header()
        mid = ctk.CTkFrame(self, fg_color="transparent")
        mid.pack(fill="both", expand=True)
        self._build_sidebar(mid)
        self._build_media_panel(mid)
        self._build_preview(mid)
        self._build_props(mid)
        self._build_timeline()
        # loading overlay
        self._overlay = ctk.CTkFrame(self, fg_color=BG_DEEP)
        self._overlay.place(relx=0,rely=0,relwidth=1,relheight=1)
        ctk.CTkLabel(self._overlay,text="Loading…",
                     font=ctk.CTkFont(size=16),text_color=TXT_L
                     ).place(relx=.5,rely=.5,anchor="center")

    # ── header ────────────────────────────────────────────────────────────────
    def _build_header(self):
        h = ctk.CTkFrame(self,height=44,fg_color=PANEL_DARK,corner_radius=0)
        h.pack(side="top",fill="x"); h.pack_propagate(False)

        ctk.CTkButton(h,text="← Back",width=64,height=28,corner_radius=6,
                      fg_color="transparent",hover_color=PANEL_MID,
                      font=ctk.CTkFont(size=11),command=self._back
                      ).pack(side="left",padx=8,pady=8)
        ctk.CTkLabel(h,text="MediaPro",
                     font=ctk.CTkFont(size=14,weight="bold"),
                     text_color=C_BLUE).pack(side="left",padx=4)

        ctk.CTkButton(h,text="Export",height=28,width=80,corner_radius=6,
                      fg_color=C_BLUE,hover_color=_dark(C_BLUE),
                      font=ctk.CTkFont(size=11,weight="bold"),
                      command=self._export).pack(side="right",padx=8,pady=8)

        ctk.CTkButton(h,text="Save",height=28,width=60,corner_radius=6,
                      fg_color=PANEL_MID,hover_color=PANEL_LIGHT,
                      font=ctk.CTkFont(size=10),command=self._save
                      ).pack(side="right",padx=2,pady=8)

        ctk.CTkButton(h,text="Load",height=28,width=60,corner_radius=6,
                      fg_color=PANEL_MID,hover_color=PANEL_LIGHT,
                      font=ctk.CTkFont(size=10),command=self._load_project
                      ).pack(side="right",padx=2,pady=8)

        ctk.CTkOptionMenu(h,values=RATIO_OPT,variable=self.v_ratio,
                          width=82,height=26,corner_radius=6,
                          fg_color=PANEL_MID,button_color=PANEL_LIGHT,
                          command=lambda v: self._render(self.fi)
                          ).pack(side="right",padx=6,pady=8)

        for lbl,cmd in [("↩",self._undo_do),("↪",self._redo_do)]:
            ctk.CTkButton(h,text=lbl,width=30,height=28,corner_radius=6,
                          fg_color="transparent",hover_color=PANEL_MID,
                          font=ctk.CTkFont(size=13),command=cmd
                          ).pack(side="right",padx=1,pady=8)

        self._stat = ctk.CTkLabel(h,text="Space=play  J/K/L=speed  S=split  G=ripple-del  M=mute",
                                   font=ctk.CTkFont(size=8),text_color=TXT_G)
        self._stat.pack(side="left",padx=12)

    # ── icon sidebar ──────────────────────────────────────────────────────────
    def _build_sidebar(self, parent):
        sb = ctk.CTkFrame(parent,width=48,fg_color=PANEL_DARK,corner_radius=0)
        sb.pack(side="left",fill="y"); sb.pack_propagate(False)
        self._tbtn = {}
        # เฉพาะแท็บที่ใช้งานจริง (ลบ Fx / Color / Audio ออก)
        tabs = [("M","Media"),("T","Text"),("S","Subs")]
        for icon,name in tabs:
            b = ctk.CTkButton(sb,text=icon,width=36,height=36,corner_radius=8,
                              fg_color="transparent",hover_color=PANEL_LIGHT,
                              font=ctk.CTkFont(size=10,weight="bold"),
                              command=lambda n=name: self._tab(n))
            b.pack(pady=3,padx=6)
            self._tbtn[name] = b

    # ── media panel ───────────────────────────────────────────────────────────
    def _build_media_panel(self, parent):
        self._mpanel = ctk.CTkFrame(parent,width=255,fg_color=PANEL_DARK,corner_radius=0)
        self._mpanel.pack(side="left",fill="y"); self._mpanel.pack_propagate(False)
        self._ptitle = ctk.CTkLabel(self._mpanel,text="Media",
                                     font=ctk.CTkFont(size=11,weight="bold"),
                                     text_color=TXT_L)
        self._ptitle.pack(anchor="w",padx=12,pady=(10,4))
        self._pscroll = ctk.CTkScrollableFrame(self._mpanel,fg_color="transparent",
                                                scrollbar_button_color=PANEL_LIGHT)
        self._pscroll.pack(fill="both",expand=True,padx=4,pady=4)

    # ── preview ───────────────────────────────────────────────────────────────
    def _build_preview(self, parent):
        wrap = ctk.CTkFrame(parent,fg_color="transparent")
        wrap.pack(side="left",fill="both",expand=True)

        self.canvas = tk.Canvas(wrap,bg="#000000",highlightthickness=0)
        self.canvas.pack(fill="both",expand=True,padx=8,pady=(8,0))

        # ── Overlay drag: ให้ลาก text/subtitle clip บนคานวิดีโอได้โดยตรง ─────
        self._ov_dragging = False
        self._ov_drag_clip = None
        self.canvas.bind("<Button-1>",      self._canvas_ov_press)
        self.canvas.bind("<B1-Motion>",     self._canvas_ov_drag)
        self.canvas.bind("<ButtonRelease-1>",self._canvas_ov_release)

        self._scrub_v = tk.DoubleVar(value=0)
        self._scrub = ctk.CTkSlider(wrap,from_=0,to=1000,variable=self._scrub_v,
                                     height=10,corner_radius=4,
                                     button_color=C_RED,progress_color=C_RED,
                                     fg_color=PANEL_MID,command=self._scrub_seek)
        self._scrub.pack(fill="x",padx=8,pady=(2,0))

        ctrl = ctk.CTkFrame(wrap,height=50,fg_color=PANEL_DARK,corner_radius=0)
        ctrl.pack(fill="x"); ctrl.pack_propagate(False)

        row = ctk.CTkFrame(ctrl,fg_color="transparent")
        row.place(relx=.5,rely=.5,anchor="center")

        self._mb(row,"|<",self._go_start).pack(side="left",padx=2)
        self._mb(row,"<<",self._skip_b).pack(side="left",padx=2)

        self._pbtn = ctk.CTkButton(row,text="▶",width=52,height=40,corner_radius=20,
                                    fg_color=TXT_W,text_color=BG_DEEP,
                                    hover_color="#d8d8e8",
                                    font=ctk.CTkFont(size=16,weight="bold"),
                                    command=self._toggle_play)
        self._pbtn.pack(side="left",padx=8)

        self._mb(row,">>",self._skip_f).pack(side="left",padx=2)
        self._mb(row,">|",self._go_end).pack(side="left",padx=2)

        ctk.CTkFrame(row,width=1,height=26,fg_color=BORD).pack(side="left",padx=10)
        ctk.CTkButton(row,text="Split [S]",width=76,height=26,corner_radius=6,
                      fg_color=PANEL_MID,hover_color=PANEL_LIGHT,text_color=TXT_L,
                      font=ctk.CTkFont(size=9),command=self._split
                      ).pack(side="left",padx=2)
        ctk.CTkButton(row,text="Delete",width=60,height=26,corner_radius=6,
                      fg_color=PANEL_MID,hover_color="#2a1015",text_color=C_RED,
                      font=ctk.CTkFont(size=9),command=self._del_sel
                      ).pack(side="left",padx=2)

        self._tlbl = ctk.CTkLabel(ctrl,text="00:00.00 / 00:00.00",
                                   font=ctk.CTkFont(size=9),text_color=TXT_G)
        self._tlbl.pack(side="right",padx=12)

    def _mb(self,p,t,c):
        return ctk.CTkButton(p,text=t,width=30,height=26,corner_radius=6,
                             fg_color="transparent",hover_color=PANEL_MID,
                             font=ctk.CTkFont(size=10),command=c)

    # ── Properties panel: context-sensitive (แสดงตาม clip ที่ select) ──────────
    def _build_props(self, parent):
        """Right panel: แสดง properties ตาม clip ที่เลือก (context-sensitive)"""
        pp = ctk.CTkFrame(parent, width=225, fg_color=PANEL_DARK, corner_radius=0)
        pp.pack(side="left", fill="y"); pp.pack_propagate(False)
        self._pp = pp
        ctk.CTkLabel(pp, text="Properties",
                     font=ctk.CTkFont(size=11, weight="bold"),
                     text_color=TXT_L).pack(anchor="w", padx=12, pady=(10,4))
        # Dynamic area — clear+rebuild ทุกครั้งที่ selection เปลี่ยน
        self._pp_dyn = ctk.CTkScrollableFrame(pp, fg_color="transparent",
                                               scrollbar_button_color=PANEL_LIGHT)
        self._pp_dyn.pack(fill="both", expand=True, padx=4, pady=(0,4))
        # Track controls ด้านล่าง (แสดงตลอด)
        ctk.CTkFrame(pp, height=1, fg_color=BORD).pack(fill="x")
        ctk.CTkLabel(pp, text="TRACK CONTROLS",
                     font=ctk.CTkFont(size=8, weight="bold"),
                     text_color=TXT_G).pack(anchor="w", padx=12, pady=(6,2))
        self._track_ctrl_frame = ctk.CTkFrame(pp, fg_color="transparent")
        self._track_ctrl_frame.pack(fill="x", padx=12, pady=(0,8))
        self._build_track_controls()
        self._refresh_props()  # แสดง initial state

    def _refresh_props(self):
        """Clear dynamic area แล้ว rebuild ตาม clip ที่ select"""
        sc = self._pp_dyn
        for w in sc.winfo_children(): w.destroy()
        items = self.tracks.get(self.sel_track, [])
        if not (0 <= self.sel_idx < len(items)):
            ctk.CTkLabel(sc, text="เลือก clip ใน timeline\nเพื่อตั้งค่า",
                         font=ctk.CTkFont(size=10), text_color=TXT_G,
                         justify="center").pack(pady=40)
            return
        clip = items[self.sel_idx]
        kind = TRACK_BY_KEY.get(self.sel_track, {}).get("kind", "")
        if   self.sel_track == "subtitle": self._props_subtitle(sc, clip)
        elif self.sel_track == "text":     self._props_text(sc, clip)
        elif kind == "audio":              self._props_audio(sc, clip)
        else:                              self._props_video(sc, clip)

    def _plbl(self, sc, t, col=None):
        ctk.CTkLabel(sc, text=t, font=ctk.CTkFont(size=9, weight="bold"),
                     text_color=col or TXT_G).pack(anchor="w", pady=(8,2))

    def _props_video(self, sc, clip):
        """Video: speed + volume"""
        self._plbl(sc, "Speed")
        self.v_speed.set(clip.get("speed", 1.0))
        self._spd_lbl = ctk.CTkLabel(sc, text=f"{clip.get('speed',1):.2f}×",
                                      font=ctk.CTkFont(size=9), text_color=TXT_G)
        ctk.CTkSlider(sc, from_=0.1, to=3.0, variable=self.v_speed,
                      progress_color=C_TEAL, button_color=C_TEAL,
                      command=self._apply_speed).pack(fill="x", padx=4)
        self._spd_lbl.pack(anchor="e", padx=4)
        self._plbl(sc, "Volume")
        self.v_vol.set(clip.get("volume", 1.0))
        self._vol_lbl = ctk.CTkLabel(sc, text=f"{int(clip.get('volume',1)*100)}%",
                                      font=ctk.CTkFont(size=9), text_color=TXT_G)
        ctk.CTkSlider(sc, from_=0, to=2, variable=self.v_vol,
                      progress_color=C_GREEN, button_color=C_GREEN,
                      command=self._apply_vol).pack(fill="x", padx=4)
        self._vol_lbl.pack(anchor="e", padx=4)
        self._props_info(sc, clip)

    def _props_audio(self, sc, clip):
        """Audio: volume เท่านั้น"""
        self._plbl(sc, "Volume")
        self.v_vol.set(clip.get("volume", 1.0))
        self._vol_lbl = ctk.CTkLabel(sc, text=f"{int(clip.get('volume',1)*100)}%",
                                      font=ctk.CTkFont(size=9), text_color=TXT_G)
        ctk.CTkSlider(sc, from_=0, to=2, variable=self.v_vol,
                      progress_color=C_GREEN, button_color=C_GREEN,
                      command=self._apply_vol).pack(fill="x", padx=4)
        self._vol_lbl.pack(anchor="e", padx=4)
        self._props_info(sc, clip)

    def _props_text(self, sc, clip):
        """Text clip: edit + font size/color + drag hint"""
        self._plbl(sc, "ข้อความ", C_PINK)
        tv = tk.StringVar(value=clip.get("name",""))
        ctk.CTkEntry(sc, textvariable=tv, height=28, corner_radius=6,
                     fg_color=PANEL_MID, border_color=C_PINK
                     ).pack(fill="x", padx=4, pady=(0,3))
        def _save():
            clip["name"] = tv.get().strip() or clip["name"]
            self._push_undo(); self._draw_tl(); self._refresh_preview()
        ctk.CTkButton(sc, text="✓ บันทึก", height=24, corner_radius=6,
                      fg_color=C_PINK, hover_color=_dark(C_PINK),
                      font=ctk.CTkFont(size=9, weight="bold"),
                      command=_save).pack(fill="x", padx=4, pady=(0,6))
        self._plbl(sc, "ขนาดฟอนต์")
        sz_v = tk.IntVar(value=clip.get("font_size", 36))
        sz_lbl = ctk.CTkLabel(sc, text=f"{sz_v.get()}px",
                              font=ctk.CTkFont(size=9), text_color=TXT_G)
        ctk.CTkSlider(sc, from_=12, to=96, variable=sz_v,
                      progress_color=C_PINK, button_color=C_PINK,
                      command=lambda v: (clip.update({"font_size":int(float(v))}),
                                        sz_lbl.configure(text=f"{int(float(v))}px"),
                                        self._refresh_preview())
                      ).pack(fill="x", padx=4)
        sz_lbl.pack(anchor="e", padx=4)
        self._plbl(sc, "สี (#hex)")
        cv = tk.StringVar(value=clip.get("font_color","#ffffff"))
        ce = ctk.CTkEntry(sc, textvariable=cv, height=26, corner_radius=6,
                          fg_color=PANEL_MID)
        ce.pack(fill="x", padx=4, pady=(0,4))
        ce.bind("<Return>", lambda e: (clip.update({"font_color":cv.get()}),
                                       self._refresh_preview()))
        self._plbl(sc, "◎ ลากบนวิดีโอเพื่อย้าย", C_AMBER)
        self._props_info(sc, clip)

    def _props_subtitle(self, sc, clip):
        """Subtitle: edit text + font size + drag hint"""
        self._plbl(sc, "ข้อความซับ", C_AMBER)
        sv = tk.StringVar(value=clip.get("sub_text", clip.get("name","")))
        ctk.CTkEntry(sc, textvariable=sv, height=28, corner_radius=6,
                     fg_color=PANEL_MID, border_color=C_AMBER
                     ).pack(fill="x", padx=4, pady=(0,3))
        def _save_sub():
            t = sv.get().strip()
            if not t: return
            clip["sub_text"] = t; clip["name"] = t[:24]
            for seg in self.segments:
                if abs(seg["start"] - clip.get("tl",0)) < 0.1:
                    seg["text"] = t; break
            self._push_undo(); self._draw_tl(); self._refresh_preview()
        ctk.CTkButton(sc, text="✓ บันทึก", height=24, corner_radius=6,
                      fg_color=C_AMBER, hover_color=_dark(C_AMBER),
                      font=ctk.CTkFont(size=9, weight="bold"),
                      command=_save_sub).pack(fill="x", padx=4, pady=(0,6))
        self._plbl(sc, "ขนาดฟอนต์ (ทั้งหมด)")
        sz_v = tk.IntVar(value=self.style.font_size)
        sz_lbl = ctk.CTkLabel(sc, text=f"{sz_v.get()}px",
                              font=ctk.CTkFont(size=9), text_color=TXT_G)
        ctk.CTkSlider(sc, from_=12, to=72, variable=sz_v,
                      progress_color=C_AMBER, button_color=C_AMBER,
                      command=lambda v: (setattr(self.style,"font_size",int(float(v))),
                                        sz_lbl.configure(text=f"{int(float(v))}px"),
                                        self._refresh_preview())
                      ).pack(fill="x", padx=4)
        sz_lbl.pack(anchor="e", padx=4)
        self._plbl(sc, "◎ ลากบนวิดีโอเพื่อย้าย", C_AMBER)
        self._props_info(sc, clip)

    def _props_info(self, sc, clip):
        """Clip metadata footer"""
        ctk.CTkFrame(sc, height=1, fg_color=BORD).pack(fill="x", pady=(10,4))
        dur = (clip["end"]-clip["start"]) / max(clip.get("speed",1), 0.01)
        ctk.CTkLabel(sc,
                     text=f"Name: {clip['name'][:18]}\nDur:  {_ft(dur)}\nPos:  {_ft(clip.get('tl',0))}",
                     font=ctk.CTkFont(size=8), text_color=TXT_G,
                     justify="left").pack(anchor="w", padx=4)

    def _build_track_controls(self):
        for w in self._track_ctrl_frame.winfo_children(): w.destroy()
        for key,(_l,col,_h,_k) in [(t[0],(t[1],t[2],t[3],t[4])) for t in TRACKS]:
            muted  = self._muted.get(key, False)
            soloed = self._solo_key == key
            row_bg = "#1a0a0a" if muted else "#0a1a2a" if soloed else PANEL_MID
            lbl_c  = TXT_G if muted else C_AMBER if soloed else col
            row = ctk.CTkFrame(self._track_ctrl_frame, fg_color=row_bg,
                                corner_radius=6, height=26)
            row.pack(fill="x", pady=2); row.pack_propagate(False)
            ctk.CTkLabel(row, text=_l, font=ctk.CTkFont(size=8,weight="bold"),
                         text_color=lbl_c, width=55).pack(side="left", padx=6)
            if muted:
                ctk.CTkLabel(row, text="MUTED", font=ctk.CTkFont(size=7),
                             text_color=C_RED).pack(side="left")
            elif soloed:
                ctk.CTkLabel(row, text="SOLO", font=ctk.CTkFont(size=7),
                             text_color=C_AMBER).pack(side="left")
            # M button
            m_col = C_RED if muted else PANEL_LIGHT
            ctk.CTkButton(row, text="M", width=22, height=18, corner_radius=4,
                          fg_color=m_col, hover_color=PANEL_HOV,
                          font=ctk.CTkFont(size=7),
                          command=lambda k=key: self._toggle_mute_track(k)
                          ).pack(side="right", padx=(0,4))
            # S button
            s_col = C_AMBER if soloed else PANEL_LIGHT
            ctk.CTkButton(row, text="S", width=22, height=18, corner_radius=4,
                          fg_color=s_col, hover_color=C_BLUE,
                          font=ctk.CTkFont(size=7),
                          command=lambda k=key: self._solo_track(k)
                          ).pack(side="right", padx=2)

    def _psec(self,t):
        ctk.CTkLabel(self._pp,text=t,
                     font=ctk.CTkFont(size=9,weight="bold"),
                     text_color=TXT_G).pack(anchor="w",padx=12,pady=(12,3))

    # ── timeline ──────────────────────────────────────────────────────────────
    def _build_timeline(self):
        outer = ctk.CTkFrame(self,fg_color=PANEL_DARK,corner_radius=0)
        outer.pack(side="bottom",fill="x")
        self._tl_outer = outer

        # toolbar
        tb = ctk.CTkFrame(outer,height=26,fg_color=BG_DARK,corner_radius=0)
        tb.pack(fill="x"); tb.pack_propagate(False)
        ctk.CTkLabel(tb,text="TIMELINE",
                     font=ctk.CTkFont(size=8,weight="bold"),
                     text_color=TXT_G).pack(side="left",padx=12)
        ctk.CTkLabel(tb,text="Drag=move  │  Edge=trim  │  Scroll=zoom  │  RClick=menu  │  M=mute",
                     font=ctk.CTkFont(size=8),text_color=TXT_G
                     ).pack(side="left",padx=4)
        ctk.CTkLabel(tb,text="Zoom",font=ctk.CTkFont(size=8),
                     text_color=TXT_G).pack(side="right",padx=(0,4))
        ctk.CTkSlider(tb,from_=0.2,to=14.0,width=100,variable=self.v_zoom,
                      progress_color=C_BLUE,button_color=C_BLUE,
                      command=lambda v: self._draw_tl()
                      ).pack(side="right",padx=(0,10),pady=4)

        body = tk.Frame(outer,bg=PANEL_DARK)
        body.pack(fill="both",expand=True)

        # label column
        self._lcol = tk.Frame(body,bg=PANEL_DARK,width=LABEL_W)
        self._lcol.pack(side="left",fill="y"); self._lcol.pack_propagate(False)
        tk.Label(self._lcol,text="",bg=TL_RULER,height=1).pack(fill="x")
        for key,col,h,kind in [(t[0],t[2],t[3],t[4]) for t in TRACKS]:
            meta = TRACK_BY_KEY[key]
            row_h = h + TGAP*2 + 2
            fr = tk.Frame(self._lcol,bg=PANEL_DARK,height=row_h)
            fr.pack(fill="x"); fr.pack_propagate(False)
            # color strip
            tk.Frame(fr,bg=col,width=3).pack(side="left",fill="y")
            inner = tk.Frame(fr,bg=PANEL_DARK)
            inner.pack(side="left",fill="both",expand=True)
            tk.Label(inner,text=meta["label"],bg=PANEL_DARK,fg=col,
                     font=("Helvetica",7,"bold")).pack(anchor="w",padx=4,pady=2)
            # mute indicator placeholder
            tk.Label(inner,text=kind[0].upper(),bg=PANEL_DARK,fg=TXT_G,
                     font=("Helvetica",6)).pack(anchor="w",padx=4)

        # canvas
        self._tlc = tk.Canvas(body,bg=TL_BG,highlightthickness=0,height=230)
        self._tlc.pack(side="left",fill="both",expand=True)
        hs = tk.Scrollbar(outer,orient="horizontal",command=self._tlc.xview)
        hs.pack(fill="x")
        self._tlc.configure(xscrollcommand=hs.set)

        self._tlc.bind("<Button-1>",       self._tl_press)
        self._tlc.bind("<B1-Motion>",       self._tl_drag)
        self._tlc.bind("<ButtonRelease-1>", self._tl_release)
        self._tlc.bind("<Button-3>",        self._tl_rclick)
        self._tlc.bind("<Configure>",       lambda e: self._draw_tl())
        self._tlc.bind("<MouseWheel>",      self._tl_zoom)
        self._tlc.bind("<Motion>",          self._tl_hover)

    # ─────────────────────────────────────────────────────────────────────────
    # Tab panels
    # ─────────────────────────────────────────────────────────────────────────
    def _tab(self, name):
        self._ptitle.configure(text=name)
        for w in self._pscroll.winfo_children(): w.destroy()
        getattr(self,f"_pt_{name.lower()}",lambda: None)()
        for n,b in self._tbtn.items():
            b.configure(fg_color=PANEL_MID if n==name else "transparent")

    def _pt_media(self):
        ctk.CTkButton(self._pscroll,text="+ Import Media",height=32,corner_radius=6,
                      fg_color=C_BLUE,hover_color=_dark(C_BLUE),
                      font=ctk.CTkFont(size=10,weight="bold"),
                      command=self._import).pack(fill="x",pady=(0,8))
        for a in self.assets: self._acard(a)

    def _pt_text(self):
        ctk.CTkLabel(self._pscroll,text="Text Content",
                     font=ctk.CTkFont(size=10,weight="bold"),
                     text_color=TXT_G).pack(anchor="w",pady=(0,4))
        ctk.CTkEntry(self._pscroll,placeholder_text="Enter text…",
                     height=32,corner_radius=6,
                     fg_color=PANEL_MID,border_color=BORD,
                     textvariable=self.v_text).pack(fill="x",pady=(0,6))
        ctk.CTkButton(self._pscroll,text="Add Text Clip",
                      height=30,corner_radius=6,fg_color=C_PINK,
                      command=self._add_text).pack(fill="x")

    def _pt_effects(self):
        for eff,col in [("Blur",C_PURPLE),("Glow",C_TEAL),("Sharpen",C_BLUE),
                        ("Vignette",C_AMBER),("Vintage",C_PINK),("Cold","#60a5fa"),("Warm",C_AMBER)]:
            f = ctk.CTkFrame(self._pscroll,fg_color=PANEL_MID,corner_radius=8,height=38)
            f.pack(fill="x",pady=2); f.pack_propagate(False)
            ctk.CTkLabel(f,text=eff,font=ctk.CTkFont(size=10),
                         text_color=col).pack(side="left",padx=10)
            ctk.CTkButton(f,text="Apply",width=52,height=22,corner_radius=5,
                          fg_color=PANEL_LIGHT).pack(side="right",padx=6)

    def _pt_color(self):
        for lbl,col,lo,hi in [
            ("Brightness",C_TEAL,-100,100),("Contrast",C_BLUE,-100,100),
            ("Saturation",C_GREEN,-100,100),("Hue",C_PURPLE,-180,180),
            ("Temperature",C_AMBER,-100,100)]:
            ctk.CTkLabel(self._pscroll,text=lbl,font=ctk.CTkFont(size=9),
                         text_color=TXT_G).pack(anchor="w",padx=4,pady=(8,0))
            ctk.CTkSlider(self._pscroll,from_=lo,to=hi,
                          progress_color=col,button_color=col
                          ).pack(fill="x",padx=4,pady=(0,2))

    def _pt_audio(self):
        ctk.CTkButton(self._pscroll,text="+ Import Audio",height=32,corner_radius=6,
                      fg_color=C_TEAL,hover_color=_dark(C_TEAL),
                      command=self._import_audio).pack(fill="x",pady=(0,8))
        for a in [x for x in self.assets if x.get("type")=="audio"]:
            self._acard(a)

    def _pt_subs(self):
        ctk.CTkLabel(self._pscroll,text="Auto Subtitles",
                     font=ctk.CTkFont(size=10,weight="bold"),
                     text_color=TXT_G).pack(anchor="w",pady=(0,6))
        ctk.CTkButton(self._pscroll,text="Generate Subtitles",height=32,corner_radius=6,
                      fg_color=C_BLUE if HAS_SUBTITLES else PANEL_MID,
                      command=self._sub_dialog).pack(fill="x")
        if self.segments:
            ctk.CTkLabel(self._pscroll,
                         text=f"✓ {len(self.segments)} segments loaded",
                         font=ctk.CTkFont(size=9),text_color=C_GREEN
                         ).pack(anchor="w",pady=(6,0))
            # Export row
            row = ctk.CTkFrame(self._pscroll,fg_color="transparent")
            row.pack(fill="x",pady=(4,2))
            ctk.CTkButton(row,text="Export with Subs",height=28,corner_radius=6,
                          fg_color=C_GREEN,hover_color=_dark(C_GREEN),
                          command=self._export_subs
                          ).pack(side="left",fill="x",expand=True,padx=(0,2))
            ctk.CTkButton(row,text="Clear",height=28,width=50,corner_radius=6,
                          fg_color=PANEL_MID,command=self._clear_subs
                          ).pack(side="left")
            # SRT row
            ctk.CTkButton(self._pscroll,text="💾  Save SRT…",height=28,corner_radius=6,
                          fg_color=C_AMBER,hover_color=_dark(C_AMBER),
                          font=ctk.CTkFont(size=9,weight="bold"),
                          command=self._save_srt).pack(fill="x",pady=(0,4))

    def _acard(self, asset):
        f = ctk.CTkFrame(self._pscroll,fg_color=PANEL_MID,corner_radius=8)
        f.pack(fill="x",pady=3)
        thumb = self._get_thumb(asset)
        if thumb:
            lbl = tk.Label(f,image=thumb,bg=PANEL_MID)
            lbl.image = thumb; lbl.pack(side="left",padx=5,pady=5)
        else:
            col = {"video":C_BLUE,"audio":C_TEAL,"image":C_PURPLE}.get(asset.get("type"),TXT_G)
            ctk.CTkLabel(f,text=asset["type"][0].upper() if asset.get("type") else "?",
                         font=ctk.CTkFont(size=15,weight="bold"),
                         width=32,text_color=col).pack(side="left",padx=6,pady=6)
        info = ctk.CTkFrame(f,fg_color="transparent")
        info.pack(side="left",fill="both",expand=True)
        nm = asset["name"]; sh = nm[:18]+"…" if len(nm)>18 else nm
        ctk.CTkLabel(info,text=sh,font=ctk.CTkFont(size=10),text_color=TXT_W).pack(anchor="w")
        ctk.CTkLabel(info,text=asset.get("type","?"),
                     font=ctk.CTkFont(size=8),text_color=TXT_G).pack(anchor="w")
        ctk.CTkButton(f,text="+",width=26,height=26,corner_radius=5,
                      fg_color=C_BLUE,hover_color=_dark(C_BLUE),
                      font=ctk.CTkFont(size=12),
                      command=lambda x=asset: self._add_to_tl(x)
                      ).pack(side="right",padx=6,pady=6)

    def _get_thumb(self, asset):
        p = asset.get("path","")
        if p in self._thumbs: return self._thumbs[p]
        if asset.get("type") not in ("video","image"): return None
        try:
            cap=cv2.VideoCapture(p); ok,fr=cap.read(); cap.release()
            if not ok: return None
            h,w=fr.shape[:2]; tw=int(w*40/h)
            fr=cv2.resize(fr,(tw,40))
            ph=ImageTk.PhotoImage(Image.fromarray(cv2.cvtColor(fr,cv2.COLOR_BGR2RGB)))
            self._thumbs[p]=ph; return ph
        except: return None

    # ─────────────────────────────────────────────────────────────────────────
    # Rendering
    # ─────────────────────────────────────────────────────────────────────────
    def _render(self, fi: int):
        t = fi / float(TARGET_FPS)
        self._upd_time(t); self._upd_scrub(t)
        self._draw_tl(); self._upd_info()

        clip = self._at("main", t)
        if not clip: return

        src_t = (t - clip["tl"]) * clip["speed"] + clip["start"]
        if self.cap is None or self._cap_path != clip["path"]:
            if self.cap: self.cap.release()
            self.cap = cv2.VideoCapture(clip["path"])
            self._cap_path = clip["path"]

        self.cap.set(cv2.CAP_PROP_POS_MSEC, src_t*1000.0)
        ok, frame = self.cap.read()
        if ok: self._show(frame)

    def _show(self, bgr):
        """Render frame: crop → cache raw → draw text clips → draw subtitle → display"""
        bgr = self._crop_ratio(bgr)
        self._last_raw_bgr = bgr.copy()  # cache raw frame (ก่อนวาดซับ)
        t = self.fi / float(TARGET_FPS)
        if HAS_SUBTITLES:
            # ── วาด text clips (ตำแหน่งสัมพัทธ์ custom_x/custom_y) ────────
            for tc in self.tracks.get("text", []):
                dur = max(tc["end"]-tc["start"], 0.05) / max(tc.get("speed",1), 0.01)
                tl  = tc.get("tl", 0.0)
                if tl <= t <= tl + dur and tc.get("name","").strip():
                    try:
                        from subtitle_config import SubtitleStyle
                        ts = SubtitleStyle()
                        ts.font_name  = tc.get("font_name", "Tahoma")
                        ts.font_size  = tc.get("font_size", 36)
                        ts.font_color = tc.get("font_color", "#ffffff")
                        ts.decoration = tc.get("decoration", "shadow")
                        ts.animation  = "none"
                        ts.position   = "custom"
                        ts.custom_x   = tc.get("custom_x", 0.5)
                        ts.custom_y   = tc.get("custom_y", 0.2)
                        bgr = draw_subtitles_on_frame(bgr, tc["name"], ts, 0.5)
                    except Exception: pass
            # ── วาด subtitle clip (ถ้ามี) ────────────────────────────────
            if self._sub_visible if hasattr(self, "_sub_visible") else True:
                sub, prog = self._find_sub(t)
                if sub:
                    try: bgr = draw_subtitles_on_frame(bgr, sub, self.style, prog)
                    except Exception: pass
        cw=max(self.canvas.winfo_width(),640); ch=max(self.canvas.winfo_height(),360)
        h,w=bgr.shape[:2]; sc=min(cw/w,ch/h)
        ow,oh=int(w*sc),int(h*sc)
        bgr=cv2.resize(bgr,(ow,oh),interpolation=cv2.INTER_LINEAR)
        img=ImageTk.PhotoImage(Image.fromarray(cv2.cvtColor(bgr,cv2.COLOR_BGR2RGB)))
        self.canvas.delete("all")
        self.canvas.create_image(cw//2,ch//2,anchor="center",image=img)
        self._disp_img=img

    def _refresh_preview(self):
        """Re-composite subtitle onto cached raw frame (ไม่ต้อง seek วิดีโอ)"""
        if self._last_raw_bgr is not None:
            self._show(self._last_raw_bgr.copy())
        else:
            self._render(self.fi)

    def _crop_ratio(self, f):
        h,w=f.shape[:2]
        rm={"16:9":16/9,"9:16":9/16,"1:1":1.0,"4:3":4/3,"2.35:1":2.35}
        r=rm.get(self.v_ratio.get(),16/9); cur=w/h
        if abs(cur-r)<.01: return f
        if cur>r: nw=int(h*r); x=(w-nw)//2; return f[:,x:x+nw]
        else:     nh=int(w/r); y=(h-nh)//2; return f[y:y+nh,:]

    def _find_sub(self, t):
        # ถ้ามี subtitle track ให้ดึงจากที่นั่น (primary)
        for clip in self.tracks.get("subtitle", []):
            dur = max(clip["end"] - clip["start"], 0.05) / max(clip["speed"], 0.01)
            tl  = clip.get("tl", 0.0)
            if tl <= t <= tl + dur:
                prog = (t - tl) / max(dur, 0.001)
                return clip.get("sub_text", clip.get("name", "")), prog
        # fallback: self.segments list (backward compat)
        for s in self.segments:
            if s["start"] <= t <= s["end"]:
                return s["text"], (t - s["start"]) / max(s["end"] - s["start"], 0.001)
        return "", 0.5

    def _upd_time(self,t):
        total=self._dur()
        self._tlbl.configure(text=f"{_ft(t)} / {_ft(total)}")

    def _upd_scrub(self,t):
        total=max(self._dur(),0.1)
        self._scrub_v.set(t/total*1000)

    def _upd_info(self):
        """Refresh right props panel เมื่อ selection เปลี่ยน"""
        self._refresh_props()

    def _save_sub_text(self):
        """Legacy: เรียกจาก _props_subtitle แทน (backward compat)"""
        pass

    # ── Canvas overlay drag: ลาก text/subtitle บนวิดีโอเพื่อย้ายตำแหน่ง ─────────
    def _canvas_ov_press(self, e):
        """เริ่ม drag ถ้า text/subtitle clip ถูก select อยู่"""
        if self.sel_track not in ("text", "subtitle"): return
        items = self.tracks.get(self.sel_track, [])
        if not (0 <= self.sel_idx < len(items)): return
        self._ov_dragging = True
        self._ov_drag_clip = items[self.sel_idx]

    def _canvas_ov_drag(self, e):
        """อัปเดต custom_x, custom_y ของ clip ตามตำแหน่งเมาส์บน canvas"""
        if not self._ov_dragging or self._ov_drag_clip is None: return
        cw = max(self.canvas.winfo_width(), 640)
        ch = max(self.canvas.winfo_height(), 360)
        x  = max(0.0, min(1.0, e.x / cw))
        y  = max(0.0, min(1.0, e.y / ch))
        clip = self._ov_drag_clip
        clip["custom_x"] = x
        clip["custom_y"] = y
        # ถ้าเป็น subtitle ให้อัปเดต style ด้วย (เพื่อ draw_subtitles_on_frame ใช้)
        if self.sel_track == "subtitle":
            self.style.position = "custom"
            self.style.custom_x = x
            self.style.custom_y = y
        self._refresh_preview()

    def _canvas_ov_release(self, e):
        """สิ้นสุด drag และบันทึก undo"""
        if self._ov_dragging:
            self._push_undo()
        self._ov_dragging = False
        self._ov_drag_clip = None

    # ── Smooth playback (background decode + frame buffer) ────────────────────
    def _toggle_play(self):
        if self.playing: self._stop()
        else:            self._play()

    def _play(self):
        self.playing = True
        self._pbtn.configure(text="⏸")

        self._dec_stop.clear()
        # flush old buffer
        while not self._fbuf.empty():
            try: self._fbuf.get_nowait()
            except queue.Empty: break

        # Start decoder thread first, then delay audio until buffer has frames
        self._dec_th = threading.Thread(target=self._dec_worker, daemon=True)
        self._dec_th.start()

        # Wait briefly for buffer to fill (max 150ms) before starting audio
        start_sec = self.fi / float(TARGET_FPS)
        def _start_audio_and_clock():
            deadline = time.perf_counter() + 0.15
            while self._fbuf.empty() and time.perf_counter() < deadline:
                time.sleep(0.005)
            try:
                pygame.mixer.music.play(start=start_sec)
            except Exception:
                pass
            # Set clock AFTER audio starts so they stay in sync
            self._pt0  = time.perf_counter()
            self._pfi0 = self.fi
        threading.Thread(target=_start_audio_and_clock, daemon=True).start()
        # Start tick loop (it will see empty buffer at first and just wait)
        self.after(20, self._tick)

    def _stop(self):
        self.playing = False
        self._dec_stop.set()
        self._pt0 = -1.0  # reset clock sentinel
        self._pbtn.configure(text="▶")
        try: pygame.mixer.music.pause()
        except: pass

    def _dec_worker(self):
        """Background: decode frames into queue ahead of playback."""
        fi = self.fi
        cap_path = None; cap = None
        try:
            while not self._dec_stop.is_set():
                t    = fi / float(TARGET_FPS)
                clip = self._at("main", t)
                if not clip: time.sleep(0.01); continue

                src_t = (t - clip["tl"]) * clip["speed"] + clip["start"]
                if cap is None or cap_path != clip["path"]:
                    if cap: cap.release()
                    cap = cv2.VideoCapture(clip["path"])
                    cap_path = clip["path"]

                cap.set(cv2.CAP_PROP_POS_MSEC, src_t*1000.0)
                ok, fr = cap.read()
                if not ok: time.sleep(0.01); continue
                try:
                    self._fbuf.put((fi, fr), timeout=0.05)
                except queue.Full: pass
                fi += 1
        finally:
            if cap: cap.release()

    # Timeline redraw throttle – avoid redrawing on every frame (kills CPU)
    _tl_redraw_interval = 8   # redraw timeline every N ticks (~8*14ms ≈ 112ms)
    _tl_tick_count      = 0

    def _tick(self):
        if not self.playing: return
        # If clock not set yet (audio pre-roll still pending), just wait
        if self._pt0 < 0:
            self.after(14, self._tick); return

        elapsed   = time.perf_counter() - self._pt0
        target_fi = self._pfi0 + int(elapsed * TARGET_FPS)
        total_fi  = int(self._dur() * TARGET_FPS)

        if target_fi >= total_fi:
            self.fi = 0; self._stop(); self._render(0); return

        # Consume buffer up to target
        shown = None
        while True:
            try:
                fi, fr = self._fbuf.get_nowait()
                if fi <= target_fi: shown = (fi, fr)
                else:
                    try: self._fbuf.put_nowait((fi, fr))
                    except queue.Full: pass
                    break
            except queue.Empty: break

        if shown:
            self.fi = shown[0]
            self._show(shown[1])
            t = self.fi / float(TARGET_FPS)
            self._upd_time(t)
            self._upd_scrub(t)
            # Only redraw timeline every N ticks to save CPU
            EditorPage._tl_tick_count += 1
            if EditorPage._tl_tick_count % EditorPage._tl_redraw_interval == 0:
                self._draw_tl()

        self.after(14, self._tick)   # ~70 fps tick

    def _go_start(self):  self._stop(); self.fi=0; self._render(0)
    def _go_end(self):
        self._stop()
        self.fi=max(0,int(self._dur()*TARGET_FPS)-1); self._render(self.fi)
    def _skip_b(self):
        self._stop(); self.fi=max(0,self.fi-TARGET_FPS*5); self._render(self.fi)
    def _skip_f(self):
        self._stop()
        self.fi=min(int(self._dur()*TARGET_FPS)-1,self.fi+TARGET_FPS*5)
        self._render(self.fi)
    def _step(self,d):
        self._stop()
        self.fi=max(0,min(int(self._dur()*TARGET_FPS)-1,self.fi+d))
        self._render(self.fi)
    def _scrub_seek(self,val):
        self._stop(); total=max(self._dur(),0.1)
        t=float(val)/1000*total; self.fi=int(t*TARGET_FPS); self._render(self.fi)

    # ─────────────────────────────────────────────────────────────────────────
    # Timeline Draw  (CapCut style)
    # ─────────────────────────────────────────────────────────────────────────
    def _draw_tl(self):
        if not hasattr(self,"_tlc"): return
        c=self._tlc; W=c.winfo_width(); H=c.winfo_height()
        if W<10: return
        c.delete("all")

        total  = max(20.0, self._dur()*1.3+5)
        scale  = self._scale()
        cw     = max(W, int(total*scale)+120)
        c.configure(scrollregion=(0,0,cw,H))

        # Ruler
        c.create_rectangle(0,0,cw,RULER_H,fill=TL_RULER,outline="")
        step = self._nice_step(total/self.v_zoom.get())
        t=0.0
        while t<=total+step:
            x=t*scale
            maj=(round(t/step)*step-t)<0.001 if step else True
            c.create_line(x,RULER_H-(9 if maj else 4),x,RULER_H,fill="#363650")
            if maj: c.create_text(x+3,RULER_H//2,text=_ft(t),
                                   fill=TXT_G,anchor="w",font=("Courier",7))
            t=round(t+step/2,6)

        # Tracks (CapCut order: overlay top → audio bottom)
        y = RULER_H+2
        for key,col,th,kind in [(t[0],t[2],t[3],t[4]) for t in TRACKS]:
            row_h = th + TGAP*2
            active = self._is_active(key)
            # Row background — dim if muted
            row_bg = "#0a0a0d" if not active else TL_ROW_BG
            c.create_rectangle(0,y,cw,y+row_h,fill=row_bg,outline="")
            c.create_line(0,y+row_h,cw,y+row_h,fill=BORD)
            if key=="main":
                c.create_line(0,y,cw,y,fill="#252535",width=1)

            # Muted badge on row
            if self._muted.get(key):
                c.create_text(8, y+row_h//2, text="MUTED",
                               fill=C_RED, anchor="w", font=("Helvetica",7,"bold"))
            elif self._solo_key == key:
                c.create_text(8, y+row_h//2, text="SOLO",
                               fill=C_AMBER, anchor="w", font=("Helvetica",7,"bold"))

            for i,item in enumerate(self.tracks[key]):
                dur=(item["end"]-item["start"])/max(item["speed"],0.01)
                tl = item.get("tl",0.0)
                x1 = tl*scale; x2=(tl+dur)*scale
                ty1=y+TGAP; ty2=y+TGAP+th
                sel=(self.sel_track==key and self.sel_idx==i)

                # Dim clip if track is muted/not active
                clip_col = _dark(col,40) if not active else col

                # Shadow
                c.create_rectangle(x1+2,ty1+2,x2+2,ty2+2,fill="#030306",outline="")
                # Body
                c.create_rectangle(x1,ty1,x2,ty2,fill=clip_col,outline="")
                # Darker inner
                c.create_rectangle(x1+1,ty1+6,x2-1,ty2-1,
                                    fill=_dark(clip_col,18),outline="")

                # Audio: waveform bars
                if kind=="audio":
                    self._draw_waveform(c,item,x1,ty1,x2,ty2,clip_col)

                # Video overlay: mini thumbnail hint
                if kind=="video" and key!="main" and x2-x1>20:
                    c.create_rectangle(x1+2,ty1+2,x1+28,ty2-2,
                                        fill=_dark(clip_col,30),outline="")

                # Sheen top strip
                c.create_rectangle(x1,ty1,x2,ty1+4,
                                    fill=_bright(clip_col),outline="")

                # Selection glow
                if sel:
                    c.create_rectangle(x1-1,ty1-1,x2+1,ty2+1,
                                        fill="",outline="#ffffff",width=2)
                    c.create_rectangle(x1-3,ty1-3,x2+3,ty2+3,
                                        fill="",outline=clip_col,width=1)

                # Fade-in triangle (bottom-left corner)
                fade_in = item.get("fade_in", 0.0)
                if fade_in > 0:
                    fx = x1 + fade_in * scale
                    c.create_polygon(x1,ty2, fx,ty2, x1,ty1+th//2,
                                      fill="#000000",outline="",stipple="gray50")

                # Fade-out triangle (bottom-right corner)
                fade_out = item.get("fade_out", 0.0)
                if fade_out > 0:
                    fx = x2 - fade_out * scale
                    c.create_polygon(x2,ty2, fx,ty2, x2,ty1+th//2,
                                      fill="#000000",outline="",stipple="gray50")

                # Fade handles (small triangles at top corners)
                c.create_polygon(x1,ty1, x1+FADE_ZONE,ty1, x1,ty1+FADE_ZONE,
                                  fill=_bright(clip_col,60),outline="")
                c.create_polygon(x2,ty1, x2-FADE_ZONE,ty1, x2,ty1+FADE_ZONE,
                                  fill=_bright(clip_col,60),outline="")

                # Clip label + time (CapCut style)
                if x2-x1>16:
                    name=item.get("name",""); name=name[:16]+"…" if len(name)>16 else name
                    dur_str=_ft(dur)
                    dark_txt = key in ("main",)
                    tc = "#00111f" if dark_txt else "#ffffff"
                    c.create_text(x1+7,ty1+th//2-(5 if th>30 else 0),
                                   text=name,fill=tc,anchor="w",
                                   font=("Helvetica",7,"bold"))
                    if th>28:
                        c.create_text(x1+7,ty1+th//2+8,
                                       text=dur_str,fill=tc,anchor="w",
                                       font=("Courier",6))
                    if x2-x1>70:
                        et=_ft(tl+dur)
                        bw=len(et)*5+4
                        c.create_rectangle(x2-bw-2,ty1+2,x2-2,ty1+11,
                                            fill="#000000",outline="",stipple="gray50")
                        c.create_text(x2-4,ty1+6,text=et,fill="#dddddd",
                                       anchor="e",font=("Courier",6))

                # Resize handles
                for ex in (x1,x2):
                    c.create_rectangle(ex-3,ty1+4,ex+3,ty2-4,
                                        fill="#ffffff",outline="")

            y+=row_h

        # Playhead — thin red line + diamond cap (CapCut style)
        px=(self.fi/float(TARGET_FPS))*scale
        c.create_line(px,0,px,H,fill=C_RED,width=1)
        c.create_polygon(px-6,0,px+6,0,px+2,10,px-2,10,
                          fill=C_RED,outline="")

    def _draw_waveform(self, c, item, x1, ty1, x2, ty2, col):
        """Draw simple amplitude bars for audio clips."""
        path = item.get("path","")
        bars = self._waveforms.get(path)
        if bars is None:
            # Generate fake waveform (replace with real FFT later)
            random.seed(hash(path) % 9999)
            bars = [0.2+random.random()*0.8 for _ in range(80)]
            self._waveforms[path] = bars

        clip_w = x2-x1; mid = (ty1+ty2)//2; amp = (ty2-ty1)//2-3
        bar_w  = max(1, clip_w/len(bars))
        for i,a in enumerate(bars):
            bx = x1+i*bar_w
            if bx>x2: break
            hh = max(1, int(a*amp))
            c.create_line(bx,mid-hh,bx,mid+hh,fill=_bright(col,20))

    def _nice_step(self, span):
        for s in [0.25,0.5,1,2,5,10,15,30,60,120,300,600]:
            if span/s<18: return s
        return 600

    def _scale(self):
        W=self._tlc.winfo_width()
        total=max(20.0,self._dur()*1.3+5)
        return (W-10)/total*self.v_zoom.get()

    # ─────────────────────────────────────────────────────────────────────────
    # Timeline interaction
    # ─────────────────────────────────────────────────────────────────────────
    def _tl_press(self, e):
        self._stop()
        sc=self._scale(); cx=self._tlc.canvasx(e.x)
        t_click=cx/sc

        hit_k=hit_i=None; mode="scrub"
        y=RULER_H+2
        for key,col,th,kind in [(t[0],t[2],t[3],t[4]) for t in TRACKS]:
            row_h=th+TGAP*2; ty1=y+TGAP; ty2=y+TGAP+th
            if ty1<=e.y<=ty2:
                for i,item in enumerate(self.tracks[key]):
                    dur=(item["end"]-item["start"])/max(item["speed"],0.01)
                    tl=item.get("tl",0.0)
                    x1=tl*sc; x2=(tl+dur)*sc
                    if x1-EDGE_PX<=cx<=x2+EDGE_PX:
                        hit_k=key; hit_i=i
                        if cx<=x1+EDGE_PX: mode="trim_l"
                        elif cx>=x2-EDGE_PX: mode="trim_r"
                        else: mode="move"
                        break
                break
            y+=row_h

        if hit_k is not None:
            self.sel_track=hit_k; self.sel_idx=hit_i
            self._dm=mode; self._dtk=hit_k; self._di=hit_i
            cl=self.tracks[hit_k][hit_i]
            self._tl0=cl.get("tl",0.0); self._st0=cl["start"]; self._en0=cl["end"]
            self._dx0=cx
            self.v_speed.set(cl["speed"]); self.v_vol.set(cl["volume"])
            self._spd_lbl.configure(text=f"{cl['speed']:.2f}×")
            self._vol_lbl.configure(text=f"{int(cl['volume']*100)}%")
        else:
            self._dm="scrub"
            self.fi=max(0,int(t_click*TARGET_FPS)); self._render(self.fi)

        self._draw_tl()

    def _tl_drag(self, e):
        cx=self._tlc.canvasx(e.x); sc=self._scale()
        dx=(cx-self._dx0)/sc

        if self._dm=="scrub":
            self.fi=max(0,int(cx/sc*TARGET_FPS)); self._render(self.fi); return
        if not self._dm or not self._dtk: return

        cl=self.tracks[self._dtk][self._di]
        if self._dm=="move":
            raw = max(0.0, self._tl0+dx)
            cl["tl"] = self._snap(raw, self._dtk, self._di)
            self._draw_tl()
            self._refresh_preview()   # อัปเดต preview ทันที
        elif self._dm=="trim_l":
            ns=max(0.0,min(self._st0+dx,cl["end"]-0.05))
            d=ns-self._st0; cl["start"]=ns
            cl["tl"]=max(0.0,self._tl0+d/cl["speed"]); self._draw_tl()
            self._refresh_preview()
        elif self._dm=="trim_r":
            cl["end"]=max(cl["start"]+0.05,self._en0+dx); self._draw_tl()
            self._refresh_preview()

    def _tl_release(self, e):
        if self._dm in ("move","trim_l","trim_r"): self._push_undo()
        self._dm=None; self._dtk=None

    def _tl_hover(self, e):
        cx=self._tlc.canvasx(e.x); sc=self._scale()
        y=RULER_H+2
        for key,col,th,kind in [(t[0],t[2],t[3],t[4]) for t in TRACKS]:
            row_h=th+TGAP*2; ty1=y+TGAP; ty2=y+TGAP+th
            if ty1<=e.y<=ty2:
                for item in self.tracks[key]:
                    dur=(item["end"]-item["start"])/max(item["speed"],0.01)
                    tl=item.get("tl",0.0)
                    x1=tl*sc; x2=(tl+dur)*sc
                    if abs(cx-x1)<=EDGE_PX or abs(cx-x2)<=EDGE_PX:
                        self._tlc.configure(cursor="sb_h_double_arrow"); return
            y+=row_h
        self._tlc.configure(cursor="arrow")

    def _tl_rclick(self, e):
        cx=self._tlc.canvasx(e.x); sc=self._scale()
        y=RULER_H+2
        for key,col,th,kind in [(t[0],t[2],t[3],t[4]) for t in TRACKS]:
            row_h=th+TGAP*2; ty1=y+TGAP; ty2=y+TGAP+th
            if ty1<=e.y<=ty2:
                for i,item in enumerate(self.tracks[key]):
                    dur=(item["end"]-item["start"])/max(item["speed"],0.01)
                    tl=item.get("tl",0.0)
                    x1=tl*sc; x2=(tl+dur)*sc
                    if x1<=cx<=x2:
                        self.sel_track=key; self.sel_idx=i
                        self._ctx_menu(e,key,i); return
            y+=row_h

    def _ctx_menu(self, e, tk_key, idx):
        m=tk.Menu(self,tearoff=0,bg=PANEL_MID,fg=TXT_W,
                   activebackground=C_BLUE,activeforeground=TXT_W,
                   relief="flat",bd=0,font=("Helvetica",10))
        m.add_command(label=" Split Here",          command=self._split)
        m.add_command(label=" Delete",              command=self._del_sel)
        m.add_command(label=" Ripple Delete [G]",   command=self._ripple_delete)
        m.add_command(label=" Duplicate",           command=lambda:self._dup(tk_key,idx))
        m.add_separator()
        for sp in (4.0,2.0,1.5,1.0,0.75,0.5,0.25):
            m.add_command(label=f" Speed ×{sp}",
                          command=lambda s=sp:self._set_speed(idx,tk_key,s))
        m.add_separator()
        m.add_command(label=" Fade In 0.5s",
                      command=lambda:self._set_fade(idx,tk_key,"fade_in",0.5))
        m.add_command(label=" Fade In 1.0s",
                      command=lambda:self._set_fade(idx,tk_key,"fade_in",1.0))
        m.add_command(label=" Fade Out 0.5s",
                      command=lambda:self._set_fade(idx,tk_key,"fade_out",0.5))
        m.add_command(label=" Fade Out 1.0s",
                      command=lambda:self._set_fade(idx,tk_key,"fade_out",1.0))
        m.add_command(label=" Remove Fades",
                      command=lambda:self._set_fade(idx,tk_key,"both",0.0))
        m.add_separator()
        m.add_command(label=" Move to Overlay Track",
                      command=lambda:self._move_track(idx,tk_key,"overlay"))
        m.add_command(label=" Move to Main Video",
                      command=lambda:self._move_track(idx,tk_key,"main"))
        m.add_command(label=" Move to Audio 1",
                      command=lambda:self._move_track(idx,tk_key,"audio1"))
        m.add_command(label=" Move to Audio 2",
                      command=lambda:self._move_track(idx,tk_key,"audio2"))
        try: m.tk_popup(e.x_root,e.y_root)
        finally: m.grab_release()

    def _set_fade(self, idx, tk_key, which, val):
        items = self.tracks.get(tk_key, [])
        if 0 <= idx < len(items):
            if which in ("fade_in","both"):  items[idx]["fade_in"]  = val
            if which in ("fade_out","both"): items[idx]["fade_out"] = val
            self._push_undo(); self._draw_tl()
            self._status(f"Fade set: {which}={val}s")

    def _tl_zoom(self, e):
        d=1.13 if e.delta>0 else 0.88
        self.v_zoom.set(max(0.2,min(14.0,self.v_zoom.get()*d)))
        self._draw_tl()

    # ─────────────────────────────────────────────────────────────────────────
    # Editing operations
    # ─────────────────────────────────────────────────────────────────────────
    def _split(self):
        t=self.fi/float(TARGET_FPS)
        # Split whichever track is selected
        clip=self._at(self.sel_track,t)
        if not clip:
            clip=self._at("main",t)  # fallback to main
        if not clip: self._status("No clip at playhead"); return

        track_key=self.sel_track if self._at(self.sel_track,t) else "main"
        items=self.tracks[track_key]; idx=items.index(clip)
        src_t=(t-clip["tl"])*clip["speed"]+clip["start"]
        if src_t<=clip["start"]+0.04 or src_t>=clip["end"]-0.04:
            self._status("Too close to edge"); return
        new=copy.deepcopy(clip)
        clip["end"]=src_t; new["start"]=src_t; new["tl"]=t
        items.insert(idx+1,new)
        self._push_undo(); self._status(f"Split at {_ft(t)}"); self._draw_tl()

    def _del_sel(self):
        items=self.tracks.get(self.sel_track,[])
        if 0<=self.sel_idx<len(items):
            items.pop(self.sel_idx)
            self.sel_idx=max(0,self.sel_idx-1)
            self._push_undo(); self._draw_tl(); self._status("Deleted")

    def _dup(self, tk_key, idx):
        items=self.tracks[tk_key]
        if 0<=idx<len(items):
            d=copy.deepcopy(items[idx]); dur=(d["end"]-d["start"])/max(d["speed"],0.01)
            d["tl"]=d.get("tl",0)+dur; items.insert(idx+1,d)
            self._push_undo(); self._draw_tl(); self._status("Duplicated")

    def _add_text(self):
        """เพิ่ม text clip ลง text track พร้อมข้อมูลตำแหน่งและสไตล์ตัวอักษร"""
        txt = self.v_text.get().strip() or "Text"
        self.tracks["text"].append({
            "path": "", "name": txt,
            "start": 0, "end": 5,
            "speed": 1, "volume": 1,
            "tl": self.fi / float(TARGET_FPS),
            "fps": TARGET_FPS,
            # ตำแหน่งบนวิดีโอ (สามารถลากด้วยเมาส์บน canvas)
            "custom_x": 0.5, "custom_y": 0.2,
            # สไตล์ตัวอักษร
            "font_name": "Tahoma", "font_size": 36,
            "font_color": "#ffffff", "decoration": "shadow",
        })
        self._push_undo(); self._draw_tl()
        self._status(f'Text: "{txt}"')

    def _move_track(self, idx, src_key, dst_key):
        items=self.tracks.get(src_key,[])
        if 0<=idx<len(items):
            clip=items.pop(idx)
            self.tracks[dst_key].append(clip)
            self.sel_track=dst_key; self.sel_idx=len(self.tracks[dst_key])-1
            self._push_undo(); self._draw_tl()

    def _set_speed(self, idx, tk_key, spd):
        items=self.tracks.get(tk_key,[])
        if 0<=idx<len(items):
            items[idx]["speed"]=spd; self.v_speed.set(spd)
            self._spd_lbl.configure(text=f"{spd:.2f}×")
            self._push_undo(); self._draw_tl()

    def _apply_speed(self, val):
        self._spd_lbl.configure(text=f"{float(val):.2f}×")
        items=self.tracks.get(self.sel_track,[])
        if 0<=self.sel_idx<len(items):
            items[self.sel_idx]["speed"]=float(val); self._draw_tl()

    def _apply_vol(self, val):
        self._vol_lbl.configure(text=f"{int(float(val)*100)}%")
        items=self.tracks.get(self.sel_track,[])
        if 0<=self.sel_idx<len(items):
            items[self.sel_idx]["volume"]=float(val)

    # ─────────────────────────────────────────────────────────────────────────
    # Import / asset management
    # ─────────────────────────────────────────────────────────────────────────
    def _import(self):
        path=filedialog.askopenfilename(
            title="Import Media",
            filetypes=[("Media","*.mp4 *.mov *.avi *.mkv *.webm *.wav *.mp3 *.aac *.jpg *.png"),
                       ("All","*.*")])
        if not path: return
        ext=os.path.splitext(path)[1].lower()
        atype=("video" if ext in (".mp4",".mov",".avi",".mkv",".webm")
               else "audio" if ext in (".wav",".mp3",".aac",".ogg")
               else "image")
        self.assets.append({"path":path,"name":os.path.basename(path),"type":atype})
        self._tab("Media"); self._status(f"Imported: {os.path.basename(path)}")

    def _import_audio(self):
        path=filedialog.askopenfilename(
            title="Import Audio",
            filetypes=[("Audio","*.wav *.mp3 *.aac *.ogg"),("All","*.*")])
        if path:
            self.assets.append({"path":path,"name":os.path.basename(path),"type":"audio"})
            self._tab("Audio")

    def _add_to_tl(self, asset):
        ext=os.path.splitext(asset["path"])[1].lower()
        is_audio=ext in (".wav",".mp3",".aac",".ogg")
        fps=TARGET_FPS; dur=5.0
        if not is_audio:
            cap=cv2.VideoCapture(asset["path"])
            fps=cap.get(cv2.CAP_PROP_FPS) or TARGET_FPS
            cnt=int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            dur=cnt/fps if cnt>0 else 5.0; cap.release()

        # Choose track based on type
        if is_audio:
            # audio goes to audio1 first, then audio2
            tk="audio1" if not self.tracks["audio1"] else "audio2"
        elif ext in (".jpg",".png",".jpeg"):
            tk="overlay"
        else:
            tk="overlay" if self.tracks["main"] else "main"

        self.tracks[tk].append(
            self._clip(asset["path"],asset["name"],0,dur,
                       tl=self._dur(),fps=fps))
        # Extract waveform for audio files
        if is_audio or ext in (".mp4",".mov",".avi",".mkv",".webm"):
            self._extract_waveforms_bg(asset["path"])
        self._push_undo(); self._draw_tl()
        self._status(f"Added to [{tk}]: {asset['name']}")

    # ─────────────────────────────────────────────────────────────────────────
    # Helpers
    # ─────────────────────────────────────────────────────────────────────────
    def _at(self, track, t):
        for item in self.tracks.get(track,[]):
            dur=(item["end"]-item["start"])/max(item["speed"],0.01)
            tl=item.get("tl",0.0)
            if tl<=t<tl+dur: return item
        return None

    def _dur(self):
        clips=self.tracks.get("main",[])
        if not clips: return 0.1
        return max((c.get("tl",0)+((c["end"]-c["start"])/max(c["speed"],0.01)))
                   for c in clips)

    def _status(self, msg):
        if hasattr(self,"_stat"): self._stat.configure(text=msg)

    # ─────────────────────────────────────────────────────────────────────────
    # Undo / Redo
    # ─────────────────────────────────────────────────────────────────────────
    def _push_undo(self):
        snap=copy.deepcopy(self.tracks); self._undo.append(snap)
        if len(self._undo)>MAX_UNDO: self._undo.pop(0)
        self._redo.clear()

    def _undo_do(self):
        if len(self._undo)<2: self._status("Nothing to undo"); return
        self._redo.append(self._undo.pop())
        self.tracks=copy.deepcopy(self._undo[-1])
        self._draw_tl(); self._render(self.fi); self._status("Undo")

    def _redo_do(self):
        if not self._redo: self._status("Nothing to redo"); return
        snap=self._redo.pop(); self._undo.append(copy.deepcopy(self.tracks))
        self.tracks=copy.deepcopy(snap)
        self._draw_tl(); self._render(self.fi); self._status("Redo")

    # ─────────────────────────────────────────────────────────────────────────
    # Export
    # ─────────────────────────────────────────────────────────────────────────
    def _export(self):
        if not self.tracks["main"]:
            messagebox.showwarning("Export","No video on main track!"); return
        out=filedialog.asksaveasfilename(title="Export",defaultextension=".mp4",
                                         filetypes=[("MP4","*.mp4"),("All","*.*")])
        if not out: return
        self._status("Exporting…")
        threading.Thread(target=self._export_worker,args=(out,),daemon=True).start()

    def _export_worker(self, out):
        try:
            ff=imageio_ffmpeg.get_ffmpeg_exe()
            clips=sorted(self.tracks["main"],key=lambda c:c.get("tl",0))
            n=len(clips); cmd=[ff,"-y"]
            for cl in clips:
                cmd+=["-ss",str(cl["start"]),"-to",str(cl["end"]),"-i",cl["path"]]

            fc=[]; vp=[]; ap=[]
            for idx,cl in enumerate(clips):
                sp=cl["speed"]
                fc.append(f"[{idx}:v]setpts={1/sp}*PTS[v{idx}]")
                at=min(max(sp,0.5),2.0)
                fc.append(f"[{idx}:a]atempo={at}[a{idx}]")
                vp.append(f"[v{idx}]"); ap.append(f"[a{idx}]")

            fc.append("".join(vp)+f"concat=n={n}:v=1:a=0[vout]")
            fc.append("".join(ap)+f"concat=n={n}:v=0:a=1[aout]")
            cmd+=["-filter_complex",";".join(fc),
                  "-map","[vout]","-map","[aout]",
                  "-c:v","libx264","-crf","20","-preset","fast",
                  "-c:a","aac","-b:a","192k",out]

            res=subprocess.run(cmd,capture_output=True,text=True)
            if res.returncode==0:
                self.after(0,lambda:(
                    self._status(f"Exported: {os.path.basename(out)}"),
                    messagebox.showinfo("Done",f"Saved:\n{out}")))
            else:
                err=(res.stderr or "")[-500:]
                self.after(0,lambda:(self._status("Export failed"),
                                      messagebox.showerror("Error",err)))
        except Exception as ex:
            self.after(0,lambda:(self._status("Export error"),
                                  messagebox.showerror("Error",str(ex))))

    # ─────────────────────────────────────────────────────────────────────────
    # Subtitle dialog (unchanged logic, updated style)
    # ─────────────────────────────────────────────────────────────────────────
    def _sub_dialog(self):
        if not HAS_SUBTITLES:
            messagebox.showinfo("Subtitles",
                "subtitle_config.py not found.\nPlace it next to editor_page.py."); return
        if not self.tracks["main"]:
            messagebox.showwarning("Subtitles","No video on main track."); return
        _SubtitleDialog(self.master,self.style,self._on_sub)

    def _on_sub(self, style, model_size, words_per_line=8, srt_path=""):
        self.style = style
        vpath = self.tracks["main"][0]["path"]
        def run():
            self.after(0, lambda: self._status("Transcribing…"))
            try:
                segs = transcribe_video(
                    vpath,
                    model_size=model_size,
                    words_per_line=words_per_line,
                    progress_cb=lambda m: self.after(0, lambda ms=m: self._status(ms)),
                )
                self.segments = segs

                # ── ใส่ segments เป็น clip ใน subtitle track ──────────────────
                self.tracks["subtitle"] = []
                for seg in segs:
                    self.tracks["subtitle"].append({
                        "path": "",
                        "name": seg["text"][:24],
                        "start": 0,
                        "end": max(seg["end"] - seg["start"], 0.05),
                        "speed": 1.0,
                        "volume": 1.0,
                        "tl": seg["start"],
                        "fps": TARGET_FPS,
                        "sub_text": seg["text"],
                    })

                # Auto-save SRT
                if srt_path and segs:
                    try:
                        from transcriber import save_srt
                        save_srt(segs, style, srt_path)
                        self.after(0, lambda p=srt_path: self._status(
                            f"Done: {len(segs)} segs – SRT saved: {os.path.basename(p)}"))
                    except Exception as srt_ex:
                        self.after(0, lambda ex=srt_ex: self._status(f"SRT save error: {ex}"))
                else:
                    self.after(0, lambda: self._status(f"Done: {len(segs)} segments"))
                self.after(0, self._tab, "Subs")
                self.after(0, self._draw_tl)
            except Exception as ex:
                self.after(0, lambda: (self._status(f"Error: {ex}"),
                                       messagebox.showerror("Error", str(ex))))
        threading.Thread(target=run, daemon=True).start()

    def _export_subs(self):
        if not self.segments: messagebox.showwarning("Export","No subtitles."); return
        out=filedialog.asksaveasfilename(defaultextension=".mp4",
                                         filetypes=[("MP4","*.mp4")])
        if not out: return
        vpath=self.tracks["main"][0]["path"]
        self._status("Exporting with subtitles…")
        def run():
            try:
                export_video_with_subtitles(vpath,out,self.segments,self.style,
                    progress_cb=lambda m:self.after(0,lambda ms=m:self._status(ms)))
                self.after(0,lambda:messagebox.showinfo("Done",f"Saved:\n{out}"))
            except Exception as ex:
                self.after(0,lambda:messagebox.showerror("Error",str(ex)))
        threading.Thread(target=run,daemon=True).start()

    def _save_srt(self):
        """Let the user pick a path and write the SRT file."""
        if not self.segments:
            messagebox.showwarning("Save SRT","No subtitles to save."); return
        from transcriber import save_srt
        video_stem = ""
        if self.tracks.get("main"):
            video_stem = os.path.splitext(os.path.basename(self.tracks["main"][0]["path"]))[0]
        init = video_stem + ".srt" if video_stem else "subtitles.srt"
        out = filedialog.asksaveasfilename(
            title="Save SRT File",
            initialfile=init,
            defaultextension=".srt",
            filetypes=[("SRT Subtitle","*.srt"),("All files","*.*")])
        if not out: return
        try:
            save_srt(self.segments, self.style, out)
            self._status(f"SRT saved: {os.path.basename(out)}")
            messagebox.showinfo("SRT Saved", f"Saved:\n{out}")
        except Exception as ex:
            messagebox.showerror("Save SRT Error", str(ex))

    def _clear_subs(self):
        self.segments=[]; self._tab("Subs"); self._status("Subtitles cleared")

    # ─────────────────────────────────────────────────────────────────────────
    # Save / Autosave
    # ─────────────────────────────────────────────────────────────────────────
    def _save(self):
        path=filedialog.asksaveasfilename(defaultextension=".json",
                                          filetypes=[("Project","*.json")])
        if path:
            try:
                payload = {
                    "tracks":  self.tracks,
                    "assets":  self.assets,
                    "muted":   self._muted,
                    "version": 5,
                }
                with open(path,"w") as f:
                    json.dump(payload,f,indent=2,default=str)
                self._status(f"Saved: {os.path.basename(path)}")
            except Exception as ex: messagebox.showerror("Save Error",str(ex))

    def _autosave_start(self):
        def loop():
            while True:
                time.sleep(60)
                try:
                    with open("autosave.json","w") as f:
                        json.dump({"tracks":self.tracks},f,default=str)
                except: pass
        threading.Thread(target=loop,daemon=True).start()

    # ─────────────────────────────────────────────────────────────────────────
    # Back / cleanup
    # ─────────────────────────────────────────────────────────────────────────
    def _back(self):
        self._stop()
        if self.cap: self.cap.release()
        try: pygame.mixer.music.unload()
        except: pass
        self._on_back()


# ═════════════════════════════════════════════════════════════════════════════
class _SubtitleDialog(ctk.CTkToplevel):
    # path ของโมเดล local ที่วางไว้ข้าง editor_page.py
    _LOCAL_MODEL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "whisper-small-final")

    def __init__(self, master, style, on_done):
        super().__init__(master)
        self.title("Subtitle Settings")
        self.geometry("500x700"); self.resizable(False, False)
        self.configure(fg_color=PANEL_DARK)
        self._on_done  = on_done
        self._style    = copy.deepcopy(style)
        self._srt_path = ""
        self._build()
        self.after(100, self._raise)

    def _build(self):
        ctk.CTkLabel(self, text="Auto-Subtitle Settings",
                     font=ctk.CTkFont(size=15, weight="bold"),
                     text_color=TXT_W).pack(pady=(18, 2))
        ctk.CTkLabel(self, text="Whisper (Local Model) + PyThaiNLP",
                     font=ctk.CTkFont(size=10), text_color=TXT_G).pack()

        sc = ctk.CTkScrollableFrame(self, fg_color="transparent")
        sc.pack(fill="both", expand=True, padx=14, pady=10)

        def sec(t):
            ctk.CTkLabel(sc, text=t, font=ctk.CTkFont(size=9, weight="bold"),
                         text_color=TXT_G).pack(anchor="w", pady=(10, 3))

        # ── Model path ────────────────────────────────────────────────────────
        sec("โมเดล (Model Path)")
        # ค่า default = local folder whisper-small-final
        _default_model = self._LOCAL_MODEL if os.path.isdir(self._LOCAL_MODEL) else "base"
        self._cm = ctk.CTkEntry(sc, placeholder_text="Path to model folder…",
                                height=28, corner_radius=6, fg_color=PANEL_MID)
        self._cm.pack(fill="x", pady=(0,2))
        self._cm.insert(0, _default_model)

        # fallback radio (ใช้เมื่อ field ด้านบนว่าง)
        ctk.CTkLabel(sc, text="Fallback (ถ้าไม่ระบุ path):",
                     font=ctk.CTkFont(size=8), text_color=TXT_G).pack(anchor="w")
        self._mv = tk.StringVar(value="base")
        mf = ctk.CTkFrame(sc, fg_color="transparent"); mf.pack(fill="x", pady=(0, 6))
        for m in ["tiny", "base", "small", "medium"]:
            ctk.CTkRadioButton(mf, text=m, value=m, variable=self._mv,
                               font=ctk.CTkFont(size=9)).pack(side="left", padx=6)

        # ── Words per line (PyThaiNLP) ─────────────────────────────────────────
        sec("จำนวนคำต่อซับ (Words per subtitle)")
        wpl_row = ctk.CTkFrame(sc, fg_color="transparent")
        wpl_row.pack(fill="x", pady=(0, 6))
        self._wpl_v = tk.IntVar(value=8)
        ctk.CTkSlider(wpl_row, from_=3, to=20, variable=self._wpl_v,
                      width=200, progress_color=C_AMBER, button_color=C_AMBER,
                      command=lambda v: self._wpl_lbl.configure(
                          text=f"{int(float(v))} คำ")
                      ).pack(side="left", padx=(0, 8))
        self._wpl_lbl = ctk.CTkLabel(wpl_row, text="8 คำ",
                                      font=ctk.CTkFont(size=10), text_color=TXT_W)
        self._wpl_lbl.pack(side="left")

        if PRESETS:
            sec("Style Preset")
            pf=ctk.CTkFrame(sc,fg_color="transparent"); pf.pack(fill="x",pady=(0,6))
            for pi,p in enumerate(PRESETS):
                ctk.CTkButton(pf,text=p["name"],width=108,height=26,corner_radius=6,
                              fg_color=PANEL_MID,hover_color=PANEL_LIGHT,
                              font=ctk.CTkFont(size=9),
                              command=lambda pp=p:self._preset(pp)
                              ).grid(row=pi//4,column=pi%4,padx=2,pady=2)

        sec("Font")
        self._fv=tk.StringVar(value=self._style.font_name)
        fonts=FONT_CHOICES or ["Arial","Tahoma","Courier New"]
        ctk.CTkOptionMenu(sc,values=fonts,variable=self._fv,height=28,
                          corner_radius=6,fg_color=PANEL_MID).pack(fill="x",pady=(0,4))
        r=ctk.CTkFrame(sc,fg_color="transparent"); r.pack(fill="x",pady=(0,6))
        ctk.CTkLabel(r,text="Size:",font=ctk.CTkFont(size=9),text_color=TXT_G,width=38).pack(side="left")
        self._sv=tk.IntVar(value=self._style.font_size)
        ctk.CTkSlider(r,from_=14,to=60,variable=self._sv,width=180).pack(side="left",padx=6)
        ctk.CTkLabel(r,textvariable=self._sv,font=ctk.CTkFont(size=9),text_color=TXT_G).pack(side="left")

        cr=ctk.CTkFrame(sc,fg_color="transparent"); cr.pack(fill="x",pady=(0,6))
        ctk.CTkLabel(cr,text="Color:",font=ctk.CTkFont(size=9),text_color=TXT_G).pack(side="left")
        self._cv=tk.StringVar(value=self._style.font_color)
        ctk.CTkEntry(cr,textvariable=self._cv,width=80,height=26,
                     corner_radius=6,fg_color=PANEL_MID).pack(side="left",padx=6)

        sec("Decoration")
        self._dv=tk.StringVar(value=self._style.decoration)
        ctk.CTkOptionMenu(sc,values=DECORATION_CHOICES or["none","outline","shadow","box"],
                          variable=self._dv,height=28,corner_radius=6,
                          fg_color=PANEL_MID).pack(fill="x",pady=(0,6))

        sec("Animation")
        self._av=tk.StringVar(value=self._style.animation)
        ctk.CTkOptionMenu(sc,values=ANIMATION_CHOICES or["none","fade_in","slide_up"],
                          variable=self._av,height=28,corner_radius=6,
                          fg_color=PANEL_MID).pack(fill="x",pady=(0,6))

        sec("Position")
        self._pv=tk.StringVar(value=self._style.position)
        ctk.CTkOptionMenu(sc,values=POSITION_CHOICES or["bottom_center","top_center"],
                          variable=self._pv,height=28,corner_radius=6,
                          fg_color=PANEL_MID).pack(fill="x",pady=(0,6))

        # ── SRT Export ────────────────────────────────────────────────────────
        sec("SRT Export")
        self._srt_var = tk.BooleanVar(value=False)
        srt_check = ctk.CTkCheckBox(sc, text="Auto-save SRT after transcription",
                                     variable=self._srt_var,
                                     font=ctk.CTkFont(size=10),
                                     command=self._toggle_srt_path)
        srt_check.pack(anchor="w", pady=(0,4))

        self._srt_row = ctk.CTkFrame(sc, fg_color="transparent")
        self._srt_row.pack(fill="x", pady=(0,6))
        self._srt_entry = ctk.CTkEntry(self._srt_row,
                                        placeholder_text="Output .srt path…",
                                        height=28, corner_radius=6,
                                        fg_color=PANEL_MID, state="disabled")
        self._srt_entry.pack(side="left", fill="x", expand=True, padx=(0,4))
        self._srt_browse = ctk.CTkButton(self._srt_row, text="Browse",
                                          width=62, height=28, corner_radius=6,
                                          fg_color=PANEL_LIGHT, hover_color=PANEL_HOV,
                                          font=ctk.CTkFont(size=9),
                                          state="disabled",
                                          command=self._browse_srt)
        self._srt_browse.pack(side="left")

        br=ctk.CTkFrame(self,fg_color="transparent")
        br.pack(fill="x",padx=14,pady=(0,14))
        ctk.CTkButton(br,text="Cancel",width=100,height=34,corner_radius=8,
                      fg_color=PANEL_MID,hover_color=PANEL_LIGHT,
                      command=self.destroy).pack(side="left")
        ctk.CTkButton(br,text="Generate Subtitles",height=34,corner_radius=8,
                      fg_color=C_BLUE,hover_color=_dark(C_BLUE),
                      font=ctk.CTkFont(size=11,weight="bold"),
                      command=self._submit).pack(side="right")

    def _raise(self):
        """Force this dialog on top of all other windows (Windows z-order fix)."""
        self.lift()
        self.focus_force()
        self.grab_set()   # make modal – blocks input to main window

    def _toggle_srt_path(self):
        state = "normal" if self._srt_var.get() else "disabled"
        self._srt_entry.configure(state=state)
        self._srt_browse.configure(state=state)

    def _browse_srt(self):
        path = filedialog.asksaveasfilename(
            title="Save SRT File As",
            initialfile="subtitles.srt",
            defaultextension=".srt",
            filetypes=[("SRT Subtitle","*.srt"),("All files","*.*")])
        if path:
            self._srt_path = path
            self._srt_entry.configure(state="normal")
            self._srt_entry.delete(0, "end")
            self._srt_entry.insert(0, path)
            self._srt_entry.configure(state="readonly")

    def _preset(self,p):
        self._fv.set(p.get("font","Tahoma")); self._sv.set(p.get("size",32))
        self._cv.set(p.get("color","#ffffff")); self._dv.set(p.get("deco","outline"))
        self._av.set(p.get("anim","none"))

    def _submit(self):
        s = self._style
        s.font_name  = self._fv.get()
        s.font_size  = self._sv.get()
        s.font_color = self._cv.get()
        s.decoration = self._dv.get()
        s.animation  = self._av.get()
        s.position   = self._pv.get()
        mp    = self._cm.get().strip()
        model = mp if mp else self._mv.get()
        words_per_line = int(self._wpl_v.get())
        srt_path = ""
        if self._srt_var.get():
            srt_path = self._srt_path or self._srt_entry.get().strip()
        self.destroy()
        self._on_done(s, model, words_per_line, srt_path)