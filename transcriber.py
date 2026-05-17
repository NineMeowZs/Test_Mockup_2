"""transcriber.py – Whisper speech-to-text + PyThaiNLP segmentation + SRT generation"""

import os
import numpy as np

# ── ให้ Python หา ffmpeg เองผ่าน imageio-ffmpeg ────────────────────────────
try:
    import imageio_ffmpeg
    _ffmpeg_dir = os.path.dirname(imageio_ffmpeg.get_ffmpeg_exe())
    os.environ["PATH"] = _ffmpeg_dir + os.pathsep + os.environ.get("PATH", "")
except Exception:
    pass

# ── Default path ของโมเดล local ──────────────────────────────────────────────
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_MODEL_PATH = os.path.join(_THIS_DIR, "whisper-small-final")

from subtitle_config import SubtitleStyle


# ─────────────────────────────────────────────────────────────────────────────
def _extract_audio_numpy(video_path: str) -> np.ndarray:
    """แยก audio จากวิดีโอด้วย ffmpeg → numpy float32 @ 16000 Hz mono"""
    import subprocess
    import imageio_ffmpeg

    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    cmd = [
        ffmpeg_exe, "-y", "-i", video_path,
        "-vn", "-acodec", "pcm_s16le",
        "-ar", "16000", "-ac", "1",
        "-f", "s16le", "pipe:1",
    ]
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0:
        err = proc.stderr.decode(errors="ignore")[:400]
        raise RuntimeError(f"ffmpeg แยก audio ไม่ได้: {err}")

    audio = np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float32) / 32768.0
    if len(audio) == 0:
        raise RuntimeError("ไม่พบ audio ในไฟล์วิดีโอ")
    return audio


# ─────────────────────────────────────────────────────────────────────────────
def _run_vad(audio: np.ndarray, progress_cb=None) -> list[dict]:
    """
    รัน Silero-VAD เพื่อหาช่วงที่มีเสียงพูด
    ถ้าโหลดไม่ได้ → คืนทั้งคลิปเป็น 1 chunk (ไม่ raise)
    """
    import torch, warnings
    warnings.filterwarnings("ignore", category=UserWarning)
    try:
        if progress_cb:
            progress_cb("VAD: ตรวจจับช่วงที่มีคนพูด …")
        vad_model, utils = torch.hub.load(
            repo_or_dir="snakers4/silero-vad",
            model="silero_vad",
            force_reload=False,
            trust_repo=True,
            verbose=False,
        )
        get_speech_timestamps = utils[0]
        tensor = torch.from_numpy(audio).float()
        speech_ts = get_speech_timestamps(tensor, vad_model, sampling_rate=16000)
        if speech_ts:
            return list(speech_ts)
        if progress_cb:
            progress_cb("VAD: ไม่พบเสียงพูด → ใช้ทั้งคลิปแทน")
        return [{"start": 0, "end": len(audio)}]
    except Exception as e:
        if progress_cb:
            progress_cb(f"VAD ล้มเหลว ({type(e).__name__}: {e}) → ใช้ทั้งคลิป")
        return [{"start": 0, "end": len(audio)}]


# ─────────────────────────────────────────────────────────────────────────────
def _run_local_whisper(
    model_path: str,
    audio: np.ndarray,
    speech_ts: list[dict],
    progress_cb=None,
) -> list[dict]:
    """
    ถอดเสียงด้วย WhisperForConditionalGeneration.generate() โดยตรง
    + forced_decoder_ids=Thai เพื่อให้แน่ใจว่าได้ภาษาไทย
    """
    import torch
    from transformers import WhisperProcessor, WhisperForConditionalGeneration

    if progress_cb:
        progress_cb(f"โหลดโมเดล: {os.path.basename(model_path)} …")

    try:
        processor = WhisperProcessor.from_pretrained(model_path, local_files_only=True)
    except Exception as e:
        raise RuntimeError(
            f"โหลด WhisperProcessor จาก '{model_path}' ไม่ได้\n"
            f"ตรวจสอบไฟล์ใน folder: tokenizer_config.json, preprocessor_config.json\n"
            f"รายละเอียด: {type(e).__name__}: {e}"
        )

    try:
        model = WhisperForConditionalGeneration.from_pretrained(
            model_path, local_files_only=True
        )
        device_str = "cuda" if torch.cuda.is_available() else "cpu"
        model = model.to(device_str)
        model.eval()
    except Exception as e:
        raise RuntimeError(
            f"โหลด WhisperForConditionalGeneration ไม่ได้\n"
            f"รายละเอียด: {type(e).__name__}: {e}"
        )

    # Force Thai language + transcribe task
    try:
        forced_decoder_ids = processor.get_decoder_prompt_ids(
            language="thai", task="transcribe"
        )
    except Exception:
        forced_decoder_ids = None

    raw: list[dict] = []
    total = len(speech_ts)

    for i, ts in enumerate(speech_ts):
        if progress_cb:
            progress_cb(f"chunk {i+1}/{total} …")

        start_sec = ts["start"] / 16000.0
        end_sec   = ts["end"]   / 16000.0
        chunk     = audio[ts["start"]:ts["end"]]

        if len(chunk) < 1600:   # < 0.1 วิ ข้าม
            continue

        try:
            inputs = processor(
                chunk.astype(np.float32),
                sampling_rate=16000,
                return_tensors="pt",
            )
            input_features = inputs.input_features.to(device_str)

            with torch.no_grad():
                gen_kwargs = {"max_new_tokens": 128}
                if forced_decoder_ids is not None:
                    gen_kwargs["forced_decoder_ids"] = forced_decoder_ids
                out = model.generate(input_features, **gen_kwargs)

            text = processor.batch_decode(out, skip_special_tokens=True)[0].strip()
        except Exception as e:
            if progress_cb:
                progress_cb(f"chunk {i+1} error: {type(e).__name__}: {e}")
            continue

        if text:
            raw.append({"start": start_sec, "end": end_sec, "text": text})

    return raw


