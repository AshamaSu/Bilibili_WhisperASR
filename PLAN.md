# B 站商务英语 67 集 → 复习 Markdown 流水线 · 执行计划

> 交由 CodeBuddy 执行。计划基于 2026-10-13 的环境验证结果，所有路径已实测存在。

## 0. 环境与路径（已验证 ✅，勿改动）

| 项 | 值 |
|---|---|
| 工作区 | `/Users/aaronliu/Workspaces-whisperASR/` |
| venv | `whisperASR_env/`（Python 3.12.13；faster-whisper 1.2.1 / ctranslate2 4.8.1 / onnxruntime 1.31.0 已装好） |
| 模型缓存 | `models/`（空，执行时 `export HF_HOME=/Users/aaronliu/Workspaces-whisperASR/models`） |
| B 站缓存 | `data/c_<cid>/`，共 **67 集**，每集含 `entry.json`、`<quality>/audio.m4s`、`cover.jpg` |
| 顺序截图 | `data/视频顺序.jpg`（两列网格：左1 右2 / 左3 右4，已人工核对 = cid 升序） |
| 产出目录 | `/Users/aaronliu/WorkBuddy/EnglishLearning/output/`（不存在则创建） |

激活环境：

```bash
source /Users/aaronliu/Workspaces-whisperASR/whisperASR_env/bin/activate
export HF_HOME=/Users/aaronliu/Workspaces-whisperASR/models
```

## 1. 总量与耗时预期

- 67 集，音频总时长 **约 56 小时**（单集 30~75 分钟）
- CPU 推理（small 模型 int8）约 1.5~2× 实时 → 全量约 **30~40 小时机器时间**
- **必须分批后台执行**，每集落盘一个批次，中断可续跑（见 §6 续跑机制）

## 2. 阶段一：编号映射（先出表，等人确认，再动手）

1. 解析全部 `data/c_*/entry.json`，取 `page_data.part`（本集英文标题）与 `page_data.cid`、`total_bytes`
2. 按 **cid 升序**编号 01~67（已与《视频顺序.jpg》人工核对一致）
3. 产出两份文件（放 `~/Workspaces-whisperASR/index/`）：
   - `episodes.json`：机器用。schema：
     ```json
     [{"no": 1, "cid": "38192677309", "dir": "c_38192677309",
       "title_raw": "How to explain a project you worked on",
       "title_fixed": "How to explain a project you worked on",
       "slug": "Howtoexplainaprojectyouworkedon",
       "file_stem": "01_Howtoexplainaprojectyouworkedon",
       "status": "pending"}]
     ```
   - `episodes.md`：给人看的对照表（编号 | cid | 原标题 | 修正后 | 时长），**输出给用户逐行确认后再进入阶段二**
4. 标题清洗规则：
   - 修正源数据拼写错误（已知：`Wriet→Write`、`Busness→Business`、`Approchers→Approaches`、`workede→worked`，逐一通读再补）
   - slug：去空格去标点（`/ : & ,` 等删除或连写），保留驼峰原样；文件名格式 `NN_<slug>`
   - 引号、`|` 一律不进文件名

## 3. 阶段二：音频转换

```bash
ffmpeg -y -i data/c_<cid>/<q>/audio.m4s -ar 16000 -ac 1 -c:a pcm_s16le work/<cid>.wav
```

- `audio.m4s` 所在子目录名不固定（如 `64/`），用 `find` 定位
- WAV 放 `work/`，转完即可转写；全量约 60GB，**转一集删一集**（WAV 用完即删，m4s 不动）

## 4. 阶段三：批量转写（faster-whisper）

参数基线：

```python
from faster_whisper import WhisperModel
model = WhisperModel("small", device="cpu", compute_type="int8", cpu_threads=8)
segments, info = model.transcribe(
    wav_path, language="en", vad_filter=True,
    vad_parameters={"min_silence_duration_ms": 500},
    condition_on_previous_text=True, beam_size=5,
)
```

