from flask import Flask, request, jsonify, send_from_directory
from stx_engine_v3_1 import STXConjunctionEngine
import os

# GET ABSOLUTE PATH
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__)

# Initialize engine with GREEN suppression option
# suppress_green=True means only YELLOW/RED events generate reports
engine = STXConjunctionEngine(suppress_green=False)  # Change to True for production high-volume ops

print(f"--- STX ORBITAL v3.1 LAUNCHED ---")
print(f"Root Directory: {BASE_DIR}")
print(f"Auto-Detection: ISS/Tiangong/Starlink/OneWeb/Kuiper")
print(f"Features: Pc calc | Relative velocity | RIC plots | Object type detection")

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

    # Get suppress_green parameter (for high-volume constellation ops)
    suppress_green = request.form.get('suppress_green', 'false').lower() == 'true'
    
    # Initialize engine with suppression setting
    active_engine = STXConjunctionEngine(suppress_green=suppress_green)

    # DEMO MODE: Always screen ISS vs Tiangong
    # NOTE: Engine will auto-detect ISS (25544) as "Manned Asset - High Caution"
    asset_id = 25544
    threat_id = 48274

    try:
        print(f">>> Processing Request (suppress_green={suppress_green})...")
        asset_tle = active_engine.fetch_live_tle(asset_id)
        threat_tle = active_engine.fetch_live_tle(threat_id)
        
        if not asset_tle or not threat_tle:
            return jsonify({"error": "Satellite Data Unavailable"}), 500

        telemetry = active_engine.screen_conjunction(asset_tle, threat_tle)
        
        # If GREEN suppression is on and event is suppressed
        if telemetry is None:
            return jsonify({
                "status": "suppressed",
                "message": "Event filtered (GREEN risk level, below alert thresholds)"
            })
        
        ai_decision = active_engine.generate_maneuver_plan(telemetry)
        pdf_filename = active_engine.generate_pdf_report(telemetry, ai_decision)
        
        # Format Pc for display
        if telemetry['pc'] < 1e-10:
            pc_display = "< 1e-10"
        else:
            pc_display = f"{telemetry['pc']:.2e}"
        
        response_data = {
            "status": "success",
            "risk_level": telemetry['risk_level'],
            "threats": [{
                "asset": telemetry['primary'],
                "intruder": telemetry['secondary'],
                "min_km": round(telemetry['min_dist_km'], 3),
                "relative_velocity_kms": round(telemetry['relative_velocity_kms'], 2),
                "pc": pc_display,
                "tca": telemetry['tca_utc'],
                "pdf_url": pdf_filename,
                "risk_level": telemetry['risk_level']
            }],
            "decision": ai_decision,
            "profile": telemetry['profile'],
            "profile_type": telemetry['profile_type'],
            "geometry": telemetry['geometry'],
            "has_ric_plot": telemetry.get('ric_plot') is not None
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
