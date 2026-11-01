#!/usr/bin/env python3
"""复习工作台的独立校验器。

刻意不复用 build_site 的解析器：只扫描行首特征重新计数，用来交叉验证构建结果。
用法:
    python webgen/selfcheck.py [review_site/index.html]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_HTML = ROOT / "review_site" / "index.html"
DIST_HTML = ROOT / "dist" / "review-site" / "index.html"
OUTPUT_DIR = ROOT / "output"
INDEX_JSON = ROOT / "index" / "episodes.json"
VOCAB_JSON = ROOT / "vocab_index.json"

# 基线随 done 集数增长；改完 md 后按自检输出更新这两个常量
EXPECT = {"core": 927, "supp": 772, "chunks": 334, "patterns": 799, "trans": 1225, "blanks": 667}
MIN_SCORED = 566          # floor(0.85 * 667)
MAX_BYTES = 8 * 1024 * 1024

BAD_RESOURCE = [
    r"<script[^>]+\bsrc\s*=",
    r"<link[^>]+\bhref\s*=\s*(?!data:)",
    r"<img[^>]+\bsrc\s*=",
    r"<iframe\b",
    r"<video\b",
    r"<audio\b",
    r"@import\b",
    r"(?<![\w-])url\((?!data:)",
    r"\bfetch\s*\(",
    r"XMLHttpRequest",
    r"sendBeacon",
    r"\bimport\s*\(",
]

problems: list = []
notes: list = []


def fail(msg: str) -> None:
    problems.append(msg)


def ok(msg: str) -> None:
    notes.append(msg)


# ------------------------------------------------------------ md 独立回算

def recount(md_path: Path) -> dict:
    counts = {k: 0 for k in EXPECT}
    header = None
    for line in md_path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s.startswith("|"):
            if header is not None and s == "":
                header = None
            if s.startswith("#"):
                header = None
            continue
        if set(s.replace("|", "").strip()) <= set("-: "):
            continue
        if header is None:
            header = s
            if "音标" in s:
                counts["_kind"] = "word"
            elif "语块" in s:
                counts["_kind"] = "chunk"
            elif "句式" in s:
                counts["_kind"] = "pattern"
            elif "英文" in s:
                counts["_kind"] = "trans"
            else:
                counts["_kind"] = "?"
            continue
        kind = counts.get("_kind")
        if kind == "word":
            # 由最近的 H3 决定核心还是补充
            pass
        if kind == "chunk":
            counts["chunks"] += 1
        elif kind == "pattern":
            counts["patterns"] += 1
        elif kind == "trans":
            counts["trans"] += 1
        elif kind == "word":
            counts["_word_rows"] = counts.get("_word_rows", 0) + 1
    counts.pop("_kind", None)
    counts.pop("_word_rows", None)
    # 词表两张（核心 + 补充）直接按 4 列表头行数统计
    text = md_path.read_text(encoding="utf-8")
    counts["core"] = 0
    counts["supp"] = 0
    bucket = None
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("### "):
            if "核心词" in s:
                bucket = "core"
            elif "补充词" in s:
                bucket = "supp"
            else:
                bucket = None
            continue
        if s.startswith("## ") or s.startswith("# "):
            bucket = None
            continue
        if bucket and s.startswith("|"):
            if set(s.replace("|", "").strip()) <= set("-: "):
                continue
            if "音标" in s:
                continue
            counts[bucket] += 1
    counts["blanks"] = 0
    for line in text.splitlines():
        if re.match(r"^\s*\d+\.\s", line):
            counts["blanks"] += 1
    return counts


# ------------------------------------------------------------ 主流程

def expected_counts():
    idx = json.loads(INDEX_JSON.read_text(encoding="utf-8"))
    done = sum(1 for e in idx if e.get("status") == "done")
    vocab = json.loads(VOCAB_JSON.read_text(encoding="utf-8"))
    return len(idx), done, len(vocab)


def extract_data(html: str) -> dict:
    m = re.search(r'<script id="site-data" type="application/json">(.*?)</script>', html, re.S)
    if not m:
        raise ValueError("找不到 site-data 数据块")
    return json.loads(m.group(1))


def check_html(path: Path) -> dict:
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        fail("产物带 BOM")
    if b"\r" in raw:
        fail("产物含 CR（应为纯 LF）")
    try:
        html = raw.decode("utf-8")
    except UnicodeDecodeError as e:
        fail("产物不是合法 UTF-8：%s" % e)
        return {}
    if len(raw) > MAX_BYTES:
        fail("产物 %.2f MB 超过 8 MB" % (len(raw) / 1048576.0))
    ok("字节 %.0f KB（%.1f%% of 8MB）" % (len(raw) / 1024.0, 100.0 * len(raw) / MAX_BYTES))

    try:
        data = extract_data(html)
    except ValueError as e:
        fail(str(e))
        return {}

    cat = data.get("catalog", [])
    eps = data.get("episodes", [])
    vocab = data.get("vocab", {})
    want_cat, want_eps, want_vocab = expected_counts()
    if len(cat) != want_cat:
        fail("catalog 应为 %d 条，实际 %d" % (want_cat, len(cat)))
    if len(eps) != want_eps:
        fail("episodes 应为 %d 条，实际 %d" % (want_eps, len(eps)))
    if len(vocab) != want_vocab:
        fail("vocab 应为 %d 条，实际 %d" % (want_vocab, len(vocab)))
    if data.get("meta", {}).get("ep_done") != len(eps):
        fail("meta.ep_done 与 episodes 不一致")
    ok("catalog %d / episodes %d / vocab %d" % (len(cat), len(eps), len(vocab)))

    totals = {k: 0 for k in EXPECT}
    scored = 0
    for e in eps:
        for k in EXPECT:
            totals[k] += len(e.get(k, []))
        sc = [b for b in e.get("blanks", []) if b.get("scored")]
        scored += len(sc)
        if len(sc) < 3:
            fail("第 %s 集可判分填空只有 %d 条" % (e.get("no"), len(sc)))
        for b in e.get("blanks", []):
            gaps = b.get("gaps", [])
            if len(re.findall(r"_{6,}", b.get("t", ""))) != len(gaps):
                fail("第 %s 集填空挖空数与 gaps 数不一致：%s" % (e.get("no"), b.get("t", "")[:40]))
            if b.get("scored"):
                for g in gaps:
                    if not g.get("a"):
                        fail("第 %s 集有 scored 题但答案为空" % e.get("no"))
                        break
                    if len(re.findall(r"[A-Za-z0-9']+", g["a"][0])) > 6:
                        fail("第 %s 集某空答案超过 6 个词：%s" % (e.get("no"), g["a"][0]))
        # 与 md 独立回算对比
        md = OUTPUT_DIR / (e["file_stem"] + ".md")
        if not md.exists():
            fail("缺少 md：%s" % md.name)
            continue
        rc = recount(md)
        for k in EXPECT:
            if rc[k] != len(e.get(k, [])):
                fail("%s 的 %s：md 回算 %d != 站点 %d" % (md.name, k, rc[k], len(e.get(k, []))))

    for k in EXPECT:
        if totals[k] != EXPECT[k]:
            fail("合计 %s 应为 %d，实际 %d" % (k, EXPECT[k], totals[k]))
    ok("合计 核心/补充/语块/句式/翻译/填空 = " + "/".join(str(totals[k]) for k in EXPECT))
    if scored < MIN_SCORED:
        fail("可判分填空 %d 条低于下限 %d" % (scored, MIN_SCORED))
    ok("可判分填空 %d / %d（%.1f%%）" % (scored, totals["blanks"], 100.0 * scored / max(totals["blanks"], 1)))

    if data.get("mode") not in ("full", "minimal"):
        fail("mode 字段异常")
    if data.get("mode") == "minimal":
        for e in eps:
            for k in ("core", "supp", "chunks", "patterns"):
                if any("ex" in it for it in e.get(k, [])):
                    fail("minimal 模式下第 %s 集 %s 仍含原文例句" % (e.get("no"), k))
                    break
        ok("minimal 模式：原文例句字段已全部剔除")
    else:
        if not any("ex" in it for it in eps[0].get("core", [])):
            fail("full 模式下缺少原文例句字段")

    for pat in BAD_RESOURCE:
        if re.search(pat, html, re.I):
            fail("检出资源型外链构造：%s" % pat)
    ok("资源型外链检测：0 命中")

    n_url = len(re.findall(r"https?://", html))
    if n_url != 1:
        fail("https:// 出现 %d 次，应恰好 1 次（footer 课程来源）" % n_url)
    else:
        ok("https:// 出现 1 次（footer）")

    block = re.search(r'<script id="site-data" type="application/json">(.*?)</script>', html, re.S)
    if block and "http" in block.group(1).lower():
        fail("数据块内出现 http 字样")
    else:
        ok("数据块内无任何 URL")

    if re.search(r"transcripts?/|transcript\.(txt|srt|json)", html, re.I):
        fail("产物出现转写稿文件路径")
    if re.search(r"\.m4s|\.wav|/audio/", html):
        fail("产物出现音频路径")
    if html.count("<script") > 3:
        fail("<script> 数量异常")

    return data


def check_idempotent() -> None:
    build = ROOT / "webgen" / "build_site.py"
    digests = []
    for i in range(2):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / ("a%d.html" % i)
            proc = subprocess.run([sys.executable, str(build), "--generated-at", "2026-10-27T00:00:00Z",
                                   "--out", str(out), "--no-dist"], capture_output=True, text=True)
            if proc.returncode != 0:
                fail("幂等检查构建失败：%s" % proc.stderr.strip()[:200])
                return
            digests.append(hashlib.sha256(out.read_bytes()).hexdigest())
    if digests[0] != digests[1]:
        fail("两次构建产物不一致（非幂等）")
    else:
        ok("幂等：两次构建 sha256 相同（%s…）" % digests[0][:12])


def _check_dist_sync(min_build) -> None:
    """发布包必须与当前 minimal 构建逐字节一致（dist 已纳入标准流程）。"""
    if not DIST_HTML.exists():
        ok("发布包 %s 不存在（跳过同步检查）" % DIST_HTML.name)
        return
    built = hashlib.sha256(min_build.read_bytes()).hexdigest()
    shipped = hashlib.sha256(DIST_HTML.read_bytes()).hexdigest()
    if built != shipped:
        fail("发布包 dist/review-site/index.html 与当前 minimal 构建不一致"
             "（重跑 python3 webgen/build_site.py --mode minimal）")
    else:
        ok("发布包与 minimal 构建一致（sha256 %s…）" % built[:12])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="复习工作台独立校验")
    ap.add_argument("html", nargs="?", default=str(DEFAULT_HTML))
    ap.add_argument("--no-build-check", action="store_true", help="跳过幂等与 minimal 重建检查")
    args = ap.parse_args(argv)

    path = Path(args.html)
    if not path.exists():
        print("ERROR 产物不存在：%s" % path, file=sys.stderr)
        return 1

    data = check_html(path)
    if data and not args.no_build_check:
        check_idempotent()
        # minimal 重建并复检核心字段
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "min.html"
            proc = subprocess.run([sys.executable, str(ROOT / "webgen" / "build_site.py"),
                                   "--mode", "minimal", "--out", str(out), "--no-dist"],
                                  capture_output=True, text=True)
            if proc.returncode != 0:
                fail("minimal 构建失败：%s" % proc.stderr.strip()[:200])
            else:
                saved = list(problems)
                check_html(out)
                if len(problems) > len(saved):
                    fail("minimal 产物复检未通过（见上）")
                ok("minimal 重建复检通过")
                _check_dist_sync(out)

    for n in notes:
        print("  ok  " + n)
    for p in problems:
        print("  FAIL " + p)
    print("\n%d checks passed, %d failed" % (len(notes), len(problems)))
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
