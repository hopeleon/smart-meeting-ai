"""
Qwen3-ASR 近实时转写微服务（独立隔离环境运行：/home/zhong/qwen3asr-venv，py3.12 / torch cu130）。

主应用（py3.11）通过 HTTP 调用本服务做 ASR，从而隔离 Qwen 的依赖、不污染主 .venv。

接口：
  GET  /health            -> {"status":"ok","ready":bool}
  POST /asr?language=zh   -> body 为 int16 PCM 16k 单声道原始字节；返回 {"text","language","infer_ms"}
                             language: Chinese / English / auto（auto=自动判语种）

启动： /home/zhong/qwen3asr-venv/bin/python qwen_asr_service/server.py [port=8030]
"""
import os
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("HF_HUB_OFFLINE", "1")  # 模型已缓存，离线加载避免连不上 HF 卡住

import sys
import json
import time
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

import numpy as np
import torch
from qwen_asr import Qwen3ASRModel

MODEL = None
_LOCK = threading.Lock()  # 串行化 GPU 调用（单路会议流，顺序请求）

_LANG_MAP = {"zh": "Chinese", "chinese": "Chinese", "en": "English", "english": "English"}


def load_model():
    global MODEL
    print("[QwenASR] 加载 Qwen3-ASR-1.7B ...", flush=True)
    t0 = time.time()
    MODEL = Qwen3ASRModel.from_pretrained(
        "Qwen/Qwen3-ASR-1.7B",
        dtype=torch.bfloat16,
        device_map="cuda:0",
        max_inference_batch_size=8,
        max_new_tokens=256,
    )
    print("[QwenASR] 模型就绪，耗时 %.1fs" % (time.time() - t0), flush=True)


class Handler(BaseHTTPRequestHandler):
    def _json(self, obj, code=200):
        b = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if urlparse(self.path).path == "/health":
            self._json({"status": "ok", "ready": MODEL is not None})
        else:
            self.send_error(404)

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path != "/asr":
            self.send_error(404)
            return
        if MODEL is None:
            self._json({"error": "model not ready"}, code=503)
            return
        try:
            n = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(n) if n else b""
            pcm = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
            if pcm.size == 0:
                self._json({"text": "", "language": None, "infer_ms": 0})
                return
            q = parse_qs(parsed.query)
            lraw = (q.get("language", ["Chinese"])[0] or "Chinese").strip()
            language = None if lraw.lower() == "auto" else _LANG_MAP.get(lraw.lower(), lraw)
            t0 = time.time()
            with _LOCK:
                res = MODEL.transcribe(audio=(pcm, 16000), language=language)
            self._json({
                "text": res[0].text,
                "language": res[0].language,
                "infer_ms": int((time.time() - t0) * 1000),
            })
        except Exception as e:
            self._json({"error": "%s: %s" % (type(e).__name__, e)}, code=500)

    def log_message(self, *args):
        pass  # 静默访问日志


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8030
    load_model()
    srv = HTTPServer(("127.0.0.1", port), Handler)
    print("[QwenASR] 微服务监听 127.0.0.1:%d" % port, flush=True)
    srv.serve_forever()
