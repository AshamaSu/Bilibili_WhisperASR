"""批量编排：逐集 convert→transcribe→extract→删WAV（转一集删一集），支持续跑。

后端可切换（PLAN §9）：默认 http（自建 GPU 服务），--backend local 走 faster-whisper。
"""
import datetime
import time
import traceback

from . import (candidates_stage, config, convert_stage, http_backend, remote_probe,
               state, transcribe_stage)


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


def _record_failure(episodes, ep, log, err):
    """运行时/可重试失败：消耗一次重试预算，超限转 failed。"""
    ep["retries"] += 1
    if ep["retries"] >= config.MAX_RETRIES:
        state.set_status(ep, "failed")
        result = "failed"
    else:
        state.set_status(ep, "pending")
        result = "retry"
    state.save(episodes)
    log(f"[ep{ep['no']}] ✗ {type(err).__name__}: {err} → {result} "
        f"(retries={ep['retries']})")
    if result == "failed":
        traceback.print_exc()
    return result


def process_episode(episodes, ep, log, beam_size=None, limit_minutes=None,
                    backend=None, min_span=None):
    """单集完整链。返回 dict(result/words/rep/span/bytes/seconds)。

    wav 只可能是 work/ 下的 ffmpeg 产物（可删）；src 是转写输入，http 下指向
    data/ 里的只读原件，绝不可删除——两者必须是两个变量。
    """
    be = backend or transcribe_stage.backend()
    wav = None
    src = None
    t0 = time.time()
    try:
        if be == "http":
            src = convert_stage.http_source_audio(ep)
            log(f"[ep{ep['no']}] upload {src.name} "
                f"({src.stat().st_size / 1e6:.1f}MB) → {config.HTTP_URL}")
            state.set_status(ep, "transcribing")
            state.save(episodes)
        else:
            state.set_status(ep, "converting")
            state.save(episodes)
            wav = convert_stage.convert_episode(ep)
            src = wav
            state.set_status(ep, "transcribing")
            state.save(episodes)

        segments = metrics = None
        if be == "http":
            # 远端忽略 beam_size/no_speech_threshold，重跑同参同结果：单次即可
            segments, metrics = transcribe_stage.transcribe(
                src, expected_duration=ep["duration_min"] * 60,
                limit_minutes=limit_minutes, min_span=min_span)
            rep = transcribe_stage.ngram_repeat_ratio(segments)
            if rep >= config.REPETITION_LOOP_RATIO:
                log(f"[ep{ep['no']}] ⚠ 复读迹象 rep={rep:.2f}，交 QA 判定（远端不重跑）")
        else:
            # 首选参数 + 一次保守重试（复读循环时）；后续重试走通用预算
            attempts = [({}, beam_size),
                        (dict(config.RETRY_PARAMS), None)]
            last_err = None
            for extra, explicit_beam in attempts:
                try:
                    segments, metrics = transcribe_stage.transcribe_wav(
                        src, beam_size=explicit_beam,
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
                         "flags": qa["flags"],
                         "warnings": qa["warnings"],
                         "sparse_count": qa["sparse_count"],
                         "sparse": qa["sparse"]})
        info = {"words": qa["word_count"], "rep": qa["repetition_ratio"],
                "span": metrics.get("span_ratio"),
                "sparse": qa["sparse_count"],
                "bytes": metrics.get("upload_bytes") or 0,
                "seconds": round(time.time() - t0, 1)}
        if qa["sparse_count"]:
            preview = "; ".join(
                f"{x['start']:.0f}s/{x['span']:.1f}s/{x['words']}w"
                for x in qa["sparse"][:3])
            log(f"[ep{ep['no']}] ⚠ 疑似吞句 {qa['sparse_count']} 段"
                f"（跨度/词数：{preview}），仅告警不阻断")
        if qa["flags"]:
            state.set_status(ep, "suspect")
            state.save(episodes)
            log(f"[ep{ep['no']}] ⚠ suspect flags={qa['flags']} "
                f"words={qa['word_count']} rep={qa['repetition_ratio']}")
            return dict(info, result="suspect")

        state.set_status(ep, "extracting")
        state.save(episodes)
        transcribe_stage.write_artifacts(ep, segments, metrics)
        candidates_stage.extract_and_write(ep, segments)
        state.set_status(ep, "done")
        state.save(episodes)
        log(f"[ep{ep['no']}] ✓ done words={qa['word_count']} "
            f"rep={qa['repetition_ratio']} {info['seconds']:.1f}s")
        return dict(info, result="done")

    except transcribe_stage.BackendUnavailable:
        raise   # 批次级中止：不消耗本集重试预算，状态留给下次重入
    except transcribe_stage.LanguageGateError as e:
        ep["qa"]["flags"] = ["lang"]
        state.set_status(ep, "suspect")
        state.save(episodes)
        log(f"[ep{ep['no']}] ⚠ suspect(语种): {e}")
        return {"result": "suspect", "words": 0, "rep": 0.0, "span": None,
                "bytes": 0, "seconds": round(time.time() - t0, 1)}
    except transcribe_stage.RemoteError as e:
        if e.retryable:
            res = _record_failure(episodes, ep, log, e)
        else:
            state.set_status(ep, "failed")
            state.save(episodes)
            log(f"[ep{ep['no']}] ✗ 不可重试的远端错误: {e} → failed")
            res = "failed"
        return {"result": res, "words": 0, "rep": 0.0, "span": None,
                "bytes": 0, "seconds": round(time.time() - t0, 1)}
    except Exception as e:  # 运行时失败 → 通用重试预算
        res = _record_failure(episodes, ep, log, e)
        return {"result": res, "words": 0, "rep": 0.0, "span": None,
                "bytes": 0, "seconds": round(time.time() - t0, 1)}
    finally:
        # 只删 work/ 下的 ffmpeg 产物；data/ 原件受工作目录守卫保护
        if wav is not None and wav.parent == config.WORK_DIR:
            try:
                wav.unlink(missing_ok=True)
            except OSError:
                pass


