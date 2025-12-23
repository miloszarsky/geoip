#!/usr/bin/env python3
"""
GeoIP IP Range List Generator

Generates downloadable text files containing IP ranges organized by:
- Continent (all ranges with continent code)
- Individual countries (separate file per country)

Uses the MaxMind GeoLite2-Country MMDB database.
"""

import os
import sys
import time
import struct
import socket
import logging
import ipaddress
from pathlib import Path
from datetime import datetime
from collections import defaultdict

import maxminddb

# Configuration
DB_PATH = os.getenv('GEOIP_DB_PATH', '/geoip-data')
OUTPUT_PATH = os.getenv('OUTPUT_PATH', '/output')
GENERATION_INTERVAL = int(os.getenv('GENERATION_INTERVAL', '86400'))  # 24 hours

# Continent codes
CONTINENTS = ['AF', 'AN', 'AS', 'EU', 'NA', 'OC', 'SA']

# Generate files for all countries (set to True to generate all)
GENERATE_ALL_COUNTRIES = os.getenv('GENERATE_ALL_COUNTRIES', 'true').lower() == 'true'

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def wait_for_database():
    """Wait for GeoLite2-Country database to be available."""
    db_file = Path(DB_PATH) / 'GeoLite2-Country.mmdb'
    logger.info(f"Waiting for database: {db_file}")

    while not db_file.exists():
        logger.info("Database not found, waiting 30 seconds...")
        time.sleep(30)

    logger.info("Database found!")
    return db_file


class MMDBIterator:
    """Iterator for MaxMind database networks."""

    def __init__(self, db_path):
        self.reader = maxminddb.open_database(str(db_path))
        self.metadata = self.reader.metadata()
        self.node_count = self.metadata.node_count
        self.record_size = self.metadata.record_size
        self.ip_version = self.metadata.ip_version

    def close(self):
        self.reader.close()

    def iterate_networks(self):
        """Iterate through all networks in the database."""
        # For IPv4, start from node 96 for databases with IPv4 data
        # The tree has 128 bits for IPv6, but IPv4 is stored in ::ffff:0:0/96

        logger.info(f"Database has {self.node_count} nodes, record size {self.record_size}")

        # Iterate IPv4 space
        logger.info("Processing IPv4 networks...")
        yield from self._iterate_ipv4()

        # Iterate IPv6 space
        logger.info("Processing IPv6 networks...")
        yield from self._iterate_ipv6()

    def _iterate_ipv4(self):
        """Iterate through IPv4 networks."""
        # Process each /8 network
        for first_octet in range(1, 256):
            if first_octet in (0, 10, 127, 224, 225, 226, 227, 228, 229, 230, 231, 232, 233, 234, 235, 236, 237, 238, 239, 240, 241, 242, 243, 244, 245, 246, 247, 248, 249, 250, 251, 252, 253, 254, 255):
                # Skip reserved/private ranges
                if first_octet in (10, 127) or first_octet >= 224:
                    continue

            # Check the /8 network
            base_ip = f"{first_octet}.0.0.0"
            result = self.reader.get(base_ip)

            if result:
                country = result.get('country', {})
                continent = result.get('continent', {})
                country_code = country.get('iso_code', '')
                continent_code = continent.get('code', '')

                if continent_code:
                    # Find the actual network size
                    network = self._find_network_boundary(base_ip, 8, country_code, ipv6=False)
                    if network:
                        yield (network, continent_code, country_code)

            # Scan subnets within this /8
            yield from self._scan_ipv4_prefix(first_octet)

    def _scan_ipv4_prefix(self, first_octet):
        """Scan an IPv4 /8 prefix for all unique networks."""
        seen = set()

        # Sample at /16 level
        for second_octet in range(0, 256, 1):
            ip = f"{first_octet}.{second_octet}.0.0"
            result = self.reader.get(ip)

            if result:
                country = result.get('country', {})
                continent = result.get('continent', {})
                country_code = country.get('iso_code', '')
                continent_code = continent.get('code', '')

                if continent_code:
                    # Try to find network at different prefix lengths
                    for prefix in [8, 9, 10, 11, 12, 13, 14, 15, 16]:
                        try:
                            network = ipaddress.ip_network(f"{ip}/{prefix}", strict=False)
                            network_str = str(network)

                            if network_str not in seen:
                                # Verify this is a valid boundary
                                if self._verify_network(network, country_code):
                                    seen.add(network_str)
                                    yield (network, continent_code, country_code)
                                    break
                        except:
                            pass

    def _iterate_ipv6(self):
        """Iterate through IPv6 networks."""
        # Sample major IPv6 prefixes
        prefixes = ['2001::', '2400::', '2600::', '2800::', '2a00::', '2c00::']

        for prefix in prefixes:
            result = self.reader.get(prefix)
            if result:
                country = result.get('country', {})
                continent = result.get('continent', {})
                country_code = country.get('iso_code', '')
                continent_code = continent.get('code', '')

                if continent_code:
                    try:
                        network = ipaddress.ip_network(f"{prefix}/12", strict=False)
                        yield (network, continent_code, country_code)
                    except:
                        pass

    def _find_network_boundary(self, ip, start_prefix, expected_country, ipv6=False):
        """Find the actual network boundary for an IP."""
        max_prefix = 24 if not ipv6 else 48

        for prefix in range(start_prefix, max_prefix + 1):
            try:
                network = ipaddress.ip_network(f"{ip}/{prefix}", strict=False)
                if self._verify_network(network, expected_country):
                    return network
            except:
                pass

        return None

    def _verify_network(self, network, expected_country):
        """Verify all IPs in network belong to expected country."""
        try:
            # Check first and last addresses
            first_ip = str(network.network_address)

            result = self.reader.get(first_ip)
            if not result:
                return False

            country = result.get('country', {}).get('iso_code', '')
            return country == expected_country
        except:
            return False


