# §9 WhisperX 转写后端 · 已验证方案

> 2026-10-29 实测验证。目标项目 `/Users/aaronliu/Workspaces/whisperASR`。
> **2026-10-29 落地时修订**：默认后端已改为 `http`（本机 GPU 服务），本地用 `--backend local` 切回；
> 分批只告警不拦截；远端残留实测为 0（上传不落盘）。最终决策见 `PLAN.md` §9。

## 9.0 结论

**能。**（落地时按 PLAN §9 改为多文件协作，但转写入参仍是单点） 18 分钟真实播客实测：本地 faster-whisper small = 266s，WhisperX large-v3（GPU）= 33.5s，约 **8×**；文本量基本持平，额外得到词级时间戳与说话人（后者暂不可信，见 9.1-6）。

转写入口单点：`pipeline/transcribe_stage.py`（`get_model` :28 / `transcribe_wav` :52 / `write_artifacts` :111）。下游 `candidates_stage.py:159` 只读 `segments[].start/text`，`webgen` 不读 transcripts → 换代零影响。

## 9.1 实测数据（2026-10-29，全部可复现）

| # | 项目 | 结果 |
|---|---|---|
| 1 | 服务 | `GET /health` → `{"status":"healthy","device":"cuda","loaded_models":["large-v3"]}`，whisperx-asr-service 0.4.4 |
| 2 | 53s 英文三人对话 | 200 / 2.71s（diarize）/ 1.98s（不 diarize），21 段 |
| 3 | 准确率（105 词人工稿） | WhisperX WER **2.5%**；本地 small **3.3%**（两模型同一处错 `lend a hand`→`let the ad`） |
| 4 | ep60（18.0min / 9.7MB m4a） | **33.55s 端到端**，395 段 / 2363 词 / 覆盖 0.45→1080.7s / 重复率 6.25% |
| 5 | 同集本地 small | 265.7s（RTF 4.07），348 段 / 2373 词 / 重复率 4.0% / lang_prob 0.999 |
| 6 | 说话人 | 服务端 `ENABLE_DIARIZATION=true`，**不传参也会分离**；ep60 双人对话拆出 4 个 label（211/181/2/1），3 人对话只出 2 人 → 暂不可信 |

复现命令：

```bash
curl -s http://82.156.1.74:9010/health
curl -F "audio_file=@data/m4a/60_BusinessEnglishConversationforBusiness.m4a" \
  "http://82.156.1.74:9010/asr?language=en&word_timestamps=true&diarize=false&output_format=json"
```

## 9.2 接口契约（`POST /asr`）

- multipart 只有 `audio_file`；其余全为 query 参数：`task / language / initial_prompt / hotwords / word_timestamps(默认 true) / output_format / model / num_speakers / min_speakers / max_speakers / diarize`
- 响应顶层：`text / language / segments / word_segments`
- `segments[]`：`{start, end, text, words[{word,start,end,score,speaker}], speaker}`
- 🔴 缺 `language_probability` 与 `duration`
- 🔴 顶层 `text` 被误填成 segment 列表，禁止当字符串用；`word_segments` 是冗余展开，丢弃

## 9.3 配置（后端开关，默认 local）

```python
BACKEND = os.environ.get("ASR_BACKEND", "local").strip().lower()   # local | http
HTTP_URL = os.environ.get("ASR_URL", "").rstrip("/")               # 空则失败快，不写死公网 IP
HTTP_MODEL = os.environ.get("ASR_MODEL", "large-v3")
HTTP_TIMEOUT_S = float(os.environ.get("ASR_TIMEOUT_S", "1800"))
HTTP_DIARIZE = os.environ.get("ASR_DIARIZE", "0") == "1"           # 实测不可信，默认关
HTTP_MIN_SPAN_RATIO = 0.95                                          # 段覆盖门槛
```

未设 `ASR_BACKEND` 时全链路与现在逐字节一致。

## 9.4 字段映射（三个坑）

| 服务字段 | 处理 |
|---|---|
| `language` | 存 `metrics.language`；≠`en` → `LanguageGateError` → suspect（无概率可用，只能字符串门禁） |
| `language_probability` | 存 **`null`**，不编造（`index_stage.py:59` 本就是 null 初值；再靠 `qa_check` 词数/重复率兜底） |
| `duration` | 用 `ep["duration_min"] * 60`（索引期 `convert_stage.py:53-59` 已按 WAV 实际大小 ±10% 校验过） |
| `segments[].words` | 增量写入 `transcript.json`（约 +165KB/集），词级时间戳是本次最大收益 |
| `segments[].speaker` | 仅 `ASR_DIARIZE=1` 时保留，否则字段不存在 |
| 顶层 `text` / `word_segments` | 忽略 |

