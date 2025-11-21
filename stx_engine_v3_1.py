import os
import time
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
import matplotlib
matplotlib.use('Agg')  # Non-interactive backend
import matplotlib.pyplot as plt
from io import BytesIO
import base64

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
# OBJECT CATALOG DATABASE - AUTO-DETECTION
# =============================================================================
MANNED_ASSETS = {
    25544: "ISS (International Space Station)",
    48274: "CSS (Tiangong Space Station)",
    # Add other manned assets as needed
}

# Starlink NORAD ID ranges (approximate - Starlink uses 44000-59999 range heavily)
STARLINK_RANGES = [(44000, 46000), (46500, 59999)]

# OneWeb ID ranges
ONEWEB_RANGES = [(47000, 47999), (48000, 48500)]

# Kuiper (when they launch)
KUIPER_RANGES = [(58000, 59000)]


def detect_object_type(norad_id):
    """
    Auto-detect operational profile based on NORAD catalog ID
    Returns: (profile_name, object_description)
    """
    # Check for manned assets first (highest priority)
    if norad_id in MANNED_ASSETS:
        return ("ISS_CLASS", MANNED_ASSETS[norad_id])

    # Check Starlink
    for start, end in STARLINK_RANGES:
        if start <= norad_id <= end:
            return ("CONSTELLATION", f"Starlink Satellite (NORAD {norad_id})")

    # Check OneWeb
    for start, end in ONEWEB_RANGES:
        if start <= norad_id <= end:
            return ("CONSTELLATION", f"OneWeb Satellite (NORAD {norad_id})")

    # Check Kuiper
    for start, end in KUIPER_RANGES:
        if start <= norad_id <= end:
            return ("CONSTELLATION", f"Kuiper Satellite (NORAD {norad_id})")

    # Default to commercial for unknown objects
    return ("COMMERCIAL", f"RSO-{norad_id}")


# =============================================================================
# OPERATIONAL PROFILES
# =============================================================================
OPERATIONAL_PROFILES = {
    "ISS_CLASS": {
        "name": "Manned Asset - High Caution",
        "yellow_threshold_km": 25.0,
        "red_threshold_km": 10.0,
        "yellow_pc": 1e-6,
        "red_pc": 1e-5,
        "maneuver_recommendation_threshold": 10.0,
        "default_covariance_km": 0.5
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
        "name": "Mega-Constellation (High Volume)",
        "yellow_threshold_km": 20.0,
        "red_threshold_km": 5.0,
        "yellow_pc": 1e-5,
        "red_pc": 1e-4,
        "maneuver_recommendation_threshold": 2.0,
        "default_covariance_km": 2.0
    }
}


