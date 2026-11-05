#!/usr/bin/env python3
"""把 transcripts/ 句级字幕 + data/m4a 音频组装成精读页 intensive/index.html。

默认单文件（字幕内联，file:// 可直接双击）；--split 则字幕按集拆到 data/<stem>.json，
页面按需 fetch 并内存缓存，供服务器部署使用（首屏从 1.4 MB gzip 降到 ~13 KB）。

数据源：index/episodes.json（67 集）+ transcripts/<file_stem>/transcript.json（segments）。
音频不内联：<audio> 指向 data/<Season>/m4a/<file_stem>.m4a（相对路径按产物位置自动计算，file:// 双击即用）。
用法:
    python webgen/build_intensive.py            # 生成 intensive/index.html
    python webgen/build_intensive.py --dump     # 只打印统计
退出码: 0 成功 / 1 数据校验失败 / 3 产物校验失败
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import List, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from pipeline import config  # 季路径唯一来源

INDEX_JSON = None      # 由 use_season() 填充
TRANSCRIPTS = None
M4A_DIR = None
TEMPLATE = Path(__file__).resolve().parent / "template_intensive.html"


def use_season(name=None):
    """切换季并同步本模块的路径常量。"""
    global INDEX_JSON, TRANSCRIPTS, M4A_DIR, DEFAULT_OUT
    config.set_season(name)
    INDEX_JSON = config.EPISODES_JSON
    TRANSCRIPTS = config.TRANSCRIPTS_DIR
    M4A_DIR = config.M4A_DIR
    DEFAULT_OUT = config.SEASON_DIR / "intensive" / "index.html"
    return config.SEASON

MAX_BYTES = 8 * 1024 * 1024
WARN_BYTES = 6 * 1024 * 1024
PLACEHOLDER = "__SITE_DATA__"
CONFIG_PLACEHOLDER = "__SITE_CONFIG__"



def load_episodes(path: Path) -> List[dict]:
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list) or not data:
        raise ValueError("episodes.json 不是非空数组")
    done = [e for e in data if e.get("status") == "done"]
    done.sort(key=lambda e: e["no"])
    for i, e in enumerate(done):
        if e["no"] != i + 1:
            raise ValueError("done 集号不连续：期望 %d 实际 %d" % (i + 1, e["no"]))
    return done


def load_sents(file_stem: str) -> List[list]:
    path = TRANSCRIPTS / file_stem / "transcript.json"
    with path.open(encoding="utf-8") as f:
        d = json.load(f)
    sents = []
    for s in d["segments"]:
        text = str(s.get("text", "")).strip()
        if not text:
            continue
        sents.append([round(float(s["start"]), 2), round(float(s["end"]), 2), text])
    if not sents:
        raise ValueError("%s 无有效句子" % file_stem)
    # 时间轴单调校验
    for a, b in zip(sents, sents[1:]):
        if b[0] < a[0] - 0.01:
            raise ValueError("%s 句子时间轴非单调" % file_stem)
    return sents


def build_data(embed_sents: bool = True) -> tuple:
    """返回 (页面数据, 每集字幕)。embed_sents=False 时页面只带集目录，字幕按集拆文件。"""
    eps = []
    sents_map = {}
    total = 0
    for ep in load_episodes(INDEX_JSON):
        stem = ep["file_stem"]
        m4a = M4A_DIR / (stem + ".m4a")
        if not m4a.exists():
            raise ValueError("音频缺失: %s" % m4a)
        sents = load_sents(stem)
        sents_map[stem] = sents
        total += len(sents)
        row = {"no": ep["no"], "file_stem": stem,
               "title": ep.get("title_fixed") or ep.get("title_raw", ""),
               "dur_min": ep.get("duration_min"), "n": len(sents)}
        if embed_sents:
            row["sents"] = sents
        eps.append(row)
    return {"v": 2, "eps": eps, "total_sents": total}, sents_map


def embed_json(data: dict) -> str:
    s = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    s = s.replace("</", "<\\/")
    s = s.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    if "</script" in s.lower():
        raise ValueError("内联 JSON 仍含 </script")
    return s


def render_template(tpl: str, payload: str, config: str = "") -> str:
    for ph in (PLACEHOLDER, CONFIG_PLACEHOLDER):
        if tpl.count(ph) != 1:
            raise ValueError("占位符 %s 出现 %d 次，必须恰好 1 次" % (ph, tpl.count(ph)))
    return tpl.replace(PLACEHOLDER, payload).replace(CONFIG_PLACEHOLDER, config)


def write_atomic(path: Path, text: str) -> None:
    """内容未变则跳过写入：保留 mtime，rsync 按 size+mtime 判定时不会重传。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = text.encode("utf-8")
    if path.exists() and path.read_bytes() == data:
        return
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(tmp, path)