`transcript.json` 老键不变（`no/file_stem/cid/title_fixed/metrics{language,language_probability,duration}/segments[{start,end,text}]`）；http 路径额外写 `metrics.backend="http"` / `words_added` / `span_ratio`，便于事后审计「这份不是本地出的」。

## 9.5 代码改动点

| 文件 | 改动 |
|---|---|
| `pipeline/config.py` ~:48 | 追加 9.3 的 6 个变量 |
| `pipeline/transcribe_stage.py` | 新增 `RemoteError(TranscribeError)`（带 `fatal`）与分派函数 `transcribe(audio_path, beam_size, limit_minutes, extra, expected_duration)`；`transcribe_wav()`/`write_artifacts()` 一字不改 |
| `pipeline/http_backend.py`（新建） | `transcribe_file()`：httpx 上传原件 → 映射 segments → 校验 language/非空/span≥95% → 返回 `(segments, metrics)` |
| `pipeline/runner.py` | http 时 `src = convert_stage.source_audio(ep)` 跳过 ffmpeg；重试阶梯只保留 1 次（服务忽略 `beam_size/no_speech_threshold`，temperature=0 重跑同结果） |
| `pipeline/cli.py` :81-85 | 同上分支；注意 `transcribe` 子命令会无条件 `write_artifacts`，做 A/B 时别用它 |

🔴 `runner.py` 的 `finally` 只 unlink 名为 `wav` 的临时文件：`src`（`data/m4a` 原件）与 `wav`（ffmpeg 产物）必须是两个变量，否则会把母带删掉。

## 9.6 校验门槛与错误语义

| 情况 | 结果 |
|---|---|
| 连接失败 / 5xx / 408 / 429 | `RemoteError` → 走既有重试预算 |
| 4xx（400/413/422） | `RemoteError(fatal)` → 直接 failed，不写任何产物 |
| `language != en` | suspect，无产物，不重试 |
| `segments` 空 / JSON 不可解析 | 重试 → failed |
| 段覆盖 < 95% 期望时长 | 重试 → failed（防静默截断） |
| 重复率 > 20% | `qa_check` flag → suspect |

长文件：最长 ep51 = 82.8min（约 42.5MB m4a），GPU 约 160s，1800s 超时余量约 11×。串行执行，不并发打这台共享 GPU（上面还跑着 speakr 业务）。

## 9.7 验收步骤

```bash
ssh -N -L 9010:127.0.0.1:9010 ubuntu@82.156.1.74 &   # 服务无鉴权，别裸连公网
export ASR_URL=http://127.0.0.1:9010

# 1 隔离：不设 ASR_BACKEND 时必须仍是 local
whisperASR_env/bin/python -c "from pipeline import config as c; print(c.BACKEND, repr(c.HTTP_URL))"

# 2 冒烟：截 1 分钟，不写产物
ASR_BACKEND=http whisperASR_env/bin/python -c "
from pipeline import transcribe_stage as t
s,m=t.transcribe('data/m4a/60_BusinessEnglishConversationforBusiness.m4a',
                 limit_minutes=1.0, expected_duration=1080.0)
print(m); print(len(s), s[0], s[-1])"
# 断言：backend=http / language=en / language_probability is None / len(s)>=15 / 每段带 words

# 3 A/B：选 1 集已完成，只读内存，别写回
# 断言：词数 ±10%、重复率 <0.2、flags==[]、span_ratio>=0.95

# 4 批量：先单集验证产物，再跑 51-67
# 跑完核对：17 集全 done、data/m4a 仍为 67 个文件、work/ 无残留 wav
```

## 9.8 红线与风险

- 默认 local；http 需 3 个显式环境变量；运行日志打印 backend
- 🔴 服务无鉴权且公网可达 → 绑 `127.0.0.1` + SSH 隧道；`ASR_URL` 只走环境变量，不写死、不提交
- 🔴 不改 `transcribe_wav()` / `write_artifacts()` / SRT 逻辑；不并发；不在本机装 whisperx/torch
- 不本地装 whisperx 的理由：需 `torch~=2.8 + pyannote-audio>=4 + numpy>=2.1`，与现状 numpy 2.5.3 / av 18.1.0 固定组合冲突，Mac 无 CUDA，且 56h 音频在 CPU 上跑不动
- 粘贴位置：PLAN.md 末尾，作为 `# §9`；同时给 §7 第 2 条红线「不上传任何音频/转写稿到云端」加一行 2026-10-29 修订注（默认本地，`ASR_BACKEND=http` 为显式 opt-in，只发自有 GPU 服务器）

