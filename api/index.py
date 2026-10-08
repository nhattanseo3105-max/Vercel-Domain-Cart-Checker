import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, request, jsonify
from flask_cors import CORS
from core import process, result_summary, parse_original_list, parse_cart, compare, output_command_2, load_banned_tlds

app = Flask(__name__)
CORS(app)

@app.route('/', defaults={'path': ''}, methods=['GET', 'POST', 'OPTIONS'])
@app.route('/<path:path>', methods=['GET', 'POST', 'OPTIONS'])
def catch_all(path):
    if request.method == 'OPTIONS':
        return '', 204

    actual_path = request.path.lower()

    if request.method == 'POST':
        data = request.get_json(silent=True) or {}
        if 'command' in data:
            return handle_run(data)
        else:
            return jsonify({"error": "Chế độ Vercel không cho phép lưu thay đổi cấu hình. Vui lòng sửa trực tiếp file banned_tlds.json trên kho lưu trữ GitHub."}), 403

    if request.method == 'GET':
        # Phân biệt route tĩnh (đề phòng Vercel forward root path vào Flask)
        if 'banned' in actual_path or path.endswith('banned'):
            return handle_banned_get()
        else:
            # Phục vụ file giao diện index.html
            html_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'index.html')
            try:
                with open(html_path, 'r', encoding='utf-8') as f:
                    return f.read(), 200, {'Content-Type': 'text/html; charset=utf-8'}
            except Exception as e:
                return jsonify({"error": "Cannot load index.html"}), 500

    return jsonify({"error": "Method not allowed"}), 405

def handle_run(data):
    try:
        original = str(data.get("original", ""))
        cart = str(data.get("cart", ""))
        command = str(data.get("command", ""))
        mention = str(data.get("mention", "@Pii_S8_003"))

        if command not in ("step1", "step2", "1", "2", "lechgia"):
            return jsonify({"error": "Lệnh không hợp lệ"}), 400

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

        return jsonify({
            "result": text,
            "summary": summary,
            "provider": provider,
            "domains": domains,
            "ds_import": ds_import,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

def handle_banned_get():
    try:
        banned_tlds, uk_pure_banned = load_banned_tlds()
        return jsonify({
            "banned_tlds": {
                k: sorted(list(v)) for k, v in banned_tlds.items()
            },
            "uk_pure_banned": uk_pure_banned,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500
