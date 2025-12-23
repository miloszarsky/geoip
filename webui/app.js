/**
 * GeoIP Lookup - Frontend Application
 */

(function() {
    'use strict';

    const CONFIG = {
        apiBaseUrl: '/api',
        healthCheckInterval: 30000,
    };

    // DOM Elements
    const elements = {
        form: document.getElementById('lookup-form'),
        ipInput: document.getElementById('ip-input'),
        lookupBtn: document.getElementById('lookup-btn'),
        btnText: document.querySelector('.btn-text'),
        btnLoading: document.querySelector('.btn-loading'),
        errorSection: document.getElementById('error-section'),
        errorMessage: document.getElementById('error-message'),
        resultsSection: document.getElementById('results-section'),
        toggleJsonBtn: document.getElementById('toggle-json-btn'),
        rawJsonSection: document.getElementById('raw-json-section'),
        rawJson: document.getElementById('raw-json'),
        apiStatus: document.getElementById('api-status'),
        mapContainer: document.getElementById('map-container'),
        worldMap: document.getElementById('world-map'),
        mapInfo: document.getElementById('map-info'),
        resultIp: document.getElementById('result-ip'),
        resultAsn: document.getElementById('result-asn'),
        resultOrg: document.getElementById('result-org'),
        resultCountry: document.getElementById('result-country'),
        resultRegion: document.getElementById('result-region'),
        resultCity: document.getElementById('result-city'),
        resultPostal: document.getElementById('result-postal'),
        resultContinent: document.getElementById('result-continent'),
        resultCoords: document.getElementById('result-coords'),
        resultAccuracy: document.getElementById('result-accuracy'),
        resultTimezone: document.getElementById('result-timezone'),
        resultEu: document.getElementById('result-eu'),
        resultMap: document.getElementById('result-map'),
    };

    // Map configuration based on MapSVG geoViewBox
    const MAP = {
        width: 1009.6727,
        height: 665.96301,
        minLon: -169.110266,
        maxLon: 190.486279,
        minLat: -58.508473,
        maxLat: 83.600842
    };

    let isLoading = false;
    let mapLoaded = false;

    async function init() {
        elements.form.addEventListener('submit', handleSubmit);
        elements.toggleJsonBtn.addEventListener('click', toggleRawJson);
        elements.ipInput.addEventListener('input', clearError);

        // Load the map
        await loadMap();

        checkApiHealth();
        setInterval(checkApiHealth, CONFIG.healthCheckInterval);

        elements.ipInput.focus();

        // Check for IP in URL query parameter
        const urlParams = new URLSearchParams(window.location.search);
        const ipParam = urlParams.get('ip');
        if (ipParam && isValidIpFormat(ipParam)) {
            elements.ipInput.value = ipParam;
            performLookup(ipParam);
        }
    }

    async function loadMap() {
        try {
            const response = await fetch('world-map.svg');
            const svgText = await response.text();

            // Parse the SVG
            const parser = new DOMParser();
            const svgDoc = parser.parseFromString(svgText, 'image/svg+xml');
            const svgElement = svgDoc.querySelector('svg');

            if (svgElement) {
                // Copy attributes
                elements.worldMap.setAttribute('viewBox', `0 0 ${MAP.width} ${MAP.height}`);
                elements.worldMap.setAttribute('preserveAspectRatio', 'xMidYMid meet');

                // Copy all paths (countries)
                const paths = svgElement.querySelectorAll('path');
                paths.forEach(path => {
                    const newPath = path.cloneNode(true);
                    elements.worldMap.appendChild(newPath);
                });

                mapLoaded = true;
            }
        } catch (error) {
            console.error('Failed to load map:', error);
        }
    }

    async function handleSubmit(event) {
        event.preventDefault();
        const ip = elements.ipInput.value.trim();

        if (!ip) {
            showError('Please enter an IP address');
            return;
        }

        if (!isValidIpFormat(ip)) {
            showError('Invalid IP address format');
            return;
        }

        await performLookup(ip);
    }

    async function performLookup(ip) {
        if (isLoading) return;

        setLoading(true);
        hideError();
        hideResults();

        try {
            const response = await fetch(`${CONFIG.apiBaseUrl}/lookup/${encodeURIComponent(ip)}`);
            const data = await response.json();

            if (!response.ok) {
                throw new Error(data.detail || data.error || `HTTP ${response.status}`);
            }

            displayResults(data);
            // Update URL with query parameter
            const newUrl = `${window.location.pathname}?ip=${encodeURIComponent(ip)}`;
            window.history.replaceState({}, '', newUrl);

        } catch (error) {
            console.error('Lookup error:', error);
            if (error.name === 'TypeError' && error.message.includes('fetch')) {
                showError('Unable to connect to the API');
            } else {
                showError(error.message || 'An unexpected error occurred');
            }
        } finally {
            setLoading(false);
        }
    }

    function displayResults(data) {
        // IP Information
        elements.resultIp.textContent = data.ip || '-';
        elements.resultAsn.textContent = data.asn ? `AS${data.asn}` : '-';
        elements.resultOrg.textContent = data.asn_org || '-';

        // Location
        elements.resultCountry.textContent = formatCountry(data.country_name, data.country_code);
        elements.resultRegion.textContent = formatRegion(data.subdivision_name, data.subdivision_code);
        elements.resultCity.textContent = data.city_name || '-';
        elements.resultPostal.textContent = data.postal_code || '-';

        // Geography
        elements.resultContinent.textContent = formatContinent(data.continent_name, data.continent_code);
        elements.resultCoords.textContent = formatCoordinates(data.latitude, data.longitude);
        elements.resultAccuracy.textContent = data.accuracy_radius ? `~${data.accuracy_radius} km` : '-';
        elements.resultTimezone.textContent = data.timezone || '-';

        // Additional Info
        elements.resultEu.textContent = data.is_in_european_union === true ? 'Yes' : data.is_in_european_union === false ? 'No' : '-';
        elements.resultMap.innerHTML = formatMapLink(data.latitude, data.longitude);

        // Raw JSON
        elements.rawJson.textContent = JSON.stringify(data, null, 2);

        // Update map
        updateMap(data);

        elements.resultsSection.hidden = false;
    }

    function updateMap(data) {
        const lat = data.latitude;
        const lon = data.longitude;

        // Update info overlay
        const parts = [];
        if (data.city_name) parts.push(data.city_name);
        if (data.country_name) parts.push(data.country_name);

        let infoHtml = `<strong>${data.ip}</strong>`;
        if (parts.length > 0) {
            infoHtml += `<span class="location">${parts.join(', ')}</span>`;
        }
        if (lat != null && lon != null) {
            const latStr = Math.abs(lat).toFixed(4) + (lat >= 0 ? 'N' : 'S');
            const lonStr = Math.abs(lon).toFixed(4) + (lon >= 0 ? 'E' : 'W');
            infoHtml += `<span class="coords">${latStr}, ${lonStr}</span>`;
        }
        elements.mapInfo.innerHTML = infoHtml;

        // Highlight the country
        if (data.country_code) {
            highlightCountry(data.country_code);
        }
    }

    function highlightCountry(countryCode) {
        // Reset all countries
        const allPaths = elements.worldMap.querySelectorAll('path');
        allPaths.forEach(path => {
            path.classList.remove('highlighted');
        });

        // Highlight the target country
        const countryPath = elements.worldMap.querySelector(`#${countryCode.toUpperCase()}`);
        if (countryPath) {
            countryPath.classList.add('highlighted');
        }
    }

    function formatCountry(name, code) {
        if (!name && !code) return '-';
        if (name && code) {
            const flag = countryCodeToFlag(code);
            return `${flag} ${name} (${code})`;
        }
        return name || code;
    }

    function formatRegion(name, code) {
        if (!name && !code) return '-';
        if (name && code) return `${name} (${code})`;
        return name || code;
    }

    function formatContinent(name, code) {
        if (!name && !code) return '-';
        if (name && code) return `${name} (${code})`;
        return name || code;
    }

    function formatCoordinates(lat, lon) {
        if (lat == null || lon == null) return '-';
        const latDir = lat >= 0 ? 'N' : 'S';
        const lonDir = lon >= 0 ? 'E' : 'W';
        return `${Math.abs(lat).toFixed(4)}${latDir}, ${Math.abs(lon).toFixed(4)}${lonDir}`;
    }

    function formatMapLink(lat, lon) {
        if (lat == null || lon == null) return '-';
        const url = `https://www.openstreetmap.org/?mlat=${lat}&mlon=${lon}&zoom=10`;
        return `<a href="${url}" target="_blank" rel="noopener">View on OpenStreetMap</a>`;
    }

    function countryCodeToFlag(code) {
        if (!code || code.length !== 2) return '';
        const codePoints = [...code.toUpperCase()].map(c => 0x1F1E6 + c.charCodeAt(0) - 65);
        return String.fromCodePoint(...codePoints);
    }

    function toggleRawJson() {
        const isHidden = elements.rawJsonSection.hidden;
        elements.rawJsonSection.hidden = !isHidden;
        elements.toggleJsonBtn.textContent = isHidden ? 'Hide Raw JSON' : 'Show Raw JSON';
    }

    function isValidIpFormat(ip) {
        const ipv4 = /^(\d{1,3}\.){3}\d{1,3}$/;
        const ipv6 = /^([0-9a-fA-F]{0,4}:){2,7}[0-9a-fA-F]{0,4}$|^::([0-9a-fA-F]{1,4}:){0,5}[0-9a-fA-F]{1,4}$|^([0-9a-fA-F]{1,4}:){1,6}:$|^::$/;

        if (ipv4.test(ip)) {
            return ip.split('.').every(n => parseInt(n, 10) <= 255);
        }
        return ipv6.test(ip);
    }

    function setLoading(loading) {
        isLoading = loading;
        elements.lookupBtn.disabled = loading;
        elements.ipInput.disabled = loading;
        elements.btnText.hidden = loading;
        elements.btnLoading.hidden = !loading;
    }

    function showError(message) {
        elements.errorMessage.textContent = message;
        elements.errorSection.hidden = false;
    }

    function hideError() {
        elements.errorSection.hidden = true;
    }

    function clearError() {
        if (!elements.errorSection.hidden) hideError();
    }

    function hideResults() {
        elements.resultsSection.hidden = true;
        elements.rawJsonSection.hidden = true;
        elements.toggleJsonBtn.textContent = 'Show Raw JSON';
        elements.mapInfo.innerHTML = '';
        // Reset country highlights
        if (mapLoaded) {
            const allPaths = elements.worldMap.querySelectorAll('path');
            allPaths.forEach(path => {
                path.classList.remove('highlighted');
            });
        }
    }

    async function checkApiHealth() {
        try {
            const response = await fetch(`${CONFIG.apiBaseUrl}/health`);
            if (response.ok) {
                const data = await response.json();
                updateApiStatus(data.status, data.databases_loaded);
            } else {
                updateApiStatus('offline', false);
            }
        } catch (error) {
            updateApiStatus('offline', false);
        }
    }

    function updateApiStatus(status, dbLoaded) {
        const el = elements.apiStatus;
        el.classList.remove('status-checking', 'status-healthy', 'status-degraded', 'status-offline');

        switch (status) {
            case 'healthy':
                el.classList.add('status-healthy');
                el.textContent = 'Healthy';
                break;
            case 'degraded':
                el.classList.add('status-degraded');
                el.textContent = dbLoaded ? 'Degraded' : 'Waiting for DB';
                break;
            default:
                el.classList.add('status-offline');
                el.textContent = 'Offline';
        }
    }

    window.hideError = hideError;

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

})();
