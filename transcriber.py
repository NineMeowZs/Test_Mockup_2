"""transcriber.py – Whisper speech-to-text + SRT generation"""

import os
import sys

# ── ป้องกันไม่ให้ transformers พยายามโหลด torchaudio/torchcodec ซึ่งจะทำให้เกิด Error DLL บน Windows
sys.modules['torchaudio'] = None
sys.modules['torchcodec'] = None

# ── ให้ Python หา ffmpeg เองผ่าน imageio-ffmpeg ────────────────────────────
try:
    import imageio_ffmpeg
    _ffmpeg_dir = os.path.dirname(imageio_ffmpeg.get_ffmpeg_exe())
    os.environ["PATH"] = _ffmpeg_dir + os.pathsep + os.environ.get("PATH", "")
except Exception:
    pass  # ถ้าหาไม่เจอก็ใช้ PATH ของระบบตามปกติ


import textwrap
from subtitle_config import SubtitleStyle


def _extract_audio_numpy(video_path: str) -> "np.ndarray":
    """
    แยก audio จากวิดีโอโดยใช้ imageio_ffmpeg binary โดยตรง
    (ไม่ต้องการ ffmpeg ใน PATH)
    คืนค่า numpy float32 array ที่ sample rate 16000 Hz mono
    """
    import subprocess
    import numpy as np
    import imageio_ffmpeg

    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    cmd = [
        ffmpeg_exe,
        "-y", "-i", video_path,
        "-vn",                   # ไม่เอาวิดีโอ
        "-acodec", "pcm_s16le",  # raw PCM 16-bit
        "-ar", "16000",          # 16 kHz
        "-ac", "1",              # mono
        "-f", "s16le",           # raw format
        "pipe:1",                # ส่งออก stdout
    ]
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg error: {proc.stderr.decode(errors='ignore')[:300]}")

    audio = np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float32) / 32768.0
    return audio


