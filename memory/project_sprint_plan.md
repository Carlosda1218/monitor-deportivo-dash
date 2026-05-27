# CombatIQ - Plan De Sprints De Auditoria

Fecha de inicio: 2026-05-21

## Sprint 0 - Sincronizacion De Memoria

Objetivo:

- Crear memoria operativa real en `memory/`.
- Alinear `COMBATIQ_MASTER_PROMPT.md` con el prompt actualizado del usuario.
- Registrar que `memory/` no existia al iniciar esta sesion.

Alcance:

- Archivos de memoria.
- Prompt maestro raiz.
- Registro de cambios.

No alcance:

- Cambios funcionales en la app.

Validacion:

- Confirmar existencia de archivos `memory/*.md`.
- Revisar `git status --short`.

Estado:

- Completado el 2026-05-21.

## Sprint 1 - Validacion Base

Objetivo:

- Saber si la app compila y los tests existentes pasan antes de tocar logica.

Alcance:

- `compileall` de modulos criticos.
- `pytest -q`.
- Prueba de carga ligera si aplica.

Riesgos:

- Algunas pruebas operativas pueden depender de datos locales, video o hardware.

Validacion:

```powershell
.\.venv\Scripts\python.exe -m compileall app.py db.py analysis_engine.py ai_insights.py notifications.py pose_analyzer.py questionnaires.py report_utils.py sensors.py ui_charts.py yolo_tracker.py pages views hub
.\.venv\Scripts\python.exe -m pytest -q
```

Estado:

- Completado el 2026-05-21.

Resultado:

- `compileall` de modulos criticos paso correctamente.
- `pytest -q` paso con `1 passed`.
- `test_app_flow.py` paso como script operativo con `28/28`.
- Se corrigio el bloqueo de pruebas causado por scripts operativos recolectados
  por pytest y por una sesion demo obsoleta (`30`).

## Sprint 2 - Auditoria Biomecanica Y Combate

Objetivo:

- Reforzar el pilar de analisis de combate, pose, tracking, confianza e IA
  tactica.

Archivos principales:

- `yolo_tracker.py`
- `pose_analyzer.py`
- `views/signals_view.py`
- `app.py`
- `ai_insights.py`

Checklist:

- [ ] Revisar `yolo_tracker.py` linea a linea.
- [ ] Validar filtros anti-ghost.
- [ ] Validar ByteTrack, IDs, estabilidad y reasignaciones.
- [ ] Validar unidades, caps fisiologicos y calibracion.
- [ ] Revisar biomecanica YOLO Phase 1.
- [ ] Revisar `pose_analyzer.py` linea a linea.
- [ ] Revisar modo individual y modo rojo vs azul.
- [ ] Verificar que baja confianza no se maquilla.
- [ ] Revisar graficas y explicaciones para atleta/coach.
- [ ] Ejecutar prueba `analyze_wt_deep.py` si el entorno lo permite.

Estado:

- Completado el 2026-05-21 segun prompt maestro actualizado.

Avance 2026-05-21:

- `yolo_tracker.py` auditado en primera pasada linea a linea.
- Bugs corregidos: import roto, fallback OpenVINO CPU, lectura de video lenta,
  filtro de color, fallback de color riesgoso, aliases de salida.
- `analyze_wt_deep.py` blindado para no escribir datos por defecto.
- `pose_analyzer.py` optimizado sin recortar potencia: limites explicitos en
  duelo, pool de keyframes acotado y simulacion ECG/IMU determinista.
- `ai_insights.py` y `signals_view.py` ahora separan lecturas IA por audiencia
  atleta/coach en biomecanica y Replay.
- Validaciones base siguen en verde.

Riesgo residual:

- Validar visualmente si la reduccion de picos rojos es conservadora correcta o
  si estamos perdiendo detecciones reales del atleta rojo.
- Revisar si el callback de biomecanica debe separar preview/proceso completo
  para evitar congelamientos en videos largos.

Nota:

- El riesgo residual de congelamiento pasa formalmente a Sprint 5. No se debe
  resolver bajando calidad de MediaPipe/YOLO/Claude, sino separando flujo,
  progreso y estados intermedios.

## Sprint 3 - Sensores, Hardware Y Mediciones

Objetivo:

- Confirmar que ECG/IMU, BLE, API, CSV y sensor sessions son confiables para
  demo y escalables a futuro.

Archivos principales:

- `sensors.py`
- `hub/`
- `db.py`
- `app.py`
- `views/sensors_view.py`
- `views/signals_view.py`

Checklist:

- [ ] Revisar catalogo y aliases de sensores.
- [ ] Revisar endpoints `/api/sensor-ping`, `/api/sensor-data`,
  `/api/sensor-status`.
- [ ] Revisar almacenamiento de metricas y sesion sensorial.
- [ ] Revisar desconexion/reconexion y mensajes UI.
- [ ] Revisar importacion CSV ECG/IMU.
- [ ] Revisar preparacion para hardware real y plan B demo.

Estado:

- Completado el 2026-05-21 segun prompt maestro actualizado y verificacion
  local de archivos.

Resultado:

- `sensors.py`, `hub/` y `views/sensors_view.py` existen y compilan dentro de
  la validacion general.
