# B 站商务英语 67 集 → 复习 Markdown 流水线 · 执行计划

> 交由 CodeBuddy 执行。计划基于 2026-10-13 的环境验证结果，所有路径已实测存在。

## 0. 环境与路径（已验证 ✅，勿改动）

| 项 | 值 |
|---|---|
| 工作区 | `/Users/aaronliu/Workspaces/whisperASR/` |
| venv | `whisperASR_env/`（Python 3.12.13；faster-whisper 1.2.1 / ctranslate2 4.8.1 / onnxruntime 1.31.0 已装好） |
| 模型缓存 | `models/`（空，执行时 `export HF_HOME=/Users/aaronliu/Workspaces/whisperASR/models`） |
| 源音频 | `data/BusinessEnglishCourse-Season<N>/`：`c_<cid>/`（B 站缓存原件）、`m4a/`（流复制）、`m4a_32/`（22.05kHz 单声道 32k 转码，站点与手机共用） |
| 季划分 | 第一季 67 集 / 56.1h；第二季 81 集 / 75.8h（2026-11-03 接入） |
| 产出目录 | `output/BusinessEnglishCourse-Season<N>/`（与 data 同名）：`index/` `transcripts/` `candidates/` `md/` `vocab_index.json` `intensive/` `review_site/` |
| 发布包 | `dist/server/<SeasonKey>/`（复习站 + 精听页 + 音频，不入库） |

激活环境：

```bash
source /Users/aaronliu/Workspaces/whisperASR/whisperASR_env/bin/activate
export HF_HOME=/Users/aaronliu/Workspaces/whisperASR/models
```

## 1. 总量与耗时预期

- 67 集，音频总时长 **约 56 小时**（单集 30~75 分钟）
- CPU 推理（small 模型 int8）约 1.5~2× 实时 → 全量约 **30~40 小时机器时间**
- **必须分批后台执行**，每集落盘一个批次，中断可续跑（见 §6 续跑机制）

## 2. 阶段一：编号映射（先出表，等人确认，再动手）

1. 解析全部 `data/c_*/entry.json`，取 `page_data.part`（本集英文标题）与 `page_data.cid`、`total_bytes`
2. 按 **cid 升序**编号 01~67（已与《视频顺序.jpg》人工核对一致）
3. 产出两份文件（放 `~/Workspaces/whisperASR/index/`）：
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
5. 全部完成后生成 `output/00_INDEX.md`（67 集目录页：编号、标题、时长、词数，链接到各集复习笔记 md 与 transcripts/ 下对应 transcript.txt）

## 7. 禁止事项 / 注意

- 🔴 `data/` 是唯一原件，**只读**；任何脚本不得写入、重命名、删除 `data/` 内文件
- 🔴 不上传任何音频/转写稿到云端（本地推理是本方案前提）
  - 2026-10-29 修订：新增自有 GPU 服务器后端 §9，默认 `ASR_BACKEND=http` 只把音频发到**自己的**
    服务器（非第三方云），本地 `--backend local` 随时可切回；除此之外仍不向任何云服务发送音频
- 🔴 转写失败（空输出/语种错误）不重试超过 2 次，标记 failed 并继续下一集，最后统一汇报
- 长任务一律后台执行（nohup / 任务队列），不要前台阻塞
- 产出文件名不含空格，编号两位数补零

---

# §8 复习工作台（1024 造物挑战参赛作品）· 执行计划

> 2026-10-27 按实测结果修订（原版 4 处不可执行/会误判的条款已改写，见 §8.1.0）。
> 目标：把 output/ 已完成的复习笔记做成**单文件静态网页「商务英语每日复习工作台」**，作为 WorkBuddy「1024 造物挑战」参赛作品（资料库网页作品）。本地交付为终点，上传/发布由 WorkBuddy 侧完成。

## 8.0 目标与约束

| 项 | 值 |
|---|---|
| 产物 | `review_site/index.html` —— **单文件**自包含页面（数据内联，无外部请求） |
| 数据源 | `output/`（45 集 md，01–45）、`index/episodes.json`（67 条，45 条 done，含 `duration_min`）、`vocab_index.json`（6182 词） |
| 生成脚本 | `webgen/build_site.py`（新建，纯标准库，可反复重跑） |
| 校验脚本 | `webgen/selfcheck.py`（新建，独立实现，交叉验证） |
| 技术栈 | 纯 HTML + CSS + 原生 JS，零依赖、零 CDN |
| 截稿 | 2026-11-01 23:59 |

