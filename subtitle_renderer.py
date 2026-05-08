"""subtitle_renderer.py – Draws subtitles onto an OpenCV frame"""

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from subtitle_config import SubtitleStyle
import os, platform


# --------------------------------------------------------------------------- #
#  Font helpers
# --------------------------------------------------------------------------- #

# ── Map ชื่อฟอนต์ → ไฟล์ .ttf จริงบน Windows ──────────────────────────────
_FONT_MAP: dict[str, list[str]] = {
    "Arial":           ["arial.ttf", "Arial.ttf"],
    "Tahoma":          ["tahoma.ttf", "Tahoma.ttf"],
    "TH Sarabun New":  ["THSarabunNew.ttf", "THSarabun New.ttf", "THSarabunNew Bold.ttf"],
    "Angsana New":     ["angsau32.ttf", "ANGSAU32.TTF", "AngsanaNew.ttf"],
    "Cordia New":      ["cordia.ttf", "CORDIA32.TTF", "CordiaNEW.ttf"],
    "Leelawadee":      ["leelawad.ttf", "Leelawadee.ttf"],
    "Courier New":     ["cour.ttf", "Courier New.ttf"],
    "Times New Roman": ["times.ttf", "Times New Roman.ttf"],
    "Verdana":         ["verdana.ttf", "Verdana.ttf"],
    "Impact":          ["impact.ttf", "Impact.ttf"],
}

# ฟอนต์ที่รองรับ Thai — เรียงลำดับความสำคัญ
_THAI_FALLBACK = [
    "THSarabunNew.ttf",
    "tahoma.ttf",
    "leelawad.ttf",
    "cordia.ttf",
    "angsau32.ttf",
    "CORDIA32.TTF",
    "ANGSAU32.TTF",
]
_FONTS_DIR = "C:/Windows/Fonts"


def _load_pil_font(style: SubtitleStyle) -> ImageFont.FreeTypeFont:
    """Load font that supports Thai; never fall back to bitmap default."""
    size = style.font_size

    # สร้างรายการ path ที่จะลอง
    candidates: list[str] = []
    name = style.font_name
    # 1) จาก mapping ที่รู้จัก
    for fn in _FONT_MAP.get(name, []):
        candidates.append(os.path.join(_FONTS_DIR, fn))
    # 2) ลอง path ตรง ๆ
    candidates += [
        name,
        os.path.join(_FONTS_DIR, name + ".ttf"),
        os.path.join(_FONTS_DIR, name.lower() + ".ttf"),
    ]
    # 3) Thai fallback fonts
    for fn in _THAI_FALLBACK:
        candidates.append(os.path.join(_FONTS_DIR, fn))

    for c in candidates:
        try:
            return ImageFont.truetype(c, size)
        except Exception:
            pass

    # สุดท้าย — ใช้ default แต่แจ้งเตือน (ภาษาไทยจะยังแสดงเป็นกล่อง)
    return ImageFont.load_default()


def _hex_to_rgb(hex_color: str) -> tuple:
    hex_color = hex_color.lstrip("#")
    return tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))


# --------------------------------------------------------------------------- #
#  Animation helpers
# --------------------------------------------------------------------------- #

def _compute_alpha(animation: str, progress: float) -> float:
    """
    progress: 0.0 = start of segment, 1.0 = end of segment
    Returns opacity multiplier 0‥1.
    """
    if animation == "fade_in":
        return min(1.0, progress * 5)          # fade in during first 20%
    if animation == "pop":
        # pop: scale effect approximated by full alpha
        return 1.0 if progress > 0.0 else 0.0
    return 1.0  # none, slide_up, slide_down, typewriter, box


def _compute_offset(animation: str, progress: float, frame_h: int) -> tuple[int, int]:
    """Returns (dx, dy) pixel offset for the subtitle block."""
    if animation == "slide_up":
        dy = int((1 - min(1.0, progress * 5)) * 40)
        return 0, dy
    if animation == "slide_down":
        dy = -int((1 - min(1.0, progress * 5)) * 40)
        return 0, dy
    return 0, 0


def _typewriter_text(text: str, progress: float) -> str:
    """Return portion of text revealed so far for typewriter effect."""
    n = max(1, int(len(text) * min(1.0, progress * 3)))
    return text[:n]


# --------------------------------------------------------------------------- #
#  Position helpers
# --------------------------------------------------------------------------- #

