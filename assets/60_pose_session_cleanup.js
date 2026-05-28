/* CombatIQ - limpia resultados biomecanicos persistidos al cerrar sesion.
   dcc.Store(session) vive en sessionStorage; Flask limpia la sesion del servidor,
   pero el navegador necesita retirar estas llaves para no restaurar un analisis
   anterior despues de logout. */
(function () {
  var CURRENT_ANALYZER_VERSION = "chamber_angle_v1_2026_05_28";
  var VERSION_KEY = "combatiq-pose-analyzer-version";

  function clearBiomechSessionState(clearSelectors) {
    var exactKeys = {
      "pose-results": true,
      "pose-mediapipe-store": true,
      "pose-speed-store": true
    };
    [window.sessionStorage, window.localStorage].forEach(function (storage) {
      if (!storage) return;
      var toRemove = [];
      for (var i = 0; i < storage.length; i += 1) {
        var key = storage.key(i);
        if (!key) continue;
        if (
          exactKeys[key] ||
          (clearSelectors && key.indexOf("pose-target-select") !== -1) ||
          (clearSelectors && key.indexOf("pose-num-rounds") !== -1)
        ) {
          toRemove.push(key);
        }
      }
      toRemove.forEach(function (key) {
        storage.removeItem(key);
      });
    });
  }

  function clearStaleBiomechVersion() {
    try {
      var stored = window.localStorage && window.localStorage.getItem(VERSION_KEY);
      if (stored === CURRENT_ANALYZER_VERSION) return;
      clearBiomechSessionState(false);
      if (window.localStorage) {
        window.localStorage.setItem(VERSION_KEY, CURRENT_ANALYZER_VERSION);
      }
    } catch (e) {}
  }

  function shouldClear() {
    try {
      var params = new URLSearchParams(window.location.search || "");
      return window.location.pathname === "/logout" ||
        (window.location.pathname === "/login" && params.get("logged_out") === "1");
    } catch (e) {
      return window.location.pathname === "/logout";
    }
  }

  function run() {
    clearStaleBiomechVersion();
    if (!shouldClear()) return;
    clearBiomechSessionState(true);
    if (window.location.pathname === "/login" && window.history && window.history.replaceState) {
      window.history.replaceState({}, document.title, "/login");
    }
  }

  run();
  document.addEventListener("DOMContentLoaded", run);
  ["pushState", "replaceState"].forEach(function (method) {
    var original = window.history && window.history[method];
    if (!original) return;
    window.history[method] = function () {
      var result = original.apply(this, arguments);
      window.setTimeout(run, 0);
      return result;
    };
  });
  window.addEventListener("popstate", function () {
    window.setTimeout(run, 0);
  });
})();
