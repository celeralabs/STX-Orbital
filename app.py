from flask import Flask, request, jsonify, send_from_directory
from stx_engine_v2 import STXConjunctionEngine
import os

# 1. GET ABSOLUTE PATH (Crucial for Windows)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__)
engine = STXConjunctionEngine()

print(f"--- SERVER LAUNCHED ---")
print(f"Root Directory: {BASE_DIR}")

# --- EXPLICIT FILE ROUTES (Guarantees files load) ---

@app.route('/')
def root():
    return send_from_directory(BASE_DIR, 'index.html')

@app.route('/index.html')
def home():
    return send_from_directory(BASE_DIR, 'index.html')

@app.route('/login.html')
def login_page():
    return send_from_directory(BASE_DIR, 'login.html')

@app.route('/dashboard.html')
def dashboard_page():
    return send_from_directory(BASE_DIR, 'dashboard.html')

@app.route('/style.css')
def serve_css():
    # Forces correct MIME type for CSS
    return send_from_directory(BASE_DIR, 'style.css', mimetype='text/css')

@app.route('/main.js')
def serve_js():
    # Forces correct MIME type for JS
    return send_from_directory(BASE_DIR, 'main.js', mimetype='text/javascript')

# --- PDF DOWNLOAD ROUTE ---
@app.route('/<path:filename>')
def download_pdf(filename):
    # This catches the generated PDF files (e.g., STX_Report_123456.pdf)
    if filename.endswith('.pdf'):
        return send_from_directory(BASE_DIR, filename)
    return "File not found", 404

# --- API ENDPOINT ---

@app.route('/screen', methods=['POST'])
def screen_fleet():
    auth_header = request.headers.get('Authorization')
    if auth_header != 'Bearer stx-authorized-user':
        return jsonify({"error": "Unauthorized: Payment Required"}), 401

    # DEMO MODE: Always screen ISS vs Tiangong
    asset_id = 25544
    threat_id = 48274

    try:
        print(">>> Processing Request...")
        asset_tle = engine.fetch_live_tle(asset_id)
        threat_tle = engine.fetch_live_tle(threat_id)
        
        if not asset_tle or not threat_tle:
            return jsonify({"error": "Satellite Data Unavailable"}), 500

        telemetry = engine.screen_conjunction(asset_tle, threat_tle)
        ai_decision = engine.generate_maneuver_plan(telemetry)
        pdf_filename = engine.generate_pdf_report(telemetry, ai_decision)
        
        return jsonify({
            "status": "success",
            "threats": [{
                "asset": telemetry['primary'],
                "intruder": telemetry['secondary'],
                "min_km": round(telemetry['min_dist_km'], 3),
                "tca": telemetry['tca_utc'],
                "pdf_url": pdf_filename
            }],
            "decision": ai_decision
        })
    except Exception as e:
        print(f"ERROR: {e}")
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(port=5000, debug=True)