def _compute_xy(position: str, block_w: int, block_h: int,
                frame_w: int, frame_h: int,
                margin_x: int, margin_y: int,
                custom_x: float = 0.5, custom_y: float = 0.85) -> tuple[int, int]:
    pos = position.lower()
    if pos == "custom":
        x = int(custom_x * frame_w) - block_w // 2
        y = int(custom_y * frame_h) - block_h // 2
        # clamp to screen
        x = max(0, min(x, frame_w - block_w))
        y = max(0, min(y, frame_h - block_h))
        return x, y

    if "left" in pos:
        x = margin_x
    elif "right" in pos:
        x = frame_w - block_w - margin_x
    else:
        x = (frame_w - block_w) // 2

    if "top" in pos:
        y = margin_y
    elif "bottom" in pos:
        y = frame_h - block_h - margin_y
    else:
        y = (frame_h - block_h) // 2
    return x, y


# --------------------------------------------------------------------------- #
#  Main draw function
# --------------------------------------------------------------------------- #

def draw_subtitles_on_frame(
    frame: np.ndarray,
    text: str,
    style: SubtitleStyle,
    progress: float = 0.5,          # how far into this segment we are (0‥1)
) -> np.ndarray:
    """
    Draw *text* onto *frame* (H×W×3 BGR numpy array) according to *style*.
    Returns modified frame.
    """
    if not text.strip():
        return frame

    # Convert to PIL for rich text rendering
    frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    pil_img = Image.fromarray(frame_rgb)
    h, w = frame.shape[:2]

    font = _load_pil_font(style)
    text_color = _hex_to_rgb(style.font_color)
    deco_color = _hex_to_rgb(style.decoration_color)

    # Apply typewriter effect
    display_text = _typewriter_text(text, progress) if style.animation == "typewriter" else text

    # Measure text block
    dummy_draw = ImageDraw.Draw(pil_img)
    lines = display_text.split("\n")
    line_sizes = [dummy_draw.textbbox((0, 0), line, font=font) for line in lines]
    line_heights = [bb[3] - bb[1] for bb in line_sizes]
    line_widths  = [bb[2] - bb[0] for bb in line_sizes]
    block_w = max(line_widths) if line_widths else 1
    block_h = sum(line_heights) + style.line_spacing * (len(lines) - 1)

    # Compute position + animation offset
    alpha  = _compute_alpha(style.animation, progress)
    dx, dy = _compute_offset(style.animation, progress, h)
    base_x, base_y = _compute_xy(
        style.position, block_w, block_h, w, h,
        style.margin_x, style.margin_y,
        style.custom_x, style.custom_y
    )
    base_x += dx
    base_y += dy

    # Create overlay layer for alpha blending
    overlay = pil_img.copy().convert("RGBA")
    draw = ImageDraw.Draw(overlay)

    # Draw decoration behind text
    pad = 10
    if style.decoration in ("box", "highlight"):
        box_alpha = int(style.bg_opacity * 255 * alpha)
        box_color = (*deco_color, box_alpha)
        draw.rectangle(
            [base_x - pad, base_y - pad,
             base_x + block_w + pad, base_y + block_h + pad],
            fill=box_color,
        )

    # Draw each line
    cur_y = base_y
    for line_idx, line in enumerate(lines):
        lw = line_widths[line_idx]
        # Center each line within the block
        lx = base_x + (block_w - lw) // 2

        if style.decoration == "shadow":
            draw.text((lx + 2, cur_y + 2), line, font=font,
                      fill=(*deco_color, int(200 * alpha)))
        elif style.decoration == "outline":
            for ox, oy in [(-2, 0), (2, 0), (0, -2), (0, 2),
                           (-2, -2), (2, -2), (-2, 2), (2, 2)]:
                draw.text((lx + ox, cur_y + oy), line, font=font,
                          fill=(*deco_color, int(255 * alpha)))

        draw.text((lx, cur_y), line, font=font,
                  fill=(*text_color, int(255 * alpha)))
        cur_y += line_heights[line_idx] + style.line_spacing

    # Merge overlay back
    result = Image.alpha_composite(pil_img.convert("RGBA"), overlay)
    result_bgr = cv2.cvtColor(np.array(result.convert("RGB")), cv2.COLOR_RGB2BGR)
    return result_bgr
