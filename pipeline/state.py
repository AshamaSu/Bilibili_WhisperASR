"""剧集状态：原子持久化、状态机、集号选择解析（评审修正 #4）。"""
import json
import os
import tempfile
from pathlib import Path

from . import config

STATUSES = {"pending", "converting", "transcribing", "extracting",
            "done", "failed", "suspect"}

TRANSITIONS = {
    "pending": {"converting"},
    "converting": {"transcribing", "failed", "pending"},
    "transcribing": {"extracting", "suspect", "failed", "pending"},
    "extracting": {"done", "suspect", "failed", "pending"},
    "done": set(),
    "failed": {"pending"},   # --retry-failed 重新武装
    "suspect": {"pending"},
}


class StateError(RuntimeError):
    pass


def write_text_atomic(path, text):
    """写文本文件：临时文件 + fsync + os.replace，杜绝半截文件。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def load():
    if not config.EPISODES_JSON.exists():
        raise StateError(f"{config.EPISODES_JSON} 不存在，请先运行: python -m pipeline index")
    episodes = json.loads(config.EPISODES_JSON.read_text(encoding="utf-8"))
    if not isinstance(episodes, list):
        raise StateError("episodes.json 格式错误：应为数组")
    return episodes


def save(episodes):
    write_text_atomic(config.EPISODES_JSON,
                      json.dumps(episodes, ensure_ascii=False, indent=2))


def by_no(episodes, no):
    for ep in episodes:
        if ep["no"] == no:
            return ep
    raise StateError(f"没有编号为 {no} 的剧集")


def set_status(ep, new_status):
    if new_status not in STATUSES:
        raise StateError(f"未知状态: {new_status}")
    cur = ep["status"]
    if new_status not in TRANSITIONS[cur]:
        raise StateError(f"非法状态转换: {cur} → {new_status} (no={ep['no']})")
    ep["status"] = new_status


def parse_selection(spec, total):
    """'1' | '1-5' | '1,3,5' | 'all' → 有序集号列表。"""
    spec = str(spec).strip().lower()
    if spec == "all":
        return list(range(1, total + 1))
    out = set()
    try:
        for part in spec.split(","):
            part = part.strip()
            if not part:
                continue
            if "-" in part:
                a, b = part.split("-", 1)
                lo, hi = int(a), int(b)
                if lo > hi:
                    raise StateError(f"区间反了: {part}")
                out.update(range(lo, hi + 1))
            else:
                out.add(int(part))
    except ValueError as e:
        raise StateError(f"无法解析集号选择: {spec!r} ({e})") from None
    nos = sorted(n for n in out if 1 <= n <= total)
    if not nos:
        raise StateError(f"选择为空: {spec}")
    return nos
