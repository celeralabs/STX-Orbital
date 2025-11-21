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
from scipy.stats import chi2

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

# =============================================================================
# OPERATIONAL PROFILES
# =============================================================================
OPERATIONAL_PROFILES = {
    "ISS_CLASS": {
        "name": "Manned Asset (ISS-Class)",
        "yellow_threshold_km": 1.0,
        "red_threshold_km": 0.5,
        "yellow_pc": 1e-6,
        "red_pc": 1e-5,
        "maneuver_recommendation_threshold": 0.5,
        "default_covariance_km": 0.5  # Conservative estimate if no data
    },
    "COMMERCIAL": {
        "name": "Commercial Unmanned",
        "yellow_threshold_km": 5.0,
        "red_threshold_km": 1.0,
        "yellow_pc": 1e-6,
        "red_pc": 1e-5,
        "maneuver_recommendation_threshold": 1.0,
        "default_covariance_km": 1.0
    },
    "CONSTELLATION": {
        "name": "Mega-Constellation (Starlink-Class)",
        "yellow_threshold_km": 20.0,
        "red_threshold_km": 5.0,
        "yellow_pc": 1e-5,
        "red_pc": 1e-4,
        "maneuver_recommendation_threshold": 2.0,
        "default_covariance_km": 2.0
    }
}