def transcribe_video(video_path: str, model_size: str = "base", progress_cb=None) -> list[dict]:
    """
    ถอดเสียงจากวิดีโอด้วย Whisper หรือ Custom Model
    ใช้ VAD หั่นเสียงก่อนแปลเพื่อแก้ปัญหาอาการหลอนแบบชะงัด
    """
    if progress_cb:
        progress_cb("กำลังแยกเสียงจากวิดีโอ …")

    audio = _extract_audio_numpy(video_path)

    # --- 1. รัน VAD ก่อนเลยเพื่อตัดปัญหาซับหลอน ---
    if progress_cb:
        progress_cb("กำลังใช้ VAD ตรวจจับช่วงที่มีคนพูด (ตัดเสียงเงียบ)...")
        
    import torch
    import warnings
    warnings.filterwarnings("ignore", category=UserWarning)
    try:
        vad_model, utils = torch.hub.load(repo_or_dir='snakers4/silero-vad',
                                          model='silero_vad',
                                          force_reload=False,
                                          trust_repo=True,
                                          verbose=False)
        get_speech_timestamps = utils[0]
        # หาช่วงที่มีคนพูด (ได้คืนมาเป็นเฟรม 16000 เฟรม = 1 วิ)
        tensor = torch.from_numpy(audio).float()
        speech_ts = get_speech_timestamps(tensor, vad_model, sampling_rate=16000)
    except Exception as e:
        if progress_cb: progress_cb(f"VAD Error: {e}")
        # ถ้าพังให้จำลองว่าพูดทั้งคลิป
        speech_ts = [{"start": 0, "end": len(audio)}]

    if not speech_ts:
        if progress_cb: progress_cb("ไม่พบเสียงคนพูดในวิดีโอเลย!")
        return []

    segments = []

    # --- 2. การถอดเสียงแยกตามโมเดล ---
    if os.path.isdir(model_size):
        if progress_cb:
            progress_cb("กำลังโหลดโมเดล Custom (Transformers) …")
        from transformers import pipeline, AutoConfig, AutoTokenizer, AutoFeatureExtractor
        
        device = 0 if torch.cuda.is_available() else -1
        
        base_model = "openai/whisper-small"
        try:
            config = AutoConfig.from_pretrained(model_size)
            if hasattr(config, "encoder_layers"):
                if config.encoder_layers == 4: base_model = "openai/whisper-tiny"
                elif config.encoder_layers == 6: base_model = "openai/whisper-base"
                elif config.encoder_layers == 12: base_model = "openai/whisper-small"
                elif config.encoder_layers == 24: base_model = "openai/whisper-medium"
                elif config.encoder_layers == 32: base_model = "openai/whisper-large"
            if hasattr(config, "_name_or_path") and config._name_or_path:
                if not os.path.exists(config._name_or_path):
                    base_model = config._name_or_path
        except: pass

        try:
            tok = AutoTokenizer.from_pretrained(base_model)
            feat = AutoFeatureExtractor.from_pretrained(base_model)
            pipe = pipeline("automatic-speech-recognition", model=model_size, tokenizer=tok, feature_extractor=feat, device=device)
        except Exception as e:
            raise RuntimeError(f"ไม่สามารถโหลด Tokenizer: {e}")
            
        if progress_cb:
            progress_cb(f"กำลังแปลผล {len(speech_ts)} ประโยค (Custom Model) …")
            
        for ts in speech_ts:
            start_sec = ts['start'] / 16000.0
            end_sec = ts['end'] / 16000.0
            chunk_audio = audio[ts['start']:ts['end']]
            
            if len(chunk_audio) < 1600: continue # สั้นกว่า 0.1 วิ ข้าม
            
            res = pipe(chunk_audio, generate_kwargs={"max_new_tokens": 128})
            text = res.get("text", "").strip()
            
            if text:
                segments.append({
                    "start": start_sec,
                    "end": end_sec,
                    "text": text
                })
    else:
        if progress_cb:
            progress_cb("กำลังโหลดโมเดล Whisper …")

        import whisper
        model = whisper.load_model(model_size)

        if progress_cb:
            progress_cb(f"กำลังแปลผล {len(speech_ts)} ประโยค (Whisper) …")

        for ts in speech_ts:
            start_sec = ts['start'] / 16000.0
            end_sec = ts['end'] / 16000.0
            chunk_audio = audio[ts['start']:ts['end']]
            
            if len(chunk_audio) < 1600: continue
            
            result = model.transcribe(
                chunk_audio,
                task="transcribe",
                language="th",
                no_speech_threshold=0.6,
                condition_on_previous_text=False,
                word_timestamps=True,
                initial_prompt="ภาษาไทย Thai language subtitle transcription",
            )
            
            text = result.get("text", "").strip()
            if text:
                # อัปเดตเวลาให้ตรงกับวิดีโอหลัก
                chunk_words = []
                for s in result.get("segments", []):
                    for w in s.get("words", []):
                        chunk_words.append({
                            "word": w["word"],
                            "start": w["start"] + start_sec,
                            "end": w["end"] + start_sec
                        })
                        
                segments.append({
                    "start": start_sec,
                    "end": end_sec,
                    "text": text,
                    "words": chunk_words
                })

    if progress_cb:
        progress_cb(f"ถอดเสียงสำเร็จ! ได้ {len(segments)} ประโยค")

    return segments


def wrap_segment(text: str, max_chars: int, max_lines: int) -> str:
    """Word-wrap *text* to fit subtitle style constraints."""
    lines = textwrap.wrap(text.strip(), width=max_chars)
    lines = lines[:max_lines]
    return "\n".join(lines)


def segments_to_srt(segments: list[dict], style: SubtitleStyle) -> str:
    """Convert Whisper segments to SRT string with word-wrap applied."""
    def fmt_time(t: float) -> str:
        h = int(t // 3600)
        m = int((t % 3600) // 60)
        s = int(t % 60)
        ms = int((t % 1) * 1000)
        return f"{h:02}:{m:02}:{s:02},{ms:03}"

    lines = []
    for i, seg in enumerate(segments, 1):
        text = wrap_segment(seg["text"], style.max_chars_per_line, style.max_lines)
        lines.append(str(i))
        lines.append(f"{fmt_time(seg['start'])} --> {fmt_time(seg['end'])}")
        lines.append(text)
        lines.append("")
    return "\n".join(lines)


def save_srt(segments: list[dict], style: SubtitleStyle, output_path: str):
    """Write SRT file to disk."""
    srt_content = segments_to_srt(segments, style)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(srt_content)
    return output_path
