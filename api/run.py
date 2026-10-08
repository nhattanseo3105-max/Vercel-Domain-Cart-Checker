import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, request, jsonify
from flask_cors import CORS
from core import process, result_summary, parse_original_list, parse_cart, compare, output_command_2

app = Flask(__name__)
CORS(app)

@app.route('/', defaults={'path': ''}, methods=['POST', 'OPTIONS'])
@app.route('/<path:path>', methods=['POST', 'OPTIONS'])
def run_command(path):
    if request.method == 'OPTIONS':
        return '', 204
        
    try:
        data = request.get_json() or {}
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