硬约束（红线）：
- 🔴 `data/` 只读；站点**不得包含**任何音频、视频、转写稿原文（`transcripts/` 内容不进站点，构建期也不读）
- 🔴 版权：站点只承载衍生学习内容；md 的「原文例句摘录」属少量引用，footer 须声明出处与原课程链接，并保留授权说明槽位
- 🔴 单文件 ≤ 8 MB；无统计脚本、外链图片、字体请求
- 🔴 学习进度只存浏览器 localStorage，不上传

## 8.1 阶段 A：数据提取脚本 `webgen/build_site.py`

### 8.1.0 相对原版的修订（实测驱动）

| 原条款 | 问题 | 修订 |
|---|---|---|
| §8.1.2 按标题层级切分 | `### 补充词` 有 3 种后缀变体；`output/` 还有 `00_INDEX.md`（含链接）与 `00_总词表.md`（6182 行无标题），glob 会崩 | H3 前缀匹配；只取 `episodes.json` 中 done 的 `file_stem`；列数强校验；错误带行号 |
| §8.1.3 无答案结构 | 填空版没有答案键 | 构建期从本集 md 反推答案（实测 381/430 = 88.6%），其余标为不判分 |
| §8.1.4 minimal | 漏删 `patterns.ex`，句式例句同为原文引用 | minimal 一并删除 |
| §8.2.3 逐词判分 | 约 10% 填空无法可靠还原，强行判分会「答对判错」 | 改三层题型（拼写 / 可判分填空 / 对照练习） |
| §8.3.3 `grep -c http` | 与 footer 课程链接自相矛盾；`grep -c` 数行数（内联 JSON 仅 1 行）；无匹配时退出码 1；抓不到 `url()`/`@import`/`fetch` | 改资源型构造黑名单 + `https://` 白名单计数 |
| §8.2 视图 | `vocab_index.json` 被列为数据源却无视图使用 | 新增第五视图「词表」 |

### 8.1.1 取数与解析
1. 读 `index/episodes.json`，只取 `status == "done"`（当前 45 集），并断言集号 1..N 连续
2. 逐集解析 `output/<file_stem>.md`（**不 glob 目录**，否则会吞掉 `00_*`）
3. 严格状态机（H1 → 词/句式/翻译三节），表类型由**表头元组**判定，共 4 种签名：

| 表头 | 归属 | 列 |
|---|---|---|
| 词/音标/释义/原文例句摘录 | 核心词、补充词 | w/ipa/g/ex |
| 语块/使用场景/原文例句摘录 | 语块 | c/s/ex |
| 句式/使用场景/原文例句 | 句式 | p/s/ex |
| 英文/中文 | 翻译 | en/zh |

4. 容错规则：
   - H3 用前缀匹配 `核心词|补充词|语块|填空版`（补充词后缀有 3 种写法）
   - 跳过分隔行；单元格数必须等于表头数；空单元格报错
   - 填空题必须有序号连续且含 `______`（6 个下划线，全库 877 处）
   - 未知非空行、乱序标题一律报错，**不静默丢数据**；错误格式 `output/07_x.md:42: 表格列数 3 != 表头 4`，收集后统一打印并 exit 1
   - 各节行数区间作为回归护栏（核心 8–15 / 补充 8–16 / 语块 4–5 / 句式 10–14 / 翻译 15–26 / 填空 8–12），超出只告警

### 8.1.2 填空答案还原（新增）
- 候选句 = 本集 md 全部句子（句式 p 与 ex、词条 ex、语块 ex、翻译 en），**不读 transcripts**
- 用 `______` 切分后构造 `^(.+?)字面量(.+?)$` 正则匹配；要求：唯一匹配、每空非空、相邻两空之间字面量为空时每空恰好 1 token、捕获 ≤ 4 词且不含 `，；：()`、长度 ≤ 40
- 通过则 `scored=true` 并写入可接受答案（大小写/弯引号/缩写展开等写法等价，不做词形变化）；否则 `scored=false`，UI 标「对照练习·不判分」
- 构建期断言：还原率 ≥ 0.85、每集 scored ≥ 3（实测 0.886 / 最少 4），跌破即 exit 1

