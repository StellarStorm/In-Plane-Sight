window.FlightWall = {
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
    }
};
