/**
 * Wickham Roofing CRM - Unified Storm Activity Monitor Module
 * Centralizes API fetching, rendering, date/time formatting, and WebSocket filtering.
 */

const StormRadar = {
    /**
     * Formats a date string into a short date format (e.g., "Aug 17").
     */
    formatShortDate(dateStr) {
        if (!dateStr) return '';
        try {
            const dateObj = new Date(dateStr);
            if (isNaN(dateObj.getTime())) return dateStr;
            return dateObj.toLocaleDateString([], { month: 'short', day: 'numeric' });
        } catch (e) {
            return dateStr;
        }
    },

    /**
     * Formats a date string into a time format (e.g., "02:30 PM").
     */
    formatTime(dateStr) {
        if (!dateStr) return '';
        try {
            const dateObj = new Date(dateStr);
            if (isNaN(dateObj.getTime())) return dateStr;
            return dateObj.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        } catch (e) {
            return dateStr;
        }
    },

    /**
     * Formats a date string into a combined date and time format (e.g., "Aug 28, 05:34 PM").
     */
    formatDateTime(dateStr) {
        if (!dateStr) return '';
        try {
            const dateObj = new Date(dateStr);
            if (isNaN(dateObj.getTime())) return dateStr;
            const d = dateObj.toLocaleDateString([], { month: 'short', day: 'numeric' });
            const t = dateObj.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
            return `${d}, ${t}`;
        } catch (e) {
            return dateStr;
        }
    },

    /**
     * Fetches recent storm events from the backend.
     */
    async fetchRecentStorms(windowHours = 72, token = null, minHail = null, minWind = null) {
        const headers = {};
        if (token) {
            headers['x-internal-token'] = token;
        }
        let url = `/api/storms/recent?window_hours=${windowHours}`;
        if (minHail !== null) url += `&min_hail=${minHail}`;
        if (minWind !== null) url += `&min_wind=${minWind}`;
        const response = await fetch(url, { headers });
        if (!response.ok) {
            throw new Error(`Failed to fetch recent storms: HTTP ${response.status}`);
        }
        return await response.json();
    },

    /**
     * Fetches storm activity summary from the backend.
     */
    async fetchStormSummary(windowHours = 72, token = null, minHail = null, minWind = null) {
        const headers = {};
        if (token) {
            headers['x-internal-token'] = token;
        }
        let url = `/api/storms/summary?window_hours=${windowHours}`;
        if (minHail !== null) url += `&min_hail=${minHail}`;
        if (minWind !== null) url += `&min_wind=${minWind}`;
        const response = await fetch(url, { headers });
        if (!response.ok) {
            throw new Error(`Failed to fetch storm summary: HTTP ${response.status}`);
        }
        return await response.json();
    },

    /**
     * Client-side WebSocket event filter enforcing minimum magnitude thresholds.
     * Returns true if event is valid (Hail >= 1.0" or Wind >= 40.0 mph or Tornado), false if ignored.
     */
    filterWebSocketAlert(data) {
        if (!data || !data.event_type) return false;
        const minHail = (typeof window !== 'undefined' && typeof window.STORM_MIN_HAIL_INCHES !== 'undefined') ? window.STORM_MIN_HAIL_INCHES : 1.0;
        const minWind = (typeof window !== 'undefined' && typeof window.STORM_MIN_WIND_MPH !== 'undefined') ? window.STORM_MIN_WIND_MPH : 50.0;
        const etype = data.event_type.toUpperCase();
        if (etype === 'HAIL') {
            const hail = parseFloat(data.hail_size_inches);
            return !isNaN(hail) && hail >= minHail;
        }
        if (etype === 'WIND') {
            const wind = parseFloat(data.wind_speed_mph);
            return !isNaN(wind) && wind >= minWind;
        }
        return true; // Keep tornado/other significant events
    },

    /**
     * Renders a storm event card.
     */
    renderAlertItem(alert, isAdmin = false) {
        const dateTimeStr = this.formatDateTime(alert.report_time_utc);
        
        let detailStr = '';
        let badgeColor = 'bg-red-900/80 text-red-300 border-red-700';
        
        const etype = alert.event_type ? alert.event_type.toUpperCase() : 'UNKNOWN';
        if (etype === 'HAIL') {
            badgeColor = 'bg-amber-900/80 text-amber-300 border-amber-600';
            const hailVal = parseFloat(alert.hail_size_inches || 0).toFixed(2);
            detailStr = isAdmin ? `${hailVal}" Hail` : `☄️ ${hailVal}" Hail`;
        } else if (etype === 'WIND') {
            badgeColor = 'bg-blue-900/80 text-blue-300 border-blue-600';
            const windVal = Math.round(alert.wind_speed_mph || 0);
            detailStr = isAdmin ? `${windVal} mph Wind` : `💨 ${windVal} mph Wind`;
        } else if (etype === 'TORNADO') {
            badgeColor = 'bg-red-950/80 text-red-400 border-red-600 animate-pulse';
            detailStr = isAdmin ? `Tornado` : `🌪️ Tornado`;
        } else {
            detailStr = 'Storm Event';
        }
        
        if (isAdmin) {
            return `
                <div class="bg-gray-950/80 border border-gray-800 p-2 rounded text-xs text-left">
                    <div class="flex justify-between items-center mb-1">
                        <span class="font-bold text-purple-400">${alert.county || 'Unknown'}</span>
                        <span class="text-[9px] text-gray-500">${dateTimeStr}</span>
                    </div>
                    <div class="flex justify-between items-center">
                        <span class="text-white font-medium">${detailStr}</span>
                        <span class="text-[9px] px-1 py-0.2 rounded border ${badgeColor}">${alert.event_type || 'UNKNOWN'}</span>
                    </div>
                </div>
            `;
        } else {
            return `
                <div class="bg-gray-900/80 border border-gray-800 p-3 rounded-lg shadow-sm hover:border-purple-500/50 transition-colors text-left">
                    <div class="flex justify-between items-start mb-1">
                        <span class="text-xs font-bold font-mono text-purple-400">${alert.county || 'Unknown Location'}</span>
                        <span class="text-[10px] text-gray-500">${dateTimeStr}</span>
                    </div>
                    <div class="flex justify-between items-center mt-1">
                        <div class="text-sm font-bold text-white">${detailStr}</div>
                        <span class="text-[10px] border px-2 py-0.5 rounded-full font-bold ${badgeColor}">${alert.event_type || 'UNKNOWN'}</span>
                    </div>
                    ${alert.remarks ? `<p class="text-xs text-gray-400 mt-1 italic line-clamp-2">${alert.remarks}</p>` : ''}
                </div>
            `;
        }
    }
};

window.StormRadar = StormRadar;
