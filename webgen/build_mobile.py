#!/usr/bin/env python3
"""把精读页打包成「手机本地服务器」形态（Android 离线随身学习）。

产物 dist/mobile/：
    index.html                 音频基址指向 audio/，其余与桌面版同源
    audio/<file_stem>.m4a      语音码率转码版（默认 data/m4a_32）
    README.md                  手机侧安装步骤

手机端不需要 HTTPS、不需要 Service Worker：用一个静态 HTTP 服务器 app
把本目录挂到 127.0.0.1，浏览器打开即可，完全离线。

用法:
    python webgen/build_mobile.py            # 打包 + 校验
    python webgen/build_mobile.py --dump     # 只统计
    python webgen/build_mobile.py --verify   # 只校验已有产物
退出码: 0 成功 / 1 数据缺失 / 3 校验失败
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import build_intensive as bi  # noqa: E402

ROOT = bi.ROOT
DEFAULT_OUT = None      # 由 use_season() 填充：dist/mobile/<season>
DEFAULT_AUDIO = None    # data/<Season>/m4a_32


def use_season(name=None):
    global DEFAULT_OUT, DEFAULT_AUDIO
    season = bi.use_season(name)
    DEFAULT_OUT = ROOT / "dist" / "mobile" / bi.config.SEASON_KEY
    DEFAULT_AUDIO = bi.config.DATA_DIR / "m4a_32"
    return season
MAX_HTML = 8 * 1024 * 1024
BT = chr(96)

README = """# 精读辅助 · 手机离线版

单页应用 + 本地音频，不需要联网、不需要 HTTPS、不需要装插件。
原理：手机上的一个静态 HTTP 服务器 app 把本目录挂到 127.0.0.1，浏览器打开即可。
不用 Service Worker，因此夸克等国内浏览器也能用。

## 一次性准备

1. 装 Termux（F-Droid 或 GitHub releases 的包；应用商店的旧版不要用）。
2. 把整个 mobile/ 文件夹拷到手机存储，例如 /Download/whisperASR/
   （USB 连接 Mac → 文件传输）。
3. 打开 Termux，依次执行（只需一次装环境）：

   termux-setup-storage      # 弹窗允许访问存储
   pkg update && pkg install -y python

4. 每次启动服务（目录里已带 start.sh + serve.py，Termux 里 sh start.sh）：

   cd /sdcard/Download/whisperASR
   python -m http.server 8080 --bind 127.0.0.1

   看到 服务中: http://127.0.0.1:8080/ 就是起来了，别关这个会话（可长按返回键保持后台）。

> 不想用命令行的话，装 KSWEB（内置 nginx，图形界面选目录）或任意
> 「HTTP Server」类小工具也可以，要求两条：能指定根目录、只监听 127.0.0.1。

## 每次学习

1. 启动那个服务器 app（多数可设开机自启或桌面快捷方式）。
2. 浏览器打开 http://127.0.0.1:8080/
3. 浏览器菜单「添加到桌面」，下次点图标即进。

## 已知限制