- El roadmap maestro declara IMU custom BLE/API/CSV listo para demo controlada,
  ECG/HR por CSV/API y hardware comercial como opcion a evaluar.
- `test_s105_load.py --skip-video` confirma parse ECG y queries criticas DB en
  tiempos aceptables.

Riesgo residual:

- Falta prueba con hardware fisico real conectado en esta sesion.
- Si se expone fuera de localhost, revisar token/API, red y plan de seguridad.

## Sprint 4 - IA Contextual

Objetivo:

- Asegurar que la IA explica datos en lenguaje util y no falla de forma opaca.

Archivos principales:

- `ai_insights.py`
- `views/signals_view.py`
- `analysis_engine.py`

Checklist:

- [ ] Validar modelo y fallback declarado.
- [ ] Revisar timeouts y cache.
- [ ] Revisar prompts por rol: atleta, coach, demo/inversor.
- [ ] Validar que no se manden datos sensibles innecesarios.
- [ ] Validar que la salida proponga acciones y ejercicios.
- [ ] Verificar que errores de API se explican sin romper flujo.

Estado:

- Completado el 2026-05-21 segun prompt maestro actualizado y verificacion
  local de firmas/prompts.

Resultado:

- `ai_insights.py` compila.
- Las lecturas IA reciben audiencia/visor en los flujos revisados.
- `detect_video_events()` acepta `target_vest` y `signals_view.py` lo pasa
  desde `pose-target-select`.
- El prompt maestro registra 3 funciones `_legacy` como deuda tecnica baja.

Riesgo residual:

- Validar con API activa que Claude respeta audiencia, target de peto y no
  mezcla atleta/coach.
- Las funciones `_legacy` no bloquean demo, pero deben limpiarse cuando toque
  deuda tecnica de IA.

## Sprint 5 - Rendimiento Y Congelamientos

Objetivo:

- Detectar causas de lentitud o congelamiento en pestanas pesadas.

Zonas criticas:

- `signals_view.py`
- `analysis_view.py`
- `compare_view.py`
- `dashboard.py`
- `wellbeing.py`
- callbacks globales de `app.py`

Checklist:

- [ ] Buscar callbacks con loops pesados.
- [ ] Buscar lecturas repetidas de CSV/video.
- [ ] Buscar stores demasiado grandes.
- [ ] Buscar polling frecuente innecesario.
- [ ] Buscar queries N+1.
- [ ] Revisar cargas de IA sin timeout.
- [ ] Proponer caches/limites solo si no cambian la logica.

Estado:

- En progreso desde 2026-05-21.

Punto de partida:

- Congelamiento diagnosticado en `signals_view.py`: MediaPipe, YOLO y Claude
  pueden correr en serie dentro de `analyze_pose_callback`.
- No se debe bajar calidad ni recortar analisis para acelerar.
- Solucion esperada: separar procesamiento en callbacks/estados intermedios
  con `dcc.Store`, feedback de progreso y resultados parciales seguros.

Avance 2026-05-21:

- Confirmado que `signals_view.py` ya tenia el flujo dividido en 3 callbacks:
  MediaPipe, YOLO y render/IA.
- Hallazgo nuevo: aunque el flujo estaba dividido, los `dcc.Store` seguian
  transportando objetos pesados con frames, imagenes base64 y reportes
  completos. Eso puede congelar el navegador por serializacion JSON aunque el
  servidor termine el analisis.
- Fix aplicado: cache temporal de trabajos de pose en servidor. El navegador
  guarda solo `job_id` y un reporte ligero; PDF, simulacion y guardado resuelven
  el reporte completo desde cache.
- Fix aplicado: cache para IA de vision en Replay. Repetir el mismo video,
  evento o fotograma ya no deberia relanzar llamadas identicas a Claude dentro
  del TTL.
- Fix aplicado: eviccion real en cache IA para mantener maximo 50 entradas
  activas.
- Fix aplicado: cache de procesamiento ECG para no recalcular suavizado/picos R
  al mover ventana o exportar con los mismos parametros.
- Fix aplicado: cache de fotogramas Replay para no reabrir el video al repetir
  el mismo evento visual.
- No se tocaron umbrales deportivos, precision, MediaPipe, YOLO ni Claude.

Validacion:

- `python -m compileall views\signals_view.py`: OK.
- `python -m pytest -q`: OK, `1 passed`.
- `python test_app_flow.py`: OK, `28/28`.
- `python test_s105_load.py --skip-video`: OK, ECG parse y DB queries.
- Prueba directa de helpers de cache: OK, el reporte ligero no incluye frames y
  el resolver recupera el reporte completo.
- `python -m compileall ai_insights.py views\signals_view.py`: OK tras cache de
  vision.
- Prueba directa de eviccion IA: OK, `ai_cache_eviction_ok 50`.
- Revalidacion final de este tramo: `pytest -q` OK y `test_app_flow.py` OK.
- 2026-05-22: `ecg_process_cache_ok 1500 4 0 1`, compileall OK, pytest OK,
  `test_app_flow.py` OK y `test_s105_load.py --skip-video` OK.
- 2026-05-22: `replay_frame_cache_boundary_ok`, pytest OK y
  `test_app_flow.py` OK.

Riesgo residual:

- La cache es local al proceso y dura 45 minutos. En despliegue multi-worker se
  debe migrar a Redis, disco temporal o tabla de jobs.
