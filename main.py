from flask import Flask, request, jsonify, send_from_directory
from stx_engine_v3_1 import STXConjunctionEngine
import os

# GET ABSOLUTE PATH
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__)

# Initialize engine with GREEN suppression option (set to True for real constellation ops)
engine = STXConjunctionEngine(suppress_green=False)

print(f"--- STX ORBITAL v3.1 PRODUCTION READY ---")
print(f"Root Directory: {BASE_DIR}")
print(f"Upload endpoint now fully functional - TLE file or NORAD IDs accepted")

# --- STATIC FILE ROUTES ---
@app.route('/')
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

@app.route('/<path:filename>')
def download_pdf(filename):
    if filename.endswith('.pdf'):
        return send_from_directory(BASE_DIR, filename)
    return "File not found", 404

# --- MAIN API ENDPOINT ---
@app.route('/screen', methods=['POST'])
def screen_fleet():
    # === AUTHENTICATION ===
    auth_header = request.headers.get('Authorization')
    if auth_header != 'Bearer stx-authorized-user':
        return jsonify({"error": "Unauthorized: Payment Required"}), 401

    # Get suppression setting
    suppress_green = request.form.get('suppress_green', 'false').lower() == 'true'
    active_engine = STXConjunctionEngine(suppress_green=suppress_green)

    asset_tle = None
    threat_tle = None
    primary_norad = None
    secondary_norad = None

    try:
        # === OPTION 1: UPLOADED TLE FILE (highest priority) ===
        if 'tle_file' in request.files:
            file = request.files['tle_file']
            if file.filename != '':
                content = file.read().decode('utf-8')
                lines = [line.strip() for line in content.splitlines() if line.strip()]

                tles = []
                names = []
                current_name = "Unknown"
                current_tle = []

                for line in lines:
                    if line.startswith('1 '):
                        if current_tle:  # save previous
                            tles.append(current_tle)
                            names.append(current_name)
                        current_tle = [line]
                        current_name = "Unknown"
                    elif line.startswith('2 '):
                        if current_tle:
                            current_tle.append(line)
                            tles.append(current_tle)
                            names.append(current_name)
                            current_tle = []
                            current_name = "Unknown"
                        else:
                            # malformed
                            pass
                    elif not line.startswith(('1 ', '2 ')):
                        current_name = line  # name line

                # don't forget last one
                if current_tle and len(current_tle) == 2:
                    tles.append(current_tle)
                    names.append(current_name)

                if len(tles) >= 2:
                    asset_tle = [names[0], tles[0][0], tles[0][1]]
                    threat_tle = [names[1], tles[1][0], tles[1][1]]

                    # extract NORAD IDs for proper profile detection
                    primary_norad = int(tles[0][0].split()[1][:-1])  # strip U
                    secondary_norad = int(tles[1][0].split()[1][:-1])

                    print(f">>> USING UPLOADED TLE FILE: {primary_norad} vs {secondary_norad}")
                else:
                    return jsonify({"error": "Uploaded file must contain at least two valid TLE sets"}), 400

        # === OPTION 2: NORAD IDs via form (fallback) ===
        if asset_tle is None:
            primary_id = request.form.get('primary_id')
            secondary_id = request.form.get('secondary_id')

            if primary_id and secondary_id:
                try:
                    primary_norad = int(primary_id)
                    secondary_norad = int(secondary_id)
                    print(f">>> USING FORM NORAD IDs: {primary_norad} vs {secondary_norad}")
                except:
                    return jsonify({"error": "Invalid NORAD ID format"}), 400
            else:
                # === FALLBACK TO DEMO (you can delete this block in full production) ===
                primary_norad = 25544
                secondary_norad = 48274
                print(">>> NO INPUT PROVIDED - FALLING BACK TO DEMO (ISS vs CSS)")

            asset_tle = active_engine.fetch_live_tle(primary_norad)
            threat_tle = active_engine.fetch_live_tle(secondary_norad)

            if not asset_tle or not threat_tle:
                return jsonify({"error": "Failed to fetch live TLEs"}), 500

        # === RUN SCREENING ===
        print(f">>> Processing {primary_norad or 'uploaded'} vs {secondary_norad or 'uploaded'} (suppress_green={suppress_green})...")
        telemetry = active_engine.screen_conjunction(
            asset_tle, threat_tle,
            primary_norad=primary_norad,
            secondary_norad=secondary_norad
        )

        if telemetry is None:
            return jsonify({
                "status": "suppressed",
                "message": "Event filtered (GREEN risk level)"
            })

        ai_decision = active_engine.generate_maneuver_plan(telemetry)
        pdf_filename = active_engine.generate_pdf_report(telemetry, ai_decision)

        # Format Pc display
        pc_display = "< 1e-10 (negligible)" if telemetry['pc'] < 1e-10 else f"{telemetry['pc']:.2e}"

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
            }],
            "decision": ai_decision,
            "profile": telemetry['profile'],
            "geometry": telemetry['geometry'],
            "has_ric_plot": telemetry.get('ric_plot') is not None
        }

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
    app.run(host='0.0.0.0', port=port, debug=False)  # debug=False for production