# ─────────────────────────────────────────────────────────────────────────────
def _segment_thai(text: str, words_per_line: int = 8) -> list[str]:
    """
    ตัดคำภาษาไทยด้วย PyThaiNLP แล้วแบ่งเป็น chunk ตาม words_per_line
    """
    try:
        from pythainlp.tokenize import word_tokenize
        words = word_tokenize(text.strip(), engine="newmm", keep_whitespace=False)
        words = [w for w in words if w.strip()]
    except Exception:
        words = text.strip().split() or [text.strip()]

    if not words:
        return [text.strip()]

    chunks = []
    for i in range(0, len(words), words_per_line):
        chunk = "".join(words[i: i + words_per_line])
        if chunk:
            chunks.append(chunk)
    return chunks or [text.strip()]


# ─────────────────────────────────────────────────────────────────────────────
def transcribe_video(
    video_path: str,
    model_size: str = "",
    words_per_line: int = 8,
    progress_cb=None,
) -> list[dict]:
    """
    ถอดเสียงจากวิดีโอ → list[{start, end, text}]

    model_size: path ของ local model folder หรือชื่อ whisper ("tiny","base",...)
    words_per_line: จำนวนคำ PyThaiNLP ต่อ 1 ซับ
    """
    if not model_size:
        model_size = DEFAULT_MODEL_PATH

    # 1. แยก audio
    if progress_cb:
        progress_cb("กำลังแยกเสียงจากวิดีโอ …")
    audio = _extract_audio_numpy(video_path)

    # 2. VAD
    speech_ts = _run_vad(audio, progress_cb=progress_cb)
    if not speech_ts:
        return []

    raw_segments: list[dict] = []

    # 3. ถอดเสียง
    if os.path.isdir(model_size):
        # ─── Local transformers model (direct generate, forced Thai) ──────────
        raw_segments = _run_local_whisper(model_size, audio, speech_ts, progress_cb)


    else:
        # ─── Standard openai-whisper package ───────────────────────────────
        try:
            import whisper
        except ImportError:
            raise RuntimeError(
                "ไม่พบ library 'whisper'\n"
                "ติดตั้งด้วย: pip install openai-whisper\n"
                f"หรือระบุ path ของ local model ที่ถูกต้อง (ได้รับ: '{model_size}')"
            )

        if progress_cb:
            progress_cb(f"โหลด Whisper '{model_size}' …")
        model = whisper.load_model(model_size)

        if progress_cb:
            progress_cb(f"ถอดเสียง {len(speech_ts)} chunk (Whisper) …")

        for i, ts in enumerate(speech_ts):
            if progress_cb:
                progress_cb(f"chunk {i+1}/{len(speech_ts)} …")
            start_sec = ts["start"] / 16000.0
            end_sec   = ts["end"]   / 16000.0
            chunk     = audio[ts["start"]:ts["end"]]
            if len(chunk) < 1600:
                continue
            try:
                result = model.transcribe(
                    chunk.copy(),
                    task="transcribe",
                    language="th",
                    no_speech_threshold=0.6,
                    condition_on_previous_text=False,
                    initial_prompt="ภาษาไทย",
                )
                text = (result.get("text") or "").strip()
            except Exception as pipe_err:
                if progress_cb:
                    progress_cb(f"chunk {i+1} error: {type(pipe_err).__name__}: {pipe_err}")
                continue
            if text:
                raw_segments.append({"start": start_sec, "end": end_sec, "text": text})

    # 4. PyThaiNLP word segmentation → แบ่ง segment ย่อย
    if progress_cb:
        progress_cb("ตัดคำด้วย PyThaiNLP …")

    final_segments: list[dict] = []
    for seg in raw_segments:
        lines = _segment_thai(seg["text"], words_per_line=words_per_line)
        dur   = seg["end"] - seg["start"]
        tpl   = dur / max(len(lines), 1)
        for j, line in enumerate(lines):
            final_segments.append({
                "start": seg["start"] + j * tpl,
                "end":   seg["start"] + (j + 1) * tpl,
                "text":  line,
            })

    if progress_cb:
        progress_cb(f"เสร็จ! ได้ {len(final_segments)} ซับ จาก {len(raw_segments)} chunk")

    return final_segments


# ─────────────────────────────────────────────────────────────────────────────
# SRT utilities
# ─────────────────────────────────────────────────────────────────────────────

def segments_to_srt(segments: list[dict], style=None) -> str:
    def fmt_time(t: float) -> str:
        h  = int(t // 3600)
        m  = int((t % 3600) // 60)
        s  = int(t % 60)
        ms = int((t % 1) * 1000)
        return f"{h:02}:{m:02}:{s:02},{ms:03}"

    lines = []
    for i, seg in enumerate(segments, 1):
        lines.append(str(i))
        lines.append(f"{fmt_time(seg['start'])} --> {fmt_time(seg['end'])}")
        lines.append(seg["text"].strip())
        lines.append("")
    return "\n".join(lines)


def save_srt(segments: list[dict], style, output_path: str) -> str:
    srt_content = segments_to_srt(segments, style)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(srt_content)
    return output_path


# compat alias
def wrap_segment(text: str, max_chars: int, max_lines: int) -> str:
    import textwrap
    lines = textwrap.wrap(text.strip(), width=max_chars)
    return "\n".join(lines[:max_lines])