- Aun falta medir manualmente el video largo en UI y revisar otras pestanas
  pesadas antes de cerrar Sprint 5.
- Falta validar con video real la cache de fotogramas y la experiencia de clic
  repetido en eventos Replay.
- Observacion pendiente: `/analyze-pose` en `app.py` sigue siendo ruta legacy
  sin cache ligera; no se toca hasta confirmar si algun flujo externo depende
  de ella.

Actualizacion 2026-05-24:

- Sprint 5 recibe refuerzo por congelamientos reales.
- Retirado diagnostico temporal de `app.py` (`__diag`, interceptores globales,
  `__test-interval`, boton `DASH TEST` y callback).
- Replay IA visual pasa a boton explicito `Analizar IA video`.
- Replay IA de combate pasa a boton explicito `Generar lectura IA`.
- Corregidos bugs IA:
  - `analyze_event_frame()` ahora define/cachea `cache_key`.
  - `generate_athlete_note()` ya no usa variables inexistentes de event frame.
- Servidor `8051` reiniciado limpio y verificado:
  `HasDiag=False`, `HasTestCallback=False`, `HasAiButtonCallbacks=True`.
- Validacion en verde: compileall, pytest, `test_app_flow.py`,
  `test_s105_load.py --skip-video`, `/_dash-dependencies`.

Pendiente Sprint 5:

- Validacion manual navegando Replay con video real largo.
- Si aun hay congelamientos en biomecanica completa, proximo salto es jobs
  background con progreso/cancelacion para MediaPipe/YOLO/Claude.

Incidente critico 2026-05-23:

- Botones bloqueados por error Dash `Duplicate callback outputs` en
  `pages/wellbeing.py`.
- Fix aplicado: `_callback_once()` para `load_q_trend` y `save_wellbeing`.
- Refuerzo aplicado: `_callback_once()` revisa callbacks globales, sentinel en
  `builtins` y `dash.get_app().callback_map` para cubrir hot reload antes y
  despues del setup de Dash.
- Validado con reload de modulo, `/_dash-dependencies`, compileall, pytest,
  `test_app_flow.py` y `test_s105_load.py --skip-video`.
- Regla aprendida: no usar `allow_duplicate=True` como parche si el callback
  guarda datos; primero evitar el doble registro.
- Fix definitivo: se combino `load_q_trend` dentro de `save_wellbeing` y se
  elimino `allow_duplicate=True` de `q-trend`.
- Validacion definitiva: `/_dash-dependencies` muestra una sola dependencia
  para `q-gauge/q-explain/q-trend` y sin sufijo `@hash`.

## Sprint 6 - UI Modo Claro Y Pulido De Demo

Objetivo:

- Corregir inconsistencias donde el modo claro conserva estilos oscuros y
  mejorar claridad solo donde aporte valor.

Archivos principales:

- `assets/10_theme.css`
- `assets/40_light_theme.css`
- `assets/50_theme_init.js`
- `ui_charts.py`
- vistas con graficas Plotly.

Checklist:

- [ ] Revisar variables CSS dark/light.
- [ ] Buscar colores hardcodeados oscuros.
- [ ] Revisar templates Plotly y paper/plot bg.
- [ ] Validar cards/tabs/dropdowns en modo claro.
- [ ] No redisenar pantallas completas sin confirmacion.

Estado:

- Pendiente.

## Incidente Resuelto 2026-05-24 - Logout En Loading

Problema:

- Al salir de sesion, la app podia quedarse congelada en loading.

Causa raiz:

- El logout usaba navegacion interna Dash (`dcc.Link`) hacia `/logout`.
- El fallback `pages/logout.py` limpiaba sesion y montaba un `dcc.Location`
  anidado para redirigir a `/login`.

Fix aplicado:

- `app.py` ahora registra `/logout` como ruta Flask real.
- La ruta ejecuta `session.clear()` y `redirect("/login")`.
- El link "Salir" de la barra lateral ahora es `html.A`, no `dcc.Link`.
- `pages/logout.py` queda como fallback defensivo sin redireccion automatica.

Validacion:

- Compileall OK.
- Flask test client: `GET /logout` -> `302 /login`, sesion limpia.
- Servidor vivo `8051`: `GET /logout` -> `302 /login`.
- `pytest -q`: `1 passed`.
- `test_app_flow.py`: `28/28`.
- `test_s105_load.py --skip-video`: OK.

Regla aprendida:

- Logout/autenticacion no debe depender de callbacks ni de componentes Dash
  anidados. Usar redirect HTTP server-side como camino principal.

## Micro-Sprint 2026-05-24 - IA Bajo Demanda Para Evitar Loading No Pedido

Objetivo:

- Reducir congelamientos percibidos sin quitar potencia a la app.

Cambios:

- Ficha de atleta coach: IA narrativa pasa a boton `Generar analisis IA`.
- `/sesion` atleta: lectura del dia pasa a boton `Generar lectura IA`.
- `/sesion` coach: resumen del equipo pasa a boton `Generar resumen IA`.
- Las tarjetas quedan con placeholder visible en vez de loading automatico.

Validacion:

