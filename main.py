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

# --- API ENDPOINT (NOW SUPPORTS REAL TLE UPLOAD) ---
@app.route('/screen', methods=['POST'])
def screen_fleet():
    auth_header = request.headers.get('Authorization')
    if auth_header != 'Bearer stx-authorized-user':
        return jsonify({"error": "Unauthorized: Payment Required"}), 401

    # Get suppress_green parameter
    suppress_green = request.form.get('suppress_green', 'false').lower() == 'true'
    active_engine = STXConjunctionEngine(suppress_green=suppress_green)

    # Check if a TLE file was uploaded
    if 'file' not in request.files:
        return jsonify({"error": "No TLE file uploaded"}), 400
    
    file = request.files['file']
    if file.filename == '' or not file.filename.lower().endswith(('.tle', '.txt')):
        return jsonify({"error": "Invalid or missing TLE file"}), 400

    try:
        content = file.read().decode('utf-8').strip()
        lines = [l.strip() for l in content.splitlines() if l.strip()]

        # Support both 2-line and 3-line TLE formats
        if len(lines) < 2:
            return jsonify({"error": "TLE file must contain at least two lines"}), 400

        # First line may be satellite name (optional)
        name_line = lines[0] if not lines[0].startswith('1 ') else None
        tle_start = 1 if name_line else 0

        if (len(lines) - tle_start) % 2 != 0:
            return jsonify({"error": "Incomplete TLE pairs in file"}), 400

        threats = []
        last_telemetry = None  # To reuse for AI decision & PDF if needed

        for i in range(tle_start, len(lines), 2):
            line1 = lines[i]
            line2 = lines[i + 1]

            if not (line1.startswith('1 ') and line2.startswith('2 ')):
                continue  # Skip malformed lines

            norad_id = int(line2[2:7])
            print(f">>> Screening primary asset vs NORAD {norad_id}")

            # Primary asset TLE from uploaded file
            primary_tle = ["PRIMARY ASSET", line1, line2]
            if name_line and i == tle_start:
                primary_tle[0] = name_line

            secondary_tle = active_engine.fetch_live_tle(norad_id)
            if not secondary_tle:
                threats.append({
                    "asset": primary_tle[0],
                    "intruder": f"NORAD {norad_id} (data unavailable)",
                    "min_km": "N/A",
                    "pc": "N/A",
                    "tca": "N/A",
                    "risk_level": "UNKNOWN"
                })
                continue

            telemetry = active_engine.screen_conjunction(
                primary_tle, secondary_tle,
                primary_norad=None,
                secondary_norad=norad_id
            )

            if telemetry is None:  # GREEN and suppressed
                continue

            last_telemetry = telemetry
            ai_decision = active_engine.generate_maneuver_plan(telemetry)
            pdf_filename = active_engine.generate_pdf_report(telemetry, ai_decision)

            pc_display = "< 1e-10" if telemetry['pc'] < 1e-10 else f"{telemetry['pc']:.2e}"

            threats.append({
                "asset": telemetry['primary'],
                "intruder": telemetry['secondary'],
                "min_km": round(telemetry['min_dist_km'], 3),
                "relative_velocity_kms": round(telemetry['relative_velocity_kms'], 2),
                "pc": pc_display,
                "tca": telemetry['tca_utc'],
                "pdf_url": pdf_filename,
                "risk_level": telemetry['risk_level']
            })

        if not threats:
            return jsonify({
                "status": "suppressed",
                "message": "No actionable conjunctions found (all GREEN or below threshold)"
            })

        # Use the last high-interest telemetry for AI decision block
        response_data = {
            "status": "success",
            "risk_level": threats[0]['risk_level'],
            "threats": threats,
            "decision": active_engine.generate_maneuver_plan(last_telemetry),
            "profile": last_telemetry['profile'],
            "profile_type": last_telemetry['profile_type'],
            "geometry": last_telemetry['geometry'],
            "has_ric_plot": last_telemetry.get('ric_plot') is not None
        }

        if last_telemetry.get('maneuver'):
            response_data['maneuver'] = last_telemetry['maneuver']

        return jsonify(response_data)

    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)