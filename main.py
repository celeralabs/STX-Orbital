from flask import Flask, request, jsonify, send_from_directory
from stx_engine_v3 import STXConjunctionEngine
import os

# 1. GET ABSOLUTE PATH
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__)

# Initialize engines for different profiles
engines = {
    "ISS_CLASS": STXConjunctionEngine(profile="ISS_CLASS"),
    "COMMERCIAL": STXConjunctionEngine(profile="COMMERCIAL"),
    "CONSTELLATION": STXConjunctionEngine(profile="CONSTELLATION")
}

print(f"--- STX ORBITAL v3.0 LAUNCHED ---")
print(f"Root Directory: {BASE_DIR}")
print(f"Operational Profiles: ISS_CLASS, COMMERCIAL, CONSTELLATION")

# --- EXPLICIT FILE ROUTES ---

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
    return send_from_directory(BASE_DIR, 'style.css', mimetype='text/css')

@app.route('/main.js')
def serve_js():
    return send_from_directory(BASE_DIR, 'main.js', mimetype='text/javascript')

# --- PDF DOWNLOAD ROUTE ---
@app.route('/<path:filename>')
def download_pdf(filename):
    if filename.endswith('.pdf'):
        return send_from_directory(BASE_DIR, filename)
    return "File not found", 404

# --- API ENDPOINT ---
@app.route('/screen', methods=['POST'])
def screen_fleet():
    auth_header = request.headers.get('Authorization')
    if auth_header != 'Bearer stx-authorized-user':
        return jsonify({"error": "Unauthorized: Payment Required"}), 401

    # Get profile from request or default to COMMERCIAL
    profile = request.form.get('profile', 'COMMERCIAL')
    engine = engines.get(profile, engines['COMMERCIAL'])

    # DEMO MODE: Always screen ISS vs Tiangong
    asset_id = 25544
    threat_id = 48274

    try:
        print(f">>> Processing Request with {profile} profile...")
        asset_tle = engine.fetch_live_tle(asset_id)
        threat_tle = engine.fetch_live_tle(threat_id)
        
        if not asset_tle or not threat_tle:
            return jsonify({"error": "Satellite Data Unavailable"}), 500

        telemetry = engine.screen_conjunction(asset_tle, threat_tle)
        ai_decision = engine.generate_maneuver_plan(telemetry)
        pdf_filename = engine.generate_pdf_report(telemetry, ai_decision)
        
        response_data = {
            "status": "success",
            "risk_level": telemetry['risk_level'],
            "threats": [{
                "asset": telemetry['primary'],
                "intruder": telemetry['secondary'],
                "min_km": round(telemetry['min_dist_km'], 3),
                "pc": f"{telemetry['pc']:.2e}",
                "tca": telemetry['tca_utc'],
                "pdf_url": pdf_filename,
                "risk_level": telemetry['risk_level']
            }],
            "decision": ai_decision,
            "profile": telemetry['profile'],
            "geometry": telemetry['geometry']
        }
        
        # Add maneuver data if available
        if telemetry.get('maneuver'):
            response_data['maneuver'] = telemetry['maneuver']
        
        return jsonify(response_data)
        
    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
