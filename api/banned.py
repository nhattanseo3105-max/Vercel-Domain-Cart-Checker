import json
from http.server import BaseHTTPRequestHandler
import sys
import os

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from core import load_banned_tlds

class handler(BaseHTTPRequestHandler):
    def _json(self, code: int, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        try:
            banned_tlds, uk_pure_banned = load_banned_tlds()
            self._json(
                200,
                {
                    "banned_tlds": {
                        k: sorted(list(v)) for k, v in banned_tlds.items()
                    },
                    "uk_pure_banned": uk_pure_banned,
                },
            )
        except Exception as e:
            self._json(500, {"error": str(e)})

    def do_POST(self):
        self._json(403, {"error": "Chế độ Vercel không cho phép lưu thay đổi. Vui lòng sửa trực tiếp trên GitHub."})

