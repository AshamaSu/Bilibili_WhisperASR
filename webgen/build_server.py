#!/usr/bin/env python3
"""把复习站 + 精听页 + 音频组装成一个可直接 rsync 的静态站点目录。

产物 dist/server/<SeasonKey>/：
    index.html                 复习站（默认 full，含原文例句）
    listen/index.html          精听外壳（audioBase="audio/"，字幕按集懒加载）
    listen/data/<stem>.json    每集字幕（约 60 KB）
    listen/audio/<stem>.m4a    硬链接到 data/<Season>/m4a_32
    serve.py                   本地预览（等价于 nginx 的 MIME + Range）
    nginx.conf  README.md      部署用

URL 约定：/ 复习站，/listen/ 精听页；两页互不链接（精听走直链）。

用法:
    python webgen/build_server.py --season season1
    python webgen/build_server.py --season season1 --mode full --audio-version 20261104
    python webgen/build_server.py --season season1 --verify   # 只校验已有产物
退出码: 0 成功 / 1 数据缺失 / 3 校验失败
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

import build_intensive as bi  # noqa: E402
import build_site as bs  # noqa: E402
from pipeline import config  # noqa: E402

MAX_HTML = 8 * 1024 * 1024
AUDIO_SUB = "listen/audio"


def default_out() -> Path:
    return ROOT / "dist" / "server" / config.SEASON_KEY


def link_audio(src_dir: Path, out_dir: Path, stems):
    out_dir.mkdir(parents=True, exist_ok=True)
    linked = copied = 0
    want = set(s + ".m4a" for s in stems)
    for name in list(out_dir.iterdir()):
        if name.name not in want and name.is_file():
            name.unlink()
    for name in want:
        src = src_dir / name
        dst = out_dir / name
        if dst.exists() and dst.stat().st_size == src.stat().st_size:
            continue
        if dst.exists():
            dst.unlink()
        try:
            os.link(src, dst)
            linked += 1
        except OSError:
            shutil.copy2(src, dst)
            copied += 1
    return linked, copied


def prune_data(data_dir: Path, stems) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    want = set(s + ".json" for s in stems)
    for name in list(data_dir.iterdir()):
        if name.name not in want:
            name.unlink()


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def nginx_conf(root: str, season_key: str) -> str:
    conf = """# %(season)s 站点：/ 复习站，/listen/ 精听页（两页互不链接）
# 放到 /etc/nginx/conf.d/ 下；把 root 指到站点目录后 nginx -t && systemctl reload nginx
server {
    listen 80;
    server_name _;
    root %(root)s;
    charset utf-8;
    sendfile on; tcp_nopush on; keepalive_timeout 65;

    gzip on;
    gzip_types text/html application/json;
    gzip_min_length 1024;

    location = / {
        try_files /index.html =404;
        add_header X-Robots-Tag "noindex, nofollow" always;
        add_header Cache-Control "no-cache";
    }
    # 精听页没有入口；改目录名即可隐藏（页面内全是相对路径）
    location /listen/ {
        try_files $uri $uri/index.html =404;
        add_header X-Robots-Tag "noindex, nofollow" always;
        add_header Cache-Control "no-cache";
        # 只给自己用可开 Basic Auth：
        # auth_basic "restricted"; auth_basic_user_file /etc/nginx/.htpasswd;
    }
    location /listen/data/ {
        add_header X-Robots-Tag "noindex, nofollow" always;
        add_header Cache-Control "public, max-age=604800";
    }
    # ^~ 让下面的 .m4a 拦截规则不作用于本目录（否则音频会被 404）
    location ^~ /listen/audio/ {
        types { audio/mp4 m4a; }
        default_type audio/mp4;
        add_header Accept-Ranges bytes;
        add_header Cache-Control "public, max-age=31536000, immutable";
        open_file_cache max=64 inactive=1h;
        open_file_cache_valid 10m;
        # 5 Mbps 出口建议限并发：先在 http{} 加
        #   limit_conn_zone $binary_remote_addr zone=perip:10m;
        # 再打开下一行
        # limit_conn perip 2;
    }
    # 音频只能从这个目录取，防止绕过缓存头
    location ~* \\.m4a$ { return 404; }
}
"""
    return conf % {"season": season_key, "root": root}


README = """# %(season)s 站点

- / 复习站（%(mode)s 模式）
- /listen/ 精听页（直链进入，两页互不链接）
- 音频 %(count)d 集 / %(size).0f MB，单集 22.05 kHz 单声道 AAC

## 本地预览

    python3 serve.py --port 8080
    # 打开 http://127.0.0.1:8080/        复习站
    # 打开 http://127.0.0.1:8080/listen/ 精听页

## 部署到服务器

    # 1. 传新版本（不要直接覆盖旧目录）
    rsync -av --delete dist/server/%(season)s/ user@host:/var/www/whisper/releases/%(stamp)s/
    # 2. 切软链
    ssh user@host 'ln -sfn /var/www/whisper/releases/%(stamp)s /var/www/whisper/current'
    # 3. 把 nginx.conf 拷进 /etc/nginx/conf.d/whisper.conf，root 指到 current
    scp nginx.conf user@host:/etc/nginx/conf.d/whisper.conf
    ssh user@host 'nginx -t && systemctl reload nginx'

线上不需要 serve.py：nginx 自己提供 audio/mp4 MIME 与 Range/206。serve.py 只用于本地预览。
同一台机器要同时放两季时，改 listen 端口或 server_name（或用 /s1/ /s2/ 两级目录，root 指到上一级）。

## 注意

