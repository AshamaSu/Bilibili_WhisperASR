# whisperASR · B 站商务英语 67 集 → 复习 Markdown 流水线

把 B 站缓存的一套商务英语课程（67 集，约 56 小时音频）在本地全自动转写，并加工成结构化的英语复习 Markdown（核心词 / 补充词 / 语块 / 句式填空 / 英中翻译）。

> 本仓库仅供个人学习使用。`data/` 不含任何音频（仅一个置空占位的样例目录展示缓存结构），课程音频与视频版权归原 UP 主所有。

## 仓库结构

```
whisperASR/
├── pipeline/                 # 流水线：编号 → 音频 → 转写 → 提取 + 批量编排 + CLI
├── data/                     # 源音频（只读，不入库；仅保留脱敏样例目录）
│   ├── BusinessEnglishCourse-Season1/
│   │   ├── m4a/              # 流复制出的完整容器（67 个）
│   │   └── m4a_32/           # 手机版转码（单声道 22.05kHz，约 800MB）
│   ├── BusinessEnglishCourse-Season2/
│   │   ├── c_<cid>/          # B 站缓存原件（entry.json + 64/audio.m4s）
│   │   └── m4a/              # prepare-audio 流复制产物
│   └── c_00000000000/        # 脱敏样例（结构演示，不参与编号）
├── output/                   # 产出物（入库），子目录名与 data 严格同名
│   └── BusinessEnglishCourse-Season1|Season2/
│       ├── index/            # 阶段一：episodes.json / episodes.md
│       ├── transcripts/      # 阶段三：每集 transcript.txt / .srt / .json
│       ├── candidates/       # 阶段四：每集候选词/短语
│       ├── md/               # 学习 md（每集一篇）+ 00_INDEX.md + 00_总词表.md
│       ├── vocab_index.json  # 本季词表索引
│       ├── intensive/        # 精读页（生成物，不入库）
│       ├── review_site/      # §8 复习工作台（生成物）
│       └── work/             # 瞬态：WAV / 日志（不入库）
├── webgen/                   # 构建脚本 + 模板 + 能力探针 + 独立校验器
├── dist/                     # 发布包（不入库）：server/<season>（复习站 + 精听 + 音频）
└── PLAN.md                   # 执行计划（含 §5 v2 提取规则、§8 复习工作台）
```

**所有产出物按季隔离在 `output/<与 data 同名的季目录>/`**，命令用 `--season` 切换：短名 `season1` 与全名 `BusinessEnglishCourse-Season1` 等价（默认 season1，也可设环境变量 `ASR_SEASON`）。

## 流水线

| 阶段 | 命令 | 说明 |
|---|---|---|
| 预检 | `python -m pipeline preflight` | 默认探活服务器；`--backend local` 才下载/校验 whisper small 模型（HF_HOME=models/） |
| 〇 · 音频准备 | `python -m pipeline prepare-audio` | `audio.m4s` → m4a 流复制（不重编码）+ 时长校验；**新季第一件事** |
| 一 · 编号映射 | `python -m pipeline index` | 解析 entry.json → **按分 P 的 page 排序**编号 → 标题 typo 修正 → output/<季目录>/index/episodes.{json,md} |
| 二 · 音频转换 | （`run` 内置，仅 local） | ffmpeg → 16k mono WAV，转一集删一集；缓存原件缺失时回退 `m4a/<file_stem>.m4a` |
| 三 · 转写 | `python -m pipeline transcribe` | 默认上传 WhisperX large-v3（GPU）；+ 语种门禁 + 复读检测 + 自动 QA |
| 四 · 候选提取 | `python -m pipeline extract` | 词频/短语/语块候选 + 全局词表（CEFR 分级） |
| 批量 | `python -m pipeline run --episodes 51-55` | 逐集 上传/convert→transcribe→extract，断点续跑 |

所有子命令都接受 `--season`，例如 `python -m pipeline run --season season2 --episodes all`。
> **编号顺序以分 P 的 `page` 为准，不用 cid 升序**：第一季两者恰好一致，第二季实测不一致（cid 最小的不是第 1 集）。

## 转写与提取要点

- faster-whisper `small` / CPU int8，`condition_on_previous_text=False` 防长音频幻觉漂移，实测 ≈4× 实时
- 语种门禁：`language_probability < 0.8` 判 suspect，不烧重试预算；复读循环自动降参重试
- 续跑幂等：状态原子写（temp+fsync+rename），三件产物齐才置 `done`；总词表每次全量重渲染
- 提取规则 v2（PLAN §5）：标题实词硬性入选核心词；CEFR 分层只收 B1+；语块（3~5 条固定表达）单列；系列词去重标注「→ 见总词表」

## 转写后端（§9）

默认走自建 GPU 服务（WhisperX large-v3），本地 faster-whisper 仍是可切换的后端：

