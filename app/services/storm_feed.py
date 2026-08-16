import math
import httpx
import structlog
from datetime import datetime, timezone

logger = structlog.get_logger("app.services.storm_feed")

_county_cache = {}

def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate the great-circle distance between two points in miles."""
    R = 3958.8  # Earth radius in miles
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

async def get_county_for_coordinates(lat: float, lon: float) -> str:
    """Reverse geocode coordinates using OpenStreetMap Nominatim with caching."""
    # Round to 3 decimal places to group close locations and limit external API hits
    key = (round(lat, 3), round(lon, 3))
    if key in _county_cache:
        return _county_cache[key]

    url = "https://nominatim.openstreetmap.org/reverse"
    headers = {
        "User-Agent": "WickhamRoofingCRM/1.0 (contact@wickhamroofing.com)"
    }
    params = {
        "lat": str(lat),
        "lon": str(lon),
        "format": "json"
    }
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, params=params, headers=headers, timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                county = data.get("address", {}).get("county")
                if not county:
                    # Fallback to city or other keys if county is missing
                    county = data.get("address", {}).get("city") or data.get("address", {}).get("town") or "Unknown County"
                _county_cache[key] = county
                logger.debug("reverse_geocode_success", lat=lat, lon=lon, county=county)
                return county
    except Exception as e:
        logger.warning("reverse_geocode_failed", error=str(e), lat=lat, lon=lon)

    return "Unknown County"

class NWSLiveStormFeed:
    """Service to fetch and parse public storm data from NOAA NWS ArcGIS REST service."""
    
    URL = "https://mapservices.weather.noaa.gov/vector/rest/services/obs/nws_local_storm_reports/MapServer/0/query"

    async def fetch_recent_reports(self) -> list[dict]:
        """Fetch storm reports from the last 24h for GA and FL."""
        params = {
            "where": "state in ('GA', 'FL')",
            "f": "json",
            "outFields": "objectid,wfo_id,wfo,lsr_validtime,descript,loc_desc,state,magnitude,units,remarks,valid_time",
            "returnGeometry": "true",
            "outSR": "4326"
        }
        
        logger.info("fetching_nws_storm_reports", url=self.URL)
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
                    report_time_utc = datetime.now(timezone.utc).isoformat()
            else:
                report_time_utc = datetime.now(timezone.utc).isoformat()
                
            # Perform reverse geocoding to get the county name
            county = await get_county_for_coordinates(lat, lon)
            
            report = {
                "id": str(objectid),
                "event_type": event_type,
                "hail_size_inches": hail_size,
                "wind_speed_mph": wind_speed,
                "latitude": lat,
                "longitude": lon,
                "county": county,
                "report_time_utc": report_time_utc,
                "remarks": attrs.get("remarks", ""),
                "loc_desc": attrs.get("loc_desc", ""),
                "state": attrs.get("state", "")
            }
            parsed_reports.append(report)
            
        logger.info("nws_feed_parsed", parsed_count=len(parsed_reports))
        return parsed_reports
