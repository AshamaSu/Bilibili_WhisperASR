"""CLI 入口：python -m pipeline <subcommand>。"""
import argparse
import json
import sys

from . import (audio_prep, candidates_stage, config, convert_stage, index_stage,
               preflight, remote_probe, runner, state, transcribe_stage)


def _add_episodes_arg(sp):
    sp.add_argument("--episodes", default="all",
                    help="集号选择: 1 | 1-5 | 1,3,5 | all")


def _add_season_arg(sp):
    sp.add_argument("--season", default=None, metavar="NAME",
                    help="课程季，如 season1 / season2（默认 season1，或环境变量 ASR_SEASON）")


def _add_backend_args(sp):
    sp.add_argument("--backend", choices=config.BACKENDS, default=None,
                    help="转写后端（默认 http，即 conn/asr.env 里的 GPU 服务）")
    sp.add_argument("--asr-url", default=None, help="覆盖 ASR_URL")
    sp.add_argument("--remote-clean", choices=config.REMOTE_CLEAN_MODES, default=None,
                    help="远端残留清理模式（默认 report，只报告不删）")


def build_parser():
    p = argparse.ArgumentParser(
        prog="pipeline", description="whisperASR 四阶段转写流水线")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("preflight", help="预检：默认探活服务器，local 则下载/校验模型")
    _add_season_arg(sp)
    sp.add_argument("--backend", choices=("http", "local", "both"), default=None)
    sp.add_argument("--asr-url", default=None)

    sp = sub.add_parser("index", help="阶段一：编号映射 → index/episodes.{json,md}")
    _add_season_arg(sp)
    sp.add_argument("--dry-run", action="store_true", help="只打印不落盘")

    sp = sub.add_parser("prepare-audio", help="阶段 0：audio.m4s → m4a（流复制，http 后端的前置）")
    _add_season_arg(sp)
    _add_episodes_arg(sp)
    sp.add_argument("--force", action="store_true", help="已有产物也重做")

    sp = sub.add_parser("convert", help="阶段二：m4s → WAV（仅 local 后端）")
    _add_season_arg(sp)
    _add_episodes_arg(sp)
    _add_backend_args(sp)

    sp = sub.add_parser("transcribe", help="阶段三：转写（调试用）")
    _add_season_arg(sp)
    _add_episodes_arg(sp)
    _add_backend_args(sp)
    sp.add_argument("--beam", type=int, default=None)
    sp.add_argument("--limit-minutes", type=float, default=None,
                    help="只转写前 N 分钟音频（冒烟用）")
    sp.add_argument("--min-span", type=float, default=None,
                    help="覆盖门禁放宽到该比例（片尾长静音等误报时显式使用）")
    sp.add_argument("--dry-run", action="store_true",
                    help="只打印统计，不写产物（A/B 对比用）")

    sp = sub.add_parser("extract", help="阶段四脚本侧：候选提取 + 词表渲染")
    _add_season_arg(sp)
    _add_episodes_arg(sp)
    sp.add_argument("--render-index", action="store_true",
                    help="渲染 output/00_总词表.md 与 00_INDEX.md")

    sp = sub.add_parser("run", help="批量：逐集 上传/convert→transcribe→extract→清理")
    _add_season_arg(sp)
    _add_episodes_arg(sp)
    _add_backend_args(sp)
    sp.add_argument("--beam", type=int, default=None)
    sp.add_argument("--limit-minutes", type=float, default=None)
    sp.add_argument("--min-span", type=float, default=None,
                    help="覆盖门禁放宽到该比例（片尾长静音等误报时显式使用）")
    sp.add_argument("--retry-failed", action="store_true",
                    help="重新武装 failed/suspect 的剧集")
    sp.add_argument("--force", action="store_true",
                    help="已 done 的集也重跑（重置该集 retries）")

    sp = sub.add_parser("status", help="查看各集状态")
    _add_season_arg(sp)
    sp = sub.add_parser("smoke", help="合成音频端到端自检（固定 local）")
    _add_season_arg(sp)

    sp = sub.add_parser("remote-probe", help="只读探测服务器残留（不删除任何东西）")
    _add_season_arg(sp)
    sp.add_argument("--asr-url", default=None)
    return p


def _run_transcribe(ep, args):
    """单集转写（调试用）：按后端选输入源，http 不生成 WAV。"""
    if transcribe_stage.backend() == "http":
        src = convert_stage.http_source_audio(ep)
    else:
        wav = convert_stage.wav_path(ep["cid"])
        if not wav.exists():
            wav = convert_stage.convert_episode(ep)
        src = wav
    segments, metrics = transcribe_stage.transcribe(
        src, expected_duration=ep["duration_min"] * 60, beam_size=args.beam,
        limit_minutes=args.limit_minutes, min_span=args.min_span)
    qa = transcribe_stage.qa_check(segments, ep["duration_min"])
    if not args.dry_run:
        transcribe_stage.write_artifacts(ep, segments, metrics)
    tail = " [dry-run 未写产物]" if args.dry_run else ""
    print(f"[ep{ep['no']}] backend={metrics.get('backend', 'local')} "
          f"lang={metrics['language']}({metrics['language_probability']}) "
          f"segs={len(segments)} words={qa['word_count']} "
          f"rep={qa['repetition_ratio']} span={metrics.get('span_ratio')} "
          f"flags={qa['flags']} sparse={qa['sparse_count']}{tail}")


