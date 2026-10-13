"""批量编排：逐集 convert→transcribe→extract→删WAV（转一集删一集），支持续跑。"""
import datetime
import traceback

from . import candidates_stage, config, convert_stage, state, transcribe_stage


class Logger:
    def __init__(self, log_file=None):
        self.fh = open(log_file, "a", encoding="utf-8") if log_file else None

    def __call__(self, msg):
        line = f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
        print(line, flush=True)
        if self.fh:
            self.fh.write(line + "\n")
            self.fh.flush()

    def close(self):
        if self.fh:
            self.fh.close()
            self.fh = None


def process_episode(episodes, ep, log, beam_size=None, limit_minutes=None):
    """单集完整链。WAV 在 finally 中删除。返回 done/suspect/failed/retry。"""
    wav = None
    try:
        state.set_status(ep, "converting")
        state.save(episodes)
        wav = convert_stage.convert_episode(ep)

        state.set_status(ep, "transcribing")
        state.save(episodes)
        # 首选参数 + 一次保守重试（复读循环时）；后续重试走通用预算
        attempts = [({}, beam_size),
                    (dict(config.RETRY_PARAMS), None)]
        last_err = None
        segments = metrics = None
        for extra, explicit_beam in attempts:
            try:
                segments, metrics = transcribe_stage.transcribe_wav(
                    wav, beam_size=explicit_beam,
                    limit_minutes=limit_minutes, extra=extra)
                if (transcribe_stage.ngram_repeat_ratio(segments)
                        >= config.REPETITION_LOOP_RATIO):
                    raise transcribe_stage.RepetitionError("复读循环")
                break
            except transcribe_stage.RepetitionError as e:
                last_err = e
                log(f"[ep{ep['no']}] 复读检测触发，换保守参数重试: {e}")
                segments = None
        if segments is None:
            raise last_err

        qa = transcribe_stage.qa_check(segments, ep["duration_min"])
        ep["qa"].update({"language": metrics["language"],
                         "language_probability": metrics["language_probability"],
                         "word_count": qa["word_count"],
                         "repetition_ratio": qa["repetition_ratio"],
                         "flags": qa["flags"]})
        if qa["flags"]:
            state.set_status(ep, "suspect")
            state.save(episodes)
            log(f"[ep{ep['no']}] ⚠ suspect flags={qa['flags']} "
                f"words={qa['word_count']} rep={qa['repetition_ratio']}")
            return "suspect"

        state.set_status(ep, "extracting")
        state.save(episodes)
        transcribe_stage.write_artifacts(ep, segments, metrics)
        candidates_stage.extract_and_write(ep, segments)
        state.set_status(ep, "done")
        state.save(episodes)
        log(f"[ep{ep['no']}] ✓ done words={qa['word_count']} "
            f"rep={qa['repetition_ratio']}")
        return "done"

    except transcribe_stage.LanguageGateError as e:
        ep["qa"]["flags"] = ["lang"]
        state.set_status(ep, "suspect")
        state.save(episodes)
        log(f"[ep{ep['no']}] ⚠ suspect(语种): {e}")
        return "suspect"
    except Exception as e:  # 运行时失败 → 通用重试预算
        ep["retries"] += 1
        if ep["retries"] >= config.MAX_RETRIES:
            state.set_status(ep, "failed")
            result = "failed"
        else:
            state.set_status(ep, "pending")
            result = "retry"
        state.save(episodes)
        log(f"[ep{ep['no']}] ✗ {type(e).__name__}: {e} → {result} "
            f"(retries={ep['retries']})")
        if result == "failed":
            traceback.print_exc()
        return result
    finally:
        if wav is not None:
            try:
                wav.unlink(missing_ok=True)
            except OSError:
                pass


def run_episodes(selection_spec="all", retry_failed=False, beam_size=None,
                 limit_minutes=None, log_file=None):
    episodes = state.load()
    nos = state.parse_selection(selection_spec, len(episodes))

    if log_file is None:
        config.LOGS_DIR.mkdir(parents=True, exist_ok=True)
        log_file = config.LOGS_DIR / f"run_{datetime.datetime.now():%Y%m%d_%H%M%S}.log"
    log = Logger(log_file)

    convert_stage.cleanup_stale_wavs()

    selected = [state.by_no(episodes, no) for no in nos]
    total_min = sum(ep["duration_min"] for ep in selected)
    log(f"批量处理 {len(nos)} 集，音频共 {total_min / 60:.1f}h，"
        f"预计机器时间 ~{total_min * 2 / 60:.0f}h（1.5–2.5× 实时，CPU int8 beam=2）")

    if not any(config.MODELS_DIR.rglob("*.bin")):
        log("[warn] models/ 下未见模型文件，建议先运行: python -m pipeline preflight")

    counts = {"done": 0, "suspect": 0, "failed": 0, "retry": 0, "skip": 0}
    try:
        for ep in selected:
            if ep["status"] == "done":
                counts["skip"] += 1
                continue
            if ep["status"] in ("failed", "suspect"):
                if not retry_failed:
                    counts["skip"] += 1
                    continue
                state.set_status(ep, "pending")
                state.save(episodes)
            elif ep["status"] in ("converting", "transcribing", "extracting"):
                # 上次中断的中间态：幂等重入（评审修正 #4）
                state.set_status(ep, "pending")
                state.save(episodes)
            result = process_episode(episodes, ep, log, beam_size, limit_minutes)
            counts[result] += 1
    finally:
        log(f"结束: {counts}（suspect/failed 事后复核: python -m pipeline status）")
        log.close()


def run_smoke():
    """合成音频端到端自检：验证链路机制（合成音非有效语音，flags 非空属预期）。"""
    import array
    import math
    import wave as wavemod

    d = config.WORK_DIR / "smoke"
    d.mkdir(parents=True, exist_ok=True)
    wavp = d / "smoke.wav"
    if not wavp.exists():
        sr = 16000
        frames = array.array("h")
        for i in range(sr * 20):
            t = i / sr
            amp = 12000 if (int(t) % 4) < 2 else 0
            frames.append(int(amp * math.sin(2 * math.pi * 220 * t)))
        with wavemod.open(wavp, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sr)
            w.writeframes(frames.tobytes())

    ep = {"no": 99, "cid": "smoke", "file_stem": "99_smoke",
          "title_fixed": "smoke test", "duration_min": 20 / 60}
    segments, metrics = transcribe_stage.transcribe_wav(wavp, limit_minutes=0.5)
    qa = transcribe_stage.qa_check(segments, ep["duration_min"])
    transcribe_stage.write_artifacts(ep, segments, metrics)
    candidates_stage.extract_and_write(ep, segments)
    print(f"[smoke] lang={metrics['language']}({metrics['language_probability']}) "
          f"segs={len(segments)} words={qa['word_count']} "
          f"rep={qa['repetition_ratio']} flags={qa['flags']}")
    print(f"[smoke] 产物: {config.TRANSCRIPTS_DIR / '99_smoke'} 与 "
          f"{config.CANDIDATES_DIR / '99_smoke'}")
    print("[smoke] 链路机制验证完成（转录质量不计）")
