"""阶段二：audio.m4s → 16k mono WAV。临时文件 + 原子替换 + 磁盘守卫（评审修正 #10）。"""
import os
import shutil
import subprocess

from . import config


class ConvertError(RuntimeError):
    pass


def wav_path(cid):
    return config.WORK_DIR / f"{cid}.wav"


def check_disk():
    free = shutil.disk_usage(config.WORK_DIR).free
    if free < config.MIN_FREE_DISK_GB * 1024 ** 3:
        raise ConvertError(
            f"磁盘剩余 {free / 1024 ** 3:.1f}GB < {config.MIN_FREE_DISK_GB}GB，中止转换")


def source_audio(ep):
    """优先用 B 站缓存原件；缓存已清理时回退到 data/m4a/<file_stem>.m4a。"""
    m4s = config.DATA_DIR / ep["dir"] / ep["m4s_rel"]
    if m4s.exists():
        return m4s
    m4a = config.DATA_DIR / "m4a" / f"{ep['file_stem']}.m4a"
    if m4a.exists():
        return m4a
    raise ConvertError(f"音频不存在: {m4s} / {m4a}")


def convert_episode(ep):
    m4s = source_audio(ep)
    config.WORK_TMP.mkdir(parents=True, exist_ok=True)
    check_disk()
    out = wav_path(ep["cid"])
    tmp = config.WORK_TMP / f"{ep['cid']}.wav.part"
    tmp.unlink(missing_ok=True)
    # -vn -map 0:a:0：即使遇到混流文件也只取音频轨（评审修正 #10）
    cmd = [
        config.FFMPEG, "-y", "-nostdin", "-i", str(m4s),
        "-vn", "-map", "0:a:0",
        "-ar", str(config.WAV_SAMPLE_RATE), "-ac", "1", "-c:a", "pcm_s16le",
        "-f", "wav",
        str(tmp),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise ConvertError(f"ffmpeg 失败 (cid={ep['cid']}): {proc.stderr[-500:]}")
    # 时长守卫：字节数应 ≈ 时长×32KB/s，±10%
    expected = ep["duration_min"] * 60 * config.WAV_BYTES_PER_SEC
    actual = tmp.stat().st_size
    if not (0.9 * expected <= actual <= 1.1 * expected):
        tmp.unlink(missing_ok=True)
        raise ConvertError(
            f"WAV 大小异常: {actual}B，预期 {expected:.0f}B (cid={ep['cid']})")
    os.replace(tmp, out)
    return out


def cleanup_stale_wavs():
    """启动时清理上次崩溃残留的 WAV/临时文件（转一集删一集，皆属瞬态）。"""
    removed = 0
    for d, patterns in ((config.WORK_DIR, ("*.wav",)),
                        (config.WORK_TMP, ("*.wav.part",))):
        for pat in patterns:
            for p in d.glob(pat):
                p.unlink(missing_ok=True)
                removed += 1
    if removed:
        print(f"[cleanup] 清理陈旧 WAV/临时文件 {removed} 个")
    return removed
