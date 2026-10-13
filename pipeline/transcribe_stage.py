"""阶段三：faster-whisper 转写 + 自动 QA 门禁 + 原子产物（评审修正 #1/#2/#3）。

门禁失败 → suspect（不耗重试预算）；运行时/复读失败 → 可重试（上限 MAX_RETRIES）。
"""
import json
import os
import re
import shutil
from collections import Counter

from . import config

_model = None


class TranscribeError(RuntimeError):
    pass


class LanguageGateError(TranscribeError):
    """语种门禁不通过——重试无意义，直接 suspect。"""


class RepetitionError(TranscribeError):
    """检测到复读循环——可用更保守参数重试。"""


def get_model():
    global _model
    if _model is None:
        from faster_whisper import WhisperModel
        _model = WhisperModel(
            config.MODEL_NAME, device=config.DEVICE,
            compute_type=config.COMPUTE_TYPE, cpu_threads=config.CPU_THREADS)
    return _model


def ngram_repeat_ratio(segments):
    """滑窗内最高 8-gram 重复占比（复读循环的强信号）。"""
    n = config.REPETITION_NGRAM
    window = config.REPETITION_WINDOW
    words_per_seg = [re.findall(r"[a-z']+", s["text"].lower()) for s in segments]
    worst = 0.0
    for i in range(0, max(1, len(segments) - window + 1)):
        chunk = [w for ws in words_per_seg[i:i + window] for w in ws]
        grams = [tuple(chunk[j:j + n]) for j in range(len(chunk) - n + 1)]
        if grams:
            worst = max(worst, max(Counter(grams).values()) / len(grams))
    return worst


def transcribe_wav(wav_path, beam_size=None, limit_minutes=None, extra=None):
    """转写单个 WAV。返回 (segments, metrics)。

    先过语种门禁再消费 segments 生成器。limit_minutes 用于冒烟测试截断。
    """
    model = get_model()
    kwargs = dict(config.TRANSCRIBE_DEFAULTS)
    if beam_size:
        kwargs["beam_size"] = beam_size
    if extra:
        kwargs.update(extra)
    segments_iter, info = model.transcribe(str(wav_path), **kwargs)

    prob = info.language_probability or 0.0
    if info.language != config.LANG_EXPECTED or prob < config.LANG_PROB_MIN:
        raise LanguageGateError(
            f"语种门禁: {info.language} p={prob:.2f} "
            f"(要求 {config.LANG_EXPECTED} p≥{config.LANG_PROB_MIN})")

    limit_end = None if limit_minutes is None else limit_minutes * 60
    segments = []
    for seg in segments_iter:
        if limit_end is not None and seg.end > limit_end:
            break
        text = seg.text.strip()
        if text:
            segments.append({"start": round(seg.start, 2),
                             "end": round(seg.end, 2), "text": text})
    metrics = {"language": info.language,
               "language_probability": round(prob, 3),
               "duration": round(info.duration, 1)}
    return segments, metrics


def qa_check(segments, duration_min):
    """自动 QA 门禁。返回 metrics dict（含 flags，非空 → suspect）。"""
    text = " ".join(s["text"] for s in segments)
    word_count = len(re.findall(r"[a-zA-Z']+", text))
    rep = ngram_repeat_ratio(segments)
    flags = []
    min_words = config.MIN_WORDS_PER_10MIN * max(duration_min, 0.1) / 10
    if word_count == 0:
        flags.append("empty")
    elif word_count < min_words:
        flags.append("word_count")
    if rep > config.REPETITION_SUSPECT_RATIO:
        flags.append("repetition")
    return {"word_count": word_count, "repetition_ratio": round(rep, 4),
            "flags": flags}


def _srt_ts(t):
    ms = int(round(t * 1000))
    h, rem = divmod(ms, 3600000)
    m, rem = divmod(rem, 60000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_artifacts(ep, segments, metrics):
    """三件产物写 .tmp 目录 → fsync → 原子改名（评审修正 #4）。"""
    final_dir = config.TRANSCRIPTS_DIR / ep["file_stem"]
    tmp_dir = config.TRANSCRIPTS_DIR / (ep["file_stem"] + ".tmp")
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    tmp_dir.mkdir(parents=True)

    txt = "\n".join(s["text"] for s in segments) + "\n"
    srt_lines = []
    for i, s in enumerate(segments, 1):
        srt_lines += [str(i),
                      f"{_srt_ts(s['start'])} --> {_srt_ts(s['end'])}",
                      s["text"], ""]
    payload = {"no": ep["no"], "file_stem": ep["file_stem"], "cid": ep["cid"],
               "title_fixed": ep["title_fixed"], "metrics": metrics,
               "segments": segments}
    files = {
        "transcript.txt": txt,
        "transcript.srt": "\n".join(srt_lines),
        "transcript.json": json.dumps(payload, ensure_ascii=False, indent=2),
    }
    for name, content in files.items():
        with open(tmp_dir / name, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
    if final_dir.exists():
        shutil.rmtree(final_dir)
    os.replace(tmp_dir, final_dir)
    return final_dir
