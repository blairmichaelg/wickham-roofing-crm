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
     * Formats a date string into human-friendly relative recency (e.g., "2d ago (Aug 15)").
     */
    formatRelativeTime(dateStr) {
        if (!dateStr) return '';
        try {
            const dateObj = new Date(dateStr);
            if (isNaN(dateObj.getTime())) return dateStr;
            const diffHours = Math.round((Date.now() - dateObj.getTime()) / (1000 * 60 * 60));
            if (diffHours <= 0) return 'Just now';
            if (diffHours < 24) return `${diffHours}h ago`;
            const diffDays = Math.floor(diffHours / 24);
            return `${diffDays}d ago (${this.formatShortDate(dateStr)})`;
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
     * Fetches ranked storm canvassing targets from the backend.
     */
    async fetchStormTargets(windowHours = 72, token = null) {
        const headers = {};
        if (token) {
            headers['x-internal-token'] = token;
        }
        const url = token ? `/api/field/storms/targets` : `/api/office/storms/targets?window_hours=${windowHours}&limit=8`;
        const response = await fetch(url, { headers });
        if (!response.ok) {
            throw new Error(`Failed to fetch storm targets: HTTP ${response.status}`);
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
    renderAlertItem(alert, isField = false) {
        const dateTimeStr = this.formatDateTime(alert.event_time_utc);
        let detailStr = '';
        let badgeColor = 'bg-gray-800 text-gray-300 border-gray-700';

        const etype = (alert.event_type || '').toUpperCase();
        if (etype === 'HAIL') {
            const size = parseFloat(alert.hail_size_inches) || 0;
            detailStr = `Hail: ${size.toFixed(2)}"`;
            badgeColor = 'bg-amber-950 text-amber-300 border-amber-700';
        } else if (etype === 'WIND') {
            const spd = Math.round(parseFloat(alert.wind_speed_mph) || 0);
            detailStr = `Wind: ${spd} mph`;
            badgeColor = 'bg-blue-950 text-blue-300 border-blue-700';
        } else if (etype === 'TORNADO') {
            detailStr = 'Tornado Activity';
            badgeColor = 'bg-red-950 text-red-300 border-red-700';
        } else {
            detailStr = alert.event_type || 'Event';
        }

        if (isField) {
            return `
                <div class="bg-gray-800/80 border border-gray-700 p-3 rounded-lg text-left shadow-sm">
                    <div class="flex justify-between items-start">
                        <span class="font-bold text-sm text-purple-300">${alert.county || 'Local Area'}</span>
                        <span class="text-xs text-gray-400">${dateTimeStr}</span>
                    </div>
                    <div class="flex justify-between items-center mt-1.5">
                        <span class="font-semibold text-white">${detailStr}</span>
                        <span class="text-[10px] border px-2 py-0.5 rounded-full font-bold uppercase ${badgeColor}">${alert.event_type || 'WEATHER'}</span>
                    </div>
                    ${alert.remarks ? `<p class="text-xs text-gray-400 mt-1 italic">${alert.remarks}</p>` : ''}
                </div>
            `;
        } else {
            return `
                <div class="bg-gray-900/80 border border-gray-800 p-2 rounded text-left hover:border-gray-700 transition-colors">
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
    },

    /**
     * Renders a standardized Canvassing Target ZIP card.
     */
    renderTargetZipCard(target, isAdmin = false, onSelectCallback = 'filterJobsByZip') {
        const zip = target.zip || target.zipcode || '';
        const loc = target.location || target.county || 'Target Area';
        const hailEvents = target.hail_events || 0;
        const windEvents = target.wind_events || 0;
        const maxHail = parseFloat(target.max_hail_inches || target.max_hail || 0);
        const maxWind = parseFloat(target.max_wind_mph || target.max_wind || 0);
        const hasTornado = target.has_tornado;
        const prio = target.priority_label || 'Standard';
        const timeStr = this.formatRelativeTime(target.latest_event_time_utc || target.last_event_utc || target.most_recent_event_utc);

        let details = [];
        if (hailEvents > 0) details.push(`${hailEvents} hail (max ${maxHail.toFixed(2)}")`);
        if (windEvents > 0) details.push(`${windEvents} wind (max ${Math.round(maxWind)} mph)`);
        if (hasTornado) details.push('🌪️ Tornado report');
        const detailStr = details.length > 0 ? details.join(' · ') : 'Qualifying storm activity';

        if (isAdmin) {
            return `
                <div class="bg-gray-950/80 border border-gray-800 p-2.5 rounded-lg text-xs text-left hover:border-purple-600/50 transition-colors flex justify-between items-center gap-2">
                    <div>
                        <div class="flex items-center gap-2">
                            <span class="font-bold text-white">📍 ZIP ${zip}</span>
                            <span class="text-[10px] text-purple-300 font-mono">${loc}</span>
                            <span class="text-[9px] px-1.5 py-0.2 rounded bg-purple-950/80 text-purple-300 border border-purple-800">${prio}</span>
                        </div>
                        <div class="text-[11px] text-gray-400 mt-0.5">${detailStr}</div>
                    </div>
                    <div class="text-right shrink-0">
                        <span class="text-[10px] text-gray-500 block">${timeStr}</span>
                    </div>
                </div>
            `;
        } else {
            return `
                <div class="bg-gray-900/90 border border-gray-700/80 hover:border-purple-500 p-3 rounded-lg text-left transition-all shadow-sm">
                    <div class="flex justify-between items-start gap-2">
                        <div>
                            <div class="flex items-center gap-2">
                                <span class="font-bold text-white text-sm">📍 ZIP ${zip}</span>
                                <span class="text-xs text-purple-300">${loc}</span>
                                <span class="text-[10px] px-2 py-0.5 rounded-full font-bold bg-purple-950 text-purple-300 border border-purple-700">${prio}</span>
                            </div>
                            <div class="text-xs text-gray-300 mt-1">${detailStr}</div>
                        </div>
                        <div class="text-right shrink-0">
                            <span class="text-[10px] text-gray-400 block">${timeStr}</span>
                        </div>
                    </div>
                    <div class="mt-2.5 flex items-center gap-2 pt-2 border-t border-gray-800">
                        <button type="button" onclick="${onSelectCallback}('${zip}')" class="text-xs bg-purple-900/60 hover:bg-purple-800 text-purple-200 px-2.5 py-1 rounded font-semibold transition-colors">
                            🔍 Filter Jobs (${zip})
                        </button>
                    </div>
                </div>
            `;
        }
    }
};

window.StormRadar = StormRadar;