### 8.1.3 内联数据结构
```json
{"schema":1,"mode":"full|minimal","generated_at":"...",
 "meta":{"ep_total":67,"ep_done":45,"counts":{"core":648,"supp":534,"chunks":224,"patterns":520,"trans":845,"blanks":430,"scored":381}},
 "catalog":[{"no":1,"t":"...","d":37.5,"s":"done"}],
 "vocab":{"word":{"d":"display","n":频次,"e":[集号],"l":"B1+"}},
 "episodes":[{"no":1,"title":"...","duration_min":37.5,"file_stem":"...",
   "core":[{"w":"","ipa":"","g":"","ex":""}],"supp":[],"chunks":[{"c":"","s":"","ex":""}],
   "patterns":[{"p":"","s":"","ex":""}],"trans":[{"en":"","zh":""}],
   "blanks":[{"t":"...","gaps":[{"a":["答案","变体"]}],"scored":true,"hint":3}]}]}
```

### 8.1.4 构建与模式
- 唯一占位符 `__SITE_DATA__`，写入 `<script id="site-data" type="application/json">`，`JSON.parse` 读取
- 转义：`</` → `<\/`，U+2028/2029 转义；`ensure_ascii=False`（393 KB vs 500 KB）
- 原子写（同目录 .tmp + `os.replace`）、UTF-8 无 BOM、纯 LF
- `--mode full`（默认，个人复习）：保留全部原文例句
- `--mode minimal`（公开投稿）：删除 core/supp/chunks/**patterns** 的 `ex`，保留句式骨架、场景与翻译表，footer 显示完整版权声明
- 幂等定义为「无输入变更 → 字节级一致」：`generated_at` 取输入文件 mtime 最大值，可用 `--generated-at` 覆盖
- 退出码：0 成功 / 1 解析或数据校验失败 / 2 用法错误 / 3 产物校验失败

## 8.2 阶段 B：页面功能（原生 JS）

布局：移动端优先单页，**五个视图**（顶部 Tab：今日 / 词句 / 自测 / 词表 / 进度），hash 路由：
`#/today`、`#/ep/<no>/<tab>`、`#/quiz/<no>`、`#/vocab`、`#/progress`

1. **今日**：streak 头、今日到期卡（附「今天可以新学」）、逾期队列、无任务时显示下次排期
   - 排程：新集按编号解锁；学过的进盒子 0–4，间隔 1/2/4/7/15 天；**凌晨 4 点换天**
   - 推荐算法零随机：到期集按 `(due, box, no)` 排序取首个；无到期则取最小编号未学集；再无则 `rest`（显示下次日期）或 `done`
2. **词句**：五子页签（核心词/补充词/语块/句式/翻译）
   - 词卡为原生 `<button aria-pressed>`，Enter/Space 翻转，Esc 收回；例句为翻转后的同级披露区（**不做长按**）
   - 语块/句式/翻译用表格，移动端 `overflow-x:auto`
3. **自测（三层题型，替代原「全部逐词判分」）**
   - L0 拼写：每集 6 词（确定性选取），答案即词表本身，可点「提示」看首字母
   - L1 可判分填空：381 条，逐词 LCS 三态着色（命中/缺失/多余），部分得分
   - L2 对照填空：49 条，标「对照练习·不判分」，显示最相近参考句式并自评
   - 「显示答案」后本次不计分；升盒只看「完成本集」，不看分数
4. **词表（新增）**：6182 词可搜索，显示 CEFR 等级、词频、出现集号，点击跳到该集词卡
5. **进度**：67 格地图（done 可点、pending 灰置「待更新」）、统计条、导出/导入 JSON、二次确认重置
6. **全局**：首次访问自动弹使用说明（localStorage 记忆）；footer 版权声明 + 课程来源链接 + 生成时间 + 模式徽标
7. **无障碍**：原生控件、`:focus-visible` 可见、路由切换聚焦 h1、判分结果 `aria-live`、触控 ≥44px、`prefers-reduced-motion` 关闭动画
8. **存储**：单键 `wh:state:v1`，版本不匹配移入 orphan 并重建；探针失败（Safari file://）切内存模式并显示降级横幅，导出/导入始终可用

## 8.3 阶段 C：验收（新）

**自动**：`python webgen/selfcheck.py`
1. 产物 UTF-8 可解码、无 BOM、无 CR
2. ≤ 8 MB（实测 865 KB，10.6%）
3. `site-data` 块可 `json.loads`
4. catalog 67 / episodes 45 / vocab 6182
5. 表体合计 = 648/534/224/520/845/430
6. 逐集与 md 独立回算一致（核心/补充/语块/句式/翻译/填空）
7. 可判分填空 ≥ 366 且每集 ≥ 3（实测 381，88.6%）
8. 每个空非空、≤4 词；挖空数 == gaps 数
9. 资源型外链黑名单 0 命中（script src / link href / img src / iframe|video|audio / @import / url() / fetch / XHR / sendBeacon / import()）
10. `https://` 恰好 1 次（footer 课程来源）
11. 数据块内无 URL
12. 无转写稿路径、无音频路径
13. 幂等：固定 `--generated-at` 两次构建 sha256 相同
14. `--mode minimal` 重建后例句字段全消失且条数不变

**人工**（Chrome/Safari，file:// 双击）：首访弹说明只弹一次 / 今日推荐刷新多次不变 / 词卡键盘可翻 / 填空逐词着色与「不判分」标注 / 完成一集后刷新记录仍在 / 375 px 无横向滚动

## 8.4 明确不做

- 不做账号/云端同步、不做 TTS 发音
- 不读 `transcripts/`（构建期也不读）
- 不做虚拟化/懒加载（实测 865 KB、单屏 < 400 节点，无必要）
- 不做 11–67 集补跑（属主流水线，补完重跑 build 即可增量进入）
- 不修改 `output/` `transcripts/` `candidates/` 既有文件

## 8.5 实施记录（2026-10-27）

| 文件 | 说明 |
|---|---|
| `webgen/build_site.py` | 解析 + 答案还原 + 内联 + 原子写，约 560 行 |
| `webgen/template.html` | 五视图单页模板，约 1000 行 |
| `webgen/selfcheck.py` | 独立校验器，17 项自动检查 |
| `review_site/index.html` | 生成物，865 KB（minimal 756 KB） |

构建：`python3 webgen/build_site.py` → `python3 webgen/selfcheck.py`（17 passed / 0 failed）

---

# §9 转写后端双轨（WhisperX GPU / 本地 faster-whisper）

> 2026-10-29 落地。完整实测数据见 `whisperASR-whisperx-转写方案.md`；本节记录**最终决策**与代码落点。

## 9.1 开关与默认值

| 项 | 值 | 说明 |
|---|---|---|
| `ASR_BACKEND` | `http`（默认）/ `local` | CLI `--backend` 覆盖 |
| `ASR_URL` | `conn/asr.env` | 不进代码：仓库有公开 remote，地址/主机只放未跟踪文件 |
| `ASR_MODEL` | `large-v3` | 服务端模型名，用于请求参数与探活比对 |
| `ASR_DIARIZE` | `0` | 实测说话人不可信，默认关；开则保留 `segments[].speaker` |
| `ASR_REMOTE_CLEAN` | `report` | `off` / `report` / `delete`，见 9.5 |

`conn/` 已加入 `.gitignore`（内含 SSH 私钥 `tencent.pem` 与服务器地址，仓库是公开 remote）。

## 9.2 代码落点

| 文件 | 改动 |
|---|---|
| `pipeline/config.py` | `BACKEND/HTTP_*/REMOTE_*` + `apply_overrides()`；地址优先级 环境变量 > `conn/asr.env` > 空（空则报错） |
| `pipeline/state.py` | `pending → {converting, transcribing}`（http 无 convert 阶段，否则状态机拒转） |
| `pipeline/convert_stage.py` | 新增 `http_source_audio()`：m4a 优先、m4s 兜底；`source_audio` 不动 |
| `pipeline/http_backend.py` | 新建：`health()` / `transcribe_file()`，返回与 `transcribe_wav` 同构的 `(segments, metrics)` |
| `pipeline/transcribe_stage.py` | 新增 `RemoteError/BackendUnavailable/CoverageError` + `backend()` + `transcribe()` 分派；`transcribe_wav`/`qa_check`/`write_artifacts` 零改动 |
| `pipeline/runner.py` | `process_episode` 双路径（`wav`/`src` 分离 + `wav.parent == WORK_DIR` 守卫）；批次探活、告警、汇总；`BackendUnavailable` 冒到批次层 |
| `pipeline/remote_probe.py` | 新建：只读 `snapshot()` + 严格白名单 `report()` |
| `pipeline/cli.py` / `__main__.py` | `--backend/--asr-url/--remote-clean`；`transcribe --dry-run`；`remote-probe`；`__main__` 补 `sys.exit`（此前退出码恒为 0） |
| `pipeline/preflight.py` | 默认只探活服务器；`--backend local` 才下载/加载本地模型 |

## 9.3 错误语义

| 情形 | 行为 |
|---|---|
| 连不上 / 401 / 403 | `BackendUnavailable` → 批次中止，退出码 2，**不消耗单集重试预算**，不动 episodes.json |
| 408 / 429 / 5xx / 超时 | `RemoteError(retryable)` → 走 `MAX_RETRIES` 预算，下次重跑该批次重试 |
| 400 / 413 / 422 | `RemoteError(retryable=False)` → 直接 `failed`，不写产物 |
| `language != en` | `LanguageGateError` → `suspect`，不重试（服务无概率，只做字符串门禁） |
| 段覆盖 < 95% | `CoverageError` → 可重试，防静默截断 |

## 9.4 分批（共享 GPU）

集数由用户指定，单批超过 5 集（或 `--episodes all`）**只告警不拦截**，打印预估 GPU 分钟与上传量。
串行执行、不并发；http 路径不生成 WAV、不跑 ffmpeg，每批结束打印汇总表（words/rep/span/秒数/RTF/上传量）。

## 9.5 远端残留审计（只读探测，2026-10-29）

- 9010 = 容器 `whisperx-asr-api`，**只挂模型缓存卷** `/.cache`，宿主机文件系统零写入
- 容器 `/tmp` 仅 2 个 16KB `tmp*/_remote_module_non_scriptable.py`（torch 加载残留）
- `find / -xdev` 找不到任何 `.m4a/.wav/.mp3`；实测 42.5MB 上传后 `/data` 与容器 `/tmp` 均无新增
- `/data/speakr/uploads/recordings/2026` 属另一个业务（容器 `speakr`，8899），与本流水线无关
- 结论：**上传音频不落盘**。`remote-probe` 只读报告；`--remote-clean delete` 仅删
  「批次前不存在 + mkdtemp 形态 + 落在批次时间窗 + 目录内能认出本批次文件名」四重命中的目录，
  逐条绝对路径删除，永不通配、永不 restart/prune、永不碰 `/.cache`

## 9.6 红线

- 不并发打这台共享 GPU；不修改服务器其他业务文件、不重启/删除任何服务
- 服务无鉴权且 0.0.0.0 暴露：如需收紧，应在部署侧加鉴权或改绑 127.0.0.1 + SSH 隧道（本仓库不改服务器）
- `data/m4a` 是唯一原件（B 站缓存已清理）：http 上传只读，`finally` 只删 `work/` 下的 WAV

## 9.7 实测（2026-10-29）

| 集 | 时长 | 上传 | 端到端 | 段 / 词 | span | 复读率 |
|---|---|---|---|---|---|---|
| 60（A/B dry-run） | 18min | 9.7MB | 21.5s | 395 / 2363 | 1.001 | 3.1% |
| 51（真实写入） | 82.8min | 42.5MB | 93.0s | 1242 / 9821 | 0.999 | 2.6% |
| 60 本地 small（对照） | 18min | — | ~266s | 348 / 2373 | — | 4.0% |

## 9.8 与站点构建的耦合

`webgen/build_site.py` 只认 `status == done` 且要求 `output/<file_stem>.md` 存在。
因此 51–67 转写完成后、学习 md 生成前，站点会报 `md 文件缺失`——属预期，补 md 后即可重建。

## 10. 多季结构（2026-11-03 起）

课程扩到第二季后，源与产出按季隔离，命令统一用 --season（默认 season1 / 环境变量 ASR_SEASON）：

- 源：data/BusinessEnglishCourse-Season<N>/，只读，不入库
- 切换方式：`--season season1` 与 `--season BusinessEnglishCourse-Season1` 等价
- 产出：output/BusinessEnglishCourse-Season<N>/（与 data 子目录严格同名），入库；每季独立编号（都从 1 开始）、独立词表、独立站点；学习 md 放 `md/`，避免 output/…/output 嵌套
- 发布：dist/server/<SeasonKey>/（`/` 复习站 + `/listen/` 精听页 + `listen/audio/` 音频；手机离线包已退役）

两条硬规则：

1. 编号按分 P 的 page 排序，不是 cid 升序。第一季两者恰好一致；第二季实测不一致（cid 最小的是第 2 集），旧逻辑会把整季顺序打乱。
2. 新季第一件事是 prepare-audio：audio.m4s 流复制成 m4a（不重编码，几秒一集），并校验时长；http 后端上传的是 m4a，不是 m4s。

典型新季流程（bash）：

    python -m pipeline preflight --season season2
    python -m pipeline prepare-audio --season season2 --episodes all
    python -m pipeline index --season season2          # 出表，人工确认 episodes.md
    python -m pipeline run --season season2 --episodes all