- `compileall app.py views\signals_view.py`: OK.
- `pytest -q`: OK.
- `/_dash-dependencies`: `risky_unhashed_duplicate_bases=0`.
- `test_app_flow.py`: `28/28`.
- `test_s105_load.py --skip-video`: OK.
- Servidor `8051` reiniciado y verificado con el codigo actual.

Regla:

- IA lenta no debe dispararse por navegacion o dropdown. Debe requerir accion
  explicita o job/background con progreso.

## Micro-Sprint 2026-05-24 - Hardening De Ruta Legacy Pose

Objetivo:

- Evitar que rutas antiguas puedan provocar congelamientos aunque la UI moderna
  ya use el flujo optimizado.

Cambios:

- `/analyze-pose` mantiene compatibilidad, pero ahora limita parametros.
- `sample_every` queda entre `1` y `60`.
- `max_frames` queda limitado por `COMBATIQ_LEGACY_POSE_ROUTE_MAX_FRAMES` o
  `1500` por defecto.
- El flujo principal de Biomecanica no se modifico.

Validacion:

- Compileall OK.
- Pytest OK.
- `test_app_flow.py`: 28/28.
- `test_s105_load.py --skip-video`: OK.
- `/_dash-dependencies`: sin duplicados peligrosos.
- Prueba simulada confirma cap: `max_frames=999999` -> `1500`.

Pendiente:

- Revisar limpieza controlada de `data/uploads`; hay multiples videos duplicados
  de pruebas, pero no deben eliminarse sin permiso explicito.

## Micro-Sprint 2026-05-24 - Inventario Uploads Duplicados

Estado:

- Inventario completado, sin mover ni borrar archivos.

Resultado:

- 56 archivos en uploads, ~4029.74 MB.
- 3 grupos duplicados por SHA-256.
- ~3816.16 MB son copias repetidas.
- DB sin referencias directas a los videos duplicados.

Plan recomendado:

- Conservar:
  - `data/uploads/20230325_213445.mp4`
  - `data/uploads/videoplayback.mp4`
  - `data/uploads/20260503_005430_07048cf5.mp4`
- Mover 53 duplicados a `data/uploads_quarantine_20260524/`.
- Validar Replay/Biomecanica/tests.
- Solo despues, si el usuario quiere, eliminar la cuarentena.

Bloqueo:

- Requiere confirmacion explicita del usuario antes de mover o borrar.

Resultado despues de autorizacion:

- 53 duplicados movidos a `data/uploads_quarantine_20260524/`.
- No hubo borrado definitivo.
- Manifest generado: `data/upload_quarantine_20260524.json`.
- Aliases actualizados para que nombres antiguos sigan apuntando al canonico.
- `check_videos.py` actualizado para usar el resolvedor real de uploads.
- Validado con compileall, `check_videos.py`, pytest, `test_app_flow.py` 28/28,
  `test_s105_load.py --skip-video` y `/_dash-dependencies`.

Pendiente:

- Validacion manual de Replay/Biomecanica con `videoplayback.mp4`.
- Decidir mas adelante si se elimina la cuarentena o se conserva como backup.

## Incidente Resuelto 2026-05-24 - Demo Atleta Loading

Problema:

- Al intentar entrar como demo atleta, la app podia quedarse en `Loading...`.

Fix:

- Se agregaron rutas HTTP server-side:
  - `/demo/atleta`
  - `/demo/coach-tkd`
  - `/demo/coach-boxeo`
- Los accesos demo del login ahora son `html.A` con `href`, no solo botones con
  callback.
- Se reinicio `8051` dejando un unico proceso vivo.

Validacion:

- Rutas demo devuelven `302 /dashboard`.
- Sesion Flask queda creada con rol/nombre/deporte correctos.
- Layout vivo confirma `btn-demo-login` como `A` con `href="/demo/atleta"`.
- Pytest, `test_app_flow.py` y `test_s105_load.py --skip-video` en verde.

Regla:

- Demo/login/logout deben tener camino HTTP robusto para presentacion; Dash
  callbacks pueden complementar, pero no ser el unico camino critico.

## Incidente Resuelto 2026-05-24 - Carga Doble Demo

Problema:

- Tras entrar a demo, la app podia sentirse como si cargara dos veces.

Fix:

- `app.py`: el layout autenticado ya no fuerza `dcc.Location(pathname=...)`.
- Dash queda encargado de leer `window.location` desde el cliente.
- `pages/auth_login.py`: se retiraron contenedores `demo-redirect` ya
  obsoletos.
- Se reinicio `8051` porque la app corre sin autoreloader.

Validacion:

- `/_dash-layout` con cookie demo devuelve `url_props {'id': 'url'}`.
- Rutas demo devuelven `302 /dashboard`.
- `/_dash-dependencies`: 144 dependencias, 0 outputs duplicados exactos.
- Compileall, pytest, `test_app_flow.py` y `test_s105_load.py --skip-video`
  en verde.

Regla:

- Para usuarios autenticados, no inferir URL real desde `request.path` en
  layout Dash; evitar navegaciones cliente duplicadas.

## Incidente Resuelto 2026-05-24 - Storage Cleanup Agresivo

Problema:

- La app podia sentirse lenta, con carga doble o perder el modo claro en
  recargas.

Fix:

- `app.index_string` ya no ejecuta `sessionStorage.clear()`.
- Ya no borra `theme-store` ni otras preferencias locales en cada carga.
- La limpieza PWA/cache queda versionada con
  `combatiq-sw-cache-cleanup-v2` y se ejecuta una sola vez.
