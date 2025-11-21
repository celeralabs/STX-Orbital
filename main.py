from flask import Flask, request, jsonify, send_from_directory
import os
import time
import traceback as tb
import threading
import uuid
from fpdf import FPDF

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
# In-memory job storage
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
        catalog_limit (int): Limit on number of catalog candidates
                             that receive full high-fidelity screening.

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
            # Case 1: line starts with '1 ' -> no name line
            if lines[i].startswith('1 '):
                name = "SATELLITE"
                line1 = lines[i]
                if i + 1 >= len(lines):
                    break
                line2 = lines[i + 1]
            else:
                # Case 2: name line followed by line1/line2
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
        "total_time_sec": 0.0
    }
    start_time = time.time()

    # === SINGLE SATELLITE MODE ===
    if len(tle_list) == 1:
        name, l1, l2, norad_id = tle_list[0]
        primary_tle = [name, l1, l2]
        print(f">>> SINGLE SAT MODE: {name} (NORAD {norad_id})")

        # ------------------------------------------------------------------
        # TIER 1: Manned assets (ISS, Tiangong)
        # ------------------------------------------------------------------
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
                        "pdf_url": None,               # per-event PDF handled later
                        "risk_level": telemetry['risk_level'],
                        "telemetry": telemetry         # stash full telemetry
                    })
                    print(f"  ✓ {target_name}: {telemetry['risk_level']} @ {telemetry['min_dist_km']:.1f} km")
                else:
                    print(f"  ✓ {target_name}: GREEN (suppressed)")

            except Exception as e:
                print(f"  ! {target_name} error: {e}")
                tb.print_exc()

        # ------------------------------------------------------------------
        # TIER 2/3: Full catalog sweep via staged pipeline
        # ------------------------------------------------------------------
        print(f">>> Tier 2/3: Catalog sweep (pipeline, catalog_limit={catalog_limit})")

        try:
            # Ask engine for candidates using full catalog + prefilters
            candidates = active_engine.get_catalog_candidates_for_primary(
                primary_tle,
                primary_norad=norad_id,
                days=7,
                coarse_points=300,
                coarse_distance_km=80.0,
                altitude_margin_km=150.0,
                inc_margin_deg=30.0,
            )

            # Optional: honor catalog_limit as a safety cap on high-fidelity passes
            if catalog_limit and catalog_limit > 0:
                candidates = candidates[:catalog_limit]

            print(f"  ✓ Pipeline produced {len(candidates)} candidate objects for full screening")

            for ci, info in enumerate(candidates, start=1):
                cat_norad = info["norad_id"]

                # Skip manned (already handled in Tier 1)
                if cat_norad in (25544, 48274):
                    continue

                cat_name = info["name"] or f"RSO {cat_norad}"
                cat_l1 = info["l1"]
                cat_l2 = info["l2"]
                cat_tle = [cat_name, cat_l1, cat_l2]

                try:
                    telemetry = active_engine.screen_conjunction(
                        primary_tle, cat_tle,
                        primary_norad=norad_id,
                        secondary_norad=cat_norad
                    )

                    stats["catalog_checked"] += 1

                    if telemetry:
                        last_telemetry = telemetry
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
                            "pdf_url": None,              # per-event PDF handled later
                            "risk_level": telemetry['risk_level'],
                            "telemetry": telemetry        # stash full telemetry
                        })

                except Exception:
                    # Silent fail per-object; you can log more here if desired
                    continue

            print(f"  ✓ Complete: {stats['catalog_checked']} fully screened, {stats['high_risk_checked']} high-risk")

        except Exception as e:
            print(f"  ! Catalog sweep pipeline failed: {e}")
            tb.print_exc()

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
                        "pdf_url": None,               # per-event PDF handled later
                        "risk_level": telemetry['risk_level'],
                        "telemetry": telemetry         # stash full telemetry
                    })
            except Exception as e:
                print(f"  ! Fleet member {name} error: {e}")
                tb.print_exc()

    # --- Finalize stats and response ---
    stats["total_time_sec"] = round(time.time() - start_time, 2)

    # Sort threats by priority then miss distance
    priority_order = {"MANNED": 0, "HIGH-RISK": 1, "CATALOG": 2}
    threats.sort(key=lambda x: (priority_order.get(x["priority"], 3), x.get("min_km", 9999)))

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

    # Determine top threat for detailed PDF / AI decision
    top_idx = None
    for idx, t in enumerate(threats):
        if t.get("risk_level") in ("RED", "YELLOW"):
            top_idx = idx
            break
    if top_idx is None:
        top_idx = 0  # all GREEN; use closest for context, but no event PDF by default

    top_threat = threats[top_idx]
    top_telemetry = top_threat.get("telemetry")
    pdf_filename = None
    decision_text = "No actionable events"

    if top_telemetry:
        try:
            decision_text = active_engine.generate_maneuver_plan(top_telemetry)
        except Exception as e:
            print(f"  ! AI decision generation failed: {e}")
            decision_text = "AI decision unavailable. Use telemetry only."

        # Only generate per-event PDF if this is YELLOW/RED
        if top_threat.get("risk_level") in ("RED", "YELLOW"):
            try:
                pdf_filename = active_engine.generate_pdf_report(top_telemetry, decision_text)
                threats[top_idx]["pdf_url"] = pdf_filename
            except Exception as e:
                print(f"  ! PDF generation failed for top threat: {e}")
                pdf_filename = "report_generation_failed.pdf"
                threats[top_idx]["pdf_url"] = pdf_filename

    # Build response payload
    response_data = {
        "status": "success",
        "risk_level": threats[0].get('risk_level', 'UNKNOWN'),
        "threats": threats,
        "decision": decision_text,
        "profile": top_telemetry.get('profile', 'N/A') if top_telemetry else "N/A",
        "profile_type": top_telemetry.get('profile_type', 'N/A') if top_telemetry else "N/A",
        "geometry": top_telemetry.get('geometry', {}) if top_telemetry else {},
        "has_ric_plot": top_telemetry.get('ric_plot') is not None if top_telemetry else False,
        "screening_stats": stats
    }

    if top_telemetry and top_telemetry.get('maneuver'):
        response_data['maneuver'] = top_telemetry['maneuver']

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


