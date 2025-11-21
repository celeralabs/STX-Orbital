from flask import Flask, request, jsonify, send_from_directory
import os
import time
import traceback as tb
import threading
import uuid

# GET ABSOLUTE PATH
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__)

print(f"--- STX ORBITAL v3.1 BULLETPROOF ---")
print(f"Root Directory: {BASE_DIR}")

# Import engine with error handling
try:
    from stx_engine_v3_1 import STXConjunctionEngine
    print("✓ Engine imported successfully")
except Exception as e:
    print(f"✗ ENGINE IMPORT FAILED: {e}")
    tb.print_exc()
    STXConjunctionEngine = None

# -----------------------------
# Simple in-memory job storage
# -----------------------------
JOBS = {}
JOBS_LOCK = threading.Lock()


def create_job():
    job_id = str(uuid.uuid4())
    with JOBS_LOCK:
        JOBS[job_id] = {
            "status": "queued",    # queued | running | success | all_clear | failed
            "result": None,
            "error": None,
            "created_at": time.time()
        }
    return job_id


def set_job_running(job_id):
    with JOBS_LOCK:
        if job_id in JOBS:
            JOBS[job_id]["status"] = "running"


def set_job_result(job_id, status, result_dict):
    with JOBS_LOCK:
        if job_id in JOBS:
            JOBS[job_id]["status"] = status  # "success" or "all_clear"
            JOBS[job_id]["result"] = result_dict


def set_job_failed(job_id, error_msg):
    with JOBS_LOCK:
        if job_id in JOBS:
            JOBS[job_id]["status"] = "failed"
            JOBS[job_id]["error"] = error_msg


