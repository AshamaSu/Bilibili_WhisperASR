"""预检：显式下载/校验 whisper small 模型 + 加载自检（评审修正 #5）。"""
import array
import tempfile
import wave
from pathlib import Path


def main():
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
    return 0
