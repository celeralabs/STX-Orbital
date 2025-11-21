from flask import Flask, request, jsonify, send_from_directory
import os
import sys
import traceback

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__)

print("=== STX DIAGNOSTIC MODE ===")
print(f"Python version: {sys.version}")
print(f"Working directory: {os.getcwd()}")
print(f"BASE_DIR: {BASE_DIR}")

# Try to import engine
try:
    from stx_engine_v3_1 import STXConjunctionEngine
    print("✓ Engine imported successfully")
    ENGINE_OK = True
except Exception as e:
    print(f"✗ ENGINE IMPORT FAILED: {e}")
    traceback.print_exc()
    ENGINE_OK = False
    STXConjunctionEngine = None

# File routes
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

# DIAGNOSTIC ENDPOINT
@app.route('/screen', methods=['POST'])
def screen_fleet():
    """Diagnostic version - returns JSON no matter what"""
    try:
        print("\n" + "="*50)
        print("SCREEN REQUEST RECEIVED")
        print("="*50)
        
        # Check engine
        if not ENGINE_OK:
            return jsonify({
                "error": "Engine failed to import on startup",
                "diagnostic": True
            }), 500
        
        # Check auth
        auth = request.headers.get('Authorization')
        print(f"Auth header: {auth}")
        
        if auth != 'Bearer stx-authorized-user':
            return jsonify({"error": "Unauthorized"}), 401
        
        # Check file
        if 'file' not in request.files:
            return jsonify({"error": "No file in request"}), 400
        
        file = request.files['file']
        print(f"File: {file.filename}")
        
        if not file or file.filename == '':
            return jsonify({"error": "Empty filename"}), 400
        
        # Read file
        try:
            content = file.read().decode('utf-8', errors='ignore')
            print(f"File content length: {len(content)} bytes")
            print(f"First 100 chars: {content[:100]}")
        except Exception as e:
            return jsonify({"error": f"File read failed: {str(e)}"}), 400
        
        # Try to initialize engine
        try:
            print("Initializing engine...")
            engine = STXConjunctionEngine(suppress_green=False)
            print("✓ Engine initialized")
        except Exception as e:
            print(f"✗ Engine init failed: {e}")
            traceback.print_exc()
            return jsonify({
                "error": f"Engine init failed: {str(e)}",
                "error_type": type(e).__name__
            }), 500
        
        # For now, just return success
        return jsonify({
            "status": "diagnostic_success",
            "message": "Engine initialized successfully",
            "file": file.filename,
            "file_size": len(content)
        })
        
    except Exception as e:
        print(f"\n{'='*50}")
        print("CRITICAL ERROR IN /screen")
        print(f"{'='*50}")
        print(f"Error: {e}")
        print(f"Type: {type(e).__name__}")
        traceback.print_exc()
        print(f"{'='*50}\n")
        
        # ALWAYS return JSON
        return jsonify({
            "error": str(e),
            "error_type": type(e).__name__,
            "diagnostic": True
        }), 500

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    print(f"\nStarting Flask on port {port}...")
    app.run(host='0.0.0.0', port=port)