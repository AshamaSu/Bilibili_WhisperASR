"""阶段一：解析 data/c_*/entry.json → index/episodes.{json,md}。

产出后停下等用户逐行确认（PLAN §2.3）。
"""
import json
import sys

from . import config, state, titles


def build_episodes():
    rows = []
    for d in sorted(config.DATA_DIR.glob("c_*")):
        entry = d / "entry.json"
        if not d.is_dir() or not entry.exists():
            continue
        data = json.loads(entry.read_text(encoding="utf-8"))
        pd = data["page_data"]
        if str(pd.get("cid")) == config.EXAMPLE_CID:
            continue  # 脱敏示例目录，不参与编号映射
        # 缓存原件可能已清理（只留 entry.json + m4a）；此时 m4s_rel 置空，
        # 音频由 convert/audio_prep 回退到 m4a/<file_stem>.m4a
        m4s_list = sorted(d.glob("*/audio.m4s"))
        m4s_rel = m4s_list[0].relative_to(d).as_posix() if m4s_list else ""
        if not m4s_rel and not any(config.M4A_DIR.glob("*.m4a")):
            print(f"[warn] {d.name}: 既无 audio.m4s 也无 m4a 备份，跳过", file=sys.stderr)
            continue
        rows.append({
            "cid": str(pd["cid"]),
            "page": pd.get("page"),
            "dir": d.name,
            "m4s_rel": m4s_rel,
            "title_raw": pd["part"],
            "duration_min": round(data.get("total_time_milli", 0) / 60000, 1),
        })
    # 排序：分 P 的 page 才是真实播放顺序；cid 升序只在缺 page 时兜底。
    # 第一季两者一致，第二季已实测不一致（务必以 page 为准）。
    if all(isinstance(r["page"], int) and r["page"] > 0 for r in rows):
        rows.sort(key=lambda r: r["page"])
        cid_sorted = sorted(rows, key=lambda r: int(r["cid"]))
        if [r["cid"] for r in cid_sorted] != [r["cid"] for r in rows]:
            print("[info] 本季 cid 升序与 page 顺序不一致，已按 page 排序", file=sys.stderr)
    else:
        rows.sort(key=lambda r: int(r["cid"]))
        print("[warn] 部分剧集缺 page 字段，退回 cid 升序", file=sys.stderr)
    if not rows:
        raise RuntimeError(f"{config.DATA_DIR} 下没有找到任何剧集")

    episodes, used = [], set()
    for i, r in enumerate(rows, 1):
        fixed, typos = titles.fix_typos(r["title_raw"])
        slug = titles.slugify(fixed)
        stem = titles.make_file_stem(i, slug)
        if stem in used:  # 评审修正 #8：碰撞兜底
            suffix = titles.collision_suffix(r["cid"])
            slug += suffix
            stem += suffix
        used.add(stem)
        episodes.append({
            "no": i,
            "cid": r["cid"],
            "dir": r["dir"],
            "m4s_rel": r["m4s_rel"],
            "title_raw": r["title_raw"],
            "title_fixed": fixed,
            "typos_applied": typos,
            "slug": slug,
            "file_stem": stem,
            "duration_min": r["duration_min"],
            "status": "pending",
            "retries": 0,
            "qa": {"language": None, "language_probability": None,
                   "word_count": None, "repetition_ratio": None, "flags": []},
        })
    return episodes


def render_md(episodes):
    lines = [
        f"# 剧集编号对照表（{len(episodes)} 集，播放顺序）",
        "",
        "> 请逐行确认「修正后」列；两列不一致的行即自动修正处，",
        "> 「自动修正」列列出具体改动。确认后即可进入阶段二。",
        "",
        "| 编号 | cid | 原标题 | 修正后 | 时长(分) | 自动修正 |",
        "|---:|---|---|---|---:|---|",
    ]
    manual = []
    for ep in episodes:
        fix_note = "；".join(ep["typos_applied"]) or "—"
        lines.append(
            f"| {ep['no']} | {ep['cid']} | {ep['title_raw']} | {ep['title_fixed']} "
            f"| {ep['duration_min']} | {fix_note} |"
        )
        for frag, hint in config.MANUAL_REVIEW_HINTS.items():
            if frag in ep["title_raw"]:
                manual.append(f"- 第 {ep['no']} 集：`{frag}` {hint}（未自动修改）")
        if any(ch in ep["title_raw"] for ch in config.CURLY_QUOTES):
            manual.append(f"- 第 {ep['no']} 集：标题含弯引号（slug 已剥离）")
    if manual:
        lines += ["", "## 待人工确认"] + manual
    return "\n".join(lines) + "\n"


def run(dry_run=False):
    episodes = build_episodes()
    md = render_md(episodes)
    if dry_run:
        print(md)
        return episodes
    state.save(episodes)
    state.write_text_atomic(config.EPISODES_MD, md)
    print(f"已写出 {config.EPISODES_JSON}")
    print(f"已写出 {config.EPISODES_MD}")
    print(f"共 {len(episodes)} 集。请打开 episodes.md 逐行确认后，再继续阶段二。")
    return episodes