- Se reinicio `8051` y se dejaron fuera procesos duplicados.

Validacion:

- HTML live sin `sessionStorage.clear`.
- HTML live no borra `theme-store`.
- Rutas demo/logout correctas.
- `/_dash-dependencies`: 144 dependencias, 0 outputs duplicados exactos.
- Compileall, pytest, `test_app_flow.py` y `test_s105_load.py --skip-video`
  en verde.

Regla:

- Nunca limpiar storage global en cada carga; hacerlo solo de forma versionada
  y sin borrar preferencias de UI.

## Incidente Resuelto 2026-05-24 - Callbacks Iniciales Signals

Problema:

- Habia trabajo innecesario al montar la vista de senales/replay.

Fix:

- `views/signals_view.py`: varios callbacks que solo pintaban placeholders o
  estado vacio ahora usan `prevent_initial_call=True`.
- El panel IA de Replay conserva mensaje inicial estatico sin callback.
- Se preservaron callbacks iniciales que cargan datos reales.

Validacion:

- Callbacks iniciales: 38 -> 29.
- `/_dash-dependencies`: 144 dependencias, 0 duplicados exactos.
- Compileall, pytest, `test_app_flow.py` y `test_s105_load.py --skip-video`
  en verde.
- Servidor `8051` reiniciado y vivo.

Regla:

- No gastar callbacks iniciales en pintar vacios si el layout ya trae
  placeholders.

## Incidente Resuelto 2026-05-24 - Segunda Pasada Initial Callbacks

Problema:

- Tras Signals quedaban callbacks iniciales menores que no aportaban datos
  reales.

Fix:

- `pages/auth_register.py`: password strength y deporte custom diferidos.
- `app.py`: fecha de peso y progreso de checklist diferidos con texto inicial
  estatico.
- `views/analysis_view.py`: nota IA legacy diferida.

Validacion:

- Callbacks iniciales: 29 -> 24.
- `/_dash-dependencies`: 144 dependencias, 0 duplicados exactos.
- Compileall, pytest, `test_app_flow.py` y `test_s105_load.py --skip-video`
  en verde.
- Servidor `8051` reiniciado y vivo.

Regla:

- Diferir solo cuando el estado inicial ya esta en el layout; no diferir datos
  reales de chat, bienestar, comparacion, sensores o vistas principales.

## Incidente Resuelto 2026-05-24 - Auditoria Peso Real Callbacks

Problema:

- Quedaban congelamientos percibidos y habia riesgo de optimizar solo por
  cantidad de callbacks, lo que podia quitar potencia a la app.

Hallazgo:

- Los callbacks restantes importantes si cargan datos reales.
- El peso medido se concentra en render/serializacion de figuras y HTML:
  bienestar historial, comparacion de sesiones, peso y nutricion.
- La DB demo respondio rapido, pero faltaban indices defensivos para escala.

Fix:

- `db.py`: migracion `200` con indices para historicos de cuestionarios,
  ECG/IMU, chat, peso y nutricion.
- No se recortaron historiales, graficas, analisis, sensores ni IA.

Validacion:

- `schema_version=200`.
- Compileall, pytest, `test_app_flow.py`, `test_s105_load.py --skip-video`
  en verde.
- `/_dash-dependencies`: 144 dependencias, 0 duplicados exactos,
  24 callbacks iniciales.
- Servidor `8051` reiniciado y vivo con un unico listener.

Regla:

- Priorizar optimizacion por peso real. Si el dataset crece, atacar primero
  render/lazy loading/payload antes de reducir informacion visible.

## Incidente Resuelto 2026-05-24 - Compare Lazy Render

Problema:

- El detalle tecnico de sensores en Comparar estaba cerrado por defecto, pero su
  callback pesado se ejecutaba al entrar.

Fix:

- `views/compare_view.py`: `session_compare_all` ahora depende de
  `cmp-detail-toggle.open`.
- Cerrado o rol distinto de coach: placeholders ligeros.
- Abierto por coach: calculo completo ECG/IMU, graficas, badges, resumen y
  recomendaciones.

Validacion:

- Cerrado: ~0.85 ms / ~0.65 KB.
- Abierto: ~224 ms / ~18.6 KB.
- Compileall, pytest, `test_app_flow.py`, `test_s105_load.py --skip-video` en
  verde.
- Dash: 144 dependencias, 0 duplicados exactos, 24 callbacks iniciales.
- Servidor `8051` reiniciado con un solo listener real.

Regla:

- Cargar bajo demanda secciones tecnicas colapsadas; no recortar datos.

## Incidente Resuelto 2026-05-25 - Wellbeing History Split

Problema:

- El historico de wellbeing devolvia resumen, tabla y graficas en un solo
  callback pesado.

Fix:

- `pages/wellbeing.py`: nuevo `h-history-data`.
- Separacion en carga de datos, resumen/tabla y graficas.
- Placeholders iniciales conservados.
- Sin limitar historial ni exportes.

Validacion:

- Antes: ~89 ms / ~24 KB.
- Ahora: datos ~7.3 ms, resumen/tabla ~2.7 ms, graficas ~72.7 ms.
- Compileall, pytest, `test_app_flow.py`, `test_s105_load.py --skip-video` en
  verde.