```bash
python -m pipeline run --episodes 51-55                   # 默认 http
python -m pipeline run --episodes 51-55 --backend local   # 切本地
python -m pipeline transcribe --episodes 60 --dry-run     # 只打印统计、不写产物（A/B 对比用）
python -m pipeline remote-probe                           # 只读探测服务器残留，不删任何东西
```

- 地址/主机放 `conn/asr.env`（`ASR_URL` / `ASR_SSH_HOST` / `ASR_MODEL`），同名环境变量优先。
  **`conn/` 已 gitignore**：里面有 SSH 私钥与服务器地址，切勿提交。
- 连不上服务器 → 立即退出码 2，不改 `episodes.json`（不静默降级到 CPU）
- 分批：集数自己指定；单批超过 5 集只告警不拦截（共享 GPU，串行不并发）
- 实测：ep60（18min）33s / 395 段 / 2363 词；ep51（82.8min）93s / 1242 段 / 9821 词
- 远端残留：实测**上传音频不落盘**，每批结束只报告容器 `/tmp` 新增条目；加
  `--remote-clean delete` 才删，且只删本批次能归属的 `tmp*` 目录（永不通配、不重启服务）
- http 后端不生成 WAV、不碰 ffmpeg；`m4a/` 原件只读上传，`finally` 只删 `work/` 下的产物

## 环境要求

- Python 3.12+（venv：`whisperASR_env/`，不入库）
- ffmpeg、faster-whisper 1.2.x、ctranslate2 4.x（仅 local 后端需要模型；http 后端只需 httpx，已随 huggingface_hub 安装）
- ⚠️ `av` 需 pin `18.1.0`：av 19 移除了 faster-whisper 1.2.1 依赖的 `metadata_errors` 参数

## 快速开始

```bash
source whisperASR_env/bin/activate   # 或自建 venv 后 pip install faster-whisper
cp conn/asr.env.example conn/asr.env  # 首次：填 ASR_URL / ASR_SSH_HOST（该文件不入库）
python -m pipeline preflight --season season2
python -m pipeline prepare-audio --season season2   # m4s → m4a（新季首步）
python -m pipeline index --season season2           # → output/BusinessEnglishCourse-Season2/index/
python -m pipeline run --season season2 --episodes 1-5
python -m pipeline extract --season season2 --episodes 1-5 --render-index
python -m pipeline status --season season2          # 查看各集状态
```

## 复习工作台（§8）

把 `output/` 里已完成的复习笔记编译成一个**单文件静态网页**（零依赖、零外链、可离线双击打开）：

```bash
python3 webgen/build_site.py --season season1                  # → output/BusinessEnglishCourse-Season1/review_site/index.html（full，含原文例句）
python3 webgen/build_site.py --season season1 --mode minimal   # 公开版：不含原文例句
python3 webgen/selfcheck.py --season season1                   # 18 项自动校验（含幂等、minimal 复检、发布包一致性）
```

页面含五个视图（今日 / 词句 / 自测 / 词表 / 进度），学习进度只存浏览器 localStorage；新增集数后重跑 build 即可增量进入站点。

## 精读页与服务器部署

`intensive/index.html` 是**音频同步的精读页**：顶部选集，逐句字幕点哪跳哪，同步滚动、倍速、进度记忆。
默认单文件内联、零外链，桌面 `file://` 双击即可；加 `--split` 则字幕按集拆成
`data/<stem>.json`，页面按需加载（服务器模式，首屏从 1.4 MB gzip 降到 ~13 KB）。

`build_server.py` 把复习站、精听页和音频组装成一个可直接 rsync 的目录：

```bash
python3 webgen/transcode_mobile.py --season season1 --bitrate 32k  # → data/<Season>/m4a_32/（22.05kHz 单声道）
python3 webgen/build_server.py --season season1                    # → dist/server/BusinessEnglishCourse-Season1/
python3 webgen/build_server.py --season season1 --verify           # 只校验已有产物
```

产物里 `/` 是复习站，`/listen/` 是精听页（直链进入，两页互不链接），`listen/audio/` 是硬链接过来的音频；
`serve.py` 本地预览，`nginx.conf` / `README.md` 直接用于部署。

- 转码参数：`-ac 1 -ar 22050 -b:a 32k -af highpass=f=70`；源为 44.1kHz stereo ~72kbps，降到 22.05kHz
  后同码率更集中在中频。`--bitrate 24k` 可再省约 170MB。
- 移动端页面分支：`@media (pointer:coarse)` 显示大按钮控制条、隐藏键盘提示、字幕区改 `dvh`；
  接 Media Session（锁屏显示集名、上一句/下一句/±10s）。
- `webgen/probe.html` 是浏览器能力探针（安全上下文 / SW / Cache / AAC 实解 / Media Session），
  换设备或换浏览器时先跑它再决定形态。

## 进度

- [x] 阶段一：67 集编号映射（index/episodes.md，6 处标题 typo 已修正）
- [x] 01–67 集转写 + 学习 md（output/）
- [x] 复习工作台（§8）与精读页
- [x] 服务器部署包（dist/server）：复习站 + 精听页 + 音频，整集 Blob 缓存