class STXConjunctionEngine:
    def __init__(self, profile="COMMERCIAL"):
        self.ts = load.timescale()
        self.profile = OPERATIONAL_PROFILES.get(profile, OPERATIONAL_PROFILES["COMMERCIAL"])
        
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
        if not self.st_client: 
            return None
        try:
            result = self.st_client.gp(norad_cat_id=norad_id, orderby='EPOCH desc', limit=1, format='tle')
            if not result: 
                return None
            
            lines = [line.strip() for line in result.splitlines() if line.strip()]
            if len(lines) < 2: 
                return None

            full_tle = [f"RSO-{norad_id} (LIVE)", lines[0], lines[1]]
            return full_tle
        except Exception as e:
            logger.error(f"API Fetch Error: {e}")
            return None

    def get_ric_components(self, primary, secondary, t):
        """Calculate Radial-In-track-Cross-track components"""
        p_pos = primary.at(t).position.km
        p_vel = primary.at(t).velocity.km_per_s
        s_pos = secondary.at(t).position.km
        
        r_vec = s_pos - p_pos
        u_r = p_pos / np.linalg.norm(p_pos)
        u_c = np.cross(p_pos, p_vel)
        u_c = u_c / np.linalg.norm(u_c)
        u_i = np.cross(u_c, u_r)
        
        radial = np.dot(r_vec, u_r)
        in_track = np.dot(r_vec, u_i)
        cross_track = np.dot(r_vec, u_c)
        
        return radial, in_track, cross_track

    def calculate_pc(self, miss_distance_km, combined_covariance_km):
        """
        Calculate Probability of Collision using 2D conjunction model
        Assumes circular hard-body radius and Gaussian distribution
        """
        # Combined object radius (conservative estimate for LEO satellites)
        combined_radius_km = 0.020  # 20 meters combined
        
        if combined_covariance_km <= 0:
            return 0.0
        
        # 2D Pc calculation (Chan, Mahalanobis distance)
        # Pc = 1 - exp(-0.5 * (R/σ)²) for circular cross-section
        sigma = combined_covariance_km
        if miss_distance_km < combined_radius_km:
            # Inside hard-body radius
            return 1.0
        
        # Mahalanobis distance
        mahal_dist = miss_distance_km / sigma
        # Probability of collision (2D circular model)
        pc = np.exp(-0.5 * mahal_dist**2) * (combined_radius_km / sigma)**2
        
        return min(pc, 1.0)

    def assess_risk_level(self, miss_km, pc):
        """Determine risk level based on thresholds"""
        if miss_km < self.profile["red_threshold_km"] or pc > self.profile["red_pc"]:
            return "RED"
        elif miss_km < self.profile["yellow_threshold_km"] or pc > self.profile["yellow_pc"]:
            return "YELLOW"
        else:
            return "GREEN"

    def calculate_delta_v(self, miss_km, radial_km, in_track_km, cross_track_km, tca_time):
        """
        Calculate optimal ΔV for conjunction avoidance
        Returns specific burn vector and execution timing
        """
        # Radial miss is primary driver for maneuvers
        # If radial component is small, radial burn is most efficient
        
        if abs(radial_km) < 1.0:
            # Small radial miss - recommend radial burn
            # ΔV scales with sqrt(miss distance needed)
            # Conservative: aim for 5x safety margin
            target_separation = max(5.0, self.profile["yellow_threshold_km"])
            delta_r_needed = target_separation - abs(radial_km)
            
            # Approximate ΔV (m/s) for radial separation
            # Using vis-viva and orbit perturbation theory
            # For LEO: ΔV ≈ 0.5 * sqrt(μ/r³) * Δr for radial shift
            # Simplified: ΔV ≈ 0.1 * Δr_km for typical LEO
            delta_v_ms = abs(delta_r_needed) * 0.1 * 1000  # Convert to m/s
            
            # Burn direction: opposite to current radial component
            burn_type = "RADIAL+" if radial_km < 0 else "RADIAL-"
            
            # Execution window: 180° before TCA for radial burns
            lead_time_hours = 1.5  # ~1/4 orbit for LEO
            
        else:
            # Large radial miss - in-track might be considered
            # But generally, we avoid in-track burns for static threats
            target_separation = max(5.0, self.profile["yellow_threshold_km"])
            delta_v_ms = 50.0  # Minimal in-track adjustment
            burn_type = "IN-TRACK"
            lead_time_hours = 0.5
        
        execution_time = tca_time - timedelta(hours=lead_time_hours)
        window_start = execution_time - timedelta(minutes=30)
        window_end = execution_time + timedelta(minutes=30)
        
        # Post-maneuver prediction (simplified)
        post_maneuver_miss = abs(radial_km) + delta_r_needed if abs(radial_km) < 1.0 else miss_km * 1.5
        
        return {
            "delta_v_ms": round(delta_v_ms, 2),
            "burn_type": burn_type,
            "execution_time": execution_time.strftime("%Y-%m-%d %H:%M:%S UTC"),
            "window_start": window_start.strftime("%Y-%m-%d %H:%M:%S UTC"),
            "window_end": window_end.strftime("%Y-%m-%d %H:%M:%S UTC"),
            "post_maneuver_miss_km": round(post_maneuver_miss, 3),
            "fuel_cost_kg": round(delta_v_ms * 0.001, 4)  # Rough estimate for 1000kg sat
        }

    def screen_conjunction(self, primary_tle, secondary_tle, days=7):
        """
        Enhanced conjunction screening with Pc calculation and risk assessment
        """
        sat1 = EarthSatellite(primary_tle[1], primary_tle[2], primary_tle[0], self.ts)
        sat2 = EarthSatellite(secondary_tle[1], secondary_tle[2], secondary_tle[0], self.ts)
        
        now = self.ts.now()
        times = self.ts.linspace(now, now + timedelta(days=days), 2000)
        
        diff = sat2.at(times) - sat1.at(times)
        dist_km = diff.distance().km
        min_idx = np.argmin(dist_km)
        min_dist = dist_km[min_idx]
        tca_time = times[min_idx]
        
        # Get RIC components at TCA
        rad, intr, cross = self.get_ric_components(sat1, sat2, tca_time)
        
        # Calculate Pc (using default covariance estimate)
        combined_covariance = self.profile["default_covariance_km"]
        pc = self.calculate_pc(min_dist, combined_covariance)
        
        # Assess risk level
        risk_level = self.assess_risk_level(min_dist, pc)
        
        # Calculate ΔV only if maneuver threshold is met
        maneuver_data = None
        if min_dist < self.profile["maneuver_recommendation_threshold"]:
            maneuver_data = self.calculate_delta_v(
                min_dist, rad, intr, cross, 
                tca_time.utc_datetime()
            )
        
        return {
            "primary": primary_tle[0],
            "secondary": secondary_tle[0],
            "tca_utc": tca_time.utc_iso(),
            "min_dist_km": float(min_dist),
            "pc": pc,
            "risk_level": risk_level,
            "geometry": {
                "radial": float(rad), 
                "in_track": float(intr), 
                "cross_track": float(cross)
            },
            "combined_covariance_km": combined_covariance,
            "maneuver": maneuver_data,
            "profile": self.profile["name"],
            "thresholds": {
                "yellow_km": self.profile["yellow_threshold_km"],
                "red_km": self.profile["red_threshold_km"],
                "yellow_pc": self.profile["yellow_pc"],
                "red_pc": self.profile["red_pc"]
            }
        }

    def generate_maneuver_plan(self, event_data):
        """
        Generate AI-powered maneuver recommendation ONLY for actionable events
        """
        if not self.ai_client:
            return "AI Engine Offline - Check API Key"
        
        # Only generate plans for RED or YELLOW events
        if event_data['risk_level'] == 'GREEN':
            return "No action required. Miss distance exceeds operational thresholds. Monitoring continues."
        
        if not event_data.get('maneuver'):
            return "Miss distance below maneuver threshold. Continue monitoring. No burn recommended at this time."
        
        maneuver = event_data['maneuver']
        
        prompt = f"""
ACT AS: Flight Dynamics Officer (FDO) for satellite conjunction avoidance.

EVENT SUMMARY:
- Primary: {event_data['primary']}
- Secondary: {event_data['secondary']}
- TCA: {event_data['tca_utc']}
- Miss Distance: {event_data['min_dist_km']:.3f} km
- Probability of Collision: {event_data['pc']:.2e}
- Risk Level: {event_data['risk_level']}

RIC GEOMETRY (km):
- Radial: {event_data['geometry']['radial']:.3f}
- In-Track: {event_data['geometry']['in_track']:.3f}
- Cross-Track: {event_data['geometry']['cross_track']:.3f}

COMPUTED MANEUVER SOLUTION:
- ΔV Required: {maneuver['delta_v_ms']} m/s
- Burn Type: {maneuver['burn_type']}
- Execution Window: {maneuver['window_start']} to {maneuver['window_end']}
- Post-Maneuver Miss: {maneuver['post_maneuver_miss_km']} km
- Fuel Cost: ~{maneuver['fuel_cost_kg']} kg

OPERATIONAL PROFILE: {event_data['profile']}

TASK: Provide concise operational assessment in 3 sections:
1. THREAT ASSESSMENT: Is this event actionable? (2-3 sentences)
2. RECOMMENDED ACTION: Execute computed maneuver or continue monitoring? (2-3 sentences)
3. EXECUTION GUIDANCE: If burn recommended, confirm timing and post-burn verification steps. (2-3 sentences)

Keep response under 200 words. Focus on operational clarity.
"""
        
        try:
            chat = self.ai_client.chat.create(model="grok-2-latest", temperature=0.1)
            chat.append(system("STX Orbital Autonomy - Flight Dynamics Officer"))
            chat.append(user(prompt))
            return chat.sample().content
        except Exception as e:
            logger.error(f"AI Generation Failed: {e}")
            return "AI analysis unavailable. Execute pre-computed maneuver per flight rules."

    def generate_pdf_report(self, telemetry, ai_analysis):
        """
        Generate professional conjunction assessment report
        """
        pdf = FPDF()
        pdf.add_page()
        
        # Header
        pdf.set_font("Arial", "B", 16)
        pdf.cell(0, 10, "STX ORBITAL // CONJUNCTION ASSESSMENT REPORT", 0, 1, "C")
        pdf.set_font("Arial", "I", 10)
        pdf.cell(0, 10, f"Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}", 0, 1, "C")
        pdf.line(10, 30, 200, 30)
        
        # Risk Level Banner
        pdf.ln(5)
        pdf.set_font("Arial", "B", 14)
        risk_color = {"RED": (255, 0, 0), "YELLOW": (255, 165, 0), "GREEN": (0, 200, 0)}
        color = risk_color.get(telemetry['risk_level'], (128, 128, 128))
        pdf.set_text_color(*color)
        pdf.cell(0, 10, f"RISK LEVEL: {telemetry['risk_level']}", 0, 1, "C")
        pdf.set_text_color(0, 0, 0)
        
        # Event Telemetry
        pdf.ln(5)
        pdf.set_font("Arial", "B", 12)
        pdf.cell(0, 10, "EVENT TELEMETRY", 0, 1)
        pdf.set_font("Courier", "", 9)
        
        data = [
            f"PRIMARY:              {telemetry['primary']}",
            f"SECONDARY:            {telemetry['secondary']}",
            f"TCA (UTC):            {telemetry['tca_utc']}",
            f"MISS DISTANCE:        {telemetry['min_dist_km']:.3f} km",
            f"COLLISION PROB (Pc):  {telemetry['pc']:.2e}",
            f"",
            f"RIC GEOMETRY:",
            f"  Radial:             {telemetry['geometry']['radial']:.3f} km",
            f"  In-Track:           {telemetry['geometry']['in_track']:.3f} km",
            f"  Cross-Track:        {telemetry['geometry']['cross_track']:.3f} km",
            f"",
            f"OPERATIONAL PROFILE:  {telemetry['profile']}",
            f"COVARIANCE ESTIMATE:  {telemetry['combined_covariance_km']:.2f} km (1-sigma)"
        ]
        
        for line in data:
            pdf.cell(0, 5, line, 0, 1)
        
        # Alert Thresholds
        pdf.ln(3)
        pdf.set_font("Arial", "B", 12)
        pdf.cell(0, 10, "STX ALERT THRESHOLDS", 0, 1)
        pdf.set_font("Courier", "", 9)
        
        thresholds = [
            f"YELLOW: Miss < {telemetry['thresholds']['yellow_km']} km OR Pc > {telemetry['thresholds']['yellow_pc']:.0e}",
            f"RED:    Miss < {telemetry['thresholds']['red_km']} km OR Pc > {telemetry['thresholds']['red_pc']:.0e}"
        ]
        
        for line in thresholds:
            pdf.cell(0, 5, line, 0, 1)
        
        # Maneuver Plan (if applicable)
        if telemetry.get('maneuver'):
            pdf.ln(5)
            pdf.set_font("Arial", "B", 12)
            pdf.cell(0, 10, "COMPUTED MANEUVER SOLUTION", 0, 1)
            pdf.set_font("Courier", "", 9)
            
            maneuver = telemetry['maneuver']
            maneuver_data = [
                f"DELTA-V REQUIRED:     {maneuver['delta_v_ms']} m/s ({maneuver['burn_type']})",
                f"EXECUTION WINDOW:     {maneuver['window_start']} to",
                f"                      {maneuver['window_end']}",
                f"POST-MANEUVER MISS:   {maneuver['post_maneuver_miss_km']} km (predicted)",
                f"FUEL COST:            ~{maneuver['fuel_cost_kg']} kg propellant"
            ]
            
            for line in maneuver_data:
                pdf.cell(0, 5, line, 0, 1)
        
        # FDO Assessment
        pdf.ln(5)
        pdf.set_font("Arial", "B", 12)
        pdf.cell(0, 10, "FLIGHT DYNAMICS ASSESSMENT", 0, 1)
        pdf.set_font("Arial", "", 9)
        pdf.multi_cell(0, 5, ai_analysis)
        
        # Footer
        pdf.ln(5)
        pdf.set_font("Arial", "I", 8)
        pdf.cell(0, 5, "Data Source: U.S. Space Force (18 SDS) via Space-Track.org", 0, 1)
        pdf.cell(0, 5, "Propagator: SGP4/SDP4 via Skyfield (NASA/NORAD standard)", 0, 1)
        pdf.cell(0, 5, "Report: STX Orbital Autonomy Engine v3.0", 0, 1)
        
        filename = f"STX_Report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
        pdf.output(filename)
        return filename
