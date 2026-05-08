"""video_exporter.py – Burn subtitles into video via moviepy + ffmpeg"""

import cv2
import subprocess
import numpy as np
from moviepy import VideoFileClip
from moviepy.video.io.ffmpeg_writer import FFMPEG_VideoWriter
from subtitle_config import SubtitleStyle
from subtitle_renderer import draw_subtitles_on_frame
import os
import imageio_ffmpeg


def _find_active_segment(t: float, segments: list[dict]) -> tuple[str, float]:
    """Return (text, progress) for time *t*; progress is position within segment."""
    for seg in segments:
        if seg["start"] <= t <= seg["end"]:
            dur = max(seg["end"] - seg["start"], 0.001)
            progress = (t - seg["start"]) / dur
            return seg["text"], progress
    return "", 0.5


def export_video_with_subtitles(
    input_path: str,
    output_path: str,
    segments: list[dict],
    style: SubtitleStyle,
    progress_cb=None,
):
    """
    Burn subtitles into video and mux audio back using imageio_ffmpeg.
    """
    clip  = VideoFileClip(input_path)
    fps   = clip.fps
    total = int(clip.duration * fps)

    # ── ขั้นที่ 1: Render วิดีโอ (ไม่มีเสียง) ─────────────────────────────
    tmp_video = output_path + "_tmp_noaudio.mp4"

    writer = FFMPEG_VideoWriter(
        tmp_video, clip.size, fps,
        codec="libx264", preset="fast", bitrate="5000k",
        audiofile=None,
    )

    if progress_cb:
        progress_cb("กำลัง Render วิดีโอ …  0%")

    for i, frame in enumerate(clip.iter_frames(fps=fps, dtype="uint8")):
        t    = i / fps
        text, progress = _find_active_segment(t, segments)
        bgr  = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
        bgr  = draw_subtitles_on_frame(bgr, text, style, progress)
        rgb  = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        writer.write_frame(rgb)

        if progress_cb and i % max(1, total // 20) == 0:
            pct = int(i / total * 100)
            progress_cb(f"กำลัง Render วิดีโอ … {pct}%")

    writer.close()
    clip.close()

    # ── ขั้นที่ 2: Mux เสียงกลับด้วย ffmpeg โดยตรง ───────────────────────
    if progress_cb:
        progress_cb("กำลังรวมเสียง …")

    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    cmd = [
        ffmpeg_exe, "-y",
        "-i", tmp_video,      # วิดีโอที่มีซับแล้ว (ไม่มีเสียง)
        "-i", input_path,     # วิดีโอต้นฉบับ (เอาแค่เสียง)
        "-c:v", "copy",       # copy วิดีโอ track (ไม่ encode ซ้ำ)
        "-c:a", "aac",        # encode เสียงเป็น AAC
        "-map", "0:v:0",      # วิดีโอจาก input แรก
        "-map", "1:a:0",      # เสียงจาก input ที่สอง
        "-shortest",
        output_path,
    ]
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        # ถ้าไม่มีเสียงในต้นฉบับ ให้ใช้วิดีโอที่ render มาเลย
        import shutil
        shutil.copy2(tmp_video, output_path)

    # ลบ temp file
    if os.path.exists(tmp_video):
        os.remove(tmp_video)

    if progress_cb:
        progress_cb(f"บันทึกไฟล์สำเร็จ: {os.path.basename(output_path)}")
