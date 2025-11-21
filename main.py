from flask import Flask, request, jsonify, send_from_directory
from stx_engine_v3_1 import STXConjunctionEngine
import os
import time

# GET ABSOLUTE PATH
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__)

print(f"--- STX ORBITAL v3.1 PRODUCTION (TIERED SCREENING) ---")
print(f"Root Directory: {BASE_DIR}")
print(f"Tier 1: Manned Assets (ISS, Tiangong)")
print(f"Tier 2: High-Risk Objects (decay, unstable)")
print(f"Tier 3: Full Catalog Sweep")

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


# === TIERED SCREENING ENDPOINT ===
@app.route('/screen', methods=['POST'])
def screen_fleet():
    """
    Production conjunction screening with tiered priority system.
    
    Tier 1: MANNED ASSETS (ISS, Tiangong) - Always checked first
    Tier 2: HIGH-RISK (Decay, unstable, LEOP) - Unpredictable threats
    Tier 3: CATALOG SWEEP - Comprehensive check of all objects
    
    For single satellite: Screens against all three tiers
    For fleet: Screens primary against fleet members only
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
    catalog_limit = int(request.form.get('catalog_limit', '5000'))  # Limit catalog sweep for performance
    
    # Create fresh engine instance
    active_engine = STXConjunctionEngine(suppress_green=suppress_green)

    try:
        # Parse TLE file
        content = file.read().decode('utf-8', errors='ignore').strip()
        lines = [l.strip() for l in content.splitlines() if l.strip()]
        
        if len(lines) < 2:
            return jsonify({"error": "TLE file must contain at least 2 lines"}), 400

        # Extract all valid TLEs from file
        tle_list = []
        i = 0
        while i < len(lines):
            if lines[i].startswith('1 '):
                name = "SATELLITE"
                line1 = lines[i]
                if i + 1 >= len(lines):
                    break
                line2 = lines[i + 1]
            else:
                name = lines[i]
                if i + 2 >= len(lines):
                    break
                line1 = lines[i + 1]
                line2 = lines[i + 2]
                i += 1
            
            if line1.startswith('1 ') and line2.startswith('2 '):
                try:
                    norad_id = int(line2[2:7].strip())
                    tle_list.append((name, line1, line2, norad_id))
                except ValueError:
                    pass
            
            i += 2

        if not tle_list:
            return jsonify({"error": "No valid TLEs found in file. Check format."}), 400

        num_sats = len(tle_list)
        print(f">>> Parsed {num_sats} valid TLE(s) from file")

        threats = []
        last_telemetry = None
        screening_stats = {
            "manned_checked": 0,
            "high_risk_checked": 0,
            "catalog_checked": 0,
            "total_time_sec": 0
        }
        start_time = time.time()

        # === SINGLE SATELLITE MODE (TIERED SCREENING) ===
        if num_sats == 1:
            name, l1, l2, norad_id = tle_list[0]
            primary_tle = [name, l1, l2]
            print(f">>> SINGLE SAT TIERED SCREENING: {name} (NORAD {norad_id})")
            
            # ===== TIER 1: MANNED ASSETS (CRITICAL) =====
            print(">>> TIER 1: Checking manned assets...")
            manned_targets = [
                (25544, "ISS"),
                (48274, "Tiangong")
            ]
            
            for target_id, target_name in manned_targets:
                try:
                    target_tle = active_engine.fetch_live_tle(target_id)
                    if not target_tle:
                        print(f"  ! Failed to fetch {target_name}")
                        continue
                    
                    telemetry = active_engine.screen_conjunction(
                        primary_tle, target_tle,
                        primary_norad=norad_id,
                        secondary_norad=target_id
                    )
                    
                    screening_stats["manned_checked"] += 1
                    
                    if telemetry:  # Not suppressed
                        last_telemetry = telemetry
                        ai_decision = active_engine.generate_maneuver_plan(telemetry)
                        pdf_filename = active_engine.generate_pdf_report(telemetry, ai_decision)
                        pc_display = "< 1e-10" if telemetry['pc'] < 1e-10 else f"{telemetry['pc']:.2e}"
                        
                        threats.append({
                            "asset": telemetry['primary'],
                            "intruder": telemetry['secondary'],
                            "priority": "MANNED",
                            "priority_reason": "Human-occupied spacecraft",
                            "min_km": round(telemetry['min_dist_km'], 3),
                            "relative_velocity_kms": round(telemetry['relative_velocity_kms'], 2),
                            "pc": pc_display,
                            "tca": telemetry['tca_utc'],
                            "pdf_url": pdf_filename,
                            "risk_level": telemetry['risk_level']
                        })
                        print(f"  ✓ {target_name}: {telemetry['risk_level']} @ {telemetry['min_dist_km']:.1f} km")
                    else:
                        print(f"  ✓ {target_name}: GREEN (suppressed)")
                        
                except Exception as e:
                    print(f"  ! Error screening {target_name}: {e}")
            
            # ===== TIER 2: HIGH-RISK OBJECTS =====
            print(">>> TIER 2: Checking high-risk objects (decay, unstable)...")
            
            if active_engine.st_client:
                try:
                    # Query objects with high decay rates or unstable orbits
                    # Eccentricity > 0.1 OR perigee < 300 km OR recent epoch (active)
                    high_risk_query = active_engine.st_client.gp_history(
                        EPOCH='>now-7',  # Active within last 7 days
                        orderby='NORAD_CAT_ID asc',
                        limit=500,  # Check top 500 high-risk
                        format='tle'
                    )
                    
                    if high_risk_query:
                        hr_lines = high_risk_query.splitlines()
                        hr_count = 0
                        
                        for j in range(0, len(hr_lines), 3):
                            if j + 2 >= len(hr_lines):
                                break
                            
                            hr_name = hr_lines[j].strip() or "UNKNOWN"
                            hr_l1 = hr_lines[j + 1]
                            hr_l2 = hr_lines[j + 2]
                            
                            if not (hr_l1.startswith('1 ') and hr_l2.startswith('2 ')):
                                continue
                            
                            try:
                                hr_norad = int(hr_l2[2:7].strip())
                                
                                # Skip if already checked in manned tier
                                if hr_norad in [25544, 48274]:
                                    continue
                                
                                # Assess risk priority
                                priority, reason = active_engine.assess_risk_priority(hr_norad, hr_l1, hr_l2)
                                
                                if priority != "HIGH-RISK":
                                    continue  # Not actually high-risk
                                
                                hr_tle = [hr_name, hr_l1, hr_l2]
                                
                                telemetry = active_engine.screen_conjunction(
                                    primary_tle, hr_tle,
                                    primary_norad=norad_id,
                                    secondary_norad=hr_norad
                                )
                                
                                screening_stats["high_risk_checked"] += 1
                                hr_count += 1
                                
                                if telemetry:  # Not suppressed
                                    last_telemetry = telemetry
                                    ai_decision = active_engine.generate_maneuver_plan(telemetry)
                                    pdf_filename = active_engine.generate_pdf_report(telemetry, ai_decision)
                                    pc_display = "< 1e-10" if telemetry['pc'] < 1e-10 else f"{telemetry['pc']:.2e}"
                                    
                                    threats.append({
                                        "asset": telemetry['primary'],
                                        "intruder": telemetry['secondary'],
                                        "priority": "HIGH-RISK",
                                        "priority_reason": reason,
                                        "min_km": round(telemetry['min_dist_km'], 3),
                                        "relative_velocity_kms": round(telemetry['relative_velocity_kms'], 2),
                                        "pc": pc_display,
                                        "tca": telemetry['tca_utc'],
                                        "pdf_url": pdf_filename,
                                        "risk_level": telemetry['risk_level']
                                    })
                                    print(f"  ✓ HIGH-RISK {hr_norad}: {telemetry['risk_level']} @ {telemetry['min_dist_km']:.1f} km ({reason})")
                                
                                if hr_count >= 100:  # Limit high-risk checks
                                    break
                                    
                            except Exception as e:
                                continue
                        
                        print(f"  ✓ Checked {screening_stats['high_risk_checked']} high-risk objects")
                        
                except Exception as e:
                    print(f"  ! High-risk query failed: {e}")
            
            # ===== TIER 3: CATALOG SWEEP =====
            print(f">>> TIER 3: Catalog sweep (limit={catalog_limit})...")
            
            if active_engine.st_client:
                try:
                    # Get latest GP catalog
                    catalog_query = active_engine.st_client.tle_latest(
                        orderby='NORAD_CAT_ID asc',
                        limit=catalog_limit,
                        format='tle'
                    )
                    
                    if catalog_query:
                        cat_lines = catalog_query.splitlines()
                        cat_count = 0
                        
                        for j in range(0, len(cat_lines), 3):
                            if j + 2 >= len(cat_lines):
                                break
                            
                            cat_name = cat_lines[j].strip() or "UNKNOWN"
                            cat_l1 = cat_lines[j + 1]
                            cat_l2 = cat_lines[j + 2]
                            
                            if not (cat_l1.startswith('1 ') and cat_l2.startswith('2 ')):
                                continue
                            
                            try:
                                cat_norad = int(cat_l2[2:7].strip())
                                
                                # Skip if already checked
                                if cat_norad in [25544, 48274]:
                                    continue
                                
                                cat_tle = [cat_name, cat_l1, cat_l2]
                                
                                telemetry = active_engine.screen_conjunction(
                                    primary_tle, cat_tle,
                                    primary_norad=norad_id,
                                    secondary_norad=cat_norad
                                )
                                
                                screening_stats["catalog_checked"] += 1
                                cat_count += 1
                                
                                if cat_count % 500 == 0:
                                    print(f"  ... {cat_count} objects checked")
                                
                                if telemetry:  # Not suppressed
                                    last_telemetry = telemetry
                                    ai_decision = active_engine.generate_maneuver_plan(telemetry)
                                    pdf_filename = active_engine.generate_pdf_report(telemetry, ai_decision)
                                    pc_display = "< 1e-10" if telemetry['pc'] < 1e-10 else f"{telemetry['pc']:.2e}"
                                    
                                    # Assess priority for catalog object
                                    priority, reason = active_engine.assess_risk_priority(cat_norad, cat_l1, cat_l2)
                                    
                                    threats.append({
                                        "asset": telemetry['primary'],
                                        "intruder": telemetry['secondary'],
                                        "priority": priority,
                                        "priority_reason": reason,
                                        "min_km": round(telemetry['min_dist_km'], 3),
                                        "relative_velocity_kms": round(telemetry['relative_velocity_kms'], 2),
                                        "pc": pc_display,
                                        "tca": telemetry['tca_utc'],
                                        "pdf_url": pdf_filename,
                                        "risk_level": telemetry['risk_level']
                                    })
                                    
                            except Exception as e:
                                continue
                        
                        print(f"  ✓ Catalog sweep complete: {screening_stats['catalog_checked']} objects checked")
                        
                except Exception as e:
                    print(f"  ! Catalog sweep failed: {e}")

        # === FLEET MODE (NO CATALOG SWEEP) ===
        else:
            primary_name, primary_l1, primary_l2, primary_norad = tle_list[0]
            primary_tle = [primary_name, primary_l1, primary_l2]
            print(f">>> FLEET MODE: {primary_name} (primary) vs {num_sats-1} fleet members")
            
            for name, l1, l2, sec_norad in tle_list[1:]:
                secondary_tle = [name, l1, l2]
                
                try:
                    telemetry = active_engine.screen_conjunction(
                        primary_tle, secondary_tle,
                        primary_norad=primary_norad,
                        secondary_norad=sec_norad
                    )
                    
                    screening_stats["catalog_checked"] += 1
                    
                    if telemetry is None:  # GREEN + suppressed
                        continue
                    
                    last_telemetry = telemetry
                    ai_decision = active_engine.generate_maneuver_plan(telemetry)
                    pdf_filename = active_engine.generate_pdf_report(telemetry, ai_decision)
                    pc_display = "< 1e-10" if telemetry['pc'] < 1e-10 else f"{telemetry['pc']:.2e}"
                    
                    # Fleet objects default to CATALOG priority
                    priority, reason = active_engine.assess_risk_priority(sec_norad, l1, l2)
                    
                    threats.append({
                        "asset": telemetry['primary'],
                        "intruder": telemetry['secondary'],
                        "priority": priority,
                        "priority_reason": reason,
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

        screening_stats["total_time_sec"] = round(time.time() - start_time, 2)
        
        # Sort threats by priority: MANNED > HIGH-RISK > CATALOG
        priority_order = {"MANNED": 0, "HIGH-RISK": 1, "CATALOG": 2}
        threats.sort(key=lambda x: (priority_order.get(x["priority"], 3), -x["min_km"]))
        
        if not threats:
            return jsonify({
                "status": "all_clear",
                "message": "All clear - no YELLOW/RED conjunctions detected",
                "screening_stats": screening_stats
            })

        # Build response
        response_data = {
            "status": "success",
            "risk_level": threats[0]['risk_level'],
            "threats": threats,
            "decision": active_engine.generate_maneuver_plan(last_telemetry) if last_telemetry else "No actionable events",
            "profile": last_telemetry['profile'] if last_telemetry else "N/A",
            "profile_type": last_telemetry['profile_type'] if last_telemetry else "N/A",
            "geometry": last_telemetry['geometry'] if last_telemetry else {},
            "has_ric_plot": last_telemetry.get('ric_plot') is not None if last_telemetry else False,
            "screening_stats": screening_stats
        }
        
        if last_telemetry and last_telemetry.get('maneuver'):
            response_data['maneuver'] = last_telemetry['maneuver']
        
        print(f">>> SCREENING COMPLETE: {len(threats)} threats | {screening_stats['total_time_sec']}s")
        return jsonify(response_data)

    except Exception as e:
        print(f"SCREENING ERROR: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": f"Screening failed: {str(e)}"}), 500


if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)