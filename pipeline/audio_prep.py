"""阶段 0：audio.m4s → m4a（流复制，不重编码）。

第二季起源只有 B 站缓存的 fragmented mp4（audio.m4s），而 http 后端上传走 m4a；
流复制几秒一集，顺带把时长校验一遍。产物落 data/<Season>/m4a/<file_stem>.m4a。
"""
import json
import subprocess
from pathlib import Path

from . import config


class PrepError(RuntimeError):
    pass


def m4a_path(ep):
    return config.M4A_DIR / (ep["file_stem"] + ".m4a")


def source(ep):
    cands = [config.M4A_DIR / (ep["file_stem"] + ".m4a")]
    if ep.get("m4s_rel"):
        cands.append(config.DATA_DIR / ep["dir"] / ep["m4s_rel"])
    for cand in cands:
        if cand.exists():
            return cand
    raise PrepError("找不到源音频: %s/%s" % (config.DATA_DIR.name, ep["dir"]))


def probe_duration(path):
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def prepare(ep, force=False):
    src = source(ep)
    out = m4a_path(ep)
    if out.suffix != ".m4a":
        raise PrepError("内部错误：输出不是 m4a")
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists() and not force and out.stat().st_mtime >= src.stat().st_mtime:
        return out, "skip"
    tmp = out.with_suffix(".m4a.part")
    tmp.unlink(missing_ok=True)
    cmd = [config.FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
           "-i", str(src), "-vn", "-map", "0:a:0", "-c:a", "copy",
           "-movflags", "+faststart", "-f", "mp4", str(tmp)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        tmp.unlink(missing_ok=True)
        raise PrepError("ffmpeg 失败 (%s): %s" % (ep["file_stem"], r.stderr.strip()[-300:]))
    got, want = probe_duration(tmp), ep["duration_min"] * 60
    if want and abs(got - want) > max(5.0, want * 0.02):
        tmp.unlink(missing_ok=True)
        raise PrepError("时长不符 %s：%.0fs vs 预期 %.0fs" % (ep["file_stem"], got, want))
    tmp.replace(out)
    return out, "ok"
