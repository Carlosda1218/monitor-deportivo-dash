/* CombatIQ — Aplica el tema desde localStorage antes de que React monte.
   Evita el flash de tema oscuro al navegar entre páginas. */
(function () {
  try {
    var stored = localStorage.getItem("theme-store");
    if (stored) {
      var theme = JSON.parse(stored);
      if (theme === "light") {
        document.documentElement.setAttribute("data-theme", "light");
        // Sincroniza el icono del botón de tema en auth una vez que React monte
        document.addEventListener("DOMContentLoaded", function () {
          ["btn-auth-theme", "btn-auth-theme-reg"].forEach(function (id) {
            var btn = document.getElementById(id);
            if (btn) btn.textContent = "☾";
          });
        });
      }
    }
  } catch (e) {}
})();