def _actionable(selected, retry_failed, force=False):
    if force:
        return list(selected)
    out = []
    for ep in selected:
        if ep["status"] == "done":
            continue
        if ep["status"] in ("failed", "suspect") and not retry_failed:
            continue
        out.append(ep)
    return out


def _warn_batch(todo, be, total_min, selection_spec):
    """共享 GPU：批次过大只告警不拦截（用户自定集数）。"""
    if be != "http" or not todo:
        return
    if selection_spec == "all" or len(todo) > config.BATCH_WARN_EPISODES:
        up = 0
        for ep in todo:
            try:
                up += convert_stage.http_source_audio(ep).stat().st_size
            except Exception:
                pass
        hours = total_min / 60
        print(f"[warn] 本批 {len(todo)} 集 / 音频 {hours:.1f}h，超过建议批次 "
              f"{config.BATCH_WARN_EPISODES} 集：预计 GPU ~{hours * 60 * config.HTTP_RTF_ESTIMATE:.0f} 分钟、"
              f"上传 ~{up / 1e6:.0f}MB。共享 GPU 串行执行，建议分批跑。", flush=True)


def _print_summary(log, rows, counts, be, total_min, wall_s):
    log("==== 批次汇总 backend=%s ====" % be)
    log("%4s %-8s %8s %6s %7s %6s %7s" % ("no", "status", "words", "rep", "span", "sparse", "sec"))
    for r in rows:
        span = "-" if r.get("span") is None else "%.3f" % r["span"]
        log("%4d %-8s %8d %6.3f %7s %6d %7.1f"
            % (r["no"], r["result"], r.get("words") or 0, r.get("rep") or 0.0,
               span, r.get("sparse") or 0, r.get("seconds") or 0.0))
    up = sum(r.get("bytes") or 0 for r in rows)
    hours = total_min / 60
    rtf = (wall_s / (total_min * 60)) if total_min else 0.0
    log("合计: " + " ".join(f"{k}={v}" for k, v in counts.items()))
    log(f"音频 {hours:.2f}h / 用时 {wall_s / 60:.1f}min / RTF {rtf:.3f} / "
        f"上传 {up / 1e6:.1f}MB")