- 单集 30~75 分钟 > 30 分钟阈值，faster-whisper 内部自带分块，无需手动切片；若内存吃紧再降 `beam_size=1`（greedy）
- 每集输出三种产物到 `work/transcripts/<file_stem>/`：`.txt`（全文）、`.srt`（时间轴）、`.json`（分段）
- **热词注入**：`initial_prompt="Business English vocabulary: stakeholder, deliverable, KPI, revenue, negotiate, deadline..."`（商务场景固定提示词，可减小专有名词误识别）
- 质量门禁（来自 whisper-local 专家 SOP）：
  - 输出不得为空；每集转完抽查开头/中间/结尾各 1 段
  - 语种必须是 en，若检测成其他语言则报错跳过并记录
  - `status` 字段更新：pending → converting → transcribing → done / failed

## 5. 阶段四：内容加工（英语学习向，逐集生成 md）

对每集转写稿提取，产出 `/Users/aaronliu/WorkBuddy/EnglishLearning/output/<file_stem>.md`，**四部分结构固定**：

```markdown
# 01 · How to explain a project you worked on

## 1. 词
### 核心词（听前必看，≤15 个）
| 词 | 音标 | 释义 | 原文例句摘录 |
### 补充词（听中查阅）
| 词 | 音标 | 释义 | 原文例句摘录 |
### 语块（整体记忆，不拆单词）
| 语块 | 使用场景 | 原文例句摘录 |

## 2. 句式
| 句式 | 使用场景 | 原文例句 |
（附「填空版」：关键短语挖空 ______，供听完自测）

## 3. 词句翻译
| 英文 | 中文 |
（核心词例句 + 语块例句 + 句式例句的英中对照，覆盖 1、2 两部分全部条目）
```

提取规则（v2，2026-10-13 按用户反馈修订：核心词难度偏低、标题词漏收）：
- **标题实词硬性入选**：每集标题里的所有实词（如 essential、phrases、explaining、project）必须进核心词，不受难度判断影响
- **按 CEFR 难度分层**：核心词只收 **B1+** 的词；A1-A2 熟词（如 formal、polite、reply、grammar、spelling）降级到补充词或直接不收；「认识单词但不熟该用法」的熟词僻义（如 available 表"有空"、regards 信末致意）优先于全新生词入选
- **语块单列**：本系列教的是 phrase（语块）而非单词。每集单列 3~5 条固定表达（如 `Dear Sir or Madam`、`Looking forward to your reply`、`keep it short and clear`），放「语块」小节整体记忆，不塞进单词表
- 核心词 = 标题实词 + B1+ 商务高频实词 + 影响理解的低频词 + 熟词僻义，每集 ≤15 个，宁缺毋滥
- 句式 = 可迁移的功能句（开场、观点、转折、委婉、总结等场景分类），每集 8~12 条
- 68 集中「100/150/1000 phrases」系列词汇重复率高：**查总词表去重**，已收词在补充表标注「→ 见总词表」
- 同步维护 `output/00_总词表.md`：字母序、去重、标注每词出现的集号与 **CEFR 等级**（后续系列去重时顺便做难度归并）

## 6. 执行顺序与续跑机制

1. **先跑第 01~05 集**（用户近期学习优先），交用户验收 md 格式
2. 验收通过 → 后台分批跑 06~67，每批 5 集
3. 续跑：启动时读 `episodes.json`，跳过 `status: done`，从第一个非 done 继续
4. 每集完成即写 md，不等全批
5. 全部完成后生成 `output/00_INDEX.md`（67 集目录页：编号、标题、时长、词数，链接到各 md）

## 7. 禁止事项 / 注意

- 🔴 `data/` 是唯一原件，**只读**；任何脚本不得写入、重命名、删除 `data/` 内文件
- 🔴 不上传任何音频/转写稿到云端（本地推理是本方案前提）
- 🔴 转写失败（空输出/语种错误）不重试超过 2 次，标记 failed 并继续下一集，最后统一汇报
- 长任务一律后台执行（nohup / 任务队列），不要前台阻塞
- 产出文件名不含空格，编号两位数补零