- Dash: 146 dependencias, 0 duplicados exactos, 24 callbacks iniciales.
- Servidor `8051` reiniciado con un solo listener real.

Regla:

- Separar callbacks por peso y funcion cuando eso mejora la percepcion de carga
  sin cambiar informacion ni resultados.

## Incidente Resuelto 2026-05-25 - Peso/Nutricion Split

Problema:

- `peso view` y `nutri view` mezclaban datos, KPIs, tablas, alertas/insight y
  graficas en un solo callback.

Fix:

- `app.py`: nuevos stores `peso-data-store` y `nutri-data-store`.
- Peso dividido en carga de datos, resumen y grafica.
- Nutricion dividida en carga de datos, resumen/insight y grafica.
- Sin cambios en guardado ni exportes.

Validacion:

- Peso: datos ~4.5 ms, resumen ~5.3 ms, grafica ~41.3 ms.
- Nutricion: datos ~5.7 ms, resumen ~9.6 ms, grafica ~41.9 ms.
- Compileall, pytest, `test_app_flow.py`, `test_s105_load.py --skip-video` en
  verde.
- Dash: 150 dependencias, 0 duplicados exactos, 24 callbacks iniciales.
- Servidor `8051` reiniciado con un solo listener real.

Regla:

- Si una vista mezcla datos accionables y grafica pesada, separar por Store y
  callbacks especializados antes de considerar recortes.

## Incidente Resuelto 2026-05-25 - Chat Poll Y Sensores Atleta

Problema:

- Chat re-renderizaba mensajes completos cada 5 segundos aunque no hubiera
  cambios.
- Sensores atleta tenia polling oculto sin efecto visible.

Fix:

- `pages/chat.py`: firma de conversacion en `chat-last-signature`.
- Polling de chat sin cambios usa `PreventUpdate` y responde 204 sin payload.
- `views/sensors_view.py`: intervalo atleta desactivado.
- Polling coach/admin se mantiene porque si alimenta informacion visible.
- `assets/chat_scroll.js`: auto-scroll por `MutationObserver` en vez de
  `setTimeout` permanente.

Validacion:

- Chat sin cambios: ~5.57 ms / 0 KB, antes ~12.93 ms / ~2.11 KB.
- `node --check assets\chat_scroll.js`: OK y asset HTTP 200.
- Compileall, pytest, `test_app_flow.py`, `test_s105_load.py --skip-video` en
  verde.
- Dash: 150 dependencias, 0 duplicados exactos, 23 callbacks iniciales.
- `q-gauge`, `q-explain`, `q-trend` sin duplicados.
- Servidor `8051` vivo con un solo listener real.

Regla:

- Polling que no cambia UI visible debe evitar re-render/payload o quedar
  desactivado.
- No reducir congelamientos quitando potencia de analisis, IA, sensores,
  senales, historiales o exportes.

## Sprint 7 - Exportes Profesionales

Objetivo:

- Asegurar PDF/XLSX/CSV presentables, con permisos, tablas y explicaciones.

Archivos principales:

- `report_utils.py`
- `views/signals_view.py`
- `app.py`
- `ui_charts.py`

Checklist:

- [ ] Verificar permisos de exportes.
- [ ] Generar PDF atleta/equipo si aplica.
- [ ] Generar XLSX/CSV de sensores/sesiones.
- [ ] Confirmar encabezados, unidades, tablas y nombres.
- [ ] Confirmar mensajes accionables si falta dependencia.

Estado:

- Pendiente.

## Sprint 8 - DB, Roles Y Escalabilidad

Objetivo:

- Revisar integridad de DB, permisos, migraciones, rol coach por deporte y
  camino futuro a DB administrada.

Archivos principales:

- `db.py`
- `app.py`
- `pages/auth_login.py`
- `pages/home.py`
- vistas de coach.

Checklist:

- [ ] Revisar migraciones.
- [ ] Revisar permisos atleta/coach/admin/inversor.
- [x] Revisar queries pesadas e indices.
- [ ] Revisar defaults secretos.
- [ ] Revisar que `db.py` sea frontera de datos.

Estado:

- Parcial: auditoria de queries/indices completada con migracion `200`.

## Incidente Resuelto 2026-05-25 - Crear Cuenta Y Forgot Password

Problema:

- "Crear cuenta" no llevaba de login a registro.
- "Olvidaste tu contraseña" era un enlace `#` sin funcionalidad.

Fix:

- `app.py`: rutas publicas de auth sin forzar pathname a `/login`.
- `pages/auth_login.py`: links internos con `dcc.Link`.
- `pages/auth_forgot.py`: pantalla nueva de recuperacion.
- `db.py`: migracion `210` y tabla `password_reset_tokens`.
- Tokens temporales hasheados, con caducidad y un solo uso.

Validacion:

- Compileall en auth/app/db en verde.
- DB en `schema_version=210`.
- Token incorrecto falla, token correcto cambia contraseña y reutilizacion
  falla.
- Pytest, `test_app_flow.py` 28/28 y `test_s105_load.py --skip-video` en verde.
- Rutas `/login`, `/registro`, `/recuperar-password`, `/forgot-password` en vivo
  con HTTP 200.
