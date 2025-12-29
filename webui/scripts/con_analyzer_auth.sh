#!/bin/bash

# Connection Analyzer with GeoIP lookup
# Analyzes active connections on ports 80/443 and enriches with GeoIP data
# Supports both IPv4 and IPv6 addresses
#
# Environment variables:
#   GEOIP_SERVER - GeoIP server address (required, e.g., "geoip.example.com")
#   GEOIP_PROTO  - Protocol: http or https (default: https)
#   GEOIP_USER   - Basic auth username (optional)
#   GEOIP_PASS   - Basic auth password (optional)
#
# Usage:
#   curl <URL>/script/con_analyzer_auth.sh | GEOIP_SERVER="host:port" bash -
#   curl <URL>/script/con_analyzer_auth.sh | GEOIP_SERVER="host:port" GEOIP_USER="user" GEOIP_PASS="pass" bash -
#   curl <URL>/script/con_analyzer_auth.sh | GEOIP_SERVER="host:port" bash -s -- 20  # Top 20

set -euo pipefail

# Debug: show where script fails
trap 'echo "ERROR: Script failed at line $LINENO (command: $BASH_COMMAND)" >&2' ERR

# Check required tools
check_requirements() {
    local missing=()
    local tools="netstat awk curl grep cut sort uniq head"

    for tool in $tools; do
        if ! command -v "$tool" &>/dev/null; then
            missing+=("$tool")
        fi
    done

    if [[ ${#missing[@]} -gt 0 ]]; then
        echo "ERROR: Missing required tools: ${missing[*]}"
        echo ""
        echo "Install them with:"
        echo "  Debian/Ubuntu: apt install net-tools curl gawk coreutils grep"
        echo "  RHEL/CentOS:   yum install net-tools curl gawk coreutils grep"
        echo "  Alpine:        apk add net-tools curl gawk coreutils grep"
        exit 1
    fi
}

check_requirements

# Configuration
# Set GEOIP_SERVER environment variable or edit this line
GEOIP_SERVER="${GEOIP_SERVER:-YOUR_GEOIP_SERVER:8080}"
GEOIP_PROTO="${GEOIP_PROTO:-https}"  # http or https
GEOIP_USER="${GEOIP_USER:-}"
GEOIP_PASS="${GEOIP_PASS:-}"
GEOIP_API="${GEOIP_PROTO}://${GEOIP_SERVER}/api/lookup"
GEOIP_NETWORK_API="${GEOIP_PROTO}://${GEOIP_SERVER}/api/network"
TOP_COUNT=${1:-10}

# Build curl auth options
CURL_AUTH=""
if [[ -n "$GEOIP_USER" && -n "$GEOIP_PASS" ]]; then
    CURL_AUTH="-u ${GEOIP_USER}:${GEOIP_PASS}"
fi

# Get connections using netstat (IPv4 and IPv6)
# Output format: IP DIRECTION (IN/OUT)
get_connections() {
    netstat -tunp 2>/dev/null | awk '
    /:443|:80/ {
        local_addr = $4
        foreign_addr = $5

        # Skip if no address
        if (foreign_addr == "" || foreign_addr == "*:*") next

        # Determine direction: IN if local has :80/:443, OUT if foreign has :80/:443
        direction = "OUT"
        if (local_addr ~ /:443$/ || local_addr ~ /:80$/) {
            direction = "IN"
        }

        addr = foreign_addr

        # Handle IPv6 addresses (format: [ipv6]:port or ipv6.port)
        if (index(addr, "[") > 0) {
            # Format: [2001:db8::1]:443
            gsub(/\[|\]/, "", addr)
            n = split(addr, parts, ":")
            # Reconstruct IPv6 (all parts except last which is port)
            ip = ""
            for (i = 1; i < n; i++) {
                ip = ip (i > 1 ? ":" : "") parts[i]
            }
        } else if (match(addr, /^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+:[0-9]+$/)) {
            # IPv4 format: 1.2.3.4:443
            sub(/:[0-9]+$/, "", addr)
            ip = addr
        } else if (index(addr, ".") > 0 && index(addr, ":") > 0) {
            # IPv6 with port as .port (rare format)
            sub(/\.[0-9]+$/, "", addr)
            ip = addr
        } else {
            # Try to extract IPv6 without brackets
            # Format: 2001:db8::1.443 or just the address
            if (match(addr, /\.[0-9]+$/)) {
                sub(/\.[0-9]+$/, "", addr)
            }
            ip = addr
        }

        # Remove ::ffff: prefix (IPv4-mapped IPv6)
        gsub(/^::ffff:/, "", ip)

        # Skip private/local IPv4 addresses
        if (ip ~ /^127\./ || ip ~ /^10\./ || ip ~ /^192\.168\./) next
        if (ip ~ /^172\.(1[6-9]|2[0-9]|3[01])\./) next

        # Skip private/local IPv6 addresses
        if (ip ~ /^::1$/) next           # Loopback
        if (ip ~ /^fe80:/i) next         # Link-local
        if (ip ~ /^fc00:/i) next         # Unique local
        if (ip ~ /^fd[0-9a-f]{2}:/i) next # Unique local
        if (ip ~ /^::$/) next            # Unspecified

        # Skip empty or invalid
        if (ip == "" || ip ~ /^[0-9]+$/) next

        print ip, direction
    }'
}

# GeoIP lookup - returns: CC,COUNTRY,ASN,ASN_NAME
get_geoip() {
    local ip=$1
    local result

    result=$(curl -sk $CURL_AUTH --connect-timeout 3 --max-time 5 "${GEOIP_API}/${ip}" 2>/dev/null) || {
        echo ",,,"
        return
    }

    # Parse JSON response - extract fields (|| true to prevent exit on no match)
    local cc country asn asn_name
    cc=$(echo "$result" | grep -o '"country_code":"[^"]*"' | cut -d'"' -f4 || true)
    country=$(echo "$result" | grep -o '"country_name":"[^"]*"' | cut -d'"' -f4 || true)
    asn=$(echo "$result" | grep -o '"asn":[0-9]*' | cut -d':' -f2 || true)
    asn_name=$(echo "$result" | grep -o '"asn_org":"[^"]*"' | cut -d'"' -f4 || true)

    echo "${cc:-},${country:-},${asn:-},${asn_name:-}"
}

# Get real network/subnet for an IP - returns: NETWORK,ASN,ASN_ORG
get_network() {
    local ip=$1
    local result

    result=$(curl -sk $CURL_AUTH --connect-timeout 3 --max-time 5 "${GEOIP_NETWORK_API}/${ip}" 2>/dev/null) || {
        echo ",,"
        return
    }

    local network asn asn_org
    network=$(echo "$result" | grep -o '"network":"[^"]*"' | cut -d'"' -f4 || true)
    asn=$(echo "$result" | grep -o '"asn":[0-9]*' | cut -d':' -f2 || true)
    asn_org=$(echo "$result" | grep -o '"asn_org":"[^"]*"' | cut -d'"' -f4 || true)

    echo "${network:-},${asn:-},${asn_org:-}"
}

# Print table for individual IPs
print_table_ips() {
    local title=$1
    local data=$2

    [[ -z "$data" ]] && return

    echo -e "\n${title}"
    printf "%-8s %-4s %-40s %-5s %-20s %-10s %s\n" "--------" "----" "----------------------------------------" "-----" "--------------------" "----------" "--------------------"
    printf "%-8s %-4s %-40s %-5s %-20s %-10s %s\n" "COUNT" "DIR" "IP" "CC" "COUNTRY" "ASN" "ASN_NAME"
    printf "%-8s %-4s %-40s %-5s %-20s %-10s %s\n" "--------" "----" "----------------------------------------" "-----" "--------------------" "----------" "--------------------"

    while IFS= read -r record; do
        [[ -z "$record" ]] && continue

        local count ip dir geoip cc country asn asn_name
        count=$(awk '{print $1}' <<< "$record" || true)
        ip=$(awk '{print $2}' <<< "$record" || true)
        dir=$(awk '{print $3}' <<< "$record" || true)

        [[ -z "$ip" ]] && continue

        geoip=$(get_geoip "$ip")
        IFS=',' read -r cc country asn asn_name <<< "$geoip"

        printf "%-8s %-4s %-40s %-5s %-20s %-10s %s\n" "$count" "$dir" "$ip" "$cc" "$country" "$asn" "$asn_name"
    done <<< "$data"
}

# Print table for real subnets from API
print_table_subnets() {
    local title=$1
    local conn_data=$2

    [[ -z "$conn_data" ]] && return

    echo -e "\n${title}"
    echo "  (fetching real network info from API...)"

    # Get unique IPs and their networks
    declare -A network_counts      # network -> connection count
    declare -A network_unique_ips  # network -> unique IP count
    declare -A network_sample_ip   # network -> sample IP
    declare -A network_asn         # network -> ASN
    declare -A network_asn_org     # network -> ASN org

    # Extract just IPs (first column) for unique list
    local unique_ips
    unique_ips=$(awk '{print $1}' <<< "$conn_data" | sort -u || true)

    while IFS= read -r ip; do
        [[ -z "$ip" ]] && continue

        # Get network info for this IP
        local net_info network asn asn_org
        net_info=$(get_network "$ip")
        IFS=',' read -r network asn asn_org <<< "$net_info"

        [[ -z "$network" ]] && continue

        # Count connections for this IP (match IP at start of line)
        local ip_count
        ip_count=$(grep -c "^${ip} " <<< "$conn_data" || true)

        # Update network stats
        if [[ -z "${network_counts[$network]:-}" ]]; then
            network_counts[$network]=0
            network_unique_ips[$network]=0
            network_sample_ip[$network]="$ip"
            network_asn[$network]="$asn"
            network_asn_org[$network]="$asn_org"
        fi

        network_counts[$network]=$((${network_counts[$network]} + ip_count))
        network_unique_ips[$network]=$((${network_unique_ips[$network]} + 1))

    done <<< "$unique_ips"

    # Sort networks by connection count and display top N
    printf "\r%-8s %-6s %-22s %-18s %-10s %s\n" "--------" "------" "----------------------" "------------------" "----------" "--------------------"
    printf "%-8s %-6s %-22s %-18s %-10s %s\n" "CONN" "UNIQUE" "SUBNET" "SAMPLE_IP" "ASN" "ASN_NAME"
    printf "%-8s %-6s %-22s %-18s %-10s %s\n" "--------" "------" "----------------------" "------------------" "----------" "--------------------"

    # Create sorted output
    for network in "${!network_counts[@]}"; do
        echo "${network_counts[$network]} ${network_unique_ips[$network]} $network ${network_sample_ip[$network]} ${network_asn[$network]} ${network_asn_org[$network]}"
    done | sort -rn | head -n "$TOP_COUNT" | while IFS=' ' read -r count unique subnet sample_ip asn asn_org; do
        printf "%-8s %-6s %-22s %-18s %-10s %s\n" "$count" "$unique" "$subnet" "$sample_ip" "$asn" "$asn_org"
    done
}

# Main
connections=$(get_connections)

if [[ -z "$connections" ]]; then
    echo "No active connections on ports 80/443"
    exit 0
fi

# Separate IPv4 and IPv6
ipv4_connections=$(grep -v ':' <<< "$connections" || true)
ipv6_connections=$(grep ':' <<< "$connections" || true)

# === IPv4 Section ===
if [[ -n "$ipv4_connections" ]]; then
    # TOP IPv4 IPs
    top_ips=$(sort <<< "$ipv4_connections" | uniq -c | sort -rn | head -n "$TOP_COUNT" || true)
    print_table_ips "TOP ${TOP_COUNT} IPv4 connections (port 80/443)" "$top_ips"

    # TOP IPv4 Real Subnets (from API)
    print_table_subnets "TOP ${TOP_COUNT} IPv4 subnets (real networks)" "$ipv4_connections"
fi

# === IPv6 Section ===
if [[ -n "$ipv6_connections" ]]; then
    # TOP IPv6 IPs
    top_ips=$(sort <<< "$ipv6_connections" | uniq -c | sort -rn | head -n "$TOP_COUNT" || true)
    print_table_ips "TOP ${TOP_COUNT} IPv6 connections (port 80/443)" "$top_ips"

    # TOP IPv6 Real Subnets (from API)
    print_table_subnets "TOP ${TOP_COUNT} IPv6 subnets (real networks)" "$ipv6_connections"
fi

echo ""