@app.route('/summary_pdf/<job_id>', methods=['GET'])
def summary_pdf(job_id):
    """
    Generate a consolidated summary PDF for a completed screening job.
    Includes all conjunction events (GREEN/YELLOW/RED).
    """
    with JOBS_LOCK:
        job = JOBS.get(job_id)

    if not job:
        return jsonify({"error": "Unknown job_id"}), 404

    if job["status"] not in ("all_clear", "success"):
        return jsonify({"error": "Job not complete"}), 400

    result = job.get("result") or {}
    threats = result.get("threats", [])
    stats = result.get("screening_stats", {})

    # Build summary PDF
    try:
        pdf = FPDF()
        pdf.add_page()

        pdf.set_font("Arial", "B", 16)
        pdf.cell(0, 10, "STX ORBITAL // SCREENING SUMMARY", 0, 1, "C")
        pdf.set_font("Arial", "I", 10)
        pdf.cell(0, 8, f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}", 0, 1, "C")
        pdf.ln(4)

        # Stats block
        pdf.set_font("Arial", "B", 12)
        pdf.cell(0, 8, "SCREENING STATISTICS", 0, 1)
        pdf.set_font("Courier", "", 9)
        pdf.cell(0, 5, f"Manned Assets Checked:   {stats.get('manned_checked', 0)}", 0, 1)
        pdf.cell(0, 5, f"High-Risk Objects:       {stats.get('high_risk_checked', 0)}", 0, 1)
        pdf.cell(0, 5, f"Catalog Objects:         {stats.get('catalog_checked', 0)}", 0, 1)
        pdf.cell(0, 5, f"Total Time:              {stats.get('total_time_sec', 0)} s", 0, 1)

        pdf.ln(4)
        pdf.set_font("Arial", "B", 12)
        pdf.cell(0, 8, "CONJUNCTION EVENTS", 0, 1)
        pdf.set_font("Courier", "", 9)

        if not threats:
            pdf.cell(0, 5, "No conjunction events reported (all clear).", 0, 1)
        else:
            for t in threats:
                pdf.ln(2)
                pdf.cell(0, 5, f"ASSET:     {t.get('asset', 'N/A')}", 0, 1)
                pdf.cell(0, 5, f"INTRUDER:  {t.get('intruder', 'N/A')}", 0, 1)
                pdf.cell(0, 5, f"PRIORITY:  {t.get('priority', 'N/A')} ({t.get('priority_reason', '')})", 0, 1)
                pdf.cell(0, 5, f"RISK LVL:  {t.get('risk_level', 'N/A')}", 0, 1)
                pdf.cell(0, 5, f"MISS:      {t.get('min_km', 'N/A')} km", 0, 1)
                pdf.cell(0, 5, f"REL VEL:   {t.get('relative_velocity_kms', 'N/A')} km/s", 0, 1)
                pdf.cell(0, 5, f"Pc:        {t.get('pc', 'N/A')}", 0, 1)
                pdf.cell(0, 5, f"TCA:       {t.get('tca', 'N/A')}", 0, 1)
                pdf.ln(2)

        pdf.ln(4)
        pdf.set_font("Arial", "I", 8)
        pdf.cell(0, 5, "Data Source: U.S. Space Force (18 SDS) via Space-Track.org", 0, 1)
        pdf.cell(0, 5, "Propagator: SGP4/SDP4 via Skyfield", 0, 1)
        pdf.cell(0, 5, "Report: STX Orbital Autonomy Engine v3.1", 0, 1)

        filename = f"STX_Summary_{job_id}.pdf"
        output_path = os.path.join(BASE_DIR, filename)
        pdf.output(output_path)

        return jsonify({"pdf_url": filename})
    except Exception as e:
        print(f"  ! Summary PDF generation failed: {e}")
        return jsonify({"error": "Failed to generate summary PDF"}), 500


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
