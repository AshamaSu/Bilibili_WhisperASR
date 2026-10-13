"""阶段一标题处理：typo 修正、slug 生成、文件名。纯函数，可单测。"""
import re

from . import config


def fix_typos(title):
    """大小写不敏感整词替换。返回 (修正后标题, 已应用修正列表)。

    保留原词首字母大小写（如 Wriet→Write、wriet→write）。
    """
    fixed = title
    applied = []
    for wrong in sorted(config.TYPO_MAP, key=len, reverse=True):
        right = config.TYPO_MAP[wrong]
        right_lower = right[0].lower() + right[1:]
        pattern = re.compile(r"\b" + re.escape(wrong) + r"\b", re.IGNORECASE)

        def _repl(m, _r=right, _rl=right_lower):
            s = m.group(0)
            return _r if s[:1].isupper() else _rl

        new = pattern.sub(_repl, fixed)
        if new != fixed:
            applied.append(f"{wrong}→{right}")
            fixed = new
    return fixed, applied


_PUNCT_RE = re.compile(r"[&|:,.\"'()\\/_!?*<>{}\[\]#%+~=^`$@;]")


def slugify(title):
    """文件名安全 slug：剥离标点与弯引号、去空格连写、保留驼峰（评审修正 #8）。

    连字符保留（提升可读性）：'Conversation - English' → 'Conversation-English'。
    """
    s = title
    for ch in config.CURLY_QUOTES:
        s = s.replace(ch, "")
    s = _PUNCT_RE.sub("", s)
    s = re.sub(r"\s+", " ", s).strip()
    s = s.replace(" ", "")
    return s[: config.SLUG_MAX_LEN]


def make_file_stem(no, slug):
    return f"{no:02d}_{slug}"


def collision_suffix(cid):
    """slug 碰撞兜底：追加 cid 后 6 位。"""
    return f"_c{str(cid)[-6:]}"
