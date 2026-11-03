"""远端残留探测与清理（PLAN §9.5）。

实测结论（2026-10-29，只读探测）：容器 whisperx-asr-api 只有模型缓存卷挂载，
上传的音频不落盘，/tmp 里只剩 torch 加载时生成的 16KB 目录 —— 正常批次残留为 0。
所以这里按「先证明、再兜底」写：默认只报告；删除必须同时满足
（不在批次前基线里）+（名字是 mkdtemp 形态）+（时间落在批次窗口）+（目录里能
认出本批次上传的文件名），且只按绝对路径逐个删，永不通配、永不 restart/prune。
"""
import re
import subprocess
import time

from . import config

TMP_NAME = re.compile(r"^tmp[a-z0-9_]{4,}$")
SSH_OPTS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
            "-o", "ControlMaster=auto",
            "-o", "ControlPath=/tmp/whisperasr-%h-%p",
            "-o", "ControlPersist=600"]


class ProbeError(RuntimeError):
    pass


def _ssh(script, timeout=60):
    if not config.REMOTE_SSH_HOST:
        raise ProbeError("未配置 ASR_SSH_HOST（conn/asr.env）")
    cmd = ["ssh", *SSH_OPTS, "-i", str(config.REMOTE_SSH_KEY),
           config.REMOTE_SSH_HOST, script]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise ProbeError((proc.stderr or "").strip()[-300:] or
                         f"ssh 退出码 {proc.returncode}")
    return proc.stdout


# 只读：确认容器名 + 镜像，再列 /tmp 一级条目（名字|字节|mtime）与磁盘余量
_PROBE_SCRIPT = """
set -e
name=$(docker ps --filter "name=^%s$" --format '{{.Names}}')
img=$(docker ps --filter "name=^%s$" --format '{{.Image}}')
[ -n "$name" ] || { echo "CONTAINER_MISSING"; exit 3; }
echo "$img" | grep -qi whisperx || { echo "IMAGE_MISMATCH:$img"; exit 4; }
echo "IMAGE:$img"
docker exec "$name" find %s -mindepth 1 -maxdepth 1 -printf 'ENTRY:%%f|%%s|%%T@\n'
df -Pk / | tail -1 | awk '{print "FREE_KB:" $4}'
""" % (config.REMOTE_CONTAINER, config.REMOTE_CONTAINER, config.REMOTE_TMP)


def snapshot(log=None):
    """批次前的只读基线；任何失败返回 None（清理是 best-effort，不能影响转写）。"""
    try:
        out = _ssh(_PROBE_SCRIPT)
    except (ProbeError, OSError, subprocess.SubprocessError) as e:
        if log:
            log(f"[probe] 跳过远端残留探测: {e}")
        return None
    entries, image, free_gb = {}, None, None
    for line in out.splitlines():
        if line.startswith("ENTRY:"):
            name, size, mtime = line[6:].split("|")
            entries[name] = {"bytes": int(size), "mtime": float(mtime)}
        elif line.startswith("IMAGE:"):
            image = line[6:].strip()
        elif line.startswith("FREE_KB:"):
            free_gb = int(line[8:]) / 1048576.0
    return {"image": image, "entries": entries, "free_gb": free_gb}


def _has_our_file(path, stems):
    """目录里能否认出本批次上传的文件名（最后一道归属校验）。"""
    if not stems:
        return False
    find_cmd = ("docker exec %s find %s -maxdepth 2 -type f -printf '%%f\n'"
                % (config.REMOTE_CONTAINER, path))
    try:
        names = set(_ssh(find_cmd, timeout=30).split())
    except (ProbeError, OSError, subprocess.SubprocessError):
        return False
    return any(any(stem in n for n in names) for stem in stems)


def _delete(path):
    """只删这一条绝对路径；路径必须是 /tmp/tmpXXXX 形态。"""
    if not re.fullmatch(r"/tmp/tmp[a-z0-9_]{4,}", path):
        raise ProbeError(f"拒绝删除非临时目录: {path}")
    _ssh("docker exec %s rm -rf -- %s" % (config.REMOTE_CONTAINER, path), timeout=30)


def report(baseline, started_at, rows, mode, log):
    """批次后：只报告本批次新增的 /tmp 条目；mode=delete 才删。"""
    if baseline is None:
        return
    after = snapshot(log)
    if after is None:
        return
    before = baseline["entries"]
    stems = {r.get("stem") for r in rows if r.get("stem")}
    now = time.time()   # 真实当前时间：候选目录 mtime 均晚于 started_at，用批次起点恒判『太新』
    new = {k: v for k, v in after["entries"].items() if k not in before}
    if not new:
        log(f"[probe] 远端 /tmp 无新增条目（基线 {len(before)} 条），无需清理")
        return

    cands, kept = [], []
    for name, meta in new.items():
        path = f"{config.REMOTE_TMP}/{name}"
        if not TMP_NAME.match(name):
            kept.append((name, meta, "非 mkdtemp 形态"))
        elif meta["mtime"] < started_at - 5:
            kept.append((name, meta, "早于本批次"))
        elif now - meta["mtime"] < config.REMOTE_MIN_AGE_S:
            kept.append((name, meta, "太新，可能正被其他请求使用"))
        elif not _has_our_file(path, stems):
            kept.append((name, meta, "目录内认不出本批次文件"))
        else:
            cands.append((name, meta, path))

    total = sum(m["bytes"] for _, m, _ in cands)
    log(f"[probe] 批次新增 {len(new)} 条 / 可归属本批次 {len(cands)} 条 "
        f"({total / 1e6:.1f}MB)，模式={mode}")
    for name, meta, why in kept:
        log(f"[probe]   跳过 {name}（{meta['bytes']}B）：{why}")
    if not cands:
        return
    if mode != "delete":
        for _, _, path in cands:
            log(f"[probe]   待删 {path}（加 --remote-clean delete 生效）")
        return
    deleted = 0
    for _, _, path in cands:
        try:
            _delete(path)
            deleted += 1
        except (ProbeError, OSError, subprocess.SubprocessError) as e:
            log(f"[probe]   删除失败 {path}: {e}")
    log(f"[probe] 已删除 {deleted}/{len(cands)} 个临时目录")
