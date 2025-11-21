from flask import Flask, request, jsonify, send_from_directory
from stx_engine_v2 import STXConjunctionEngine
import os

# FORCE ABSOLUTE PATH (Fixes "Not Found" errors on Windows)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__, static_url_path='')
engine = STXConjunctionEngine()

# Print file list to debug (This will show up in your terminal when you run it)
print(f"Server running in: {BASE_DIR}")
print(f"Files detected: {os.listdir(BASE_DIR)}")

@app.route('/')
def root():
    # Explicitly serve index.html from the absolute path
    return send_from_directory(BASE_DIR, 'index.html')

@app.route('/<path:path>')
def static_files(path):
    return send_from_directory(BASE_DIR, path)

@app.route('/screen', methods=['POST'])
def screen_fleet():
    auth_header = request.headers.get('Authorization')
    if auth_header != 'Bearer stx-authorized-user':
        return jsonify({"error": "Unauthorized: Payment Required"}), 401

    asset_id = 25544  # ISS
    threat_id = 48274 # Tiangong

    try:
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
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    # Get the port from the environment variable (Railway sets this)
    # Default to 5000 only if running locally
    port = int(os.environ.get("PORT", 5000))
    
    # Listen on 0.0.0.0 (Required for Docker/Railway)
    app.run(host='0.0.0.0', port=port)