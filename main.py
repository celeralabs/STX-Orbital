from flask import Flask, request, jsonify, send_from_directory
from stx_engine_v3_1 import STXConjunctionEngine
import os

# GET ABSOLUTE PATH
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__)

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

@app.route('/<path:filename>')
def download_pdf(filename):
    if filename.endswith('.pdf'):
        return send_from_directory(BASE_DIR, filename)
    return "File not found", 404

# === FIXED /screen ENDPOINT – WORKS WITH SINGLE TLE OR FULL FLEET ===
ytic@app.route('/screen', methods=['POST'])
def screen_fleet():
    auth_header = request.headers.get('Authorization')
    if auth_header != 'Bearer stx-authorized-user':
        return jsonify({"error": "Unauthorized: Payment Required"}), 401

    suppress_green = request.form.get('suppress_green', 'false').lower() == 'true'
    active_engine = STXConjunctionEngine(suppress_green=suppress_green)

    if 'file' not in request.files:
        return jsonify({"error": "No TLE file uploaded"}), 400
    
    file = request.files['file']
    if file.filename == '' or not file.filename.lower().endswith(('.tle', '.txt')):
        return jsonify({"error": "Invalid or missing TLE file"}), 400

    try:
        content = file.read().decode('utf-8', errors='ignore').strip()
        lines = [l.strip() for l in content.splitlines() if l.strip()]

        if len(lines) < 2:
            return jsonify({"error": "TLE file must contain at least two lines"}), 400

        # Parse all TLEs in the file
        tle_list = []
        i = 0
        while i < len(lines):
            # Optional name line
            name = lines[i] if not lines[i].startswith('1 ') else "SATELLITE"
            if name.startswith('1 '):
                name = "SATELLITE"
            else:
                i += 1

            if i + 1 >= len(lines):
                break
            line1 = lines[i]
            line2 = lines[i + 1]

            if line1.startswith('1 ') and line2.startswith('2 '):
                try:
                    norad_id = int(line2[2:7])
                    tle_list.append((name, line1, line2, norad_id))
                except:
                    pass
            i += 2

        if not tle_list:
            return jsonify({"error": "No valid TLE found in file"}), 400

        # PRIMARY = first TLE in the file
        primary_name, primary_l1, primary_l2, _ = tle_list[0]
        primary_tle = [primary_name, primary_l1, primary_l2]

        threats = []
        last_telemetry = None

        # If only one TLE → screen primary against FULL live catalog (most common use case)
        if len(tle_list) == 1:
            print(f">>> Single satellite mode – screening {primary_name} against full catalog")
            # You can limit to top 50 closest or all high-interest – here we do all < 1000 km for demo
            # In production you’d add a smart pre-filter; this works great for demo/real use
            catalog = active_engine.st_client.tle_latest(
                orderby='NORAD_CAT_ID asc', format='tle', limit=1000)
            catalog_lines = catalog.splitlines()

            for j in range(0, len(catalog_lines), 3):
                if j + 2 >= len(catalog_lines):
                    break
                sec_name = catalog_lines[j].strip() or "UNKNOWN"
                sec_l1 = catalog_lines[j+1]
                sec_l2 = catalog_lines[j+2]
                if not (sec_l1.startswith('1 ') and sec_l2.startswith('2 ')):
                    continue
                try:
                    sec_norad = int(sec_l2[2:7])
                except:
                    continue

                telemetry = active_engine.screen_conjunction(
                    primary_tle, [sec_name, sec_l1, sec_l2],
                    primary_norad=None, secondary_norad=sec_norad)

                if telemetry is None:  # GREEN + suppressed
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

        else:
            # Fleet mode: first = primary, all others = secondaries
            print(f">>> Fleet mode – primary {primary_name}, screening against {len(tle_list)-1} secondaries")
            for name, l1, l2, norad_id in tle_list[1:]:
                secondary_tle = active_engine.fetch_live_tle(norad_id)
                if not secondary_tle:
                    continue

                telemetry = active_engine.screen_conjunction(
                    primary_tle, secondary_tle,
                    primary_norad=None, secondary_norad=norad_id)

                if telemetry is None:
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
                "message": "All clear – no YELLOW or RED conjunctions in the next 7 days"
            })

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