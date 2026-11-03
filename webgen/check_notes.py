#!/usr/bin/env python3
"""校验学习 md 里的「原文例句」是否真出自该集转写稿。

只做交叉验算：把 md 表格里的例句与 transcripts/<stem>/transcript.json 比对，
归一化（小写、仅保留字母数字与空格）后做子串匹配。允许以省略号开头/结尾。
用法:
    python webgen/check_notes.py --season season2            # 全部有 md 的集
    python webgen/check_notes.py --season season2 --episodes 1-10
退出码: 0 全部通过 / 1 有未匹配
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from pipeline import config  # noqa: E402

CELL = re.compile(r"^\|")
SEP = re.compile(r"^\|[\s:|-]+\|$")


def norm(s):
    s = s.replace("…", " ").replace("...", " ")
    s = re.sub(r"[^0-9a-zA-Z' ]+", " ", s.lower())
    return re.sub(r"\s+", " ", s).strip()


def load_transcript(stem):
    p = config.TRANSCRIPTS_DIR / stem / "transcript.json"
    if not p.exists():
        return None
    d = json.loads(p.read_text(encoding="utf-8"))
    return norm(" ".join(str(s.get("text", "")) for s in d["segments"]))


def table_rows(md_text):
    """产出 (小节标题, 行单元格列表)。"""
    section = ""
    for line in md_text.splitlines():
        if line.startswith("###"):
            section = line.lstrip("#").strip()
        if not CELL.match(line) or SEP.match(line):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if all(set(c) <= set("-: ") for c in cells):
            continue
        yield section, cells


def squash(s):
    return re.sub(r"[^a-z0-9]", "", s)


def variants(quote, title):
    """一条例句可能是「A / B」并列、带中文括注或篇名引用，拆开逐段校验。"""
    q = re.sub(r"（[^）]*）|\([^)]*\)", " ", quote)
    q = q.replace("...", "…")
    parts = [p for p in re.split(r"\s*[/…]\s*", q) if p.strip()]
    out = []
    for p in parts:
        n = norm(p)
        if len(n) < 8:
            continue
        if squash(n) and squash(n) in squash(title):
            continue      # 篇名引用不算原文例句
        out.append((p, n))
    return out


def check_file(md_path, trans):
    text = md_path.read_text(encoding="utf-8")
    title = norm(md_path.stem.split("_", 1)[-1])
    checked = 0
    bad = []
    for section, cells in table_rows(text):
        if len(cells) < 3:
            continue
        if section.startswith("填空版"):
            continue
        if "翻译" in section or (len(cells) == 2 and section == ""):
            # 词句翻译表：第一列是英文
            cand = [cells[0]]
        elif section.startswith("语块"):
            cand = [cells[2]] if len(cells) >= 3 else []
        else:
            cand = [cells[-1]]
        for quote in cand:
            for raw, n in variants(quote, title):
                checked += 1
                if n not in trans:
                    bad.append((section, raw.strip()))
    return checked, bad


TOK = re.compile(r"[a-z][a-z']+")


def tokens(s):
    return {t for t in TOK.findall(s.lower()) if len(t) > 2}


def best_sentence(quote, headword, sents):
    """在原文句子里找与 quote 词元重合度最高的一句；headword 必须出现。"""
    q = tokens(quote)
    hw = tokens(headword or "")
    best, best_score = None, 0.0
    for s in sents:
        st = tokens(s)
        if hw and not (hw & st):
            continue
        inter = len(q & st)
        score = inter / max(len(q), 1)
        if score > best_score:
            best, best_score = s, score
    return best, best_score


def fix_file(md_path, sents, apply=False):
    """把对不上原文的例句替换为最接近的真实句子；返回 (fixed, unresolved)。"""
    text = md_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    title = squash(norm(md_path.stem.split("_", 1)[-1]))
    fixed, unresolved = [], []
    out = []
    section = ""
    for line in lines:
        if line.startswith("###"):
            section = line.lstrip("#").strip()
        if not CELL.match(line) or SEP.match(line):
            out.append(line)
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if (len(cells) < 3 or all(set(c) <= set("-: ") for c in cells)
                or cells[0] in ("词", "语块", "句式", "英文")):
            out.append(line)   # 表头与分隔行不参与回填
            continue
        idx = None
        if "翻译" in section or (len(cells) == 2 and not section):
            idx = 0
        elif section.startswith("语块"):
            idx = 2 if len(cells) >= 3 else None
        else:
            idx = len(cells) - 1
        if idx is None:
            out.append(line)
            continue
        quote = cells[idx]
        parts = variants(quote, norm(md_path.stem.split("_", 1)[-1]))
        trans_norm = norm(" ".join(sents))
        need = [n for _, n in parts if n not in trans_norm]
        if not need:
            out.append(line)
            continue
        head = cells[0] if idx != 0 else ""
        cand, score = best_sentence(quote, head, sents)
        if apply and cand and score >= 0.5:
            cells[idx] = cand
            fixed.append((quote, cand))
            out.append("| " + " | ".join(cells) + " |")
        else:
            unresolved.append((section, quote, cand, round(score, 2)))
            out.append(line)
    if apply and fixed:
        md_path.write_text("\n".join(out) + "\n", encoding="utf-8")
    return fixed, unresolved


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="学习 md 例句交叉校验")
    ap.add_argument("--season", default=None)
    ap.add_argument("--episodes", default="all")
    ap.add_argument("--fix", action="store_true", help="把不匹配的例句回填为最接近的真实句子")
    args = ap.parse_args(argv)

    config.set_season(args.season)
    md_dir = config.OUTPUT_DIR
    files = sorted(p for p in md_dir.glob("*.md") if not p.name.startswith("00_"))

    total = miss = 0
    for f in files:
        trans = load_transcript(f.stem)
        if trans is None:
            print("SKIP %s（无转写稿）" % f.name)
            continue
        if args.fix:
            p = config.TRANSCRIPTS_DIR / f.stem / "transcript.json"
            sents = [str(s.get("text", "")).strip() for s in
                     json.loads(p.read_text(encoding="utf-8"))["segments"]]
            fixed, unresolved = fix_file(f, sents, apply=True)
            print("FIX  %-56s 回填 %d，待人工 %d" % (f.name[:56], len(fixed), len(unresolved)))
            for section, quote, cand, score in unresolved[:4]:
                print("        ?? [%s] %s" % (section[:10], quote[:80]))
            continue
        checked, bad = check_file(f, trans)
        total += checked
        miss += len(bad)
        flag = "ok " if not bad else "FAIL"
        print("%s %-58s %3d/%3d" % (flag, f.name[:58], checked - len(bad), checked))
        for section, quote in bad[:6]:
            print("        [%s] %s" % (section[:12], quote[:90]))

    if files:
        print("\n合计 %d 条例句，未匹配 %d 条" % (total, miss))
    else:
        print("没有找到 md")
    return 0 if miss == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
