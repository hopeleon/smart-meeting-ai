#!/usr/bin/env python3
"""Static file server with /api and /ws proxy support."""
import argparse
import base64
import hashlib
import http.client
import os
import select
import socket
import ssl
import subprocess
import tempfile
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
import socketserver


class SPAHandler(BaseHTTPRequestHandler):
    backend_url = "http://localhost:8020"
    backend_ws_url = "ws://127.0.0.1:8020"
    _static_dir = "."
    _ws_guid = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

    def do_GET(self):
        if self.path.startswith("/ws"):
            self._proxy_ws()
        elif self.path.startswith("/api"):
            self._proxy_to_backend()
        elif self.path.startswith("/assets") or self.path == "/":
            self._serve_file()
        elif "." in self.path.split("?")[0]:
            self._serve_file()
        else:
            self.path = "/index.html"
            self._serve_file()

    def do_POST(self):
        if self.path.startswith("/api"):
            self._proxy_to_backend()
        else:
            self.send_error(404)

    def do_PUT(self):
        if self.path.startswith("/api"):
            self._proxy_to_backend()
        else:
            self.send_error(404)

    def do_DELETE(self):
        if self.path.startswith("/api"):
            self._proxy_to_backend()
        else:
            self.send_error(404)

    def do_PATCH(self):
        if self.path.startswith("/api"):
            self._proxy_to_backend()
        else:
            self.send_error(404)

    def do_HEAD(self):
        if self.path.startswith("/api"):
            self._proxy_to_backend()
        else:
            self.send_error(405)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, PATCH, DELETE, OPTIONS, HEAD")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, Upgrade, Connection")
        self.send_header("Access-Control-Max-Age", "3600")
        self.end_headers()

    def _serve_file(self):
        import mimetypes
        if self.path == "/":
            path = "/index.html"
        else:
            path = self.path.split("?")[0]

        local_path = os.path.normpath(self._static_dir + path)
        if not local_path.startswith(self._static_dir):
            self.send_error(403)
            return
        if not os.path.exists(local_path):
            local_path = os.path.join(self._static_dir, "index.html")

        mime, _ = mimetypes.guess_type(local_path)
        self.send_response(200)
        self.send_header("Content-Type", mime or "application/octet-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        try:
            with open(local_path, "rb") as f:
                self.wfile.write(f.read())
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    def _proxy_to_backend(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length > 0 else None
        try:
            parsed = urllib.parse.urlparse(self.backend_url)
            conn = http.client.HTTPConnection(parsed.hostname, parsed.port or 80, timeout=30)
            headers = {}
            for k, v in self.headers.items():
                kl = k.lower()
                if kl not in ("host", "connection", "transfer-encoding", "content-length"):
                    headers[k] = v
            if body:
                headers["Content-Length"] = str(len(body))
            conn.request(self.command, self.path, body=body, headers=headers)
            resp = conn.getresponse()
            self.send_response(resp.status)
            for k, v in resp.getheaders():
                if k.lower() not in ("transfer-encoding", "content-encoding", "connection"):
                    self.send_header(k, v)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(resp.read())
            conn.close()
        except Exception as e:
            self.send_error(502, f"Proxy error: {e}")

    def _proxy_ws(self):
        upgrade = self.headers.get("Upgrade", "").lower()
        if upgrade != "websocket":
            self.send_error(400, "Not a WebSocket upgrade")
            return

        try:
            parsed = urllib.parse.urlparse(self.backend_ws_url)
            host = parsed.hostname or "127.0.0.1"
            port = parsed.port or 80
            conn = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            conn.settimeout(30)
            conn.connect((host, port))
        except Exception as e:
            self.send_error(502, f"Cannot connect to backend: {e}")
            return

        key = self.headers.get("Sec-WebSocket-Key", "")
        accept = base64.b64encode(
            hashlib.sha1(key.encode() + self._ws_guid).digest()
        ).decode()

        upgrade_req = f"GET {self.path} HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n"
        for k in ("Origin", "Sec-WebSocket-Protocol"):
            v = self.headers.get(k)
            if v:
                upgrade_req += f"{k}: {v}\r\n"
        upgrade_req += "\r\n"

        conn.sendall(upgrade_req.encode())

        resp = b""
        t0 = time.time()
        while time.time() - t0 < 10:
            try:
                chunk = conn.recv(4096)
                resp += chunk
                if b"\r\n\r\n" in resp:
                    break
            except socket.timeout:
                break

        if b"101" not in resp:
            conn.close()
            self.send_error(502, f"Backend rejected WebSocket: {resp[:200]}")
            return

        # 关键修复：必须用 HTTP/1.1 响应浏览器，否则部分客户端（包括 websockets 库）会拒绝
        self.protocol_version = "HTTP/1.1"
        self.send_response(101)
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", accept)
        self.end_headers()
        self.wfile.flush()

        self._bridge_ws(conn)

    def _bridge_ws(self, backend_conn):
        client = self.connection
        client.settimeout(None)
        backend_conn.settimeout(None)

        try:
            while True:
                r, _, _ = select.select([client, backend_conn], [], [], 60)
                if not r:
                    break
                for s in r:
                    try:
                        data = s.recv(8192)
                        if not data:
                            return
                        if s is client:
                            backend_conn.sendall(data)
                        else:
                            client.sendall(data)
                    except (BlockingIOError, socket.timeout):
                        pass
                    except Exception:
                        return
        finally:
            try:
                backend_conn.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass
            backend_conn.close()

    def log_message(self, format, *args):
        print(f"[Frontend] {format % args}", flush=True)


class ThreadedHTTPServer(socketserver.ThreadingMixIn, HTTPServer):
    daemon_threads = True


def run(port: int, frontend_dir: str, backend_url: str, https_cert: str | None = None, https_key: str | None = None):
    SPAHandler._static_dir = frontend_dir
    SPAHandler.backend_url = backend_url
    parsed = urllib.parse.urlparse(backend_url)
    SPAHandler.backend_ws_url = f"ws://{parsed.hostname or '127.0.0.1'}:{parsed.port or 8020}"
    socketserver.TCPServer.allow_reuse_address = True
    httpd = ThreadedHTTPServer(("0.0.0.0", port), SPAHandler)

    scheme = "http"
    if https_cert and https_key:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(certfile=https_cert, keyfile=https_key)
        httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
        scheme = "https"

    print(f"[Server] Frontend: {scheme}://localhost:{port}")
    print(f"[Server] Backend proxy: {backend_url}")
    print(f"[Server] Static dir: {frontend_dir}")
    if scheme == "https":
        print(f"[Server] HTTPS enabled — getDisplayMedia/system audio will work on public IP")
    print(f"[Server] Serving... (Ctrl+C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[Server] Shutdown.")


def generate_self_signed_cert(cert_path: str, key_path: str, host: str = "0.0.0.0") -> bool:
    """生成自签名证书（有效期 10 年，自动续签）。"""
    from datetime import datetime, timezone, timedelta

    need_new = True
    if os.path.exists(cert_path) and os.path.exists(key_path):
        try:
            with open(cert_path, "rb") as f:
                cert_pem = f.read()
            from cryptography import x509  # type: ignore
            from cryptography.hazmat.backends import default_backend  # type: ignore
            cert = x509.load_pem_x509_certificate(cert_pem, default_backend())
            _not_after_utc = getattr(cert, "not_valid_after_utc", None)
            not_after = _not_after_utc if _not_after_utc is not None else cert.not_valid_after.replace(tzinfo=timezone.utc)
            days_left = (not_after - datetime.now(timezone.utc)).days
            if days_left > 30:
                print(f"[HTTPS] 使用现有证书（剩余 {days_left} 天）: {cert_path}")
                need_new = False
            else:
                print(f"[HTTPS] 证书即将过期（剩余 {days_left} 天），准备重新生成...")
        except Exception:
            need_new = True

    if not need_new:
        return True

    public_ip = ""
    try:
        import socket as sk2
        s = sk2.socket(sk2.AF_INET, sk2.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        public_ip = s.getsockname()[0]
        s.close()
    except Exception:
        pass

    san_entries = ["IP:127.0.0.1", "IP:0.0.0.0", "DNS:localhost", "DNS:*.local"]
    if public_ip:
        san_entries.insert(0, f"IP:{public_ip}")

    san_config = ",".join(san_entries)

    config_path = os.path.join(tempfile.gettempdir(), "smart_meeting_ssl.cnf")
    config_content = f"""[req]
default_bits       = 2048
prompt             = no
default_md         = sha256
distinguished_name = dn
x509_extensions    = v3_req

[dn]
CN = smart-meeting-ai-local

[v3_req]
subjectAltName = {san_config}
basicConstraints = CA:FALSE
keyUsage = critical, digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth
"""
    with open(config_path, "w") as f:
        f.write(config_content)

    # 备份旧证书（如果存在）
    if os.path.exists(cert_path) or os.path.exists(key_path):
        ts = datetime.now().strftime("%Y%m%d%H%M%S")
        for p in (cert_path, key_path):
            if os.path.exists(p):
                os.rename(p, f"{p}.bak.{ts}")

    print(f"[HTTPS] 生成自签名证书（SAN: {san_config}，有效期 10 年）...")
    try:
        result = subprocess.run([
            "openssl", "req", "-x509", "-newkey", "rsa:2048",
            "-keyout", key_path, "-out", cert_path,
            "-days", "3650", "-nodes",
            "-config", config_path,
        ], capture_output=True, text=True)
        if result.returncode != 0:
            print(f"[HTTPS] openssl 错误: {result.stderr}")
            return False
        print(f"[HTTPS] 证书已生成: {cert_path}")
        print(f"[HTTPS] 浏览器会提示「不安全」，点击「高级 → 继续访问」即可")
        return True
    except FileNotFoundError:
        print(f"[HTTPS] 未找到 openssl 命令，请安装 openssl")
        return False
    finally:
        try:
            os.unlink(config_path)
        except Exception:
            pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    script_dir = os.path.dirname(os.path.abspath(__file__))
    parser.add_argument("--port", type=int, default=5179)
    parser.add_argument("--frontend", default=os.path.join(script_dir, "dist"))
    parser.add_argument("--backend", default="http://localhost:8020")
    parser.add_argument("--https-cert", help="HTTPS 证书路径（启用 HTTPS，配合 --https-key 使用）")
    parser.add_argument("--https-key", help="HTTPS 私钥路径")
    parser.add_argument("--enable-https", action="store_true",
                        help="自动生成自签名证书并启用 HTTPS（getDisplayMedia 必需）")
    args = parser.parse_args()

    if args.enable_https and not (args.https_cert and args.https_key):
        cert_dir = os.path.join(script_dir, ".ssl")
        os.makedirs(cert_dir, exist_ok=True)
        args.https_cert = os.path.join(cert_dir, "cert.pem")
        args.https_key = os.path.join(cert_dir, "key.pem")
        if not generate_self_signed_cert(args.https_cert, args.https_key):
            print("[HTTPS] 回退到 HTTP 模式（getDisplayMedia 可能不可用）")
            args.https_cert = args.https_key = None

    run(args.port, args.frontend, args.backend, args.https_cert, args.https_key)