- Router renderiza layouts correctos.
- Confirmacion dirigida posterior: `15/15` sobre navegacion auth, registro,
  forgot password, cambio de password y login posterior.

Pendiente futuro:

- Conectar envio real por correo/API cuando se defina proveedor.
- Mantener token visible solo en entorno local/demo.

## Incidente Resuelto 2026-05-26 - Congelamientos Por Runtime

Problema:

- Se reportaron congelamientos/carga doble.
- Se encontraron dos listeners simultaneos en `8051` y arranque con
  `debug=True`.

Fix:

- Limpieza de procesos: se detuvieron `39024` y `40532`; queda un listener
  `35632`.
- `app.py`: `COMBATIQ_DEBUG=0` por defecto.
- `app.py`: `dev_tools_hot_reload=False`.
- `app.py`: log del router de `INFO` a `DEBUG`.
- `.env.example`: documenta `COMBATIQ_DEBUG=0`.

Validacion:

- Rutas live auth/demo/dashboard/dependencies en 200.
- Logout live validado sin loading.
- `pytest`, `test_app_flow.py` 28/28 y `test_s105_load.py --skip-video` en
  verde.

Siguiente foco:

- Si persiste congelamiento en una pestaña concreta, medir callback/payload de
  esa pestaña con servidor limpio y un solo listener.

## Incidente Resuelto 2026-05-26 - Asistente IA Connection Error

Problema:

- El asistente flotante mostraba `Error: Connection error`.
- La API externa fallaba y la UI mostraba el error crudo.

Fix:

- `ai_insights.py`: fallback local para chat.
- `app.py`: fallback defensivo adicional en `send_chat_message`.
- El fallback usa contexto interno de atleta/coach y devuelve recomendaciones.

Validacion:

- Connection error forzado sin burbuja `Error:`.
- Callback flotante validado con historial y burbujas normales.
- Compileall, pytest, `test_app_flow.py`, `test_s105_load.py --skip-video` en
  verde.
- Servidor live con un solo listener.

Siguiente foco:

- Si se quiere IA externa real en demo, validar `ANTHROPIC_API_KEY`,
  conectividad y proveedor/modelo antes de presentar.

## Incidente Resuelto 2026-05-26 - Guardar Cuestionario

Problema:

- El boton "Guardar cuestionario" no guardaba.

Fix:

- `pages/wellbeing.py`: firma de `save_wellbeing` alineada con Input/State.
- Se corrigio el desplazamiento de argumentos causado por `q-user` duplicado
  como Input y State.

Validacion:

- POST real a Dash: status 200, DB +1, `q-gauge` renderizado.
- Test live contra servidor: status 200, DB +1, sin 500.
- Registro sintetico eliminado.
- Pytest, flow y load test en verde.

## Incidente Aclarado 2026-05-26 - IA Externa

Resultado:

- La key de Anthropic existe y los modelos responden OK con red externa.
- El `Connection error` venia de arrancar la app desde un entorno con red
  restringida.
- Servidor reiniciado fuera del sandbox; asistente live responde sin error y sin
  modo local.

## Incidente Resuelto 2026-05-26 - Export IMU No Disponible Aunque Hay Grafica

Problema:

- En `Señales ECG / IMU`, la pestaña IMU mostraba grafica, KPIs y sesion
  seleccionada, pero el PDF devolvia:
  `Carga o analiza un archivo IMU antes de exportar el informe.`

Fix:

- `views/signals_view.py`: el auto-load de IMU de Combat Monitor ahora escribe
  `imu-meta` para sidecars JSON de sesion.
- Excel/PDF soportan `source=session_events` y `format=event_json`.
- Si el store esta vacio, los callbacks reconstruyen metadata desde la sesion
  seleccionada.

Validacion:

- Sesion `34` exporta Excel y PDF aunque `imu-meta=None`.
- `compileall`, `pytest`, `test_app_flow.py` y `test_s105_load.py --skip-video`
  en verde.

Siguiente foco:

- Reiniciar la app para cargar este cambio en navegador.
- Validar manualmente con Carlos Rios > sesion `34` > pestaña IMU > Excel/PDF.

## Incidente Resuelto 2026-05-26 - Al Quitar Sesion Persistian Metricas

Problema:

- Al limpiar/cerrar `signals-session`, seguian visibles ECG, IMU y KPIs de la
  sesion anterior.

Fix:

- `views/signals_view.py`: los callbacks de autoload ECG/IMU ahora devuelven
  placeholders y KPIs vacios cuando la sesion queda vacia.
- `imu-meta` tambien se limpia para que los exports no apunten a una sesion
  anterior.

Validacion:

- Callback directo con `session=None`: ECG/IMU quedan sin trazas, sin KPIs y con
  mensajes de seleccion.
- `compileall`, `pytest`, `test_app_flow.py` y `test_s105_load.py --skip-video`
  en verde.

## Incidente Resuelto 2026-05-26 - Guardar Cuestionario Lento

Problema:

- `Guardar cuestionario` podia tardar demasiado en Bienestar.

Fix:

- `pages/wellbeing.py`: se elimino la llamada sincronica a IA externa en el
  callback de guardado.
- Se agrego mensaje local instantaneo para casos `wellness < 65`.

Validacion:

- Sin usos de `generate_wellbeing_message` en `pages/wellbeing.py`.
- `compileall`, `pytest`, `test_app_flow.py` y `test_s105_load.py --skip-video`
  en verde.

Siguiente foco:

- Si queremos una explicacion IA mas rica despues del guardado, implementarla
  como boton bajo demanda o callback separado, nunca bloqueando el save.

## Sprint UI/IA Biomecanica 2026-05-26 - Interpretacion Y Evidencia

Completado:

- Desplegables `Cómo interpreto esta gráfica` bajo graficas de señales,
  replay, biomecanica individual, rojo vs azul, velocidades y simulaciones.
- Evidencia por frame/tiempo dentro de lectura IA/coaching:
  - rojo vs azul;
  - objetivo individual automatico/peto/izquierda/derecha.

Validacion:

- Compileall, import app, pytest, flow test y load test en verde.

Pendiente estrategico:

- Seguir con el nucleo fuerte: selector objetivo, multipersona, peto rojo/azul,
  tracking temporal y confianza de medicion.
- Evaluar si la IA externa debe generar un resumen final bajo demanda tomando
  estas evidencias como contexto, sin bloquear el analisis base.

Limpieza:

- Se retiro BOM UTF-8 accidental de `views/signals_view.py`.
- Validado con `compileall views\signals_view.py`.

## Sprint Demo Biomecanica 2026-05-27 - Persistencia De Resultado

Completado:

- `pose-results` persistente en sesion del navegador.
- Render biomecanico completo guardado en cache server-side por `job_id`.
- Restauracion automatica al volver a la pestana `Análisis Biomecánico`.
- Limpieza automatica al cambiar objetivo de analisis.
- Limpieza de datos `pose-*` al cerrar sesion.

Validacion:

- `compileall`, import app, pytest, flow test y load test en verde.
- Prueba directa de cache confirma recuperacion de `rendered_output`.

Siguiente foco:

- Validacion visual manual con video real en navegador:
  subir video, analizar rojo vs azul, cambiar a Replay/Señales y volver a
  Biomecanica sin perder el resultado.
- Despues, continuar con confianza de medicion: selector objetivo, multipersona,
  peto rojo/azul, tracking temporal y explicacion de limites.

## Sprint Confianza Biomecanica 2026-05-27 - Anti-Pose-Contaminada

Completado:

- Coherencia cuerpo-peto en `pose_analyzer.py`.
- Rechazo `pose_contaminada`.
- `identity_quality` e `identity_warnings` por frame.
- Confianza visible ponderada por cobertura.
- UI: `Selección + cobertura`, chips para pose mezclada/color cruzado/peto
  parcial/casco cruzado.

Validacion:

- Pruebas acotadas con `videoplayback.mp4` en rojo, azul y rojo vs azul.
- Compileall, import app, pytest, flow test y load test en verde.

Siguiente foco:

- Validacion visual manual con los frames problematicos.
- Si aun se ve mezcla, el siguiente escalon es aplicar suavizado temporal mas
  fuerte: no aceptar cambio brusco de torso/cadera aunque el color parezca
  correcto.

Refinado:

- Penalizacion por overlap corporal (`cuerpo_cruzado`, `oclusion_parcial`).
- Galeria dual filtrada para mostrar solo frames limpios.
- Metadata visible por frame destacado: `t` y `score`.

## Sprint Confianza Biomecanica 2026-05-27 - Sin Atleta Claro

Completado:

- Cierre de falso positivo por ausencia/ruido:
  - nuevo helper `_candidate_athlete_evidence()`;
  - rechazo `sin_evidencia_atleta`;
  - sin fallback a poses crudas cuando duelo no encuentra atletas claros.
- Cruce corporal severo ahora puede rechazar frame como `cuerpo_cruzado`.
- UI: chip `Sin atleta claro`.

Validacion:

- `videoplayback.mp4` con `sample_every=12`, `max_frames=540`:
  - `paired_frames`: 45;
  - `target_confidence`: 0.632;
  - `target_coverage`: 0.083;
  - keyframes limpios conservados: 48.5s, 86.0s, 95.0s, 127.5s, 136.5s, 201.0s.
- Compileall, import app, pytest, flow test y load test en verde.

Siguiente foco:

- Revisar UX para explicar cobertura baja sin que parezca fallo.
- Continuar con suavizado temporal/selector objetivo si el usuario ve saltos
  restantes en frames no destacados.

Refinado posterior:

- Se agregaron filtros para galeria:
  - `esqueleto_colapsado`;
  - `casco_sin_peto_coherente`;
  - `cuerpo_recortado`.
- Resultados antiguos se invalidan con `shape_guard_v3_2026_05_27`.
- Validacion UI completa:
  - `901` frames analizados;
  - `53` frames pareados;
  - keyframes defendibles: `45.4s`, `135.4s`, `152.9s`, `204.6s`, `241.2s`.

Siguiente foco:

- Explicar visualmente por que cobertura baja = analisis conservador.
- Evaluar suavizado temporal de tracks para reducir saltos durante contactos.

Control de version/cache:

- Completado `/debug/analyzer-version`.
- Completado reinicio limpio de puerto `8051` con una sola instancia.
- Completado auto-clear de stores biomecanicos por version en navegador.
- Completado meta visible de version + keyframes en la lectura.
