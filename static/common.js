window.InPlaneSight = {
    timeFormat: '24',
    aircraftTypeFormat: 'code',

    configure(config) {
        this.timeFormat = config.time_format === '12' ? '12' : '24';
        this.aircraftTypeFormat =
            config.aircraft_type_format === 'description' ? 'description' : 'code';
    },

    // Type label per the aircraft_type_format setting; falls back to the
    // other field when the preferred one is missing.
    aircraftType(aircraft) {
        if (this.aircraftTypeFormat === 'description') {
            return aircraft.description || aircraft.aircraft_type || '';
        }
        return aircraft.aircraft_type || aircraft.description || '';
    },

    // Rejects on a non-OK status, so an upstream outage (503) shows as a
    // failed fetch rather than as an empty sky.
    fetchJson(url) {
        return fetch(url).then(function (response) {
            if (!response.ok) {
                throw new Error(url + ' returned ' + response.status);
            }
            return response.json();
        });
    },

    formatTime(date) {
        return date.toLocaleTimeString([], {
            hour: this.timeFormat === '12' ? 'numeric' : '2-digit',
            minute: '2-digit',
            second: '2-digit',
            hour12: this.timeFormat === '12'
        });
    },

    // Hours and minutes, with the weekday when it is not today.
    formatDepartureTime(date) {
        var options = {
            hour: this.timeFormat === '12' ? 'numeric' : '2-digit',
            minute: '2-digit',
            hour12: this.timeFormat === '12'
        };
        if (date.toDateString() !== new Date().toDateString()) {
            options.weekday = 'short';
        }
        return date.toLocaleString([], options);
    }
};
