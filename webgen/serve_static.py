#!/usr/bin/env python3
"""本地预览用静态服务器：修正 .m4a 的 MIME 并支持 Range。

python -m http.server 的两个毛病会让音频播不出来：
  1. .m4a 被映射成 audio/mp4a-latm，Chromium 内核据此判定格式不支持
  2. 不支持 Range，媒体元素 seek 时只能整包重下，且会反复重连

线上由 nginx 提供同等能力（见 dist/server/<季>/nginx.conf）；本脚本只在本地
预览 dist/server 产物时用，build_server.py 会把它拷成产物里的 serve.py。

用法:
    python serve.py                 # 127.0.0.1:8080，根目录为本文件所在目录
    python serve.py --port 8081 --root /path/to/site
    python serve.py --verbose       # 打印访问日志（默认安静）
"""
from __future__ import annotations

import argparse
import os
import posixpath
import re
import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

MIME = {
    ".html": "text/html; charset=utf-8",
    ".m4a": "audio/mp4",
    ".mp4": "audio/mp4",
    ".m4b": "audio/mp4",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json",
    ".svg": "image/svg+xml",
    ".png": "image/png",
}

RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")


class Handler(SimpleHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def guess_type(self, path):
        ext = posixpath.splitext(str(path))[1].lower()
        if ext in MIME:
            return MIME[ext]
        return super().guess_type(path)

    def log_message(self, fmt, *args):
        if getattr(self.server, "verbose", False):
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def do_HEAD(self):
        self.send_head_only = True
        try:
            f = self.send_head()
            if f:
                f.close()
        finally:
            self.send_head_only = False

    def send_head(self):
        self._remaining = None
        path = self.translate_path(self.path)
        if os.path.isdir(path):
            return super().send_head()

        try:
            f = open(path, "rb")
        except OSError:
            self.send_error(404, "File not found")
            return None

        size = os.fstat(f.fileno()).st_size
        ctype = self.guess_type(path)
        start, end = 0, size - 1
        partial_resp = False

        rng = self.headers.get("Range")
        if rng and "," not in rng:
            m = RANGE_RE.match(rng.strip())
            if not m:
                f.close()
                self.send_response(416)
                self.send_header("Content-Range", "bytes */%d" % size)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return None
            a, b = m.group(1), m.group(2)
            if a == "" and b == "":
                f.close()
                self.send_error(416, "Bad Range")
                return None
            if a == "":
                start = max(0, size - int(b))
                end = size - 1
            else:
                start = int(a)
                end = min(int(b), size - 1) if b else size - 1
            if start >= size or start > end:
                f.close()
                self.send_response(416)
                self.send_header("Content-Range", "bytes */%d" % size)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return None
            partial_resp = True

        length = end - start + 1
        self.send_response(206 if partial_resp else 200)
        if partial_resp:
            self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Last-Modified", self.date_time_string(os.fstat(f.fileno()).st_mtime))
        if getattr(self, "send_head_only", False):
            self.end_headers()
            self._remaining = None
        else:
            self.end_headers()
            f.seek(start)
            self._remaining = length
        return f

    def copyfile(self, source, outputfile):
        remaining = getattr(self, "_remaining", None)
        if remaining is None:
            try:
                super().copyfile(source, outputfile)
            except (BrokenPipeError, ConnectionResetError):
                pass
            return
        try:
            while remaining > 0:
                buf = source.read(min(65536, remaining))
                if not buf:
                    break
                outputfile.write(buf)
                remaining -= len(buf)
        except (BrokenPipeError, ConnectionResetError):
            pass


def main(argv=None) -> int:
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description="手机离线版静态服务器")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--bind", default="127.0.0.1")
    ap.add_argument("--root", default=here)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)

    if not os.path.isfile(os.path.join(args.root, "index.html")):
        print("警告：%s 下没有 index.html，确认根目录是否正确" % args.root, file=sys.stderr)

    handler = partial(Handler, directory=args.root)
    ThreadingHTTPServer.allow_reuse_address = True
    httpd = ThreadingHTTPServer((args.bind, args.port), handler)
    httpd.verbose = args.verbose
    print("根目录: %s" % args.root, flush=True)
    print("服务中: http://%s:%d/   (Ctrl+C 停止)" % (args.bind, args.port), flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