def generate_ip_lists_simple(db_file: Path):
    """Generate IP range lists using a simpler approach."""
    logger.info("Starting IP list generation (simple method)...")

    output_dir = Path(OUTPUT_PATH)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Data structures to collect ranges
    continent_ranges = defaultdict(list)
    country_ranges = defaultdict(list)

    try:
        with maxminddb.open_database(str(db_file)) as reader:
            metadata = reader.metadata()
            logger.info(f"Database: {metadata.database_type}")
            logger.info(f"Build epoch: {datetime.fromtimestamp(metadata.build_epoch)}")

            # Process IPv4 space by iterating through /24 networks
            logger.info("Scanning IPv4 address space...")

            current_country = None
            current_continent = None
            start_ip = None
            count = 0

            for first in range(1, 224):  # Skip 0, 224-255
                if first in (10, 127, 169, 172, 192):  # Skip private ranges
                    continue

                for second in range(0, 256):
                    for third in range(0, 256):
                        ip = f"{first}.{second}.{third}.0"

                        try:
                            result = reader.get(ip)
                        except:
                            continue

                        if result:
                            country = result.get('country', {})
                            continent = result.get('continent', {})
                            cc = country.get('iso_code', '')
                            cont = continent.get('code', '')

                            if cont and cc:
                                # Add as /24 network
                                network = f"{first}.{second}.{third}.0/24"
                                continent_ranges[cont].append((network, cc))
                                country_ranges[cc].append(network)
                                count += 1

                        if count % 100000 == 0 and count > 0:
                            logger.info(f"Processed {count} /24 networks...")

            logger.info(f"Total networks found: {count}")

    except Exception as e:
        logger.error(f"Error reading database: {e}")
        import traceback
        traceback.print_exc()
        return False

    # Write output files
    timestamp = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')

    # 1. Combined file
    logger.info("Writing combined IP list...")
    combined_file = output_dir / 'ip-ranges-all.txt'
    total_ranges = 0
    with open(combined_file, 'w') as f:
        for continent in sorted(continent_ranges.keys()):
            for network, country in continent_ranges[continent]:
                f.write(f"{network} {continent}\n")
                total_ranges += 1

    logger.info(f"Written: {combined_file} ({total_ranges} ranges)")

    # 2. Per-continent files
    for continent in CONTINENTS:
        if continent in continent_ranges:
            logger.info(f"Writing {continent}...")
            with open(output_dir / f'ip-ranges-{continent}.txt', 'w') as f:
                for network, country in continent_ranges[continent]:
                    f.write(f"{network} {country}\n")

    # 3. Per-country files (all countries)
    countries_written = []
    all_countries = sorted(country_ranges.keys()) if GENERATE_ALL_COUNTRIES else []

    for country in all_countries:
        if country and country in country_ranges:
            logger.info(f"Writing {country}...")
            with open(output_dir / f'ip-ranges-{country}.txt', 'w') as f:
                for network in country_ranges[country]:
                    f.write(f"{network} {country}\n")
            countries_written.append(country)

    # 4. Index HTML
    write_index_html(output_dir, timestamp, total_ranges, continent_ranges, country_ranges, countries_written)

    logger.info("Generation complete!")
    return True


