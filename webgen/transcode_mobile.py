#!/usr/bin/env python3
"""把 data/<Season>/m4a/*.m4a 转成移动端语音码率（单声道 22.05kHz AAC），供离线打包使用。

源为 44.1kHz stereo AAC ~72kbps；降采样到 22.05kHz 后同样的比特更集中在中频，
24k 体积约 578MB，32k 约 770MB（全 67 集 / 56.1h）。

用法:
    python webgen/transcode_mobile.py                 # 32k（默认），并发 3
    python webgen/transcode_mobile.py --bitrate 24k
    python webgen/transcode_mobile.py --episodes 1-3  # 只转前 3 集（A/B 试听）
    python webgen/transcode_mobile.py --verify        # 只校验不转码
退出码: 0 成功 / 1 转码失败 / 2 环境缺失 / 3 校验失败
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from pipeline import config  # noqa: E402

SRC = None   # 由 --season 解析（data/<Season>/m4a）
SEASON = None


def parse_eps(spec: str) -> set[int] | None:
    if not spec:
        return None
    out: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            out.update(range(int(a), int(b) + 1))
        elif part:
            out.add(int(part))
    return out


def ep_no(path: Path) -> int:
    return int(path.name.split("_", 1)[0])


def ffmpeg_one(src: Path, dst: Path, bitrate: str) -> tuple[Path, str | None]:
    cmd = [
        "ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
        "-i", str(src),
        "-vn", "-ac", "1", "-ar", "22050",
        "-c:a", "aac", "-b:a", bitrate,
        "-af", "highpass=f=70",
        "-map_metadata", "-1", "-movflags", "+faststart",
        "-f", "mp4", str(dst),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        dst.unlink(missing_ok=True)
        return src, (r.stderr.strip().splitlines() or ["ffmpeg 失败"])[-1]
    return src, None


def probe(path: Path) -> dict:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0",
         "-show_entries", "stream=codec_name,channels,sample_rate,bit_rate:format=duration",
         "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    )
    d = json.loads(r.stdout)
    st = (d.get("streams") or [{}])[0]
    return {
        "codec": st.get("codec_name"),
        "channels": st.get("channels"),
        "rate": st.get("sample_rate"),
        "kbps": int(st.get("bit_rate") or 0) // 1000,
        "dur": float((d.get("format") or {}).get("duration") or 0),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="移动端音频转码")
    ap.add_argument("--season", default=None, help="课程季，如 season1 / season2")
    ap.add_argument("--bitrate", default="32k")
    ap.add_argument("--out", default="", help="输出目录，默认 data/m4a_<bitrate>")
    ap.add_argument("--episodes", default="", help="如 1-3,9")
    ap.add_argument("--jobs", type=int, default=3)
    ap.add_argument("--verify", action="store_true")
    args = ap.parse_args(argv)

    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        print("ERROR 需要 ffmpeg / ffprobe", file=sys.stderr)
        return 2
    try:
        season = config.set_season(args.season)
    except ValueError as e:
        print("ERROR %s" % e, file=sys.stderr)
        return 2
    src_dir = config.M4A_DIR
    if not src_dir.is_dir():
        print("ERROR 缺少 %s" % src_dir, file=sys.stderr)
        return 2

    out_dir = (Path(args.out) if args.out
               else config.DATA_DIR / ("m4a_" + args.bitrate.replace("k", "")))
    out_dir.mkdir(parents=True, exist_ok=True)

    want = parse_eps(args.episodes)
    srcs = sorted(p for p in src_dir.glob("*.m4a") if want is None or ep_no(p) in want)
    if not srcs:
        print("ERROR 没有匹配的源文件", file=sys.stderr)
        return 2

    todo = [p for p in srcs if not (args.verify or
            ((out_dir / p.name).exists() and (out_dir / p.name).stat().st_mtime >= p.stat().st_mtime))]
    print("源 %d 个 / 待转 %d 个 / 输出 %s / 码率 %s" % (len(srcs), len(todo), out_dir, args.bitrate))

    if todo and not args.verify:
        fails = []
        done = 0
        with cf.ThreadPoolExecutor(max_workers=max(1, args.jobs)) as ex:
            futs = {ex.submit(ffmpeg_one, p, out_dir / p.name, args.bitrate): p for p in todo}
            for fut in cf.as_completed(futs):
                src, err = fut.result()
                done += 1
                if err:
                    fails.append((src.name, err))
                    print("  [%d/%d] FAIL %s: %s" % (done, len(todo), src.name, err), file=sys.stderr)
                else:
                    mb = (out_dir / src.name).stat().st_size / 1048576
                    print("  [%d/%d] ok %s %.1fMB" % (done, len(todo), src.name, mb), flush=True)
        if fails:
            print("ERROR %d 个转码失败" % len(fails), file=sys.stderr)
            return 1

    # 校验
    bad = []
    total = 0
    minutes = 0.0
    for p in srcs:
        dst = out_dir / p.name
        if not dst.exists():
            bad.append("%s 缺失" % p.name)
            continue
        total += dst.stat().st_size
        try:
            m = probe(dst)
            s = probe(p)
        except Exception as e:
            bad.append("%s ffprobe 失败: %s" % (p.name, e))
            continue
        minutes += m["dur"] / 60
        if m["codec"] != "aac":
            bad.append("%s codec=%s" % (p.name, m["codec"]))
        if int(m["channels"] or 0) != 1:
            bad.append("%s channels=%s" % (p.name, m["channels"]))
        if int(m["rate"] or 0) != 22050:
            bad.append("%s sample_rate=%s" % (p.name, m["rate"]))
        if m["kbps"] > 40:
            bad.append("%s kbps=%s" % (p.name, m["kbps"]))
        if abs(m["dur"] - s["dur"]) > 0.3:
            bad.append("%s 时长差 %.2fs" % (p.name, m["dur"] - s["dur"]))

    print("校验：%d 个 / %.1f 分钟 / 合计 %.0f MB" % (len(srcs) - len([b for b in bad if "缺失" in b]), minutes, total / 1048576))
    if bad:
        for b in bad[:20]:
            print("  ERROR " + b, file=sys.stderr)
        return 3
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
