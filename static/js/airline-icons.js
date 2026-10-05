(function () {
  'use strict';

  var availableCodes = null;
  var manifestRequest = null;
  var cache = new Map();
  var pending = new Map();

  function getCode(callsign) {
    if (!callsign || callsign.length < 3) {
      return null;
    }

    return callsign.slice(0, 3).toUpperCase();
  }

  function loadManifest() {
    if (availableCodes) {
      return Promise.resolve(availableCodes);
    }

    if (manifestRequest) {
      return manifestRequest;
    }

    manifestRequest = fetch('/airline-icons')
      .then(function (response) {
        if (!response.ok) {
          return [];
        }

        return response.json();
      })
      .then(function (codes) {
        availableCodes = new Set(codes);
        return availableCodes;
      })
      .catch(function () {
        availableCodes = new Set();
        return availableCodes;
      });

    return manifestRequest;
  }

  function load(code) {
    if (!code) {
      return Promise.resolve(null);
    }

    if (cache.has(code)) {
      return Promise.resolve(cache.get(code));
    }

    if (pending.has(code)) {
      return pending.get(code);
    }

    var request = loadManifest()
      .then(function (codes) {
        if (!codes.has(code)) {
          cache.set(code, null);
          return null;
        }

        return fetch('/static/airline-icons/' + code + '.json')
          .then(function (response) {
            if (!response.ok) {
              return null;
            }

            return response.json();
          });
      })
      .then(function (icon) {
        cache.set(code, icon);
        pending.delete(code);
        return icon;
      })
      .catch(function () {
        cache.set(code, null);
        pending.delete(code);
        return null;
      });

    pending.set(code, request);
    return request;
  }

  function getIcon(callsign) {
    return load(getCode(callsign));
  }

  function render(canvas, icon, options) {
    var settings = Object.assign({
      dotScale: 0.68,
      offColor: 'rgba(14, 19, 36, 0.42)',
      showOffLeds: true,
      glow: true
    }, options || {});

    var context = canvas.getContext('2d');
    var ratio = window.devicePixelRatio || 1;
    var width = canvas.clientWidth;
    var height = canvas.clientHeight;
    var rows = icon.rows.length;
    var columns = Math.max.apply(
      null,
      icon.rows.map(function (row) {
        return row.length;
      })
    );

    canvas.width = Math.round(width * ratio);
    canvas.height = Math.round(height * ratio);
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    context.clearRect(0, 0, width, height);

    var cell = Math.min(width / columns, height / rows);
    var gridWidth = columns * cell;
    var gridHeight = rows * cell;
    var left = (width - gridWidth) / 2;
    var top = (height - gridHeight) / 2;
    var radius = cell * settings.dotScale / 2;

    icon.rows.forEach(function (row, rowIndex) {
      for (
        var columnIndex = 0;
        columnIndex < columns;
        columnIndex += 1
      ) {
        var key = row[columnIndex] || ' ';
        var color = icon.palette[key];

        if (!color && !settings.showOffLeds) {
          continue;
        }

        var x = left + (columnIndex + 0.5) * cell;
        var y = top + (rowIndex + 0.5) * cell;

        context.save();
        context.beginPath();
        context.arc(x, y, radius, 0, Math.PI * 2);
        context.fillStyle = color || settings.offColor;

        if (color && settings.glow) {
          context.shadowColor = color;
          context.shadowBlur = cell * 0.65;
        }

        context.fill();
        context.restore();
      }
    });
  }

  function clear(canvas) {
    var context = canvas.getContext('2d');
    context.clearRect(0, 0, canvas.width, canvas.height);
  }

  window.FlightWallAirlines = {
    clear: clear,
    getCode: getCode,
    getIcon: getIcon,
    load: load,
    render: render
  };
}());