# =============================================================================
# FULL-CATALOG SERVICE (STAGE 0–2 PIPELINE)
# =============================================================================
class CatalogService:
    """
    Maintains a cached view of the full Space-Track catalog and provides
    fast candidate selection for conjunction screening.

    - Periodically refreshes tle_latest from Space-Track.
    - Precomputes perigee/apogee altitudes and inclination.
    - Builds reusable EarthSatellite objects for each NORAD.
    """

    def __init__(self, st_client, ts, refresh_interval_sec=3 * 3600):
        self.st_client = st_client
        self.ts = ts
        self.refresh_interval_sec = refresh_interval_sec

        self.last_refresh = 0.0
        self.tles = {}   # norad -> dict(name, l1, l2, perigee_km, apogee_km, inc_deg, raan_deg)
        self.sats = {}   # norad -> EarthSatellite

    def has_client(self):
        return self.st_client is not None

    def has_data(self):
        return bool(self.tles)

    def _parse_orbit_params(self, line1, line2):
        """
        Extract basic orbit parameters from TLE line 1/2.
        Returns perigee_km, apogee_km, inc_deg, raan_deg.
        """
        try:
            # Mean motion [revs/day] is columns 53-63 of line 2
            mean_motion = float(line2[52:63].strip())

            # Eccentricity is columns 27-33 (no decimal in TLE)
            ecc = float('0.' + line2[26:33].strip())

            # Inclination, RAAN (deg)
            inc_deg = float(line2[8:16].strip())
            raan_deg = float(line2[17:25].strip())

            # Convert mean motion to semi-major axis (km)
            # n [rad/s] = mean_motion * 2π / 86400
            n_rad_per_sec = mean_motion * 2.0 * np.pi / 86400.0
            mu = 398600.4418  # km^3/s^2
            a = (mu / (n_rad_per_sec ** 2)) ** (1.0 / 3.0)

            # Perigee/apogee altitudes (km), Earth radius ~6378 km
            perigee_km = a * (1.0 - ecc) - 6378.0
            apogee_km = a * (1.0 + ecc) - 6378.0

            return perigee_km, apogee_km, inc_deg, raan_deg
        except Exception:
            # If parsing fails, fall back to generic values
            return 0.0, 50000.0, 0.0, 0.0

    def refresh_if_needed(self, force=False):
        """
        Refresh catalog from Space-Track if stale.
        """
        now = time.time()
        if not self.st_client:
            return

        if (not force) and (now - self.last_refresh < self.refresh_interval_sec) and self.tles:
            return

        try:
            logger.info("Refreshing Space-Track catalog snapshot (tle_latest)...")
            result = self.st_client.tle_latest(
                orderby='NORAD_CAT_ID asc',
                limit=50000,
                format='tle'
            )
            if not result:
                logger.warning("CatalogService: tle_latest returned empty result.")
                return

            lines = [line.strip() for line in result.splitlines() if line.strip()]
            new_tles = {}
            new_sats = {}

            for i in range(0, len(lines), 3):
                if i + 2 >= len(lines):
                    break
                name = lines[i] or "UNKNOWN"
                l1 = lines[i + 1]
                l2 = lines[i + 2]

                if not (l1.startswith("1 ") and l2.startswith("2 ")):
                    continue

                try:
                    norad = int(l2[2:7].strip())
                except Exception:
                    continue

                perigee_km, apogee_km, inc_deg, raan_deg = self._parse_orbit_params(l1, l2)

                new_tles[norad] = {
                    "name": name,
                    "l1": l1,
                    "l2": l2,
                    "perigee_km": perigee_km,
                    "apogee_km": apogee_km,
                    "inc_deg": inc_deg,
                    "raan_deg": raan_deg,
                }

                # Prebuild satellite object for reuse
                try:
                    sat = EarthSatellite(l1, l2, name, self.ts)
                    new_sats[norad] = sat
                except Exception:
                    # If construction fails for one TLE, skip it
                    continue

            self.tles = new_tles
            self.sats = new_sats
            self.last_refresh = now
            logger.info(f"CatalogService: loaded {len(self.tles)} objects from Space-Track.")

        except Exception as e:
            logger.error(f"CatalogService refresh failed: {e}")

    def get_stage1_candidates(self, primary_tle, altitude_margin_km=150.0, inc_margin_deg=30.0):
        """
        Stage 1: Fast geometric prefilter by altitude band and inclination.

        Returns list of NORAD IDs that plausibly intersect primary's shell.
        """
        if not self.tles:
            return []

        _, p_l1, p_l2 = primary_tle
        p_perigee, p_apogee, p_inc, _ = self._parse_orbit_params(p_l1, p_l2)

        alt_min = p_perigee - altitude_margin_km
        alt_max = p_apogee + altitude_margin_km

        candidates = []
        for norad, info in self.tles.items():
            perigee = info["perigee_km"]
            apogee = info["apogee_km"]
            inc = info["inc_deg"]

            # Altitude overlap check with margin
            if perigee > alt_max or apogee < alt_min:
                continue

            # Inclination filter (conservative)
            if abs(inc - p_inc) > inc_margin_deg:
                continue

            candidates.append(norad)

        return candidates

    def coarse_screen(self, primary_tle, candidate_ids, days=7, coarse_points=300, coarse_distance_km=80.0):
        """
        Stage 2: Coarse temporal screening. For each candidate, propagate on a
        coarse 7-day grid and keep only those whose coarse min distance is
        below coarse_distance_km.

        Returns list of NORAD IDs that pass the coarse distance threshold.
        """
        if not candidate_ids:
            return []

        p_name, p_l1, p_l2 = primary_tle
        sat_primary = EarthSatellite(p_l1, p_l2, p_name, self.ts)

        now = self.ts.now()
        times = self.ts.linspace(now, now + timedelta(days=days), coarse_points)

        p_pos = sat_primary.at(times).position.km  # (3, T)

        near_ids = []

        for norad in candidate_ids:
            sat = self.sats.get(norad)
            if sat is None:
                continue
            try:
                s_pos = sat.at(times).position.km  # (3, T)
                diff = s_pos - p_pos
                dist = np.linalg.norm(diff, axis=0)  # T samples
                d_min = float(dist.min())
                if d_min < coarse_distance_km:
                    near_ids.append(norad)
            except Exception:
                continue

        return near_ids


