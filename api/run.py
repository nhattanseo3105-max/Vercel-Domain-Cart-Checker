import json
from http.server import BaseHTTPRequestHandler
import sys
import os

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from core import process, result_summary, parse_original_list, parse_cart, compare, output_command_2

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

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            data = json.loads(raw.decode("utf-8") or "{}")

            original = str(data.get("original") or "")
            cart = str(data.get("cart") or "")
            command = str(data.get("command") or "")
            mention = str(data.get("mention") or "@Pii_S8_003")

            if command not in ("step1", "step2", "1", "2", "lechgia"):
                self._json(400, {"error": "Lệnh không hợp lệ"})
                return

            text = process(original, cart, command, mention)
            summary = None
            provider = None
            domains = None
            ds_import = None

            if command == "step1":
                groups = parse_original_list(original)
                if groups:
                    provider = groups[0].provider
                    domains = [d.domain for g in groups for d in g.domains]
            else:
                summary = result_summary(original, cart)
                if command in ("1", "2"):
                    groups = parse_original_list(original)
                    cart_obj = parse_cart(cart)
                    if groups and cart_obj.items:
                        result_obj = compare(groups, cart_obj)
                        ds_import = output_command_2(result_obj)

            self._json(
                200,
                {
                    "result": text,
                    "summary": summary,
                    "provider": provider,
                    "domains": domains,
                    "ds_import": ds_import,
                },
            )
        except Exception as e:
            self._json(500, {"error": str(e)})

