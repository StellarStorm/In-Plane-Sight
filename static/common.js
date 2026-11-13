window.InPlaneSight = {
    timeFormat: '24',

    configure(config) {
        this.timeFormat = config.time_format === '12' ? '12' : '24';
    },

    formatTime(date) {
        return date.toLocaleTimeString([], {
            hour: this.timeFormat === '12' ? 'numeric' : '2-digit',
            minute: '2-digit',
            second: '2-digit',
            hour12: this.timeFormat === '12'
        });
    },

    // Hours and minutes, e.g. "21:34" or "9:34PM", in the given IANA time
    // zone (the airport's), or the viewer's zone when none is known.
    formatClockTime(isoTime, timeZone) {
        var date = isoTime ? new Date(isoTime) : null;
        if (!date || Number.isNaN(date.getTime())) {
            return '';
        }
        var options = {
            hour: this.timeFormat === '12' ? 'numeric' : '2-digit',
            minute: '2-digit',
            hour12: this.timeFormat === '12'
        };
        var text;
        try {
            text = date.toLocaleTimeString([], Object.assign({}, options, timeZone ? { timeZone: timeZone } : {}));
        } catch (error) {
            text = date.toLocaleTimeString([], options);
        }
        return text.replace(/\s+/g, '');
    },

    // "20:41-22:16": departure in the origin's local time, arrival in the
    // destination's, laid out like the "ORD-LGA" route.
    formatFlightTimes(aircraft) {
        var departure = this.formatClockTime(aircraft.departure_time, aircraft.origin_timezone);
        var arrival = this.formatClockTime(aircraft.arrival_time, aircraft.destination_timezone);
        if (!departure && !arrival) {
            return '';
        }
        return departure + '-' + arrival;
    }
};