# =============================================================================
# CORE ENGINE
# =============================================================================
class STXConjunctionEngine:
    def __init__(self, profile="COMMERCIAL", suppress_green=False):
        """
        Initialize conjunction engine

        Args:
            profile: Default profile if auto-detection fails
            suppress_green: If True, don't generate reports for GREEN events
        """
        self.ts = load.timescale()
        self.default_profile = profile
        self.suppress_green = suppress_green

        # Priority target lists
        self.manned_assets = [25544, 48274]  # ISS, Tiangong

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

        # Full-catalog service for staged screening
        if self.st_client:
            self.catalog = CatalogService(self.st_client, self.ts)
        else:
            self.catalog = None

    # -------------------------------------------------------------------------
    # FULL-CATALOG PIPELINE ENTRYPOINT
    # -------------------------------------------------------------------------
    def get_catalog_candidates_for_primary(
        self,
        primary_tle,
        primary_norad=None,
        days=7,
        coarse_points=300,
        coarse_distance_km=80.0,
        altitude_margin_km=150.0,
        inc_margin_deg=30.0,
    ):
        """
        High-level helper: given a primary TLE, return a list of candidate
        catalog objects that should be passed to full screen_conjunction().

        This runs:
          - Catalog refresh (if needed)
          - Stage 1: altitude + inclination prefilter
          - Stage 2: coarse distance screening

        Returns list of dicts:
          { "norad_id", "name", "l1", "l2" }
        """
        if not self.catalog or not self.catalog.has_client():
            logger.warning("Catalog candidates requested but Space-Track client not available.")
            return []

        # Ensure we have fresh data
        self.catalog.refresh_if_needed()

        if not self.catalog.has_data():
            logger.warning("CatalogService has no data after refresh attempt.")
            return []

        # Stage 1: geometric filter
        stage1_ids = self.catalog.get_stage1_candidates(
            primary_tle,
            altitude_margin_km=altitude_margin_km,
            inc_margin_deg=inc_margin_deg,
        )
        logger.info(f"Stage 1 prefilter: {len(stage1_ids)} candidates (altitude/inc)")

        if not stage1_ids:
            return []

        # Stage 2: coarse distance screen
        stage2_ids = self.catalog.coarse_screen(
            primary_tle,
            stage1_ids,
            days=days,
            coarse_points=coarse_points,
            coarse_distance_km=coarse_distance_km,
        )
        logger.info(f"Stage 2 coarse screen: {len(stage2_ids)} candidates < {coarse_distance_km} km (coarse)")

        candidates = []
        for norad in stage2_ids:
            info = self.catalog.tles.get(norad)
            if not info:
                continue
            candidates.append({
                "norad_id": norad,
                "name": info["name"],
                "l1": info["l1"],
                "l2": info["l2"],
            })

        return candidates

    # -------------------------------------------------------------------------
    # EXISTING ENGINE LOGIC
    # -------------------------------------------------------------------------
    def assess_risk_priority(self, norad_id, tle_line1, tle_line2):
        """
        Determine threat priority based on object characteristics

        Returns: ("MANNED" | "HIGH-RISK" | "CATALOG", reason)
        """
        # Tier 1: Manned assets
        if norad_id in self.manned_assets:
            return ("MANNED", "Human-occupied spacecraft")

        # Parse TLE for risk assessment
        try:
            # Extract orbital parameters from TLE
            # Line 1: positions 34-43 = mean motion derivative (decay rate)
            # Line 2: positions 27-33 = eccentricity
            # Line 2: positions 53-63 = mean motion

            mean_motion_derivative = float(tle_line1[33:43].strip())
            eccentricity = float('0.' + tle_line2[26:33].strip())
            mean_motion = float(tle_line2[52:63].strip())

            # Calculate approximate perigee altitude
            # Mean motion in revs/day -> semi-major axis -> perigee
            # a = (μ / (n * 2π / 86400)^2)^(1/3)
            n_rad_per_sec = mean_motion * 2 * np.pi / 86400
            mu = 398600.4418  # Earth's gravitational parameter (km^3/s^2)
            semi_major_axis = (mu / (n_rad_per_sec ** 2)) ** (1/3)
            perigee_alt = semi_major_axis * (1 - eccentricity) - 6378  # Earth radius

            # High-risk criteria
            if perigee_alt < 300:
                return ("HIGH-RISK", f"Decaying orbit (perigee {perigee_alt:.0f} km)")

            if eccentricity > 0.1:
                return ("HIGH-RISK", f"Highly elliptical orbit (ecc {eccentricity:.3f})")

            if abs(mean_motion_derivative) > 0.00001:
                return ("HIGH-RISK", f"Active decay/maneuver (dn/dt {mean_motion_derivative:.2e})")

        except Exception as e:
            logger.debug(f"Risk assessment failed for {norad_id}: {e}")

        # Tier 3: Standard catalog object
        return ("CATALOG", "Standard catalog object")

    def fetch_live_tle(self, norad_id):
        if not self.st_client:
            return None
        try:
            result = self.st_client.gp(
                norad_cat_id=norad_id,
                orderby='EPOCH desc',
                limit=1,
                format='tle'
            )
            if not result:
                return None

            lines = [line.strip() for line in result.splitlines() if line.strip()]
            if len(lines) < 2:
                return None

            # Use detected object description instead of generic RSO
            _, obj_desc = detect_object_type(norad_id)
            full_tle = [f"{obj_desc}", lines[0], lines[1]]
            return full_tle
        except Exception as e:
            logger.error(f"API Fetch Error: {e}")
            return None

    def get_ric_components(self, primary, secondary, t):
        """Calculate Radial-In-track-Cross-track components"""
        p_pos = primary.at(t).position.km
        p_vel = primary.at(t).velocity.km_per_s
        s_pos = secondary.at(t).position.km
        s_vel = secondary.at(t).velocity.km_per_s

        r_vec = s_pos - p_pos
        u_r = p_pos / np.linalg.norm(p_pos)
        u_c = np.cross(p_pos, p_vel)
        u_c = u_c / np.linalg.norm(u_c)
        u_i = np.cross(u_c, u_r)

        radial = np.dot(r_vec, u_r)
        in_track = np.dot(r_vec, u_i)
        cross_track = np.dot(r_vec, u_c)

        # Calculate relative velocity
        rel_vel = s_vel - p_vel
        rel_speed = np.linalg.norm(rel_vel)

        return radial, in_track, cross_track, rel_speed

    def calculate_pc(self, miss_distance_km, combined_covariance_km):
        """
        Calculate Probability of Collision using 2D conjunction model
        """
        combined_radius_km = 0.020  # 20 meters combined

        if combined_covariance_km <= 0:
            return 0.0

        sigma = combined_covariance_km
        if miss_distance_km < combined_radius_km:
            return 1.0

        mahal_dist = miss_distance_km / sigma
        pc = np.exp(-0.5 * mahal_dist**2) * (combined_radius_km / sigma)**2

        return min(pc, 1.0)

    def assess_risk_level(self, miss_km, pc, profile):
        """Determine risk level based on thresholds"""
        if miss_km < profile["red_threshold_km"] or pc > profile["red_pc"]:
            return "RED"
        elif miss_km < profile["yellow_threshold_km"] or pc > profile["yellow_pc"]:
            return "YELLOW"
        else:
            return "GREEN"

    def calculate_delta_v(self, miss_km, radial_km, in_track_km, cross_track_km, tca_time):
        """Calculate optimal ΔV for conjunction avoidance"""
        if abs(radial_km) < 1.0:
            target_separation = max(5.0, 10.0)  # Conservative for close approaches
            delta_r_needed = target_separation - abs(radial_km)
            delta_v_ms = abs(delta_r_needed) * 0.1 * 1000
            burn_type = "RADIAL+" if radial_km < 0 else "RADIAL-"
            lead_time_hours = 1.5
        else:
            target_separation = max(5.0, 10.0)
            delta_v_ms = 50.0
            burn_type = "IN-TRACK"
            lead_time_hours = 0.5

        execution_time = tca_time - timedelta(hours=lead_time_hours)
        window_start = execution_time - timedelta(minutes=30)
        window_end = execution_time + timedelta(minutes=30)
        post_maneuver_miss = (
            abs(radial_km) + delta_r_needed if abs(radial_km) < 1.0 else miss_km * 1.5
        )

        return {
            "delta_v_ms": round(delta_v_ms, 2),
            "burn_type": burn_type,
            "execution_time": execution_time.strftime("%Y-%m-%d %H:%M:%S UTC"),
            "window_start": window_start.strftime("%Y-%m-%d %H:%M:%S UTC"),
            "window_end": window_end.strftime("%Y-%m-%d %H:%M:%S UTC"),
            "post_maneuver_miss_km": round(post_maneuver_miss, 3),
            "fuel_cost_kg": round(delta_v_ms * 0.001, 4)
        }

    def generate_ric_plot(self, radial, in_track, cross_track, miss_km):
        """
        Generate RIC geometry plot for close approaches
        Returns base64 encoded PNG
        """
        try:
            fig, ax = plt.subplots(figsize=(6, 6), facecolor='#0a0e14')
            ax.set_facecolor('#131920')

            # Plot primary at origin
            ax.plot(0, 0, 'o', color='#00d9ff', markersize=15, label='Primary')

            # Plot secondary at RIC coordinates
            ax.plot(in_track, radial, 'o', color='#ff4444', markersize=12, label='Secondary')

            # Draw miss distance line
            ax.plot([0, in_track], [0, radial], '--', color='#8b949e', linewidth=1, alpha=0.7)

            # Add distance annotation
            mid_x = in_track / 2
            mid_y = radial / 2
            ax.text(
                mid_x, mid_y, f'{miss_km:.3f} km',
                color='white', fontsize=10, ha='center',
                bbox=dict(boxstyle='round', facecolor='#1a2332', alpha=0.8)
            )

            # Styling
            ax.set_xlabel('In-Track (km)', color='white', fontsize=11)
            ax.set_ylabel('Radial (km)', color='white', fontsize=11)
            ax.set_title('RIC Geometry at TCA', color='#00d9ff', fontsize=13, fontweight='bold')
            ax.grid(True, color='#2a3f5f', alpha=0.3, linestyle='--')
            ax.tick_params(colors='white')
            ax.spines['bottom'].set_color('#2a3f5f')
            ax.spines['top'].set_color('#2a3f5f')
            ax.spines['left'].set_color('#2a3f5f')
            ax.spines['right'].set_color('#2a3f5f')
            ax.legend(
                loc='upper right',
                facecolor='#1a2332',
                edgecolor='#2a3f5f',
                labelcolor='white'
            )

            # Equal aspect ratio
            ax.set_aspect('equal')

            # Save to buffer
            buf = BytesIO()
            plt.tight_layout()
            plt.savefig(buf, format='png', facecolor='#0a0e14', dpi=150)
            plt.close()
            buf.seek(0)

            # Convert to base64
            plot_base64 = base64.b64encode(buf.read()).decode('utf-8')
            return plot_base64

        except Exception as e:
            logger.error(f"Plot generation failed: {e}")
            return None

    def screen_conjunction(self, primary_tle, secondary_tle, primary_norad=None, secondary_norad=None, days=7):
        """
        Enhanced conjunction screening with auto-detection

        Args:
            primary_tle: TLE data for primary object
            secondary_tle: TLE data for secondary object
            primary_norad: NORAD ID of primary (if known)
            secondary_norad: NORAD ID of secondary (if known)
            days: Screening window in days
        """
        # Auto-detect profile for primary object
        if primary_norad:
            profile_type, _ = detect_object_type(primary_norad)
        else:
            profile_type = self.default_profile

        profile = OPERATIONAL_PROFILES[profile_type]

        sat1 = EarthSatellite(primary_tle[1], primary_tle[2], primary_tle[0], self.ts)
        sat2 = EarthSatellite(secondary_tle[1], secondary_tle[2], secondary_tle[0], self.ts)

        now = self.ts.now()
        times = self.ts.linspace(now, now + timedelta(days=days), 2000)

        diff = sat2.at(times) - sat1.at(times)
        dist_km = diff.distance().km
        min_idx = np.argmin(dist_km)
        min_dist = dist_km[min_idx]
        tca_time = times[min_idx]

        # Get RIC components and relative velocity at TCA
        rad, intr, cross, rel_vel = self.get_ric_components(sat1, sat2, tca_time)

        # Calculate Pc
        combined_covariance = profile["default_covariance_km"]
        pc = self.calculate_pc(min_dist, combined_covariance)

        # Assess risk level
        risk_level = self.assess_risk_level(min_dist, pc, profile)

        # Calculate ΔV only if maneuver threshold is met
        maneuver_data = None
        if min_dist < profile["maneuver_recommendation_threshold"]:
            maneuver_data = self.calculate_delta_v(
                min_dist, rad, intr, cross,
                tca_time.utc_datetime()
            )

        # Generate RIC plot for close approaches
        ric_plot_base64 = None
        if min_dist < 10.0:  # Generate plot for events < 10 km
            ric_plot_base64 = self.generate_ric_plot(rad, intr, cross, min_dist)

        result = {
            "primary": primary_tle[0],
            "secondary": secondary_tle[0],
            "tca_utc": tca_time.utc_iso(),
            "min_dist_km": float(min_dist),
            "relative_velocity_kms": float(rel_vel),
            "pc": pc,
            "risk_level": risk_level,
            "geometry": {
                "radial": float(rad),
                "in_track": float(intr),
                "cross_track": float(cross)
            },
            "combined_covariance_km": combined_covariance,
            "maneuver": maneuver_data,
            "profile": profile["name"],
            "profile_type": profile_type,
            "thresholds": {
                "yellow_km": profile["yellow_threshold_km"],
                "red_km": profile["red_threshold_km"],
                "yellow_pc": profile["yellow_pc"],
                "red_pc": profile["red_pc"]
            },
            "ric_plot": ric_plot_base64
        }

        # Suppress GREEN events if configured
        if self.suppress_green and risk_level == "GREEN":
            return None

        return result

    def generate_maneuver_plan(self, event_data):
        """Generate AI-powered maneuver recommendation"""
        if not self.ai_client:
            return "AI Engine Offline - Check API Key"

        if event_data['risk_level'] == 'GREEN':
            return "No action required. Miss distance and Pc both below alert thresholds. Continue monitoring per standard flight rules."

        if not event_data.get('maneuver'):
            return (
                f"Miss distance ({event_data['min_dist_km']:.3f} km) below maneuver threshold. "
                f"Continue monitoring. No burn recommended at this time."
            )

        maneuver = event_data['maneuver']

        prompt = f"""
ACT AS: Flight Dynamics Officer (FDO) for satellite conjunction avoidance.

EVENT SUMMARY:
- Primary: {event_data['primary']}
- Secondary: {event_data['secondary']}
- TCA: {event_data['tca_utc']}
- Miss Distance: {event_data['min_dist_km']:.3f} km
- Relative Velocity: {event_data['relative_velocity_kms']:.2f} km/s
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
        """Generate professional conjunction assessment report"""
        pdf = FPDF()
        pdf.add_page()

        # Header
        pdf.set_font("Arial", "B", 16)
        pdf.cell(0, 10, "STX ORBITAL // CONJUNCTION ASSESSMENT REPORT", 0, 1, "C")
        pdf.set_font("Arial", "I", 10)
        pdf.cell(
            0,
            10,
            f"Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}",
            0,
            1,
            "C"
        )
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

        # Format Pc properly - never show 0.00e+00
        if telemetry['pc'] < 1e-10:
            pc_display = f"< 1e-10 (negligible)"
        else:
            pc_display = f"{telemetry['pc']:.2e}"

        data = [
            f"PRIMARY:              {telemetry['primary']}",
            f"SECONDARY:            {telemetry['secondary']}",
            f"TCA (UTC):            {telemetry['tca_utc']}",
            f"MISS DISTANCE:        {telemetry['min_dist_km']:.3f} km",
            f"RELATIVE VELOCITY:    {telemetry['relative_velocity_kms']:.2f} km/s",
            f"COLLISION PROB (Pc):  {pc_display}",
            f"",
            f"RIC GEOMETRY:",
            f"  Radial:             {telemetry['geometry']['radial']:.3f} km",
            f"  In-Track:           {telemetry['geometry']['in_track']:.3f} km",
            f"  Cross-Track:        {telemetry['geometry']['cross_track']:.3f} km",
            f"",
            f"OPERATIONAL PROFILE:  {telemetry['profile']}",
            f"COVARIANCE:           {telemetry['combined_covariance_km']:.2f} km",
            f"                      (Combined 1-sigma position uncertainty)"
        ]

        for line in data:
            pdf.cell(0, 5, line, 0, 1)

        # Alert Thresholds
        pdf.ln(3)
        pdf.set_font("Arial", "B", 12)
        pdf.cell(0, 10, "STX ALERT THRESHOLDS", 0, 1)
        pdf.set_font("Courier", "", 9)

        thresholds = [
            f"YELLOW: Miss < {telemetry['thresholds']['yellow_km']:.1f} km OR Pc > {telemetry['thresholds']['yellow_pc']:.0e}",
            f"RED:    Miss < {telemetry['thresholds']['red_km']:.1f} km OR Pc > {telemetry['thresholds']['red_pc']:.0e}"
        ]

        for line in thresholds:
            pdf.cell(0, 5, line, 0, 1)

        # RIC Plot (if available)
        if telemetry.get('ric_plot'):
            pdf.ln(5)
            pdf.set_font("Arial", "B", 12)
            pdf.cell(0, 10, "RIC GEOMETRY VISUALIZATION", 0, 1)

            # Save base64 plot as temp file
            import tempfile
            with tempfile.NamedTemporaryFile(delete=False, suffix='.png') as tmp:
                tmp.write(base64.b64decode(telemetry['ric_plot']))
                tmp_path = tmp.name

            try:
                pdf.image(tmp_path, x=50, w=100)
            except Exception:
                pass
            finally:
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass

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
        pdf.cell(0, 5, "Report: STX Orbital Autonomy Engine v3.1", 0, 1)

        filename = f"STX_Report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
        pdf.output(filename)
        return filename
