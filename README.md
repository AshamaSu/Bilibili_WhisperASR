# whisperASR · B 站商务英语 67 集 → 复习 Markdown 流水线

把 B 站缓存的一套商务英语课程（67 集，约 56 小时音频）在本地全自动转写，并加工成结构化的英语复习 Markdown（核心词 / 补充词 / 语块 / 句式填空 / 英中翻译）。

> 本仓库仅供个人学习使用。`data/` 不含任何音频（仅一个置空占位的样例目录展示缓存结构），课程音频与视频版权归原 UP 主所有。

## 仓库结构

```
whisperASR/
├── pipeline/            # Python 流水线（四阶段 + 批量编排 + CLI）
├── data/                # B 站缓存原件（只读；仅样例目录，媒体文件已置空）
│   └── c_<cid>/
│       ├── entry.json   # 缓存元数据（cid / 标题 / 时长）
│       └── 64/
│           ├── audio.m4s   # 0 字节占位
│           ├── video.m4s   # 0 字节占位
│           └── index.json
├── index/               # 阶段一产出：67 集编号映射（episodes.json / episodes.md）
├── transcripts/         # 阶段三产出：每集 transcript.txt / .srt / .json
├── candidates/          # 阶段四脚本侧：每集候选词/短语（candidates.json）
├── output/              # 最终学习 md（每集一篇）+ 00_INDEX.md + 00_总词表.md
├── work/                # 瞬态目录（WAV/日志/临时文件，不入库）
├── PLAN.md              # 执行计划（含 §5 v2 提取规则）
└── vocab_index.json     # 全局词表索引（幂等重建）
```

## 流水线

| 阶段 | 命令 | 说明 |
|---|---|---|
| 预检 | `python -m pipeline preflight` | 下载/校验 whisper small 模型（HF_HOME=models/） |
| 一 · 编号映射 | `python -m pipeline index` | 解析 entry.json → cid 升序编号 → 标题 typo 修正 → episodes.{json,md} |
| 二 · 音频转换 | （`run` 内置） | ffmpeg → 16k mono WAV，转一集删一集；缓存原件缺失时回退 `data/m4a/<file_stem>.m4a` |
| 三 · 转写 | `python -m pipeline transcribe` | faster-whisper（CPU int8）+ 语种门禁 + 复读检测 + 自动 QA |
| 四 · 候选提取 | `python -m pipeline extract` | 词频/短语/语块候选 + 全局词表（CEFR 分级） |
| 批量 | `python -m pipeline run --episodes 1-5` | 逐集 convert→transcribe→extract，断点续跑 |

## 转写与提取要点

- faster-whisper `small` / CPU int8，`condition_on_previous_text=False` 防长音频幻觉漂移，实测 ≈4× 实时
- 语种门禁：`language_probability < 0.8` 判 suspect，不烧重试预算；复读循环自动降参重试
- 续跑幂等：状态原子写（temp+fsync+rename），三件产物齐才置 `done`；总词表每次全量重渲染
- 提取规则 v2（PLAN §5）：标题实词硬性入选核心词；CEFR 分层只收 B1+；语块（3~5 条固定表达）单列；系列词去重标注「→ 见总词表」

## 环境要求

- Python 3.12+（venv：`whisperASR_env/`，不入库）
- ffmpeg、faster-whisper 1.2.x、ctranslate2 4.x
- ⚠️ `av` 需 pin `18.1.0`：av 19 移除了 faster-whisper 1.2.1 依赖的 `metadata_errors` 参数

## 快速开始

```bash
source whisperASR_env/bin/activate   # 或自建 venv 后 pip install faster-whisper
python -m pipeline preflight
python -m pipeline index             # 产出 index/episodes.{json,md}
python -m pipeline run --episodes 1-5
python -m pipeline extract --episodes 1-5 --render-index
python -m pipeline status            # 查看各集状态
```

## 进度

- [x] 阶段一：67 集编号映射（index/episodes.md，6 处标题 typo 已修正）
- [x] 01–45 集转写 + 学习 md（output/）
- [ ] 46–67 集
