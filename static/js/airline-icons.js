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

  function isHelicopter(category) {
    return String(category || '').trim().toUpperCase() === 'A7';
  }

  function loadManifest() {
    if (availableCodes) {
      return Promise.resolve(availableCodes);
    }
    if (manifestRequest) {
      return manifestRequest;
    }

    // A failure is not remembered, so the next request tries again.
    manifestRequest = fetch('/airline-icons')
      .then(function (response) {
        if (!response.ok) {
          throw new Error('/airline-icons returned ' + response.status);
        }
        return response.json();
      })
      .then(function (codes) {
        availableCodes = new Set(codes);
        return availableCodes;
      })
      .finally(function () {
        manifestRequest = null;
      });

    return manifestRequest;
  }

  async function fetchIcon(code, codes) {
    if (!codes.has(code)) {
      return Promise.resolve(null);
    }

    const response = await fetch('/static/airline-icons/' + code + '.json');
    if (!response.ok) {
      throw new Error(code + '.json returned ' + response.status);
    }
    return response.json();
  }

  function mergeAliasMetadata(icon, metadata) {
    if (!metadata) {
      return icon;
    }

    return Object.assign({}, icon, {
      code: metadata.code,
      label: metadata.label
    });
  }

  function resolveAlias(code, codes, visitedCodes, metadata) {
    if (visitedCodes.has(code)) {
      return Promise.reject(
        new Error('Circular airline icon alias: ' + code)
      );
    }

    visitedCodes.add(code);

    return fetchIcon(code, codes)
      .then(function (icon) {
        if (!icon) {
          return null;
        }

        var displayMetadata = metadata || {
          code: icon.code || code,
          label: icon.label || code
        };

        if (!icon.alias) {
          return mergeAliasMetadata(icon, displayMetadata);
        }

        return resolveAlias(
          String(icon.alias).toUpperCase(),
          codes,
          visitedCodes,
          displayMetadata
        );
      });
  }

  function resolveIcon(code, codes) {
    return resolveAlias(code, codes, new Set(), null);
  }

  function load(code) {
    if (!code) {
      return Promise.resolve(null);
    }

    code = code.toUpperCase();

    if (cache.has(code)) {
      return Promise.resolve(cache.get(code));
    }
    if (pending.has(code)) {
      return pending.get(code);
    }

    var request = loadManifest()
      .then(function (codes) {
        return resolveIcon(code, codes);
      })
      .then(function (icon) {
        cache.set(code, icon);
        pending.delete(code);
        return icon;
      })
      .catch(function () {
        // Not cached, so a failed load is tried again next time.
        pending.delete(code);
        return null;
      });

    pending.set(code, request);
    return request;
  }

  function getIcon(callsign) {
    return load(getCode(callsign));
  }

  // Real LEDs vary slightly in brightness. Returns a fixed factor in
  // [1 - amount, 1] for the LED at (panel, column, row), so a given dot is
  // always the same brightness, whatever is shown on it.
  function ledBrightness(panel, column, row, amount) {
    var h = Math.imul(panel + 1, 73856093) ^
      Math.imul(column + 1, 19349663) ^
      Math.imul(row + 1, 83492791);
    h = Math.imul(h ^ (h >>> 13), 1274126177);
    h = (h ^ (h >>> 16)) >>> 0;
    return 1 - amount * (h / 4294967296);
  }

  function render(canvas, icon, options) {
    var settings = Object.assign({
      dotScale: 0.68,
      offColor: 'rgba(14, 19, 36, 0.42)',
      showOffLeds: true,
      glow: true,
      brightnessVariation: 0
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

        if (color && settings.brightnessVariation) {
          context.globalAlpha = ledBrightness(
            -1,
            columnIndex,
            rowIndex,
            settings.brightnessVariation
          );
        }

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

  window.InPlaneSightAirlines = {
    clear: clear,
    getCode: getCode,
    getIcon: getIcon,
    isHelicopter: isHelicopter,
    ledBrightness: ledBrightness,
    load: load,
    render: render
  };
}());
