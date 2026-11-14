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

    formatTime(date) {
        return date.toLocaleTimeString([], {
            hour: this.timeFormat === '12' ? 'numeric' : '2-digit',
            minute: '2-digit',
            second: '2-digit',
            hour12: this.timeFormat === '12'
        });
    }
};