def check_output(html: str) -> List[str]:
    errs = []
    size = len(html.encode("utf-8"))
    if size > MAX_BYTES:
        errs.append("产物 %.2f MB 超过 8 MB 上限" % (size / 1048576.0))
    # 外部资源黑名单（相对路径 audio src 除外，这正是本页的核心）
    # 允许同源相对路径 fetch（按集加载字幕 / 整集预取音频），只拦绝对地址与外部资源
    bad = [r"<script[^>]+src=", r"<link[^>]+href=", r"<img[^>]+src=",
           r"<iframe", r"<video", r"@import", r"(?<![\w-])url\(",
           r"""fetch\(\s*["'](?:https?:)?//""", r"XMLHttpRequest"]
    for pat in bad:
        if re.search(pat, html, re.I):
            errs.append("检出外链/网络请求：%s" % pat)
    return errs


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="生成精读辅助单文件页面")
    ap.add_argument("--season", default=None, help="课程季，如 season1 / season2")
    ap.add_argument("--out", default=None, help="默认 output/<季目录>/intensive/index.html")
    ap.add_argument("--dump", action="store_true", help="只打印统计，不写文件")
    ap.add_argument("--audio-base", default=None,
                    help="音频相对基址；默认按产物位置到 data/<Season>/m4a/ 的相对路径计算")
    ap.add_argument("--audio-version", default="",
                    help="音频版本串，拼到 URL 的 ?v= 上（重转码后绕开 immutable 缓存）")
    ap.add_argument("--split", action="store_true",
                    help="字幕按集拆成 data/<stem>.json，页面按需加载（服务器部署用）")
    ap.add_argument("--data-dir", default=None,
                    help="拆分时字幕输出目录，默认与 index.html 同级的 data/")
    args = ap.parse_args(argv)

    try:
        use_season(args.season)
    except ValueError as e:
        print("ERROR %s" % e, file=sys.stderr)
        return 1
    if not args.out:
        args.out = str(DEFAULT_OUT)
    if not args.audio_base:
        # 产物在 output/<季目录>/intensive/ 下，音频在 data/<Season>/m4a/，按实际位置算相对路径
        args.audio_base = os.path.relpath(M4A_DIR, Path(args.out).parent) + "/"
    if not args.audio_base.endswith("/"):
        args.audio_base += "/"

    try:
        data, sents_map = build_data(embed_sents=not args.split)
    except (ValueError, OSError, json.JSONDecodeError) as e:
        print("ERROR %s" % e, file=sys.stderr)
        return 1

    print("集数 %d / 句子总数 %d / 音频全部存在" % (len(data["eps"]), data["total_sents"]))
    if args.dump:
        return 0

    out = Path(args.out)
    cfg_obj = {"audioBase": args.audio_base, "ns": config.SEASON}
    if args.audio_version:
        cfg_obj["audioVersion"] = args.audio_version
    if args.split:
        data_dir = Path(args.data_dir) if args.data_dir else out.parent / "data"
        rel = os.path.relpath(data_dir, out.parent).replace(os.sep, "/")
        cfg_obj["sentsBase"] = rel.rstrip("/") + "/"

    try:
        tpl = TEMPLATE.read_text(encoding="utf-8")
        cfg = embed_json(cfg_obj)
        html = render_template(tpl, embed_json(data), cfg)
    except (OSError, ValueError) as e:
        print("ERROR 渲染失败：%s" % e, file=sys.stderr)
        return 1

    errs = check_output(html)
    if errs:
        for e in errs:
            print("ERROR 产物校验：%s" % e, file=sys.stderr)
        return 3

    if args.split:
        for stem, rows in sents_map.items():
            write_atomic(data_dir / (stem + ".json"),
                         json.dumps(rows, ensure_ascii=False, separators=(",", ":")))
        print("字幕拆分为 %d 个文件 → %s" % (len(sents_map), data_dir))
    write_atomic(out, html)
    size = len(html.encode("utf-8"))
    if size > WARN_BYTES:
        print("WARN 产物 %.2f MB，超过 6 MB 提示线" % (size / 1048576.0), file=sys.stderr)
    print("已写出 %s (%.2f MB)" % (out, size / 1048576.0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