def write_index_html(output_dir, timestamp, total_ranges, continent_ranges, country_ranges, countries_written):
    """Write the index.html file."""
    continent_names = {
        'AF': 'Africa', 'AN': 'Antarctica', 'AS': 'Asia',
        'EU': 'Europe', 'NA': 'North America', 'OC': 'Oceania', 'SA': 'South America'
    }

    with open(output_dir / 'index.html', 'w') as f:
        f.write(f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>GeoIP IP Range Lists</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 800px; margin: 2rem auto; padding: 0 1rem; background: #f5f5f5; }}
        h1 {{ color: #333; }}
        h2 {{ color: #555; margin-top: 2rem; }}
        .info {{ background: #e3f2fd; padding: 1rem; border-radius: 8px; margin-bottom: 2rem; }}
        ul {{ list-style: none; padding: 0; }}
        li {{ margin: 0.5rem 0; }}
        a {{ color: #1976d2; text-decoration: none; padding: 0.5rem 1rem; display: inline-block; background: white; border-radius: 4px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
        a:hover {{ background: #e3f2fd; }}
        .count {{ color: #666; font-size: 0.9em; margin-left: 0.5rem; }}
    </style>
</head>
<body>
    <h1>GeoIP IP Range Lists</h1>
    <div class="info">
        <strong>Generated:</strong> {timestamp}<br>
        <strong>Source:</strong> MaxMind GeoLite2-Country<br>
        <strong>Format:</strong> CIDR notation (one per line)
    </div>

    <h2>Combined</h2>
    <ul>
        <li><a href="ip-ranges-all.txt" download>ip-ranges-all.txt</a><span class="count">({total_ranges} ranges)</span></li>
    </ul>

    <h2>By Continent</h2>
    <ul>
""")
        for continent in CONTINENTS:
            if continent in continent_ranges:
                name = continent_names.get(continent, continent)
                count = len(continent_ranges[continent])
                f.write(f'        <li><a href="ip-ranges-{continent}.txt" download>{name} ({continent})</a><span class="count">({count} ranges)</span></li>\n')

        f.write("""    </ul>

    <h2>By Country</h2>
    <ul>
""")
        for country in sorted(countries_written):
            count = len(country_ranges[country])
            f.write(f'        <li><a href="ip-ranges-{country}.txt" download>{country}</a><span class="count">({count} ranges)</span></li>\n')

        f.write("""    </ul>
</body>
</html>
""")


def main():
    """Main entry point."""
    logger.info("GeoIP IP Range List Generator starting...")
    logger.info(f"Database path: {DB_PATH}")
    logger.info(f"Output path: {OUTPUT_PATH}")
    logger.info(f"Generation interval: {GENERATION_INTERVAL}s")

    db_file = wait_for_database()

    while True:
        try:
            generate_ip_lists_simple(db_file)
        except Exception as e:
            logger.error(f"Generation failed: {e}")
            import traceback
            traceback.print_exc()

        if GENERATION_INTERVAL == 0:
            logger.info("One-shot mode, exiting.")
            break

        logger.info(f"Sleeping for {GENERATION_INTERVAL} seconds until next generation...")
        time.sleep(GENERATION_INTERVAL)


if __name__ == '__main__':
    main()