def run_episodes(selection_spec="all", retry_failed=False, beam_size=None,
                 limit_minutes=None, log_file=None, backend=None,
                 remote_clean=None, min_span=None, force=False):
    """跑一批。返回退出码：0 正常 / 2 后端不可用（批次中止）。"""
    be = backend or transcribe_stage.backend()
    episodes = state.load()
    nos = state.parse_selection(selection_spec, len(episodes))

    if log_file is None:
        config.LOGS_DIR.mkdir(parents=True, exist_ok=True)
        log_file = config.LOGS_DIR / f"run_{datetime.datetime.now():%Y%m%d_%H%M%S}.log"
    log = Logger(log_file)

    convert_stage.cleanup_stale_wavs()

    if be == "http":
        try:
            info = http_backend.health()
        except transcribe_stage.BackendUnavailable as e:
            log(f"[abort] 后端不可用，未改动任何状态: {e}")
            log.close()
            return 2
        log(f"[backend] http {config.HTTP_URL} device={info.get('device')} "
            f"models={info.get('loaded_models')}")
    else:
        log(f"[backend] local {config.MODEL_NAME} ({config.DEVICE}/{config.COMPUTE_TYPE})")

    selected = [state.by_no(episodes, no) for no in nos]
    todo = _actionable(selected, retry_failed, force)
    total_min = sum(ep["duration_min"] for ep in selected)
    todo_min = sum(ep["duration_min"] for ep in todo)
    _warn_batch(todo, be, todo_min, selection_spec)

    if be == "local" and not any(config.MODELS_DIR.rglob("*.bin")):
        log("[warn] models/ 下未见模型文件，建议先运行: python -m pipeline preflight")

    baseline = None
    batch_start = time.time()
    clean_mode = (remote_clean or config.REMOTE_CLEAN).lower()
    if be == "http" and clean_mode != "off":
        baseline = remote_probe.snapshot(log)   # 只读，失败只告警

    counts = {"done": 0, "suspect": 0, "failed": 0, "retry": 0, "skip": 0}
    rows = []
    exit_code = 0
    try:
        for ep in selected:
            if ep["status"] == "done":
                if not force:
                    counts["skip"] += 1
                    continue
                # 显式重跑：done 不在状态机里，直接重置（旧产物仍在 git 里可回滚）
                ep["status"] = "pending"
                ep["retries"] = 0
                state.save(episodes)
            if ep["status"] in ("failed", "suspect"):
                if not retry_failed and not force:
                    counts["skip"] += 1
                    continue
                state.set_status(ep, "pending")
                if force:
                    ep["retries"] = 0
                state.save(episodes)
            elif ep["status"] in ("converting", "transcribing", "extracting"):
                # 上次中断的中间态：幂等重入（评审修正 #4）
                state.set_status(ep, "pending")
                state.save(episodes)
            try:
                info = process_episode(episodes, ep, log, beam_size, limit_minutes, be, min_span)
            except transcribe_stage.BackendUnavailable as e:
                log(f"[abort] 后端不可用，批次终止（剩余集保持 pending）: {e}")
                exit_code = 2
                break
            counts[info["result"]] += 1
            rows.append(dict(info, no=ep["no"], stem=ep["file_stem"]))
    finally:
        wall = time.time() - batch_start
        _print_summary(log, rows, counts, be, todo_min, wall)
        if baseline is not None:
            remote_probe.report(baseline, batch_start, rows, clean_mode, log)
        log("suspect/failed 事后复核: python -m pipeline status")
        log.close()
    return exit_code


def run_smoke():
    """合成音频端到端自检：验证链路机制（合成音非有效语音，flags 非空属预期）。

    刻意固定走 local：http 默认化之后，这是本地后端没被改坏的哨兵。
    """
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
        with open(wavp, "wb") as fh, wavemod.open(fh, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sr)
            w.writeframes(frames.tobytes())

    ep = {"no": 99, "cid": "smoke", "file_stem": "99_smoke",
          "title_fixed": "smoke test", "duration_min": 20 / 60}
    try:
        segments, metrics = transcribe_stage.transcribe_wav(wavp, limit_minutes=0.5)
    except transcribe_stage.LanguageGateError as e:
        # 合成音不是有效语音，语种概率天然偏低：这是机制自检，不是质量自检
        print(f"[smoke] 语种门禁在合成音上误报（预期内）: {e}")
        print("[smoke] 本地链路已跑到模型推理，机制正常；真实音频请用 transcribe 验证")
        return
    qa = transcribe_stage.qa_check(segments, ep["duration_min"])
    transcribe_stage.write_artifacts(ep, segments, metrics)
    candidates_stage.extract_and_write(ep, segments)
    print(f"[smoke] lang={metrics['language']}({metrics['language_probability']}) "
          f"segs={len(segments)} words={qa['word_count']} "
          f"rep={qa['repetition_ratio']} flags={qa['flags']}")
    print(f"[smoke] 产物: {config.TRANSCRIPTS_DIR / '99_smoke'} 与 "
          f"{config.CANDIDATES_DIR / '99_smoke'}")
    print("[smoke] 链路机制验证完成（转录质量不计）")
