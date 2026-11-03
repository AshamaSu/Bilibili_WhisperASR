"""预检：默认探活服务器；local 后端才下载/校验 whisper small 模型（评审修正 #5）。"""
import array
import tempfile
import wave
from pathlib import Path


def check_http(url=None):
    """GET /health，返回 dict；失败返回 None。"""
    from . import config, http_backend
    try:
        info = http_backend.health()
    except Exception as e:   # BackendUnavailable 及其他网络错误
        print(f"[http] 探活失败: {e}")
        print("[http] 检查 conn/asr.env 的 ASR_URL，或手动: "
              "ssh -N -L 9010:127.0.0.1:9010 <host>")
        return None
    print(f"[http] {config.HTTP_URL} status={info.get('status')} "
          f"device={info.get('device')} models={info.get('loaded_models')}")
    models = info.get("loaded_models") or []
    if config.HTTP_MODEL not in models:
        print(f"[http] [warn] 服务当前加载的是 {models}，与你请求的 "
              f"{config.HTTP_MODEL} 不同（服务可能按请求再加载）")
    return info


def _check_local():
    from . import config

    print(f"HF_HOME={config.MODELS_DIR}")
    from huggingface_hub import snapshot_download
    path = Path(snapshot_download(config.MODEL_REPO))
    size = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
    print(f"模型已就绪: {path} ({size / 1e6:.0f} MB)")

    # 加载自检：1 秒静音
    from faster_whisper import WhisperModel
    model = WhisperModel(config.MODEL_NAME, device=config.DEVICE,
                         compute_type=config.COMPUTE_TYPE,
                         cpu_threads=config.CPU_THREADS)
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
        with wave.open(tf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(array.array("h", [0] * 16000).tobytes())
        tmpname = tf.name
    segments, _info = model.transcribe(tmpname, language="en")
    list(segments)
    print("模型加载自检通过")
    return True


def main(backend=None):
    from . import config

    be = (backend or config.BACKEND).strip().lower()
    print(f"backend={be} url={config.HTTP_URL or '(未配置)'}")
    ok = True
    if be in ("http", "both"):
        ok = check_http() is not None and ok
    if be in ("local", "both"):
        ok = _check_local() and ok
    return 0 if ok else 1
