#!/bin/bash

# Connection Analyzer with GeoIP lookup
# Analyzes active connections on ports 80/443 and enriches with GeoIP data
# Supports both IPv4 and IPv6 addresses
# Usage: curl geoip.master.cz/script/con_analyzer_auth.sh | bash -
#        curl geoip.master.cz/script/con_analyzer_auth.sh | bash -s -- 20  # Top 20

set -euo pipefail

# Configuration
GEOIP_API="127.0.0.1:8080/api/lookup"
TOP_COUNT=${1:-10}
OUTPUT_FILE="/tmp/raw_output.txt"

# Get connections using netstat (IPv4 and IPv6)
get_connections() {
    netstat -tunp 2>/dev/null | awk '
    /:443|:80/ {
        addr = $5

        # Skip if no address
        if (addr == "" || addr == "*:*") next

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

        print ip
    }'
}

# Check if IP is IPv6
is_ipv6() {
    [[ "$1" == *:* ]]
}

# Get IPv6 /48 prefix (for subnet grouping)
get_ipv6_prefix() {
    local ip=$1
    # Extract first 3 groups (approximately /48)
    echo "$ip" | awk -F: '{
        if (NF >= 3) {
            printf "%s:%s:%s", $1, $2, $3
        } else {
            print $0
        }
    }'
}

# GeoIP lookup - returns: CC,COUNTRY,ASN,ASN_NAME
get_geoip() {
    local ip=$1
    local result

    result=$(curl -sk --connect-timeout 3 --max-time 5 "${GEOIP_API}/${ip}" 2>/dev/null) || {
        echo ",,,"
        return
    }

    # Parse JSON response - extract fields
    local cc country asn asn_name
    cc=$(echo "$result" | grep -o '"country_code":"[^"]*"' | cut -d'"' -f4)
    country=$(echo "$result" | grep -o '"country_name":"[^"]*"' | cut -d'"' -f4)
    asn=$(echo "$result" | grep -o '"asn":[0-9]*' | cut -d':' -f2)
    asn_name=$(echo "$result" | grep -o '"asn_org":"[^"]*"' | cut -d'"' -f4)

    echo "${cc:-},${country:-},${asn:-},${asn_name:-}"
}

# Print table with data
print_table() {
    local title=$1
    local data=$2
    local show_subnet=${3:-false}
    local ip_type=${4:-ipv4}

    [[ -z "$data" ]] && return

    echo -e "\n${title}"
    printf "%-8s %-40s %-5s %-20s %-10s %s\n" "--------" "----------------------------------------" "-----" "--------------------" "----------" "--------------------"
    printf "%-8s %-40s %-5s %-20s %-10s %s\n" "COUNT" "IP" "CC" "COUNTRY" "ASN" "ASN_NAME"
    printf "%-8s %-40s %-5s %-20s %-10s %s\n" "--------" "----------------------------------------" "-----" "--------------------" "----------" "--------------------"

    while IFS= read -r record; do
        [[ -z "$record" ]] && continue

        local count ip geoip cc country asn asn_name
        count=$(awk '{print $1}' <<< "$record")

        if [[ "$show_subnet" == "true" ]]; then
            local sub
            sub=$(awk '{print $2}' <<< "$record")
            if [[ "$ip_type" == "ipv6" ]]; then
                # Find most common IP in this /48 prefix
                ip=$(grep "^${sub}" <<< "$connections" | sort | uniq -c | sort -rn | head -1 | awk '{print $2}')
            else
                # IPv4 /24 subnet
                ip=$(grep "^${sub}\." <<< "$connections" | sort | uniq -c | sort -rn | head -1 | awk '{print $2}')
            fi
        else
            ip=$(awk '{print $2}' <<< "$record")
        fi

        [[ -z "$ip" ]] && continue

        geoip=$(get_geoip "$ip")
        IFS=',' read -r cc country asn asn_name <<< "$geoip"

        printf "%-8s %-40s %-5s %-20s %-10s %s\n" "$count" "$ip" "$cc" "$country" "$asn" "$asn_name"
        echo "${count}|${ip}|${cc}|${country}|${asn}|${asn_name}" >> "$OUTPUT_FILE"
    done <<< "$data"
}

# Main
connections=$(get_connections)

if [[ -z "$connections" ]]; then
    echo "No active connections on ports 80/443"
    exit 0
fi

rm -f "$OUTPUT_FILE"

# Separate IPv4 and IPv6
ipv4_connections=$(grep -v ':' <<< "$connections" || true)
ipv6_connections=$(grep ':' <<< "$connections" || true)

# === IPv4 Section ===
if [[ -n "$ipv4_connections" ]]; then
    # TOP IPv4 IPs
    top_ips=$(sort <<< "$ipv4_connections" | uniq -c | sort -rn | head -n "$TOP_COUNT")
    print_table "TOP ${TOP_COUNT} IPv4 connections (port 80/443)" "$top_ips" false ipv4

    # TOP IPv4 Subnets (/24)
    top_subnets=$(awk -F. '{print $1"."$2"."$3}' <<< "$ipv4_connections" | sort | uniq -c | sort -rn | head -n "$TOP_COUNT")
    connections="$ipv4_connections"
    print_table "TOP ${TOP_COUNT} IPv4 subnets /24 (port 80/443)" "$top_subnets" true ipv4
fi

# === IPv6 Section ===
if [[ -n "$ipv6_connections" ]]; then
    # TOP IPv6 IPs
    top_ips=$(sort <<< "$ipv6_connections" | uniq -c | sort -rn | head -n "$TOP_COUNT")
    print_table "TOP ${TOP_COUNT} IPv6 connections (port 80/443)" "$top_ips" false ipv6

    # TOP IPv6 Prefixes (/48)
    top_prefixes=""
    while IFS= read -r ip; do
        get_ipv6_prefix "$ip"
    done <<< "$ipv6_connections" | sort | uniq -c | sort -rn | head -n "$TOP_COUNT" > /tmp/ipv6_prefixes.txt
    top_prefixes=$(cat /tmp/ipv6_prefixes.txt)

    if [[ -n "$top_prefixes" ]]; then
        connections="$ipv6_connections"
        print_table "TOP ${TOP_COUNT} IPv6 prefixes /48 (port 80/443)" "$top_prefixes" true ipv6
    fi
    rm -f /tmp/ipv6_prefixes.txt
fi

echo ""
