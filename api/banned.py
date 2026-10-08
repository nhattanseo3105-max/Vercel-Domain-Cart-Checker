import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, request, jsonify
from flask_cors import CORS
from core import load_banned_tlds

app = Flask(__name__)
CORS(app)

@app.route('/', defaults={'path': ''}, methods=['GET', 'POST', 'OPTIONS'])
@app.route('/<path:path>', methods=['GET', 'POST', 'OPTIONS'])
def banned_handler(path):
    if request.method == 'OPTIONS':
        return '', 204

    if request.method == 'GET':
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
    elif request.method == 'POST':
        return jsonify({"error": "Chế độ Vercel không cho phép lưu thay đổi. Vui lòng sửa trực tiếp trên GitHub."}), 403