- 音频 URL 带 ?v=%(ver)s；重新转码后必须换版本号，否则浏览器会一直用旧文件。
- 5 Mbps 出口：一集约 12 MB（中位），下载约 20 秒；同时开两个标签页预取会互相拖慢。
- HTTP 换 HTTPS 会改变 origin，浏览器里存的进度/上次位置会全部失效，换域名前先想清楚。
- 内容含课程原文，仅个人使用；已设 noindex，但请勿公开分享链接。
"""


def verify(out: Path) -> int:
    errs = []
    idx = out / "index.html"
    lis = out / "listen" / "index.html"
    for p in (idx, lis):
        if not p.exists():
            errs.append("缺少 %s" % p)
    if errs:
        for e in errs:
            print("ERROR " + e, file=sys.stderr)
        return 3

    review = idx.read_text(encoding="utf-8")
    listen = lis.read_text(encoding="utf-8")
    if "<audio" in review:
        errs.append("复习站不应包含 <audio>")
    for name, html in (("index.html", review), ("listen/index.html", listen)):
        size = len(html.encode("utf-8"))
        if size > MAX_HTML:
            errs.append("%s %.1f MB 超过 8 MB" % (name, size / 1048576.0))
        if re.search(r'<(?:script|link|img)[^>]+(?:src|href)\s*=\s*["\'](?:https?:)?//', html, re.I):
            errs.append("%s 含绝对外链资源" % name)
    if '"sentsBase":"data/"' not in listen:
        errs.append("listen/index.html 未启用按集加载")
    if '"audioBase":"audio/"' not in listen:
        errs.append("listen/index.html 的 audioBase 不是 audio/")

    stems = [e["file_stem"] for e in bi.load_episodes(bi.INDEX_JSON)]
    data_dir = out / "listen" / "data"
    audio_dir = out / "listen" / "audio"
    miss_d = [s for s in stems if not (data_dir / (s + ".json")).exists()]
    miss_a = [s for s in stems if not (audio_dir / (s + ".m4a")).exists()]
    if miss_d:
        errs.append("字幕缺失 %d 集" % len(miss_d))
    if miss_a:
        errs.append("音频缺失 %d 集" % len(miss_a))

    total = sum(p.stat().st_size for p in audio_dir.glob("*.m4a")) if audio_dir.exists() else 0
    if errs:
        for e in errs:
            print("ERROR " + e, file=sys.stderr)
        return 3
    print("校验通过：%d 集 / 音频 %.0f MB / 复习站 %.2f MB / 精听外壳 %.0f KB"
          % (len(stems), total / 1048576.0,
             idx.stat().st_size / 1048576.0, lis.stat().st_size / 1024.0))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="组装服务器部署目录")
    ap.add_argument("--season", default=None, help="课程季，如 season1 / season2")
    ap.add_argument("--out", default=None, help="默认 dist/server/<季目录名>")
    ap.add_argument("--mode", choices=("full", "minimal"), default="full")
    ap.add_argument("--audio", default=None, help="默认 data/<Season>/m4a_32")
    ap.add_argument("--audio-version", default="", help="音频版本串，默认用最新 mtime 的日期")
    ap.add_argument("--verify", action="store_true", help="只校验已有产物")
    ap.add_argument("--dump", action="store_true", help="只打印计划，不写文件")
    args = ap.parse_args(argv)

    try:
        season = bi.use_season(args.season)   # 同时刷新 bi 的路径常量，--verify 也能用
    except ValueError as e:
        print("ERROR %s" % e, file=sys.stderr)
        return 2

    out = Path(args.out) if args.out else default_out()
    audio_dir = Path(args.audio) if args.audio else config.DATA_DIR / "m4a_32"
    if not audio_dir.is_dir():
        print("ERROR 缺少音频目录 %s" % audio_dir, file=sys.stderr)
        return 1

    if args.verify:
        return verify(out)

    stems = [e["file_stem"] for e in bi.load_episodes(config.EPISODES_JSON)]
    if args.dump:
        print("%s → %s：%d 集，音频 %s" % (season, out, len(stems), audio_dir))
        return 0

    ver = args.audio_version
    if not ver:
        newest = max((audio_dir / (s + ".m4a")).stat().st_mtime for s in stems)
        ver = time.strftime("%Y%m%d", time.localtime(newest))

    print("构建 %s → %s（%d 集，mode=%s，音频版本 %s）" % (season, out, len(stems), args.mode, ver))
    out.mkdir(parents=True, exist_ok=True)

    rc = bs.main(["--season", args.season or season, "--mode", args.mode,
                  "--out", str(out / "index.html")])
    if rc != 0:
        print("ERROR 复习站构建失败（%d）" % rc, file=sys.stderr)
        return rc

    rc = bi.main(["--season", args.season or season, "--split",
                  "--out", str(out / "listen" / "index.html"),
                  "--audio-base", "audio/", "--audio-version", ver])
    if rc != 0:
        print("ERROR 精听页构建失败（%d）" % rc, file=sys.stderr)
        return rc

    prune_data(out / "listen" / "data", stems)
    linked, copied = link_audio(audio_dir, out / AUDIO_SUB, stems)
    shutil.copy2(HERE / "serve_static.py", out / "serve.py")

    root = "/var/www/whisper/%s" % config.SEASON_KEY
    write_text(out / "nginx.conf", nginx_conf(root, config.SEASON_KEY))
    write_text(out / "robots.txt", "User-agent: *\nDisallow: /\n")
    total = sum(p.stat().st_size for p in (out / AUDIO_SUB).glob("*.m4a"))
    write_text(out / "README.md", README % {
        "season": config.SEASON_KEY, "mode": args.mode, "count": len(stems),
        "size": total / 1048576.0, "ver": ver,
        "stamp": time.strftime("%Y%m%d-%H%M%S"),
    })
    print("音频：硬链接 %d / 复制 %d" % (linked, copied))
    return verify(out)


if __name__ == "__main__":
    sys.exit(main())