# -----------------------------
# Core screening logic
# -----------------------------
def perform_screening(content, suppress_green=False, catalog_limit=5000):
    """
    Run the full tiered conjunction screening.

    Args:
        content (str): Raw TLE file content.
        suppress_green (bool): Whether to suppress GREEN events.
        catalog_limit (int): Limit for catalog sweep.

    Returns:
        dict: Response payload with either:
              - { "status": "all_clear", ... }
              - { "status": "success", ... }

    Raises:
        Exception on failure (caught by job wrapper).
    """
    if STXConjunctionEngine is None:
        raise RuntimeError("Engine module failed to load - check server logs")

    # Initialize engine
    try:
        active_engine = STXConjunctionEngine(suppress_green=suppress_green)
        print("✓ Engine initialized")
    except Exception as e:
        print(f"✗ Engine init failed: {e}")
        tb.print_exc()
        raise RuntimeError(f"Engine initialization failed: {str(e)}")

    # Parse TLE file
    try:
        lines = [l.strip() for l in content.splitlines() if l.strip()]

        if len(lines) < 2:
            raise ValueError("File too short")

        # Extract TLEs
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
                except Exception:
                    pass

            i += 2

        if not tle_list:
            raise ValueError("No valid TLEs found")

        print(f"✓ Parsed {len(tle_list)} TLE(s)")

    except Exception as e:
        print(f"✗ TLE parsing failed: {e}")
        tb.print_exc()
        raise

    # Start screening
    threats = []
    last_telemetry = None
    stats = {
        "manned_checked": 0,
        "high_risk_checked": 0,
        "catalog_checked": 0,
        "total_time_sec": 0
    }
    start_time = time.time()

    # === SINGLE SATELLITE MODE ===
    if len(tle_list) == 1:
        name, l1, l2, norad_id = tle_list[0]
        primary_tle = [name, l1, l2]
        print(f">>> SINGLE SAT MODE: {name} (NORAD {norad_id})")

        # TIER 1: Manned assets
        print(">>> Tier 1: Manned assets")
        for target_id, target_name in [(25544, "ISS"), (48274, "Tiangong")]:
            try:
                target_tle = active_engine.fetch_live_tle(target_id)
                if not target_tle:
                    print(f"  ! {target_name}: Fetch failed")
                    continue

                telemetry = active_engine.screen_conjunction(
                    primary_tle, target_tle,
                    primary_norad=norad_id,
                    secondary_norad=target_id
                )

                stats["manned_checked"] += 1

                if telemetry:
                    last_telemetry = telemetry
                    try:
                        ai_decision = active_engine.generate_maneuver_plan(telemetry)
                        pdf_filename = active_engine.generate_pdf_report(telemetry, ai_decision)
                    except Exception as e:
                        print(f"  ! PDF generation failed: {e}")
                        pdf_filename = "report_generation_failed.pdf"

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
                print(f"  ! {target_name} error: {e}")
                tb.print_exc()

        # TIER 2/3: Catalog sweep
        print(f">>> Tier 2/3: Catalog sweep (limit={catalog_limit})")

        if active_engine.st_client:
            try:
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

                        try:
                            cat_name = cat_lines[j].strip() or "UNKNOWN"
                            cat_l1 = cat_lines[j + 1]
                            cat_l2 = cat_lines[j + 2]

                            if not (cat_l1.startswith('1 ') and cat_l2.startswith('2 ')):
                                continue

                            cat_norad = int(cat_l2[2:7].strip())

                            # Skip manned
                            if cat_norad in [25544, 48274]:
                                continue

                            cat_tle = [cat_name, cat_l1, cat_l2]

                            telemetry = active_engine.screen_conjunction(
                                primary_tle, cat_tle,
                                primary_norad=norad_id,
                                secondary_norad=cat_norad
                            )

                            stats["catalog_checked"] += 1
                            cat_count += 1

                            if cat_count % 1000 == 0:
                                print(f"  ... {cat_count} checked")

                            if telemetry:
                                last_telemetry = telemetry

                                try:
                                    ai_decision = active_engine.generate_maneuver_plan(telemetry)
                                    pdf_filename = active_engine.generate_pdf_report(telemetry, ai_decision)
                                except Exception as e:
                                    print(f"  ! PDF gen failed for {cat_norad}: {e}")
                                    pdf_filename = "report_generation_failed.pdf"

                                pc_display = "< 1e-10" if telemetry['pc'] < 1e-10 else f"{telemetry['pc']:.2e}"

                                # Assess priority
                                try:
                                    priority, reason = active_engine.assess_risk_priority(cat_norad, cat_l1, cat_l2)
                                    if priority == "HIGH-RISK":
                                        stats["high_risk_checked"] += 1
                                except Exception as e:
                                    print(f"  ! Risk assessment failed for {cat_norad}: {e}")
                                    priority, reason = ("CATALOG", "Standard catalog object")

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

                        except Exception:
                            # Silent fail for individual catalog objects
                            continue

                    print(f"  ✓ Complete: {stats['catalog_checked']} checked, {stats['high_risk_checked']} high-risk")

            except Exception as e:
                print(f"  ! Catalog sweep failed: {e}")
                tb.print_exc()
        else:
            print("  ! Space-Track client not available")

    # === FLEET MODE ===
    else:
        print(f">>> FLEET MODE: {len(tle_list)} satellites")
        primary_name, primary_l1, primary_l2, primary_norad = tle_list[0]
        primary_tle = [primary_name, primary_l1, primary_l2]

        for name, l1, l2, sec_norad in tle_list[1:]:
            try:
                secondary_tle = [name, l1, l2]

                telemetry = active_engine.screen_conjunction(
                    primary_tle, secondary_tle,
                    primary_norad=primary_norad,
                    secondary_norad=sec_norad
                )

                stats["catalog_checked"] += 1

                if telemetry:
                    last_telemetry = telemetry
                    ai_decision = active_engine.generate_maneuver_plan(telemetry)
                    pdf_filename = active_engine.generate_pdf_report(telemetry, ai_decision)
                    pc_display = "< 1e-10" if telemetry['pc'] < 1e-10 else f"{telemetry['pc']:.2e}"

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
                print(f"  ! Fleet member {name} error: {e}")

    stats["total_time_sec"] = round(time.time() - start_time, 2)

    # Sort by priority
    priority_order = {"MANNED": 0, "HIGH-RISK": 1, "CATALOG": 2}
    threats.sort(key=lambda x: (priority_order.get(x["priority"], 3), -x.get("min_km", 9999)))

    # No threats
    if not threats:
        print(f"✓ All clear ({stats['total_time_sec']}s)")
        return {
            "status": "all_clear",
            "message": "All clear - no YELLOW/RED conjunctions detected",
            "screening_stats": stats
        }

    # Build response
    print(f"✓ {len(threats)} threats found ({stats['total_time_sec']}s)")

    response_data = {
        "status": "success",
        "risk_level": threats[0].get('risk_level', 'UNKNOWN'),
        "threats": threats,
        "decision": (
            active_engine.generate_maneuver_plan(last_telemetry)
            if last_telemetry else "No actionable events"
        ),
        "profile": last_telemetry.get('profile', 'N/A') if last_telemetry else "N/A",
        "profile_type": last_telemetry.get('profile_type', 'N/A') if last_telemetry else "N/A",
        "geometry": last_telemetry.get('geometry', {}) if last_telemetry else {},
        "has_ric_plot": last_telemetry.get('ric_plot') is not None if last_telemetry else False,
        "screening_stats": stats
    }

    if last_telemetry and last_telemetry.get('maneuver'):
        response_data['maneuver'] = last_telemetry['maneuver']

    return response_data