- 音频总占用约 {size}，请预留空间。
- 播放进度、倍速、同步滚动开关存在浏览器 localStorage。
- 部分浏览器限制后台播放：锁屏后若停播，把浏览器加入电池白名单 / 允许后台运行。
- 该内核无 Wake Lock，长时间不动会自动锁屏；锁屏后音频一般继续，回到页面时字幕重新对齐。
"""


def collect(audio_dir: Path):
    eps = bi.load_episodes(bi.INDEX_JSON)
    data = {"v": 1, "eps": [], "total_sents": 0}
    missing = []
    for ep in eps:
        stem = ep["file_stem"]
        if not (audio_dir / (stem + ".m4a")).exists():
            missing.append(stem)
            continue
        sents = bi.load_sents(stem)
        data["total_sents"] += len(sents)
        data["eps"].append({
            "no": ep["no"], "file_stem": stem,
            "title": ep.get("title_fixed") or ep.get("title_raw", ""),
            "dur_min": ep.get("duration_min"), "sents": sents,
        })
    if missing:
        raise ValueError("音频缺失 %d 个：%s" % (len(missing), ", ".join(missing[:5])))
    return data


def verify(out: Path):
    errs = []
    total = 0
    html_p = out / "index.html"
    if not html_p.exists():
        return ["缺少 index.html"], 0
    html = html_p.read_text(encoding="utf-8")
    if len(html.encode("utf-8")) > MAX_HTML:
        errs.append("index.html 超过 8 MB")
    if re.search(r"""(?:src|href)\s*=\s*["']?(?:https?:)?//""", html, re.I):
        errs.append("检出绝对外链（离线环境会失效）")
    if '{"audioBase":"audio/"}' not in html:
        errs.append("音频基址不是 audio/")
    if 'id="site-data"' not in html:
        errs.append("缺少内联字幕数据")

    eps = bi.load_episodes(bi.INDEX_JSON)
    want = {e["file_stem"] + ".m4a" for e in eps}
    adir = out / "audio"
    got = {p.name for p in adir.glob("*.m4a")} if adir.is_dir() else set()
    if want - got:
        errs.append("缺少音频 %d 个" % len(want - got))
    if got - want:
        errs.append("多余音频 %d 个" % len(got - want))
    for n in got & want:
        total += (adir / n).stat().st_size
    return errs, total


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="打包手机离线版")
    ap.add_argument("--season", default=None, help="课程季，如 season1 / season2")
    ap.add_argument("--out", default=None, help="默认 dist/mobile/<season>")
    ap.add_argument("--audio", default=None, help="默认 data/<Season>/m4a_32")
    ap.add_argument("--dump", action="store_true")
    ap.add_argument("--verify", action="store_true", help="只校验不重建")
    args = ap.parse_args(argv)

    try:
        use_season(args.season)
    except ValueError as e:
        print("ERROR %s" % e, file=sys.stderr)
        return 1
    audio_dir = Path(args.audio) if args.audio else DEFAULT_AUDIO
    out = Path(args.out) if args.out else DEFAULT_OUT

    if args.verify:
        errs, total = verify(out)
        for e in errs:
            print("ERROR " + e, file=sys.stderr)
        if errs:
            return 3
        print("校验通过：音频 %.0f MB" % (total / 1048576.0))
        return 0

    try:
        data = collect(audio_dir)
    except (ValueError, OSError, json.JSONDecodeError) as e:
        print("ERROR %s" % e, file=sys.stderr)
        return 1

    total_audio = sum((audio_dir / (e["file_stem"] + ".m4a")).stat().st_size for e in data["eps"])
    print("集数 %d / 句子 %d / 音频 %.0f MB" % (len(data["eps"]), data["total_sents"], total_audio / 1048576.0))
    if args.dump:
        return 0

    tpl = bi.TEMPLATE.read_text(encoding="utf-8")
    html = bi.render_template(tpl, bi.embed_json(data), bi.embed_json({"audioBase": "audio/"}))
    errs = bi.check_output(html)
    if errs:
        for e in errs:
            print("ERROR 产物校验：%s" % e, file=sys.stderr)
        return 3

    out.mkdir(parents=True, exist_ok=True)
    bi.write_atomic(out / "index.html", html)

    adir = out / "audio"
    adir.mkdir(parents=True, exist_ok=True)
    for e in data["eps"]:
        src = audio_dir / (e["file_stem"] + ".m4a")
        dst = adir / src.name
        if dst.exists() and dst.stat().st_size == src.stat().st_size:
            continue
        dst.unlink(missing_ok=True)
        try:
            os.link(src, dst)
        except OSError:
            shutil.copy2(src, dst)

    # serve.py：修正 .m4a 的 MIME + 支持 Range，python -m http.server 会播不出声
    shutil.copy2(HERE / "serve_mobile.py", out / "serve.py")
    (out / "start.sh").write_text(
        '#!/data/data/com.termux/files/usr/bin/sh\n'
        'cd "$(dirname "$0")" || exit 1\n'
        'exec python serve.py --port 8080\n', encoding="utf-8")

    (out / "README.md").write_text(
        README.replace("{size}", "%.0f MB" % (total_audio / 1048576.0)), encoding="utf-8")

    errs, total = verify(out)
    for e in errs:
        print("ERROR " + e, file=sys.stderr)
    if errs:
        return 3
    print("已写出 %s：index.html %.2f MB + 音频 %.0f MB" % (
        out, (out / "index.html").stat().st_size / 1048576.0, total / 1048576.0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
