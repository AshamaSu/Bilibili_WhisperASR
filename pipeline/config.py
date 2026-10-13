"""中央配置：路径、模型参数、门禁阈值、typo 映射。

所有路径在此统一定义（评审修正 #7），data/ 为只读源。
"""
import os
import shutil
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent.parent

# HF 缓存必须在 faster_whisper / huggingface_hub 导入前指向 models/（评审修正 #5）。
# setdefault：用户已显式 export 时尊重用户环境。
os.environ.setdefault("HF_HOME", str(WORKSPACE / "models"))

DATA_DIR = WORKSPACE / "data"  # 只读
MODELS_DIR = WORKSPACE / "models"
INDEX_DIR = WORKSPACE / "index"
WORK_DIR = WORKSPACE / "work"
WORK_TMP = WORK_DIR / "tmp"
TRANSCRIPTS_DIR = WORKSPACE / "transcripts"   # 产出物：入库
CANDIDATES_DIR = WORKSPACE / "candidates"     # 产出物：入库
LOGS_DIR = WORK_DIR / "logs"
EPISODES_JSON = INDEX_DIR / "episodes.json"
EPISODES_MD = INDEX_DIR / "episodes.md"
VOCAB_INDEX_JSON = WORKSPACE / "vocab_index.json"
OUTPUT_DIR = WORKSPACE / "output"             # 学习 md：入库（WorkBuddy 侧为软链接）

FFMPEG = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"

MODEL_NAME = "small"
MODEL_REPO = "Systran/faster-whisper-small"
DEVICE = "cpu"
COMPUTE_TYPE = "int8"
CPU_THREADS = os.cpu_count() or 4  # 评审修正 #10：自动探测，不写死 8

TRANSCRIBE_DEFAULTS = {
    "vad_filter": True,
    "vad_parameters": {"min_silence_duration_ms": 500},
    "condition_on_previous_text": False,  # 评审修正 #2：防跨段幻觉漂移
    "language": None,  # 评审修正 #1：不强制英文，靠门禁检查
    "beam_size": 2,
    "no_speech_threshold": 0.6,  # 评审修正 #10
    "initial_prompt": (
        "Business English vocabulary: stakeholder, deliverable, KPI, revenue, "
        "negotiate, deadline, meeting, agenda, proposal, client, budget, "
        "presentation, follow up, prioritize."
    ),
}

# ---- QA 门禁（评审修正 #3：全自动，替代人工抽查）----
LANG_EXPECTED = "en"
LANG_PROB_MIN = 0.8
MIN_WORDS_PER_10MIN = 50
REPETITION_WINDOW = 10        # 滑窗段数
REPETITION_NGRAM = 8          # 词 n-gram 大小
REPETITION_LOOP_RATIO = 0.5   # 窗口内重复占比 ≥ 此值 → 复读循环（可重试）
REPETITION_SUSPECT_RATIO = 0.2  # 全篇占比 > 此值 → suspect
MAX_RETRIES = 2               # PLAN §7：重试不超过 2 次
RETRY_PARAMS = {"beam_size": 1, "no_speech_threshold": 0.7}

MIN_FREE_DISK_GB = 3
WAV_SAMPLE_RATE = 16000
WAV_BYTES_PER_SEC = WAV_SAMPLE_RATE * 2  # mono 16-bit

# ---- 阶段一标题清洗 ----
TYPO_MAP = {
    "workede": "worked",
    "Wriet": "Write",
    "Busness": "Business",
    "Approchers": "Approaches",
    "Speakup": "Speak up",
    "confidentially": "confidently",
}
# 标题含以下子串 → 在 episodes.md 尾注标出待人工决定（不自动改，评审修正 #9）
MANUAL_REVIEW_HINTS = {"Dialogs": "疑似应为 Dialogues"}
CURLY_QUOTES = "\u201c\u201d\u2018\u2019"
SLUG_MAX_LEN = 200  # macOS 255 字节文件名上限留出 NN_ 前缀与扩展名余量

# data/ 下的脱敏示例目录（仅用于展示缓存结构，不入编号映射）
EXAMPLE_CID = "00000000000"