def run_screen_job(job_id, content, suppress_green, catalog_limit):
    """
    Executes the actual conjunction screening in a background thread.
    """
    try:
        set_job_running(job_id)
        response_data = perform_screening(content, suppress_green, catalog_limit)

        status = response_data.get("status", "success")
        set_job_result(job_id, status, response_data)

    except Exception as e:
        err_msg = f"{type(e).__name__}: {str(e)}"
        print(f"\n=== JOB ERROR ({job_id}) ===")
        print(err_msg)
        tb.print_exc()
        print(f"=== END JOB ERROR ===\n")
        set_job_failed(job_id, err_msg)


# --- FILE ROUTES ---
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


# === ASYNC SCREENING ENDPOINTS ===
@app.route('/screen', methods=['POST'])
def screen_fleet():
    """
    Job-submission endpoint: validates input and queues background screening.
    """
    try:
        # Check if engine loaded
        if STXConjunctionEngine is None:
            return jsonify({"error": "Engine module failed to load - check server logs"}), 500

        # Auth check
        auth_header = request.headers.get('Authorization')
        if auth_header != 'Bearer stx-authorized-user':
            return jsonify({"error": "Unauthorized"}), 401

        # File validation
        if 'file' not in request.files:
            return jsonify({"error": "No file uploaded"}), 400

        file = request.files['file']
        if not file or file.filename == '':
            return jsonify({"error": "No file selected"}), 400

        if not file.filename.lower().endswith(('.tle', '.txt')):
            return jsonify({"error": "Invalid file type"}), 400

        # Config
        suppress_green = request.form.get('suppress_green', 'false').lower() == 'true'
        catalog_limit = int(request.form.get('catalog_limit', '5000'))

        print(f"\n=== NEW REQUEST ===")
        print(f"File: {file.filename}")
        print(f"Suppress GREEN: {suppress_green}")
        print(f"Catalog limit: {catalog_limit}")

        # Read content once
        content = file.read().decode('utf-8', errors='ignore').strip()
        if not content:
            return jsonify({"error": "Uploaded file is empty"}), 400

        # Create job and spawn background worker thread
        job_id = create_job()
        thread = threading.Thread(
            target=run_screen_job,
            args=(job_id, content, suppress_green, catalog_limit),
            daemon=True
        )
        thread.start()

        # Immediate response (job queued)
        return jsonify({
            "status": "queued",
            "job_id": job_id
        })

    except Exception as e:
        print(f"\n=== CRITICAL ERROR (submission) ===")
        print(f"Error: {e}")
        print(f"Type: {type(e).__name__}")
        tb.print_exc()
        print(f"=== END ERROR ===\n")

        return jsonify({
            "error": f"Job submission failed: {str(e)}",
            "error_type": type(e).__name__,
            "message": "Check server logs for details"
        }), 500


@app.route('/screen_status/<job_id>', methods=['GET'])
def screen_status(job_id):
    """
    Poll endpoint for job status and result.
    """
    with JOBS_LOCK:
        job = JOBS.get(job_id)

    if not job:
        return jsonify({"error": "Unknown job_id"}), 404

    status = job["status"]

    if status in ("queued", "running"):
        return jsonify({"status": status})

    if status in ("all_clear", "success"):
        return jsonify(job["result"])

    if status == "failed":
        return jsonify({
            "status": "failed",
            "error": job["error"] or "Unknown error"
        }), 500

    # Fallback (shouldn't happen)
    return jsonify({"status": status})


if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
