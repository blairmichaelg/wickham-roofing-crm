import math
import re
from datetime import UTC, datetime, timezone

import httpx
import structlog

logger = structlog.get_logger("app.services.storm_feed")


DIRECTIONS = {
    "N": "North",
    "S": "South",
    "E": "East",
    "W": "West",
    "NE": "Northeast",
    "NW": "Northwest",
    "SE": "Southeast",
    "SW": "Southwest",
    "NNE": "North-Northeast",
    "NNW": "North-Northwest",
    "SSE": "South-Southeast",
    "SSW": "South-Southwest",
    "ENE": "East-Northeast",
    "ESE": "East-Southeast",
    "WNW": "West-Northwest",
    "WSW": "West-Southwest",
}


def normalize_nws_location(location_str: str) -> str:
    """
    Parse and clean NWS meteorologist shorthand (e.g. "4 SE Peoples Still, GA" -> "4 miles Southeast of Peoples Still, GA").
    """
    if not location_str:
        return location_str
    
    # Matches a distance (number/decimal), a direction abbreviation (case-insensitive), and the remaining location text.
    pattern = r"^\s*(\d+(?:\.\d+)?)\s*(N|S|E|W|NE|NW|SE|SW|NNE|NNW|SSE|SSW|ENE|ESE|WNW|WSW)\s+(.+)$"
    match = re.match(pattern, location_str, re.IGNORECASE)
    if match:
        dist_str, dir_abbr, rest = match.groups()
        dir_full = DIRECTIONS.get(dir_abbr.upper(), dir_abbr)
        try:
            val = float(dist_str)
            unit = "mile" if val == 1.0 else "miles"
        except ValueError:
            unit = "miles"
        return f"{dist_str} {unit} {dir_full} of {rest}"
    return location_str


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate the great-circle distance between two points in miles."""
    R = 3958.8  # Earth radius in miles
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


class NWSLiveStormFeed:
    """Service to fetch and parse public storm data from NOAA NWS ArcGIS REST service."""
    
    # Layer 2 is Last_72_Hours
    URL = "https://mapservices.weather.noaa.gov/vector/rest/services/obs/nws_local_storm_reports/MapServer/2/query"

    async def fetch_recent_reports(
        self, center_lat: float, center_lon: float, radius_miles: float
    ) -> list[dict]:
        """Fetch storm reports from the last 72h within a bounding box centered on coordinates."""
        # Compute bounding-box envelope in degrees
        lat_delta = radius_miles / 69.0
        cos_lat = math.cos(math.radians(center_lat))
        lon_delta = radius_miles / (69.0 * cos_lat) if cos_lat > 0.001 else radius_miles / 69.0

        xmin = center_lon - lon_delta
        ymin = center_lat - lat_delta
        xmax = center_lon + lon_delta
        ymax = center_lat + lat_delta

        params = {
            "where": "1=1",
            "f": "json",
            "outFields": "objectid,wfo_id,wfo,lsr_validtime,descript,loc_desc,state,magnitude,units,remarks,valid_time",
            "geometry": f"{xmin},{ymin},{xmax},{ymax}",
            "geometryType": "esriGeometryEnvelope",
            "inSR": "4326",
            "spatialRel": "esriSpatialRelIntersects",
            "returnGeometry": "true",
            "outSR": "4326"
        }
        
        logger.info(
            "fetching_nws_storm_reports",
            url=self.URL,
            center=(center_lat, center_lon),
            bbox=(xmin, ymin, xmax, ymax)
        )
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(self.URL, params=params, timeout=15)
                if resp.status_code != 200:
                    logger.error("nws_feed_error_status", status=resp.status_code, body=resp.text[:500])
                    return []
                data = resp.json()
        except Exception as e:
            logger.error("nws_feed_request_failed", error=str(e))
            return []

        features = data.get("features", [])
        logger.info("nws_feed_received", total_features=len(features))
        
        parsed_reports = []
        for feat in features:
            attrs = feat.get("attributes", {})
            geom = feat.get("geometry", {})
            
            if not attrs or not geom:
                continue
                
            objectid = attrs.get("objectid")
            if not objectid:
                continue
                
            descript = str(attrs.get("descript", "")).upper()
            
            # Map description to standardized event_type
            event_type = None
            if "HAIL" in descript:
                event_type = "HAIL"
            elif "TORNADO" in descript:
                event_type = "TORNADO"
            elif any(k in descript for k in ("WIND", "GST", "DMG", "TSTM", "HURRICANE", "TROPICAL")):
                # Filter out snow/ice/winter/heat events if they happen to contain these keywords
                if not any(k in descript for k in ("SNOW", "ICE", "FREEZING", "HEAT", "WARM")):
                    event_type = "WIND"
                    
            if not event_type:
                # Discard non-severe reports
                continue
                
            raw_mag = attrs.get("magnitude")
            mag = 0.0
            if raw_mag:
                try:
                    # Clean non-numeric characters (except decimal)
                    cleaned = "".join(c for c in str(raw_mag) if c.isdigit() or c == ".")
                    mag = float(cleaned) if cleaned else 0.0
                except ValueError:
                    mag = 0.0
            
            hail_size = mag if event_type == "HAIL" else 0.0
            wind_speed = mag if event_type == "WIND" else 0.0
            
            lat = float(geom.get("y", 0.0))
            lon = float(geom.get("x", 0.0))
            
            if lat == 0.0 or lon == 0.0:
                continue
                
            valid_time = attrs.get("valid_time", "")
            # Convert "2026-08-15 17:55:00+00" to ISO "2026-08-15T17:55:00Z"
            report_time_utc = ""
            if valid_time:
                try:
                    parts = valid_time.split("+")
                    base_time = parts[0].strip()
                    if " " in base_time:
                        base_time = base_time.replace(" ", "T")
                    report_time_utc = f"{base_time}Z"
                except Exception:
                    report_time_utc = datetime.now(UTC).isoformat()
            else:
                report_time_utc = datetime.now(UTC).isoformat()
                
            # Repurpose "county" to store the location description (loc_desc + state)
            loc_desc = attrs.get("loc_desc", "")
            state = attrs.get("state", "")
            location_str = f"{loc_desc}, {state}" if state else loc_desc
            location_str = normalize_nws_location(location_str)
            
            report = {
                "id": str(objectid),
                "event_type": event_type,
                "hail_size_inches": hail_size,
                "wind_speed_mph": wind_speed,
                "latitude": lat,
                "longitude": lon,
                "county": location_str,  # Repurposed county field
                "report_time_utc": report_time_utc,
                "remarks": attrs.get("remarks", ""),
                "loc_desc": loc_desc,
                "state": state
            }
            parsed_reports.append(report)
            
        logger.info("nws_feed_parsed", parsed_count=len(parsed_reports))
        return parsed_reports
