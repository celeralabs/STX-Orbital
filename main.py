from flask import Flask, request, jsonify, send_from_directory
from stx_engine_v3_1 import STXConjunctionEngine
import os

# GET ABSOLUTE PATH
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__)

print(f"--- STX ORBITAL v3.1 PRODUCTION ---")
print(f"Root Directory: {BASE_DIR}")
print(f"Auto-Detection: ISS/Tiangong/Starlink/OneWeb/Kuiper")
print(f"Ready for operator workloads")

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


# === PRODUCTION /screen ENDPOINT ===
@app.route('/screen', methods=['POST'])
def screen_fleet():
    """
    Production conjunction screening endpoint.
    
    Requires TLE file upload with 1+ satellites.
    
    Modes:
    - SINGLE SAT (1 TLE): Screens against high-value assets (ISS/Tiangong)
    - DUAL SAT (2 TLEs): Screens sat-to-sat conjunction
    - FLEET (3+ TLEs): Screens primary vs fleet members
    """
    auth_header = request.headers.get('Authorization')
    if auth_header != 'Bearer stx-authorized-user':
        return jsonify({"error": "Unauthorized: Enterprise License Required"}), 401

    # Require file upload
    if 'file' not in request.files:
        return jsonify({"error": "No TLE file uploaded. Please select a file."}), 400
    
    file = request.files['file']
    if not file or file.filename == '':
        return jsonify({"error": "No file selected. Please upload a TLE file."}), 400
    
    if not file.filename.lower().endswith(('.tle', '.txt')):
        return jsonify({"error": "Invalid file type. Upload .tle or .txt file"}), 400

    # Get configuration parameters
    suppress_green = request.form.get('suppress_green', 'false').lower() == 'true'
    
    # Create fresh engine instance for this request
    active_engine = STXConjunctionEngine(suppress_green=suppress_green)

    try:
        # Parse TLE file
        content = file.read().decode('utf-8', errors='ignore').strip()
        lines = [l.strip() for l in content.splitlines() if l.strip()]
        
        if len(lines) < 2:
            return jsonify({"error": "TLE file must contain at least 2 lines (name + TLE lines 1 & 2)"}), 400

        # Extract all valid TLEs from file
        tle_list = []
        i = 0
        while i < len(lines):
            # Check if this line is a satellite name or TLE line 1
            if lines[i].startswith('1 '):
                # No name line, use generic name
                name = "SATELLITE"
                line1 = lines[i]
                if i + 1 >= len(lines):
                    break
                line2 = lines[i + 1]
            else:
                # Has name line
                name = lines[i]
                if i + 2 >= len(lines):
                    break
                line1 = lines[i + 1]
                line2 = lines[i + 2]
                i += 1
            
            # Validate TLE format
            if line1.startswith('1 ') and line2.startswith('2 '):
                try:
                    norad_id = int(line2[2:7].strip())
                    tle_list.append((name, line1, line2, norad_id))
                except ValueError:
                    # Invalid NORAD ID, skip this TLE
                    pass
            
            i += 2

        if not tle_list:
            return jsonify({"error": "No valid TLEs found in file. Check format."}), 400

        num_sats = len(tle_list)
        print(f">>> Parsed {num_sats} valid TLE(s) from file")

        threats = []
        last_telemetry = None

        # === SINGLE SATELLITE MODE ===
        # Screen uploaded satellite against high-value targets (ISS, Tiangong)
        if num_sats == 1:
            name, l1, l2, norad_id = tle_list[0]
            primary_tle = [name, l1, l2]
            print(f">>> SINGLE SAT: {name} (NORAD {norad_id}) screening vs ISS + Tiangong")
            
            # Screen against ISS (25544)
            try:
                iss_tle = active_engine.fetch_live_tle(25544)
                if iss_tle:
                    telemetry = active_engine.screen_conjunction(
                        primary_tle, iss_tle,
                        primary_norad=norad_id,
                        secondary_norad=25544
                    )
                    
                    if telemetry:  # Not suppressed
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
            except Exception as e:
                print(f"ISS screening error: {e}")
            
            # Screen against Tiangong (48274)
            try:
                tiangong_tle = active_engine.fetch_live_tle(48274)
                if tiangong_tle:
                    telemetry = active_engine.screen_conjunction(
                        primary_tle, tiangong_tle,
                        primary_norad=norad_id,
                        secondary_norad=48274
                    )
                    
                    if telemetry:  # Not suppressed
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
            except Exception as e:
                print(f"Tiangong screening error: {e}")
            
            if not threats:
                return jsonify({
                    "status": "all_clear",
                    "message": "All clear - no YELLOW/RED conjunctions detected in 7-day window"
                })

        # === FLEET MODE (2+ SATELLITES) ===
        # Screen primary (first TLE) against all other satellites in file
        else:
            primary_name, primary_l1, primary_l2, primary_norad = tle_list[0]
            primary_tle = [primary_name, primary_l1, primary_l2]
            print(f">>> FLEET MODE: {primary_name} (primary) vs {num_sats-1} fleet members")
            
            for name, l1, l2, norad_id in tle_list[1:]:
                secondary_tle = [name, l1, l2]
                
                try:
                    telemetry = active_engine.screen_conjunction(
                        primary_tle, secondary_tle,
                        primary_norad=primary_norad,
                        secondary_norad=norad_id
                    )
                    
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
                    
                except Exception as e:
                    print(f"Screening error for {name}: {e}")
                    continue
            
            if not threats:
                return jsonify({
                    "status": "all_clear",
                    "message": "All clear - no YELLOW/RED conjunctions detected within fleet"
                })

        # Build response with threat data
        response_data = {
            "status": "success",
            "risk_level": threats[0]['risk_level'],
            "threats": threats,
            "decision": active_engine.generate_maneuver_plan(last_telemetry) if last_telemetry else "No actionable events",
            "profile": last_telemetry['profile'] if last_telemetry else "N/A",
            "profile_type": last_telemetry['profile_type'] if last_telemetry else "N/A",
            "geometry": last_telemetry['geometry'] if last_telemetry else {},
            "has_ric_plot": last_telemetry.get('ric_plot') is not None if last_telemetry else False
        }
        
        if last_telemetry and last_telemetry.get('maneuver'):
            response_data['maneuver'] = last_telemetry['maneuver']
        
        return jsonify(response_data)

    except Exception as e:
        print(f"SCREENING ERROR: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": f"Screening failed: {str(e)}"}), 500


if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
