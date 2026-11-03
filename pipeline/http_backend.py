"""远端 WhisperX 后端：上传音频 → 映射 segments → 门禁 → (segments, metrics)。

契约与 transcribe_stage.transcribe_wav 完全一致，因此 qa_check / write_artifacts /
candidates_stage 一行都不用改。服务端字段差异见 PLAN §9：
  - 缺 language_probability / duration（概率存 None，不编造；时长用索引期时长）
  - 顶层 text 与 word_segments 是冗余垃圾，一律丢弃
"""
import subprocess
import time

import httpx

from . import config
from .transcribe_stage import (BackendUnavailable, CoverageError,
                               LanguageGateError, RemoteError)

RETRYABLE_STATUS = {408, 425, 429}
FATAL_STATUS = {400, 413, 414, 415, 422}


def endpoint():
    if not config.HTTP_URL:
        raise BackendUnavailable(
            "未配置服务器地址：在 conn/asr.env 写 ASR_URL=... 或 export ASR_URL")
    return config.HTTP_URL


def health(timeout=8.0):
    """GET /health；连不上直接抛 BackendUnavailable（批次级中止）。"""
    url = endpoint() + "/health"
    try:
        with httpx.Client(timeout=timeout) as c:
            resp = c.get(url)
    except httpx.HTTPError as e:
        raise BackendUnavailable(f"无法连接 {url}: {type(e).__name__} {e}") from None
    if resp.status_code != 200:
        raise BackendUnavailable(f"{url} 返回 HTTP {resp.status_code}")
    try:
        return resp.json()
    except ValueError:
        raise BackendUnavailable(f"{url} 返回非 JSON") from None


def _truncate(path, minutes):
    """冒烟用：截前 N 分钟到 work/tmp；http 路径唯一会碰 ffmpeg 的地方。"""
    config.WORK_TMP.mkdir(parents=True, exist_ok=True)
    out = config.WORK_TMP / (path.stem + "_smoke.wav")
    cmd = [config.FFMPEG, "-y", "-nostdin", "-i", str(path), "-vn",
           "-map", "0:a:0", "-t", str(minutes * 60),
           "-ar", str(config.WAV_SAMPLE_RATE), "-ac", "1",
           "-c:a", "pcm_s16le", "-f", "wav", str(out)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RemoteError(f"截断音频失败: {proc.stderr[-300:]}")
    return out


def _timeout_for(size_mb):
    return httpx.Timeout(
        connect=config.HTTP_TIMEOUT_CONNECT_S,
        read=config.HTTP_TIMEOUT_READ_S,
        write=max(config.HTTP_WRITE_MIN_S, size_mb * 3),
        pool=config.HTTP_TIMEOUT_CONNECT_S)


def _params():
    return {"language": config.LANG_EXPECTED,
            "word_timestamps": "true",
            "diarize": "true" if config.HTTP_DIARIZE else "false",
            "output_format": "json",
            "model": config.HTTP_MODEL}


def _check_status(resp):
    code = resp.status_code
    if code == 200:
        return
    detail = (resp.text or "")[:300].replace("\n", " ")
    if code in (401, 403):
        raise BackendUnavailable(f"远端拒绝访问 HTTP {code}: {detail}")
    if code in RETRYABLE_STATUS or code >= 500:
        raise RemoteError(f"HTTP {code}: {detail}")
    raise RemoteError(f"HTTP {code}: {detail}", retryable=False)


def _map_segments(payload):
    """只取 segments[]；保留词级时间戳（写进 transcript.json 是本次主要收益）。"""
    out = []
    for s in payload.get("segments") or []:
        text = str(s.get("text", "")).strip()
        if not text:
            continue
        item = {"start": round(float(s.get("start") or 0.0), 2),
                "end": round(float(s.get("end") or 0.0), 2),
                "text": text}
        words = s.get("words")
        if words:
            item["words"] = [
                {"word": w.get("word"), "start": round(float(w.get("start") or 0.0), 2)
                 if w.get("start") is not None else None,
                 "end": round(float(w.get("end") or 0.0), 2)
                 if w.get("end") is not None else None,
                 "score": w.get("score")}
                for w in words]
        if config.HTTP_DIARIZE and s.get("speaker") is not None:
            item["speaker"] = s["speaker"]
        out.append(item)
    out.sort(key=lambda x: x["start"])
    return out


def _check_language(payload):
    """服务不返回概率，只能做字符串门禁。"""
    lang = str(payload.get("language") or "").strip().lower()
    if lang not in ("en", "english"):
        raise LanguageGateError(
            f"语种门禁: {lang or '未知'}（要求 {config.LANG_EXPECTED}，服务不返回概率）")
    return config.LANG_EXPECTED


def _check_span(segments, expected_duration, min_span=None):
    if not expected_duration:
        return None
    floor = config.HTTP_MIN_SPAN_RATIO if min_span is None else float(min_span)
    ratio = max(s["end"] for s in segments) / float(expected_duration)
    if ratio < floor:
        raise CoverageError(
            f"段覆盖 {ratio:.3f} < {floor}（疑似静默截断；片尾长静音可用 --min-span 显式放宽）")
    return ratio


def transcribe_file(path, expected_duration=None, limit_minutes=None, min_span=None):
    """上传单个音频并返回 (segments, metrics)。"""
    url = endpoint() + "/asr"
    tmp = None
    src = path
    if limit_minutes:
        tmp = _truncate(path, limit_minutes)
        src = tmp
    try:
        size_bytes = src.stat().st_size
        eff_duration = expected_duration
        if eff_duration and limit_minutes:
            eff_duration = min(eff_duration, limit_minutes * 60)

        started = time.time()
        try:
            with httpx.Client(timeout=_timeout_for(size_bytes / 1e6)) as client, \
                    open(src, "rb") as fh:
                resp = client.post(url, params=_params(),
                                   files={"audio_file": (src.name, fh, "audio/mp4")})
        except (httpx.ConnectError, httpx.ConnectTimeout) as e:
            raise BackendUnavailable(f"无法连接 {url}: {type(e).__name__} {e}") from None
        except httpx.TimeoutException as e:
            raise RemoteError(f"请求超时 {type(e).__name__}: {e}") from None
        except httpx.HTTPError as e:
            raise RemoteError(f"传输失败 {type(e).__name__}: {e}") from None
        elapsed = round(time.time() - started, 1)

        _check_status(resp)
        try:
            payload = resp.json()
        except ValueError:
            raise RemoteError("响应不是合法 JSON") from None

        segments = _map_segments(payload)
        if not segments:
            raise RemoteError("远端返回空 segments")
        language = _check_language(payload)
        span = _check_span(segments, eff_duration, min_span)

        metrics = {
            "language": language,
            "language_probability": None,          # 服务不返回，不编造
            "duration": round(eff_duration, 1) if eff_duration else None,
            "backend": "http",
            "model": config.HTTP_MODEL,
            "span_ratio": round(span, 4) if span is not None else None,
            "segment_count": len(segments),
            "words_added": any("words" in s for s in segments),
            "diarized": config.HTTP_DIARIZE,
            "upload_bytes": size_bytes,
            "elapsed_s": elapsed,
        }
        return segments, metrics
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)