def main(argv=None):
    args = build_parser().parse_args(argv)

    try:
        if args.cmd != "preflight":
            config.set_season(getattr(args, "season", None))
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    try:
        config.apply_overrides(
            None if args.cmd == "preflight" else getattr(args, "backend", None),
            getattr(args, "asr_url", None),
            getattr(args, "remote_clean", None))
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    if args.cmd == "preflight":
        return preflight.main(args.backend)

    if args.cmd == "index":
        index_stage.run(dry_run=args.dry_run)
        return 0

    if args.cmd == "status":
        episodes = state.load()
        print(f"{'no':>3}  {'status':<12} {'ret':>3}  {'flags':<12} {'warn':<12} file_stem")
        for ep in episodes:
            qa = ep.get("qa") or {}
            flags = ",".join(qa.get("flags") or []) or "-"
            warn = ",".join(qa.get("warnings") or []) or "-"
            if qa.get("sparse_count"):
                warn += f"({qa['sparse_count']})"
            print(f"{ep['no']:>3}  {ep['status']:<12} {ep['retries']:>3}  "
                  f"{flags:<12} {warn:<12} {ep['file_stem']}")
        return 0

    if args.cmd == "prepare-audio":
        episodes = state.load()
        ok = skip = 0
        fails = []
        for no in state.parse_selection(args.episodes, len(episodes)):
            ep = state.by_no(episodes, no)
            try:
                out, how = audio_prep.prepare(ep, force=args.force)
            except (audio_prep.PrepError, OSError) as e:
                fails.append(no)
                print(f"[ep{no}] ✗ {e}", file=sys.stderr)
                continue
            if how == "ok":
                ok += 1
                print(f"[ep{no}] {out.name} {out.stat().st_size / 1e6:.1f}MB")
            else:
                skip += 1
        print(f"完成：新转 {ok}，跳过 {skip}，失败 {len(fails)}")
        return 1 if fails else 0

    if args.cmd == "convert":
        if transcribe_stage.backend() == "http":
            print("error: http 后端没有 convert 阶段（直接上传 m4a）；"
                  "需要本地 WAV 请加 --backend local", file=sys.stderr)
            return 2
        episodes = state.load()
        for no in state.parse_selection(args.episodes, len(episodes)):
            ep = state.by_no(episodes, no)
            print(f"[ep{no}] → {convert_stage.convert_episode(ep)}")
        return 0

    if args.cmd == "transcribe":
        episodes = state.load()
        for no in state.parse_selection(args.episodes, len(episodes)):
            try:
                _run_transcribe(state.by_no(episodes, no), args)
            except transcribe_stage.BackendUnavailable as e:
                print(f"error: 后端不可用: {e}", file=sys.stderr)
                return 2
            except transcribe_stage.TranscribeError as e:
                print(f"[ep{no}] ✗ {type(e).__name__}: {e}", file=sys.stderr)
        return 0

    if args.cmd == "extract":
        episodes = state.load()
        for no in state.parse_selection(args.episodes, len(episodes)):
            ep = state.by_no(episodes, no)
            tj = config.TRANSCRIPTS_DIR / ep["file_stem"] / "transcript.json"
            if not tj.exists():
                print(f"[ep{no}] 无转写产物，跳过", file=sys.stderr)
                continue
            data = json.loads(tj.read_text(encoding="utf-8"))
            candidates_stage.extract_and_write(ep, data["segments"])
            print(f"[ep{no}] candidates.json 已更新")
        candidates_stage.rebuild_vocab_index()
        if args.render_index:
            candidates_stage.render_indexes()
        return 0

    if args.cmd == "run":
        return runner.run_episodes(args.episodes, retry_failed=args.retry_failed,
                                   beam_size=args.beam,
                                   limit_minutes=args.limit_minutes,
                                   min_span=args.min_span, force=args.force)

    if args.cmd == "smoke":
        runner.run_smoke()
        return 0

    if args.cmd == "remote-probe":
        snap = remote_probe.snapshot(lambda m: print(m))
        if snap is None:
            print("error: 探测失败（检查 conn/asr.env 的 ASR_SSH_HOST 与密钥）",
                  file=sys.stderr)
            return 1
        print(f"image={snap['image']} 磁盘余量={snap['free_gb']:.1f}GB "
              f"/tmp 条目={len(snap['entries'])}")
        for name, meta in sorted(snap["entries"].items()):
            print(f"  {name}  {meta['bytes']}B")
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
