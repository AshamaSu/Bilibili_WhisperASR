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

# ---- 多季支持 ----
# 源音频：data/BusinessEnglishCourse-<SeasonN>/{m4a,c_*}      只读
# 产出物：output/BusinessEnglishCourse-<SeasonN>/{index,transcripts,candidates,md,...}
# 产出目录名与 data 子目录严格同名，便于一眼对应
DATA_ROOT = WORKSPACE / "data"
SEASONS_ROOT = WORKSPACE / "output"
MODELS_DIR = WORKSPACE / "models"
SEASON_PREFIX = "BusinessEnglishCourse-"
DEFAULT_SEASON = "season1"

# 以下路径由 set_season() 按季填充，其余模块一律通过 config.X 运行时读取
SEASON = ""
SEASON_KEY = ""
DATA_DIR = None
SEASON_DIR = None
M4A_DIR = None
INDEX_DIR = None
WORK_DIR = None
WORK_TMP = None
TRANSCRIPTS_DIR = None
CANDIDATES_DIR = None
LOGS_DIR = None
EPISODES_JSON = None
EPISODES_MD = None
VOCAB_INDEX_JSON = None
OUTPUT_DIR = None


def list_seasons():
    """data/ 下 BusinessEnglishCourse-* → ['season1', 'season2']（升序）。"""
    out = []
    for d in sorted(DATA_ROOT.glob(SEASON_PREFIX + "*")):
        if d.is_dir():
            out.append(d.name[len(SEASON_PREFIX):].strip().lower())
    return out


def normalize_season(name=None):
    """season1 / BusinessEnglishCourse-Season1 / SEASON1 统一成 season1。"""
    raw = (name or os.environ.get("ASR_SEASON") or DEFAULT_SEASON).strip()
    if raw.lower().startswith(SEASON_PREFIX.lower()):
        raw = raw[len(SEASON_PREFIX):]
    return (raw.strip().lower() or DEFAULT_SEASON)


def data_dir_of(name):
    return DATA_ROOT / (SEASON_PREFIX + normalize_season(name).capitalize())


def set_season(name=None, strict=True):
    """切换当前季；产出物重定向到 output/<与 data 同名的季目录>/。"""
    global SEASON, SEASON_KEY, DATA_DIR, SEASON_DIR, M4A_DIR, INDEX_DIR, WORK_DIR, WORK_TMP
    global TRANSCRIPTS_DIR, CANDIDATES_DIR, LOGS_DIR, EPISODES_JSON, EPISODES_MD
    global VOCAB_INDEX_JSON, OUTPUT_DIR

    n = normalize_season(name)
    ddir = data_dir_of(n)
    if strict and not ddir.is_dir():
        raise ValueError("季目录不存在: %s（可用: %s）"
                         % (ddir, ", ".join(list_seasons()) or "无"))
    SEASON = n              # 短名：season1
    SEASON_KEY = ddir.name  # 目录名：BusinessEnglishCourse-Season1
    DATA_DIR = ddir
    M4A_DIR = ddir / "m4a"
    SEASON_DIR = SEASONS_ROOT / SEASON_KEY
    INDEX_DIR = SEASON_DIR / "index"
    WORK_DIR = SEASON_DIR / "work"
    WORK_TMP = WORK_DIR / "tmp"
    TRANSCRIPTS_DIR = SEASON_DIR / "transcripts"
    CANDIDATES_DIR = SEASON_DIR / "candidates"
    LOGS_DIR = WORK_DIR / "logs"
    EPISODES_JSON = INDEX_DIR / "episodes.json"
    EPISODES_MD = INDEX_DIR / "episodes.md"
    VOCAB_INDEX_JSON = SEASON_DIR / "vocab_index.json"
    OUTPUT_DIR = SEASON_DIR / "md"   # 学习 md（避免 output/.../output 嵌套）
    return SEASON


set_season(DEFAULT_SEASON, strict=False)

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

# ---- ASR 后端（PLAN §9）：默认自建 GPU 服务，本地 faster-whisper 仍可切换 ----
ASR_ENV_FILE = WORKSPACE / "conn" / "asr.env"   # 未跟踪的本机私有配置（地址/主机）


def _load_env_file(path):
    """读 KEY=VALUE 私有配置；文件缺失或不可读返回空 dict。"""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    data = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        data[k.strip()] = v.strip().strip('"').strip("'")
    return data


_FILE_ENV = _load_env_file(ASR_ENV_FILE)


def _env(name, default=""):
    """优先级：环境变量 > conn/asr.env > 默认值。"""
    return os.environ.get(name) or _FILE_ENV.get(name) or default


BACKEND = _env("ASR_BACKEND", "http").strip().lower()   # http | local
BACKENDS = ("http", "local")

HTTP_URL = _env("ASR_URL", "").rstrip("/")   # 空则报错并提示，不写死公网地址
HTTP_MODEL = _env("ASR_MODEL", "large-v3")
HTTP_DIARIZE = _env("ASR_DIARIZE", "0") == "1"   # 实测说话人不可信，默认关
HTTP_TIMEOUT_CONNECT_S = float(_env("ASR_TIMEOUT_CONNECT_S", "8"))   # 连不上要快失败
HTTP_TIMEOUT_READ_S = float(_env("ASR_TIMEOUT_READ_S", "1200"))
HTTP_WRITE_MIN_S = 120          # 上传超时 = max(此值, 文件 MB × 3)
HTTP_MIN_SPAN_RATIO = 0.95      # 段覆盖门槛，防静默截断
HTTP_RTF_ESTIMATE = 0.05        # 仅用于批次耗时预估打印
BATCH_WARN_EPISODES = int(_env("ASR_BATCH_WARN_EPISODES", "5"))   # 只告警不拦截

REMOTE_CLEAN = _env("ASR_REMOTE_CLEAN", "report").strip().lower()  # off|report|delete
REMOTE_CLEAN_MODES = ("off", "report", "delete")
REMOTE_SSH_HOST = _env("ASR_SSH_HOST", "")
REMOTE_SSH_KEY = WORKSPACE / "conn" / "tencent.pem"
REMOTE_CONTAINER = _env("ASR_REMOTE_CONTAINER", "whisperx-asr-api")
REMOTE_TMP = "/tmp"
REMOTE_MIN_AGE_S = 120          # 太新的目录可能正被别的请求使用，不删


def apply_overrides(backend=None, url=None, remote_clean=None):
    """CLI 覆盖入口；非法值立刻抛 ValueError（fail fast）。"""
    global BACKEND, HTTP_URL, REMOTE_CLEAN
    if backend:
        b = backend.strip().lower()
        if b not in BACKENDS:
            raise ValueError(f"未知后端: {backend!r}（可选 {', '.join(BACKENDS)}）")
        BACKEND = b
    if url is not None:
        HTTP_URL = url.strip().rstrip("/")
    if remote_clean:
        m = remote_clean.strip().lower()
        if m not in REMOTE_CLEAN_MODES:
            raise ValueError(f"未知清理模式: {remote_clean!r}（可选 {', '.join(REMOTE_CLEAN_MODES)}）")
        REMOTE_CLEAN = m


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
