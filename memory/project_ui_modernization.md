# CombatIQ - Memoria UI, Tema Claro Y Presentacion

Fecha de inicio: 2026-05-21

## Objetivo

Mantener una UI profesional y clara sin redisenar por gusto. La app ya tiene una
base visual valida; solo se deben tocar elementos que mejoren lectura, confianza
o demo.

## Problema Reportado Por El Usuario

- En modo claro algunas graficas o zonas de UI permanecen oscuras.

## Archivos Criticos

- `assets/10_theme.css`
- `assets/20_tiles.css`
- `assets/30_auth.css`
- `assets/40_light_theme.css`
- `assets/50_theme_init.js`
- `ui_charts.py`
- vistas con graficas Plotly.

## Checklist De Auditoria Modo Claro

- [ ] Variables CSS para fondo, cards, texto, bordes y acentos.
- [ ] Plotly `paper_bgcolor` y `plot_bgcolor`.
- [ ] Templates Plotly globales.
- [ ] Cards/tabs/dropdowns con colores hardcodeados.
- [ ] Leyendas y ejes visibles en blanco.
- [ ] Tablas con contraste suficiente.
- [ ] Estados hover/focus.

## Regla De Producto

- No crear una pestaña de inversor visible por defecto.
- La lectura para inversores debe aparecer como claridad, diferenciacion y
  calidad de demo, no como una seccion explicita que rompa el producto deportivo.

## Riesgos

- Cambios visuales globales pueden afectar varias vistas.
- Las graficas Plotly pueden ignorar CSS si tienen colores inline.
