"""subtitle_config.py – Subtitle style configuration"""

from dataclasses import dataclass, field


FONT_CHOICES = [
    "Tahoma",           # รองรับ Thai ✓
    "TH Sarabun New",   # ฟอนต์ไทยราชการ ✓
    "Cordia New",       # ฟอนต์ไทย ✓
    "Angsana New",      # ฟอนต์ไทย ✓
    "Leelawadee",       # รองรับ Thai ✓
    "Arial",
    "Courier New",
    "Times New Roman",
    "Verdana",
    "Impact",
]

ANIMATION_CHOICES = [
    "none",
    "fade_in",
    "slide_up",
    "slide_down",
    "typewriter",
    "pop",
]

POSITION_CHOICES = [
    "bottom_center",
    "bottom_left",
    "bottom_right",
    "top_center",
    "top_left",
    "top_right",
    "center",
    "custom",
]

DECORATION_CHOICES = [
    "none",
    "shadow",
    "outline",
    "box",
    "highlight",
]


PRESETS = [
    {"name": "มาตรฐาน",    "font": "Tahoma", "size": 32, "color": "#ffffff", "deco": "outline", "anim": "none"},
    {"name": "ริบบอน",     "font": "Tahoma", "size": 28, "color": "#ffffff", "deco": "box",     "anim": "fade_in"},
    {"name": "หัวมอล",     "font": "Tahoma", "size": 34, "color": "#facc15", "deco": "shadow",  "anim": "none"},
    {"name": "ซีออนเขียว", "font": "Tahoma", "size": 30, "color": "#22c55e", "deco": "outline", "anim": "fade_in"},
    {"name": "ดาร์กโมด",   "font": "Tahoma", "size": 28, "color": "#f0f0f0", "deco": "box",     "anim": "slide_up"},
    {"name": "ป๊อปโซน",    "font": "Tahoma", "size": 34, "color": "#ff4444", "deco": "shadow",  "anim": "pop"},
    {"name": "พาสเทล",     "font": "Tahoma", "size": 30, "color": "#c084fc", "deco": "outline", "anim": "fade_in"},
    {"name": "คลาสสิก",    "font": "Tahoma", "size": 32, "color": "#ffd700", "deco": "outline", "anim": "none"},
]


@dataclass
class SubtitleStyle:
    font_name: str = "Tahoma"
    font_size: int = 32
    font_color: str = "#FFFFFF"
    bold: bool = False
    italic: bool = False
    decoration: str = "outline"          # none / shadow / outline / box / highlight
    decoration_color: str = "#000000"
    animation: str = "fade_in"           # see ANIMATION_CHOICES
    position: str = "bottom_center"      # see POSITION_CHOICES
    max_chars_per_line: int = 40         # max chars before word-wrap
    max_lines: int = 2                   # max lines shown at once
    margin_x: int = 40                   # horizontal margin (px)
    margin_y: int = 40                   # vertical margin from edge (px)
    custom_x: float = 0.5                # normalized x (0-1) for custom position
    custom_y: float = 0.85               # normalized y (0-1) for custom position
    line_spacing: int = 8               # extra px between lines
    bg_opacity: float = 0.5             # used only for box / highlight decoration
