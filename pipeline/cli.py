"""CLI 入口：python -m pipeline <subcommand>。"""
import argparse
import json
import sys

from . import (candidates_stage, config, convert_stage, index_stage,
               preflight, runner, state, transcribe_stage)


def _add_episodes_arg(sp):
    sp.add_argument("--episodes", default="all",
                    help="集号选择: 1 | 1-5 | 1,3,5 | all")


def build_parser():
    p = argparse.ArgumentParser(
        prog="pipeline", description="whisperASR 四阶段转写流水线")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("preflight", help="预下载/校验 whisper small 模型")

    sp = sub.add_parser("index", help="阶段一：编号映射 → index/episodes.{json,md}")
    sp.add_argument("--dry-run", action="store_true", help="只打印不落盘")

    sp = sub.add_parser("convert", help="阶段二：m4s → WAV（调试用）")
    _add_episodes_arg(sp)

    sp = sub.add_parser("transcribe", help="阶段三：转写（调试用）")
    _add_episodes_arg(sp)
    sp.add_argument("--beam", type=int, default=None)
    sp.add_argument("--limit-minutes", type=float, default=None,
                    help="只转写前 N 分钟音频（冒烟用）")

    sp = sub.add_parser("extract", help="阶段四脚本侧：候选提取 + 词表渲染")
    _add_episodes_arg(sp)
    sp.add_argument("--render-index", action="store_true",
                    help="渲染 output/00_总词表.md 与 00_INDEX.md")

    sp = sub.add_parser("run", help="批量：逐集 convert→transcribe→extract→删WAV")
    _add_episodes_arg(sp)
    sp.add_argument("--beam", type=int, default=None)
    sp.add_argument("--limit-minutes", type=float, default=None)
    sp.add_argument("--retry-failed", action="store_true",
                    help="重新武装 failed/suspect 的剧集")

    sub.add_parser("status", help="查看各集状态")
    sub.add_parser("smoke", help="合成音频端到端自检")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)

    if args.cmd == "preflight":
        return preflight.main()

    if args.cmd == "index":
        index_stage.run(dry_run=args.dry_run)
        return 0

    if args.cmd == "status":
        episodes = state.load()
        print(f"{'no':>3}  {'status':<12} {'ret':>3}  {'flags':<12} file_stem")
        for ep in episodes:
            flags = ",".join(ep["qa"]["flags"]) or "-"
            print(f"{ep['no']:>3}  {ep['status']:<12} {ep['retries']:>3}  "
                  f"{flags:<12} {ep['file_stem']}")
        return 0

    if args.cmd == "convert":
        episodes = state.load()
        for no in state.parse_selection(args.episodes, len(episodes)):
            ep = state.by_no(episodes, no)
            print(f"[ep{no}] → {convert_stage.convert_episode(ep)}")
        return 0

    if args.cmd == "transcribe":
        episodes = state.load()
        for no in state.parse_selection(args.episodes, len(episodes)):
            ep = state.by_no(episodes, no)
            wav = convert_stage.wav_path(ep["cid"])
            if not wav.exists():
                wav = convert_stage.convert_episode(ep)
            segments, metrics = transcribe_stage.transcribe_wav(
                wav, beam_size=args.beam, limit_minutes=args.limit_minutes)
            qa = transcribe_stage.qa_check(segments, ep["duration_min"])
            transcribe_stage.write_artifacts(ep, segments, metrics)
            print(f"[ep{no}] lang={metrics['language']}"
                  f"({metrics['language_probability']}) segs={len(segments)} "
                  f"words={qa['word_count']} rep={qa['repetition_ratio']} "
                  f"flags={qa['flags']}")
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
        runner.run_episodes(args.episodes, retry_failed=args.retry_failed,
                            beam_size=args.beam,
                            limit_minutes=args.limit_minutes)
        return 0

    if args.cmd == "smoke":
        runner.run_smoke()
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
