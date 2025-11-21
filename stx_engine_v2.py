import os
import numpy as np
import logging
from datetime import datetime, timezone, timedelta
from skyfield.api import load, EarthSatellite, wgs84
from xai_sdk import Client
from xai_sdk.chat import system, user
from dotenv import load_dotenv
from spacetrack import SpaceTrackClient
from fpdf import FPDF

# =============================================================================
# CONFIGURATION & LOGGING
# =============================================================================
if os.path.exists("Space.env"):
    load_dotenv("Space.env")
else:
    load_dotenv() 

XAI_API_KEY = os.getenv("XAI_API_KEY")
ST_USER = os.getenv("SPACETRACK_USER")
ST_PASS = os.getenv("SPACETRACK_PASS")

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | STX-CORE | %(levelname)s | %(message)s',
    handlers=[logging.FileHandler("stx_screening.log"), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

class STXConjunctionEngine:
    def __init__(self):
        self.ts = load.timescale()
        
        if XAI_API_KEY:
            self.ai_client = Client(api_key=XAI_API_KEY)
        else:
            self.ai_client = None

        if ST_USER and ST_PASS:
            try:
                self.st_client = SpaceTrackClient(identity=ST_USER, password=ST_PASS)
                logger.info("Connected to U.S. Space Force (18 SDS) via Space-Track.org")
            except Exception as e:
                logger.error(f"Space-Track Connection Failed: {e}")
                self.st_client = None
        else:
            self.st_client = None
            logger.warning("Space-Track credentials missing. Using simulation mode.")

    def fetch_live_tle(self, norad_id):
        if not self.st_client: return None
        try:
            result = self.st_client.gp(norad_cat_id=norad_id, orderby='EPOCH desc', limit=1, format='tle')
            if not result: return None
            
            # Fix Windows/Linux line ending issues
            lines = [line.strip() for line in result.splitlines() if line.strip()]
            if len(lines) < 2: return None

            full_tle = [f"RSO-{norad_id} (LIVE)", lines[0], lines[1]]
            return full_tle
        except Exception as e:
            logger.error(f"API Fetch Error: {e}")
            return None

    def get_ric_components(self, primary, secondary, t):
        p_pos = primary.at(t).position.km
        p_vel = primary.at(t).velocity.km_per_s
        s_pos = secondary.at(t).position.km
        
        r_vec = s_pos - p_pos
        u_r = p_pos / np.linalg.norm(p_pos)
        u_c = np.cross(p_pos, p_vel); u_c = u_c / np.linalg.norm(u_c)
        u_i = np.cross(u_c, u_r)
        return np.dot(r_vec, u_r), np.dot(r_vec, u_i), np.dot(r_vec, u_c)

    def screen_conjunction(self, primary_tle, secondary_tle, days=7):
        sat1 = EarthSatellite(primary_tle[1], primary_tle[2], primary_tle[0], self.ts)
        sat2 = EarthSatellite(secondary_tle[1], secondary_tle[2], secondary_tle[0], self.ts)
        
        now = self.ts.now()
        times = self.ts.linspace(now, now + timedelta(days=days), 2000)
        
        diff = sat2.at(times) - sat1.at(times)
        dist_km = diff.distance().km
        min_idx = np.argmin(dist_km)
        min_dist = dist_km[min_idx]
        tca_time = times[min_idx]
        
        rad, intr, cross = self.get_ric_components(sat1, sat2, tca_time)
        
        return {
            "primary": primary_tle[0],
            "secondary": secondary_tle[0],
            "tca_utc": tca_time.utc_iso(),
            "min_dist_km": float(min_dist),
            "geometry": {"radial": float(rad), "in_track": float(intr), "cross_track": float(cross)}
        }

    def generate_maneuver_plan(self, event_data):
        if not self.ai_client: return "AI Engine Offline - Check API Key"
        
        prompt = f"""
        ACT AS: FDO (Flight Dynamics Officer).
        TASK: Analyze conjunction & recommend maneuver.
        INPUT: TCA {event_data['tca_utc']}, Miss {event_data['min_dist_km']:.2f}km.
        RIC Geometry: R={event_data['geometry']['radial']:.2f}, I={event_data['geometry']['in_track']:.2f}, C={event_data['geometry']['cross_track']:.2f}.
        OUTPUT: 1. Assessment, 2. Strategy, 3. Execution Time.
        """
        try:
            chat = self.ai_client.chat.create(model="grok-2-latest", temperature=0.1)
            chat.append(system("STX Orbital Autonomy Kernel")); chat.append(user(prompt))
            return chat.sample().content
        except: return "AI Generation Failed"

    def generate_pdf_report(self, telemetry, ai_analysis):
        pdf = FPDF()
        pdf.add_page()
        pdf.set_font("Arial", "B", 16)
        pdf.cell(0, 10, "STX ORBITAL // CONJUNCTION ASSESSMENT", 0, 1, "C")
        pdf.set_font("Arial", "I", 10)
        pdf.cell(0, 10, f"Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}", 0, 1, "C")
        pdf.line(10, 30, 200, 30)
        
        pdf.ln(10); pdf.set_font("Arial", "B", 12); pdf.cell(0, 10, "EVENT TELEMETRY", 0, 1)
        pdf.set_font("Courier", "", 10)
        
        data = [
            f"PRIMARY:    {telemetry['primary']}",
            f"SECONDARY:  {telemetry['secondary']}",
            f"TCA (UTC):  {telemetry['tca_utc']}",
            f"MISS DIST:  {telemetry['min_dist_km']:.4f} km",
            f"RADIAL:     {telemetry['geometry']['radial']:.4f} km",
        ]
        for line in data: pdf.cell(0, 6, line, 0, 1)
            
        pdf.ln(10); pdf.set_font("Arial", "B", 12); pdf.cell(0, 10, "AUTONOMOUS MANEUVER PLAN", 0, 1)
        pdf.set_font("Arial", "", 10)
        pdf.multi_cell(0, 6, ai_analysis)
        
        filename = f"STX_Report_{datetime.now().strftime('%H%M%S')}.pdf"
        pdf.output(filename)
        return filename