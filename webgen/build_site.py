#!/usr/bin/env python3
"""把 output/NN_*.md 复习笔记编译成单文件静态站点 review_site/index.html。

数据源只用 index/episodes.json 中 status==done 的 file_stem，不 glob output/。
用法:
    python webgen/build_site.py                       # full 模式（不同步发布包）
    python webgen/build_site.py --mode minimal        # 对外版（剥掉原文例句）；部署用 build_server.py
    python webgen/build_site.py --dump                # 只打印解析统计
退出码: 0 成功 / 1 解析或数据校验失败 / 2 用法错误 / 3 产物校验失败
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from pipeline import config  # 季路径唯一来源

OUTPUT_DIR = None
INDEX_JSON = None
VOCAB_JSON = None
TEMPLATE = Path(__file__).resolve().parent / "template.html"
DEFAULT_OUT = None


def use_season(name=None):
    """切换季：产出 seasons/<season>/{output,index,vocab_index.json,review_site}。"""
    global OUTPUT_DIR, INDEX_JSON, VOCAB_JSON, DEFAULT_OUT
    season = config.set_season(name)
    OUTPUT_DIR = config.OUTPUT_DIR
    INDEX_JSON = config.EPISODES_JSON
    VOCAB_JSON = config.VOCAB_INDEX_JSON
    DEFAULT_OUT = config.SEASON_DIR / "review_site" / "index.html"
    return season

SCHEMA_VERSION = 1
MAX_BYTES = 8 * 1024 * 1024
WARN_BYTES = 2 * 1024 * 1024
PLACEHOLDER = "__SITE_DATA__"

# 未取得 UP 主授权前用占位；投稿前人工替换成真实课程链接。
SOURCE_URL = "https://www.bilibili.com/video/BV14rd3BJExq"

MIN_SCORED_RATIO = 0.85   # 实测 0.905
MIN_SCORED_PER_EP = 3     # 实测 5

RE_H1 = re.compile(r"^#\s+(\d{2})\s*·\s*(.+?)\s*$")
RE_H2 = re.compile(r"^##\s+([123])\.\s*(.+?)\s*$")
RE_H3 = re.compile(r"^###\s*(核心词|补充词|语块|填空版)")
RE_TSEP = re.compile(r"^\|(?:\s*:?-{3,}:?\s*\|)+\s*$")
RE_LIST = re.compile(r"^(\d{1,2})\.\s+(.+?)\s*$")
RE_BLANK = re.compile(r"_{6,}")
RE_BOLD = re.compile(r"\*\*(.+?)\*\*")
RE_TOKEN = re.compile(r"[a-z0-9]+(?:['\-][a-z0-9]+)*")

SEP_CELL = re.compile(r"(?<!\\)\|")

# 表头签名 -> (类型, 列字段)
SIGNATURES = {
    ("词", "音标", "释义", "原文例句摘录"): ("word", ("w", "ipa", "g", "ex")),
    ("语块", "使用场景", "原文例句摘录"): ("chunk", ("c", "s", "ex")),
    ("句式", "使用场景", "原文例句"): ("pattern", ("p", "s", "ex")),
    ("英文", "中文"): ("trans", ("en", "zh")),
}

# 行数区间（回归护栏）
RANGES = {
    "core": (8, 15),
    "supp": (8, 16),
    "chunks": (4, 5),
    "patterns": (10, 14),
    "trans": (15, 26),
    "blanks": (8, 12),
}


class ParseError(Exception):
    def __init__(self, path: Path, lineno: int, msg: str):
        super().__init__(msg)
        self.path = path
        self.lineno = lineno
        self.msg = msg

    def __str__(self) -> str:
        try:
            rel = self.path.relative_to(ROOT)
        except ValueError:
            rel = self.path
        return "%s:%d: %s" % (rel, self.lineno, self.msg)


class Collector:
    def __init__(self, limit: int = 20):
        self.errors: List[str] = []
        self.limit = limit

    def add(self, err: Exception) -> None:
        if len(self.errors) < self.limit:
            self.errors.append(str(err))

    def __bool__(self) -> bool:
        return bool(self.errors)


# ---------------------------------------------------------------- 输入

def load_episodes(path: Path) -> List[dict]:
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list) or not data:
        raise ParseError(path, 0, "episodes.json 不是非空数组")
    return data


def select_done(episodes: List[dict]) -> List[dict]:
    done = [e for e in episodes if e.get("status") == "done"]
    done.sort(key=lambda e: e["no"])
    for i, e in enumerate(done):
        if e["no"] != i + 1:
            raise ParseError(INDEX_JSON, 0, "done 集号不连续：期望 %d 实际 %d" % (i + 1, e["no"]))
        if not e.get("file_stem"):
            raise ParseError(INDEX_JSON, 0, "第 %d 集缺 file_stem" % e["no"])
    return done


def load_vocab(path: Path) -> Dict[str, dict]:
    with path.open(encoding="utf-8") as f:
        raw = json.load(f)
    out: Dict[str, dict] = {}
    for k, v in raw.items():
        out[k] = {"d": v.get("display", k), "n": v.get("count", 0),
                  "e": v.get("episodes", []), "l": v.get("level", "B1+")}
    return out


def build_catalog(episodes: List[dict]) -> List[dict]:
    return [{"no": e["no"], "t": e.get("title_fixed") or e.get("title_raw", ""),
             "d": e.get("duration_min") or 0, "s": e.get("status", "pending")}
            for e in episodes]


# ---------------------------------------------------------------- 解析

def strip_md(text: str) -> str:
    """去掉单元格内的 **粗体** 标记，其余原样保留。"""
    return RE_BOLD.sub(r"\1", text).strip()


def split_cells(line: str) -> List[str]:
    body = line.strip()
    if body.startswith("|"):
        body = body[1:]
    if body.endswith("|"):
        body = body[:-1]
    return [c.replace("\\|", "|").strip() for c in SEP_CELL.split(body)]


def _row_key(table: str, sub: str) -> str:
    if table == "word":
        return sub                      # core / supp
    return {"chunk": "chunks", "pattern": "patterns", "trans": "trans"}[table]


def _table_fits(table: str, state: int, sub: str) -> bool:
    if table == "word":
        return state == 1 and sub in ("core", "supp")
    if table == "chunk":
        return state == 1 and sub == "chunks"
    if table == "pattern":
        return state == 2
    return state == 3


def parse_episode(path: Path, ep: dict) -> Tuple[dict, List[str]]:
    """严格状态机。返回 (episode_dict, warnings)。"""
    lines = path.read_text(encoding="utf-8").splitlines()
    ep_no = ep["no"]

    result: Dict[str, object] = {
        "no": ep_no, "title": ep.get("title_fixed") or ep.get("title_raw", ""),
        "duration_min": ep.get("duration_min") or 0, "file_stem": ep["file_stem"],
        "core": [], "supp": [], "chunks": [], "patterns": [], "trans": [], "blanks": [],
    }
    state = 0            # 0 起始 / 1 词 / 2 句式 / 3 翻译（= 已见的 H2 编号）
    seen_h1 = False
    sub = ""
    table: Optional[str] = None      # 当前 table 类型
    header: Optional[Tuple[str, ...]] = None
    want_cells = 0
    warnings: List[str] = []

    for idx, raw in enumerate(lines, start=1):
        line = raw.rstrip()
        if not line.strip():
            table = None
            header = None
            continue

        m = RE_H1.match(line)
        if m:
            if seen_h1 or state != 0:
                raise ParseError(path, idx, "H1 重复或出现在正文之后")
            if int(m.group(1)) != ep_no:
                raise ParseError(path, idx, "H1 集号 %s 与 episodes.json 的 %d 不符" % (m.group(1), ep_no))
            seen_h1 = True
            continue

        m = RE_H2.match(line)
        if m:
            n = int(m.group(1))
            if n != state + 1:
                raise ParseError(path, idx, "H2 顺序错误：收到 %d，期望 %d" % (n, state + 1))
            state = n
            sub = ""
            table = None
            header = None
            continue

        m = RE_H3.match(line)
        if m:
            name = m.group(1)
            if name == "填空版":
                if state != 2:
                    raise ParseError(path, idx, "填空版出现在非句式章节")
                sub = "blanks"
            else:
                if state != 1:
                    raise ParseError(path, idx, "%s 出现在非词章节" % name)
                sub = {"核心词": "core", "补充词": "supp", "语块": "chunks"}[name]
            table = None
            header = None
            continue

        if line.startswith("|"):
            if RE_TSEP.match(line):
                if header is None:
                    raise ParseError(path, idx, "分隔行前没有表头")
                continue
            cells = split_cells(line)
            if header is None:
                sig = tuple(cells)
                info = SIGNATURES.get(sig)
                if info is None:
                    raise ParseError(path, idx, "未知表头：%s" % "|".join(cells))
                table, fields = info
                header = sig
                want_cells = len(sig)
                if not _table_fits(table, state, sub):
                    raise ParseError(path, idx, "表类型 %s 与当前小节 %r/状态 %d 不匹配" % (table, sub, state))
                continue
            if table is None:
                raise ParseError(path, idx, "表体行出现在表头之前")
            if len(cells) != want_cells:
                raise ParseError(path, idx, "表格列数 %d != 表头 %d" % (len(cells), want_cells))
            row = {f: strip_md(c) for f, c in zip(SIGNATURES[header][1], cells)}
            if not row:
                raise ParseError(path, idx, "空行单元格")
            for k, v in row.items():
                if v == "":
                    raise ParseError(path, idx, "字段 %s 为空" % k)
            result[_row_key(table, sub)].append(row)  # type: ignore[index]
            continue

        m = RE_LIST.match(line)
        if m:
            if sub != "blanks":
                raise ParseError(path, idx, "有序列表出现在非填空小节")
            if int(m.group(1)) != len(result["blanks"]) + 1:  # type: ignore[arg-type]
                raise ParseError(path, idx, "填空序号跳号：%s" % m.group(1))
            text = m.group(2).strip()
            if not RE_BLANK.search(text):
                raise ParseError(path, idx, "填空题无挖空")
            result["blanks"].append(text)  # type: ignore[union-attr]
            continue

        raise ParseError(path, idx, "无法识别的行：%s" % line[:40])

    if not seen_h1 or state != 3:
        raise ParseError(path, len(lines), "文件未跑完三个章节（停在状态 %d）" % state)

    for key, (lo, hi) in RANGES.items():
        n = len(result[key])  # type: ignore[arg-type]
        if n == 0:
            raise ParseError(path, 0, "小节 %s 为空" % key)
        if not lo <= n <= hi:
            warnings.append("%s: %s 行数 %d 超出区间 %d-%d" % (path.name, key, n, lo, hi))
    return result, warnings


# ---------------------------------------------------------------- 答案还原

CONTRACTIONS = {
    "i'm": "i am", "don't": "do not", "doesn't": "does not", "didn't": "did not",
    "can't": "cannot", "won't": "will not", "isn't": "is not", "aren't": "are not",
    "wasn't": "was not", "weren't": "were not", "haven't": "have not",
    "hasn't": "has not", "hadn't": "had not", "wouldn't": "would not",
    "shouldn't": "should not", "couldn't": "could not", "we're": "we are",
    "you're": "you are", "they're": "they are", "that's": "that is",
    "it's": "it is", "let's": "let us", "i'd": "i would", "i've": "i have",
    "we've": "we have", "they've": "they have", "i'll": "i will",
}


def variants(ans: str) -> List[str]:
    """同一答案的写法等价形式（不做词形变化）。"""
    base = ans.strip()
    out = [base]
    low = base.lower()
    if low in CONTRACTIONS:
        out.append(CONTRACTIONS[low])
    if "-" in base:
        out.append(base.replace("-", " "))
    if "'" in base:
        out.append(base.replace("'", ""))
    seen, uniq = set(), []
    for v in out:
        k = v.lower()
        if k not in seen:
            seen.add(k)
            uniq.append(v)
    return uniq


def _gaps_forced(parts: List[str], caps: Tuple[str, ...]) -> bool:
    """相邻两空之间字面量为空时，要求每个捕获恰好 1 token。"""
    for i in range(len(parts) - 1):
        if parts[i].strip() == "" and len(RE_TOKEN.findall(caps[i])) != 1:
            return False
    return True


def recover_answers(text: str, candidates: List[str]) -> Optional[List[str]]:
    parts = RE_BLANK.split(text)          # 保留原样字面量，不 strip
    if len(parts) < 2:
        return None
    rx = re.compile("^" + "(.+?)".join(re.escape(p) for p in parts) + "$")
    hits = set()
    for cand in candidates:
        m = rx.match(cand)
        if m and all(g.strip() for g in m.groups()):
            hits.add(m.groups())
    if len(hits) != 1:
        return None
    caps = next(iter(hits))
    if not _gaps_forced(parts, caps):
        return None
    cleaned = [c.strip() for c in caps]
    if not all(_plausible(c) for c in cleaned):
        return None
    return cleaned


def _plausible(cap: str) -> bool:
    """一个空只应装一个短语：拒绝标点、过长、或抓到整句的情况。"""
    if not cap or len(cap) > 40:
        return False
    if any(ch in cap for ch in ",;:()"):
        return False
    return 1 <= len(RE_TOKEN.findall(cap)) <= 4


def build_blank_items(ep: dict) -> Tuple[List[dict], int]:
    cands: List[str] = []
    for it in ep["patterns"]:
        cands.append(it["ex"])
        cands.append(it["p"])
    for key in ("core", "supp", "chunks"):
        for it in ep[key]:
            cands.append(it["ex"])
    for it in ep["trans"]:
        cands.append(it["en"])
    cands = [c for c in dict.fromkeys(cands) if c]

    items: List[dict] = []
    scored = 0
    for text in ep["blanks"]:
        answers = recover_answers(text, cands)
        if answers:
            scored += 1
            items.append({"t": text,
                          "gaps": [{"a": variants(a)} for a in answers],
                          "scored": True,
                          "hint": _closest_pattern(text, ep["patterns"])})
        else:
            items.append({"t": text,
                          "gaps": [{"a": []} for _ in range(len(RE_BLANK.findall(text)))],
                          "scored": False,
                          "hint": _closest_pattern(text, ep["patterns"])})
    return items, scored


def _closest_pattern(text: str, patterns: List[dict]) -> int:
    """确定性：token 重合度最高、并列取下标最小。"""
    want = set(RE_TOKEN.findall(text.lower()))
    best_i, best_s = 0, -1.0
    for i, p in enumerate(patterns):
        have = set(RE_TOKEN.findall(p["ex"].lower()))
        if not have:
            continue
        s = len(want & have) / float(len(have))
        if s > best_s:
            best_i, best_s = i, s
    return best_i


# ---------------------------------------------------------------- 组装

def build_site_data(done: List[dict], catalog: List[dict], vocab: Dict[str, dict],
                    mode: str, generated_at: str, errors: Collector) -> dict:
    episodes = []
    counts = {k: 0 for k in ("core", "supp", "chunks", "patterns", "trans", "blanks")}
    scored_total = 0
    for ep in done:
        path = OUTPUT_DIR / (ep["file_stem"] + ".md")
        if not path.exists():
            errors.add(ParseError(path, 0, "md 文件缺失"))
            continue
        try:
            data, warns = parse_episode(path, ep)
        except ParseError as e:
            errors.add(e)
            continue
        for w in warns:
            print("WARN " + w, file=sys.stderr)
        items, scored = build_blank_items(data)
        data["blanks"] = items
        if scored < MIN_SCORED_PER_EP:
            errors.add(ParseError(path, 0, "可判分填空仅 %d 条（< %d）" % (scored, MIN_SCORED_PER_EP)))
        scored_total += scored
        for k in counts:
            counts[k] += len(data[k])
        ep_copy = dict(data)
        if mode == "minimal":
            for key in ("core", "supp", "chunks", "patterns"):
                ep_copy[key] = [dict({kk: vv for kk, vv in it.items() if kk != "ex"}) for it in data[key]]
        episodes.append(ep_copy)

    total_blanks = counts["blanks"]
    if total_blanks and scored_total / float(total_blanks) < MIN_SCORED_RATIO:
        errors.add(ParseError(OUTPUT_DIR, 0,
                              "答案还原率 %.3f < %.2f（%d/%d）" % (
                                  scored_total / float(total_blanks), MIN_SCORED_RATIO,
                                  scored_total, total_blanks)))

    return {
        "schema": SCHEMA_VERSION,
        "mode": mode,
        "ns": config.SEASON,          # localStorage 命名空间（多季共用域名时隔离进度）
        "generated_at": generated_at,
        "meta": {
            "ep_total": len(catalog),
            "ep_done": len(episodes),
            "counts": dict(counts, scored=scored_total),
        },
        "catalog": catalog,
        "vocab": vocab,
        "episodes": episodes,
    }


def embed_json(data: dict) -> str:
    s = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    s = s.replace("</", "<\\/")
    s = s.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    if "</script" in s.lower():
        raise ValueError("内联 JSON 仍含 </script")
    return s


def render_template(tpl: str, payload: str, source_url: str) -> str:
    if tpl.count(PLACEHOLDER) != 1:
        raise ValueError("模板占位符 %s 出现 %d 次，必须恰好 1 次" % (PLACEHOLDER, tpl.count(PLACEHOLDER)))
    tpl = tpl.replace("__SOURCE_URL__", source_url)
    return tpl.replace(PLACEHOLDER, payload)


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


def input_mtime(paths: List[Path]) -> str:
    newest = max(p.stat().st_mtime for p in paths if p.exists())
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(newest))


def check_output(html: str) -> List[str]:
    errs = []
    size = len(html.encode("utf-8"))
    if size > MAX_BYTES:
        errs.append("产物 %.2f MB 超过 8 MB 上限" % (size / 1048576.0))
    bad = [r"<script[^>]+src=", r"<link[^>]+href=(?!data:)", r"<img[^>]+src=",
           r"<iframe", r"<video", r"<audio", r"@import", r"(?<![\w-])url\((?!data:)"]
    for pat in bad:
        if re.search(pat, html, re.I):
            errs.append("检出资源型外链构造：%s" % pat)
    return errs


# ---------------------------------------------------------------- 主流程

def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="生成商务英语复习工作台单文件站点")
    ap.add_argument("--mode", choices=("full", "minimal"), default="full")
    ap.add_argument("--season", default=None, help="课程季，如 season1 / season2")
    ap.add_argument("--out", default=None, help="默认 output/<季目录>/review_site/index.html")
    ap.add_argument("--generated-at", default=None)
    ap.add_argument("--dump", action="store_true", help="只打印解析统计，不写文件")
    args = ap.parse_args(argv)

    try:
        use_season(args.season)
    except ValueError as e:
        print("ERROR %s" % e, file=sys.stderr)
        return 1
    if not args.out:
        args.out = str(DEFAULT_OUT)

    try:
        episodes = load_episodes(INDEX_JSON)
        done = select_done(episodes)
    except ParseError as e:
        print("ERROR", e, file=sys.stderr)
        return 1

    catalog = build_catalog(episodes)
    vocab = load_vocab(VOCAB_JSON)
    gen = args.generated_at or input_mtime(
        [INDEX_JSON, VOCAB_JSON, TEMPLATE] + [OUTPUT_DIR / (e["file_stem"] + ".md") for e in done])

    errors = Collector()
    data = build_site_data(done, catalog, vocab, args.mode, gen, errors)
    if errors:
        for e in errors.errors:
            print("ERROR", e, file=sys.stderr)
        return 1

    counts = data["meta"]["counts"]
    print("解析 %d 集 / 共 %d 集 | 核心 %d 补充 %d 语块 %d 句式 %d 翻译 %d 填空 %d（可判分 %d = %.1f%%）"
          % (data["meta"]["ep_done"], data["meta"]["ep_total"], counts["core"], counts["supp"],
             counts["chunks"], counts["patterns"], counts["trans"], counts["blanks"],
             counts["scored"], 100.0 * counts["scored"] / max(counts["blanks"], 1)))
    if args.dump:
        return 0

    try:
        tpl = TEMPLATE.read_text(encoding="utf-8")
        html = render_template(tpl, embed_json(data), SOURCE_URL)
    except (OSError, ValueError) as e:
        print("ERROR 渲染失败：%s" % e, file=sys.stderr)
        return 1

    errs = check_output(html)
    if errs:
        for e in errs:
            print("ERROR 产物校验：%s" % e, file=sys.stderr)
        return 3

    out = Path(args.out)
    write_atomic(out, html)
    size = len(html.encode("utf-8"))
    if size > WARN_BYTES:
        print("WARN 产物 %.2f MB，已超过 2 MB 提示线" % (size / 1048576.0), file=sys.stderr)
    print("已写出 %s (%.0f KB, mode=%s)" % (out, size / 1024.0, args.mode))
    return 0


if __name__ == "__main__":
    sys.exit(main())
