"""
GeoIP Lookup REST API Service

A FastAPI-based REST API for IP geolocation lookups using MaxMind GeoLite2 databases.
Provides endpoints for looking up geographic information associated with IP addresses.
"""

import ipaddress
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional

import geoip2.database
import geoip2.errors
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

# Configure logging
logging.basicConfig(
    level=os.getenv("API_LOG_LEVEL", "info").upper(),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("geoip-api")

# Configuration
GEOIP_DATA_DIR = Path(os.getenv("GEOIP_DATA_DIR", "/geoip-data"))
CITY_DB_PATH = GEOIP_DATA_DIR / "GeoLite2-City.mmdb"
ASN_DB_PATH = GEOIP_DATA_DIR / "GeoLite2-ASN.mmdb"
COUNTRY_DB_PATH = GEOIP_DATA_DIR / "GeoLite2-Country.mmdb"

# Database reader instances (cached for performance)
city_reader: Optional[geoip2.database.Reader] = None
asn_reader: Optional[geoip2.database.Reader] = None
country_reader: Optional[geoip2.database.Reader] = None
db_load_time: Optional[datetime] = None


class GeoIPResponse(BaseModel):
    """Response model for IP geolocation lookup."""
    ip: str = Field(..., description="The queried IP address")

    # Country information
    country_code: Optional[str] = Field(None, description="ISO 3166-1 alpha-2 country code")
    country_name: Optional[str] = Field(None, description="Country name in English")

    # Subdivision (state/province)
    subdivision_code: Optional[str] = Field(None, description="ISO 3166-2 subdivision code")
    subdivision_name: Optional[str] = Field(None, description="Subdivision name in English")

    # City information
    city_name: Optional[str] = Field(None, description="City name in English")
    postal_code: Optional[str] = Field(None, description="Postal/ZIP code")

    # Geographic coordinates
    latitude: Optional[float] = Field(None, description="Latitude coordinate")
    longitude: Optional[float] = Field(None, description="Longitude coordinate")
    accuracy_radius: Optional[int] = Field(None, description="Accuracy radius in kilometers")

    # Timezone
    timezone: Optional[str] = Field(None, description="IANA timezone identifier")

    # ASN information
    asn: Optional[int] = Field(None, description="Autonomous System Number")
    asn_org: Optional[str] = Field(None, description="Autonomous System Organization name")

    # Metadata
    is_in_european_union: Optional[bool] = Field(None, description="Whether the country is in the EU")
    continent_code: Optional[str] = Field(None, description="Continent code")
    continent_name: Optional[str] = Field(None, description="Continent name")


class HealthResponse(BaseModel):
    """Response model for health check endpoint."""
    status: str
    databases_loaded: bool
    city_db_available: bool
    asn_db_available: bool
    country_db_available: bool
    last_load_time: Optional[str] = None
    message: Optional[str] = None


class ErrorResponse(BaseModel):
    """Response model for error responses."""
    error: str
    detail: str
    ip: Optional[str] = None


def load_databases() -> bool:
    """
    Load or reload the GeoIP database files.
    Returns True if at least the City database was loaded successfully.
    """
    global city_reader, asn_reader, country_reader, db_load_time

    # Close existing readers if any
    for reader in [city_reader, asn_reader, country_reader]:
        if reader:
            try:
                reader.close()
            except Exception:
                pass

    city_reader = None
    asn_reader = None
    country_reader = None

    success = False

    # Load City database (primary)
    if CITY_DB_PATH.exists():
        try:
            city_reader = geoip2.database.Reader(str(CITY_DB_PATH))
            logger.info(f"Loaded City database from {CITY_DB_PATH}")
            success = True
        except Exception as e:
            logger.error(f"Failed to load City database: {e}")
    else:
        logger.warning(f"City database not found at {CITY_DB_PATH}")

    # Load ASN database (optional)
    if ASN_DB_PATH.exists():
        try:
            asn_reader = geoip2.database.Reader(str(ASN_DB_PATH))
            logger.info(f"Loaded ASN database from {ASN_DB_PATH}")
        except Exception as e:
            logger.error(f"Failed to load ASN database: {e}")
    else:
        logger.warning(f"ASN database not found at {ASN_DB_PATH}")

    # Load Country database (fallback)
    if COUNTRY_DB_PATH.exists():
        try:
            country_reader = geoip2.database.Reader(str(COUNTRY_DB_PATH))
            logger.info(f"Loaded Country database from {COUNTRY_DB_PATH}")
            if not success:
                success = True  # Country can serve as fallback
        except Exception as e:
            logger.error(f"Failed to load Country database: {e}")
    else:
        logger.warning(f"Country database not found at {COUNTRY_DB_PATH}")

    if success:
        db_load_time = datetime.utcnow()

    return success


def is_valid_ip(ip: str) -> bool:
    """Validate if the string is a valid IPv4 or IPv6 address."""
    try:
        ipaddress.ip_address(ip)
        return True
    except ValueError:
        return False


def is_private_ip(ip: str) -> bool:
    """Check if the IP address is private/reserved."""
    try:
        ip_obj = ipaddress.ip_address(ip)
        return ip_obj.is_private or ip_obj.is_loopback or ip_obj.is_reserved
    except ValueError:
        return False


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler for startup and shutdown."""
    # Startup: attempt to load databases
    logger.info("Starting GeoIP API service...")
    if load_databases():
        logger.info("GeoIP databases loaded successfully")
    else:
        logger.warning("GeoIP databases not available yet - service will retry on requests")

    yield

    # Shutdown: close database readers
    logger.info("Shutting down GeoIP API service...")
    for reader in [city_reader, asn_reader, country_reader]:
        if reader:
            try:
                reader.close()
            except Exception:
                pass


# Create FastAPI application
app = FastAPI(
    title="GeoIP Lookup API",
    description="REST API for IP geolocation lookups using MaxMind GeoLite2 databases",
    version="1.0.0",
    lifespan=lifespan
)

# Configure CORS for WebUI communication
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, restrict to specific origins
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


@app.get("/health", response_model=HealthResponse, tags=["Health"])
async def health_check():
    """
    Health check endpoint for container orchestration.
    Returns the status of the API and database availability.
    """
    databases_loaded = city_reader is not None or country_reader is not None

    return HealthResponse(
        status="healthy" if databases_loaded else "degraded",
        databases_loaded=databases_loaded,
        city_db_available=city_reader is not None,
        asn_db_available=asn_reader is not None,
        country_db_available=country_reader is not None,
        last_load_time=db_load_time.isoformat() if db_load_time else None,
        message="All systems operational" if databases_loaded else "Waiting for database files"
    )


@app.get("/reload", tags=["Management"])
async def reload_databases():
    """
    Manually trigger a reload of the GeoIP databases.
    Useful after database files have been updated.
    """
    success = load_databases()
    if success:
        return {"status": "success", "message": "Databases reloaded successfully"}
    else:
        raise HTTPException(
            status_code=503,
            detail="Failed to reload databases - files may not be available"
        )


@app.get(
    "/lookup/{ip}",
    response_model=GeoIPResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid IP address format"},
        404: {"model": ErrorResponse, "description": "IP not found in database"},
        503: {"model": ErrorResponse, "description": "Database not available"},
    },
    tags=["Lookup"]
)
async def lookup_ip(ip: str):
    """
    Look up geolocation information for an IP address.

    - **ip**: IPv4 or IPv6 address to look up

    Returns geographic and network information associated with the IP address.
    """
    # Validate IP address format
    if not is_valid_ip(ip):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid IP address format: {ip}"
        )

    # Check for private/reserved IPs
    if is_private_ip(ip):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot look up private or reserved IP address: {ip}"
        )

    # Try to load databases if not available
    if city_reader is None and country_reader is None:
        load_databases()

    # Check if databases are available
    if city_reader is None and country_reader is None:
        raise HTTPException(
            status_code=503,
            detail="GeoIP databases are not available. Please try again later.",
            headers={"Retry-After": "60"}
        )

    # Initialize response data
    response_data = {"ip": ip}

    # Look up in City database (primary source)
    if city_reader:
        try:
            city_result = city_reader.city(ip)

            # Country information
            if city_result.country:
                response_data["country_code"] = city_result.country.iso_code
                response_data["country_name"] = city_result.country.name
                response_data["is_in_european_union"] = city_result.country.is_in_european_union

            # Continent information
            if city_result.continent:
                response_data["continent_code"] = city_result.continent.code
                response_data["continent_name"] = city_result.continent.name

            # Subdivision (state/province)
            if city_result.subdivisions and len(city_result.subdivisions) > 0:
                subdivision = city_result.subdivisions[0]
                response_data["subdivision_code"] = subdivision.iso_code
                response_data["subdivision_name"] = subdivision.name

            # City information
            if city_result.city:
                response_data["city_name"] = city_result.city.name

            # Postal code
            if city_result.postal:
                response_data["postal_code"] = city_result.postal.code

            # Location coordinates
            if city_result.location:
                response_data["latitude"] = city_result.location.latitude
                response_data["longitude"] = city_result.location.longitude
                response_data["accuracy_radius"] = city_result.location.accuracy_radius
                response_data["timezone"] = city_result.location.time_zone

        except geoip2.errors.AddressNotFoundError:
            # Fall through to try Country database
            pass
        except Exception as e:
            logger.error(f"Error looking up IP {ip} in City database: {e}")

    # If no data from City DB, try Country database as fallback
    if "country_code" not in response_data and country_reader:
        try:
            country_result = country_reader.country(ip)

            if country_result.country:
                response_data["country_code"] = country_result.country.iso_code
                response_data["country_name"] = country_result.country.name
                response_data["is_in_european_union"] = country_result.country.is_in_european_union

            if country_result.continent:
                response_data["continent_code"] = country_result.continent.code
                response_data["continent_name"] = country_result.continent.name

        except geoip2.errors.AddressNotFoundError:
            raise HTTPException(
                status_code=404,
                detail=f"IP address {ip} not found in database"
            )
        except Exception as e:
            logger.error(f"Error looking up IP {ip} in Country database: {e}")

    # Look up ASN information
    if asn_reader:
        try:
            asn_result = asn_reader.asn(ip)
            response_data["asn"] = asn_result.autonomous_system_number
            response_data["asn_org"] = asn_result.autonomous_system_organization
        except geoip2.errors.AddressNotFoundError:
            pass  # ASN data is optional
        except Exception as e:
            logger.error(f"Error looking up ASN for IP {ip}: {e}")

    # If we still have no data, the IP wasn't found
    if len(response_data) == 1:  # Only 'ip' key present
        raise HTTPException(
            status_code=404,
            detail=f"IP address {ip} not found in database"
        )

    return GeoIPResponse(**response_data)


@app.get("/lookup", tags=["Lookup"])
async def lookup_ip_query(
    ip: str = Query(..., description="IPv4 or IPv6 address to look up")
):
    """
    Alternative lookup endpoint using query parameter.
    Redirects to the path-based lookup endpoint.
    """
    return await lookup_ip(ip)


@app.get("/", tags=["Info"])
async def root():
    """Root endpoint with API information."""
    return {
        "name": "GeoIP Lookup API",
        "version": "1.0.0",
        "description": "REST API for IP geolocation lookups",
        "endpoints": {
            "lookup": "/lookup/{ip}",
            "health": "/health",
            "reload": "/reload",
            "docs": "/docs"
        }
    }


# Custom exception handler for better error responses
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """Custom handler for HTTP exceptions with consistent error format."""
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": "error",
            "detail": exc.detail,
            "status_code": exc.status_code
        },
        headers=getattr(exc, "headers", None)
    )


if __name__ == "__main__":
    import uvicorn

    host = os.getenv("API_HOST", "0.0.0.0")
    port = int(os.getenv("API_PORT", "8000"))
    log_level = os.getenv("API_LOG_LEVEL", "info").lower()

    uvicorn.run(
        "main:app",
        host=host,
        port=port,
        log_level=log_level,
        reload=False
    )
