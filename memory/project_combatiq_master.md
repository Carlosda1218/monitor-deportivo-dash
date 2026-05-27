# CombatIQ - Memoria Maestra Operativa

Fecha de creacion de esta memoria: 2026-05-21
Directorio: `c:\Users\cdare\Downloads\monitor_deportista_PROD_READY (1)`
App local esperada: `http://127.0.0.1:8051/`

## Proposito

CombatIQ es una plataforma enfocada exclusivamente en boxeo y taekwondo. El
objetivo inmediato es dejar la app limpia, estable y presentable para una demo
ante atletas, coaches e inversores, sin romper la logica funcional que ya
existe.

El pilar comercial y tecnico es:

- Analisis de combates.
- Sensores reales/simulados.
- Mediciones ECG/IMU.
- Biomecanica con vision computacional.
- IA contextual que traduzca datos a acciones utiles.
- Exportes profesionales para decisiones de entrenamiento y presentacion.

## Regla De Memoria

Desde 2026-05-21, cada modificacion relevante debe quedar registrada en estos
archivos de memoria:

- `memory/project_combatiq_master.md`: reglas, estado general y decisiones.
- `memory/project_sprint_plan.md`: sprints activos, tareas y checklists.
- `memory/project_change_log.md`: cambios realizados, validaciones y riesgos.
- Archivos tematicos segun corresponda: biomecanica, replay, UI, sensores,
  coach sport filter.

Cada entrada relevante debe indicar:

- Fecha.
- Que se hizo o se decidio.
- Por que importa.
- Archivos implicados.
- Validacion ejecutada o pendiente.
- Riesgo residual si existe.

## Reglas No Negociables

- No romper logica que ya funciona.
- Tocar solo lo necesario; no refactors por gusto.
- Cambios pequenos, validacion inmediata.
- Si aparece un error real, se pausa el sprint y se arregla antes de seguir.
- No silenciar errores importantes con `except Exception` sin logging o salida
  accionable.
- No eliminar datos, archivos o sesiones del usuario sin permiso explicito.
- No commitear `.env`. La `ANTHROPIC_API_KEY` vive ahi.
- Mantener UI en espanol claro y profesional.
- Separar fallos reales de deuda estetica.
- Cada decision debe poder defenderse ante atleta, coach e inversor.

## Cambios Permitidos Sin Consulta

- Sintaxis rota.
- Imports rotos.
- Mensajes de error mas claros.
- Manejo defensivo en boundaries.
- Pruebas de humo no invasivas.
- Correcciones puntuales de encoding cuando afecten UI/reportes.
- Dependencias faltantes que ya usa el codigo.

## Cambios Que Requieren Pausa

- Redisenar pantallas completas.
- Cambiar esquema DB de forma irreversible.
- Cambiar logica deportiva o umbrales biometricos.
- Introducir APIs externas con coste o dependencia fuerte.
- Cambiar flujo de login, roles o permisos.
- Eliminar datos o archivos reales.

## Estado Declarado Por El Usuario En 2026-05-21

El usuario indica que la app ha avanzado en:

- Stack: Dash + Flask + SQLite, Python, OpenVINO, MediaPipe, supervision
  ByteTrack.
- `yolo_tracker.py`: YOLOv8-pose + OpenVINO + ByteTrack para velocidades y
  biomecanica Phase 1.
- `pose_analyzer.py`: MediaPipe Tasks API, analisis individual y modo duel.
- `ai_insights.py`: Claude API, coaching contextual y cache.
- `analysis_engine.py`: ACWR, HRV readiness y alertas cruzadas.
- `sensors.py` / `hub/`: IMU custom BLE y pipeline de sensores.
- `signals_view.py`: Replay, senales ECG/IMU y biomecanica.
- Demo principal: Carlos Rios, id 21, Taekwondo, sesiones 31-34.
- Regla de notas: las sesiones que deben aparecer en Replay empiezan con
  `"Combat Monitor"`.
- Prioridad actual: reforzar el pilar de combates, sensores, mediciones e IA.

Algunos puntos declarados por el usuario aun deben verificarse en codigo antes
de considerarse confirmados:

- Modelo Claude activo exacto.
- Migraciones hasta version 180.
- Resultado actual de YOLO/OpenVINO con `videoplayback.mp4`.
- Estado exacto de todos los exports.
- Estado real de UI modo claro en todas las graficas.

## Problemas Observados Por El Usuario

- A veces la aplicacion va lenta o se congela en algunas pestanas.
- En modo claro algunas graficas o partes de UI mantienen estilos oscuros.
- Se requiere nueva auditoria exhaustiva linea a linea.
- Se debe reforzar especialmente analisis de combate, sensores, mediciones e IA.

## Regla De Optimizacion Aclarada El 2026-05-21

Reducir congelamientos no significa quitar potencia a CombatIQ. La prioridad es
limpiar lo que no se usa realmente, eliminar duplicados, evitar recalculos,
separar scripts peligrosos de pruebas seguras y mejorar flujos pesados sin
recortar capacidades utiles.

En biomecanica, senales e IA:

- No bajar precision por velocidad sin una razon validada.
- No desactivar YOLO, MediaPipe, sensores o IA solo para que "vaya mas rapido".
- No quitar graficas o analisis que aporten valor a atleta/coach/demo.
- Si una optimizacion implica menos datos analizados, debe quedar explicita y
  ser una opcion controlada, no un recorte silencioso.
- Preferir limpieza real: codigo muerto, imports sin uso, duplicados, I/O
  repetido, caches inexistentes, callbacks sobrecargados y scripts que escriben
  datos sin advertencia.

## Prioridad De Auditoria Actual

1. Sincronizar memoria y prompt maestro.
2. Validacion base: compileall, pytest, imports criticos.
3. Auditoria biomecanica: `yolo_tracker.py`, `pose_analyzer.py`,
   `signals_view.py`, `/analyze-pose`.
4. Auditoria sensores/mediciones: `sensors.py`, `hub/`, endpoints, DB.
5. Auditoria IA: `ai_insights.py`, prompts, cache, timeouts, fallbacks.
6. Auditoria rendimiento/congelamientos.
7. Auditoria modo claro/UI.
8. Auditoria exports PDF/XLSX/CSV.
9. Auditoria DB, permisos, roles y migracion futura.

## Validaciones Base

Comandos recomendados:

```powershell
.\.venv\Scripts\python.exe -m compileall app.py db.py analysis_engine.py ai_insights.py notifications.py pose_analyzer.py questionnaires.py report_utils.py sensors.py ui_charts.py yolo_tracker.py pages views hub
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe test_s105_load.py --skip-video
```

Validaciones especificas de biomecanica:

```powershell
.\.venv\Scripts\python.exe analyze_wt_deep.py
```

## Riesgos Residuales Conocidos

- El repositorio tiene muchos archivos modificados/no trackeados; no revertir
  nada sin permiso.
- Hay carpetas temporales con acceso denegado que ensucian `git status` y
  busquedas amplias.
- `app.py`, `db.py` y `signals_view.py` son grandes y deben auditarse por
  bloques.
- La memoria raiz previa estaba desactualizada respecto al prompt aportado por
  el usuario el 2026-05-21.
- Hay un archivo `kernel.errors.txt` con error de kernel/Intel/OpenVINO que
  podria relacionarse con aceleracion GPU y debe revisarse al auditar YOLO.

## Estado Actualizado 2026-05-21 - IA Por Rol

- La IA de biomecanica y Replay ya recibe audiencia desde la sesion:
  atleta/deportista, coach o admin.
- La lectura rojo vs azul ya no debe mezclar "mensaje para atleta" y "mensaje
  para coach" en el mismo bloque.
- Replay ECG/IMU y analisis visual de eventos ya pueden construir prompts
  distintos segun el visor.
- Pendiente: validar con API activa y revisar si `detect_video_events()` debe
  recibir objetivo rojo/azul para no asumir siempre peto azul.

## Estado Actualizado 2026-05-21 - Sprint 5 Rendimiento

- Se inicio Sprint 5 sobre congelamientos sin recortar capacidades.
- `signals_view.py` ya tenia MediaPipe, YOLO y render/IA divididos en callbacks
  encadenados.
- Se detecto que el cuello de botella restante era mover payloads muy grandes
  por `dcc.Store`: frames, base64 y reportes completos.
- Se agrego cache temporal en servidor para trabajos de pose. Los stores ahora
  llevan `job_id` y reportes ligeros.
- PDF biomecanico, simulacion ECG/IMU y guardado de sesion resuelven el reporte
  completo desde cache.
- La IA de vision en Replay ahora cachea deteccion de eventos y analisis de
  fotogramas para evitar repetir llamadas identicas a Claude.
- La cache IA ahora expulsa entradas antiguas si supera 50 elementos vivos.
- Senales ECG ahora reutiliza suavizado y deteccion de picos R por
  archivo/parametros para reducir lentitud al mover ventanas o exportar.
- Replay ahora reutiliza fotogramas extraidos por video/timestamp para que el
  panel de IA visual no reabra el video al repetir el mismo evento.
- Observacion: `POST /analyze-pose` en `app.py` existe como ruta legacy y puede
  devolver payload completo; revisar antes de exponerla en demo/produccion.

## Estado Actualizado 2026-05-23 - Botones Bloqueados

- Se corrigio un error critico de Dash que podia bloquear botones:
  `Duplicate callback outputs` en `pages/wellbeing.py`.
- La solucion fue prevenir doble registro de callbacks de bienestar con
  `_callback_once()`, no esconder el problema con `allow_duplicate=True`.
- Refuerzo: `_callback_once()` ahora cubre recarga antes/despues de setup de
  Dash mediante sentinel en `builtins` y revision de `dash.get_app()`.
- Fix definitivo posterior: Bienestar ahora usa un solo callback combinado para
  `q-gauge`, `q-explain` y `q-trend`, sin `allow_duplicate=True`.
- `/_dash-dependencies` confirma una sola dependencia sin sufijo `@hash`.
- Causa raiz confirmada en navegador/puerto real: habia procesos Dash antiguos
  sirviendo metadata vieja en `8051`. El codigo ya estaba corregido, pero el
  servidor vivo seguia entregando el callback con hash `@1bb...`.
- Regla operativa: si reaparece `Duplicate callback outputs` con hash `@...`,
  verificar primero `/_dash-dependencies`, procesos Python en `8051`, reinicio
  limpio de la app y recarga dura del navegador antes de tocar codigo.
- Validacion final del servidor vivo: `HasOldHash=False`, `HasCombined=True`.
- Auditoria extra: `risky_unhashed_duplicate_bases=0`. Los duplicados que
  quedan en Replay/IMU/Combat Monitor/equipo/tema son intencionales con
  `allow_duplicate=True`, no bloqueantes.
- Tambien se limpio un `SyntaxWarning` de `app.py` en JS embebido.
- Validaciones en verde: compileall, `/_dash-dependencies`, pytest,
  `test_app_flow.py` y `test_s105_load.py --skip-video`.
- Validacion actual: compileall de `views/signals_view.py`, pytest,
  `test_app_flow.py` y `test_s105_load.py --skip-video` en verde.
- Riesgo: la cache local sirve para demo/proceso unico; produccion multi-worker
  debe usar Redis, disco temporal o jobs persistidos.

## Estado Actualizado 2026-05-24 - Congelamientos Replay/IA

- Se hizo auditoria de congelamientos enfocada en no recortar potencia de
  biomecanica, senales ni IA.
- Se elimino de `app.py` un diagnostico temporal completo que interceptaba
  `fetch`, `console.error`, caminaba React fiber y mostraba un panel fijo
  `__diag`.
- Se eliminaron `__test-interval`, `__dash-test-btn`, `__dash-test-out` y su
  callback de prueba.
- Replay IA visual ahora es accion explicita: boton `Analizar IA video`.
- Replay IA de combate ahora es accion explicita: boton `Generar lectura IA`.
- `detect_video_events()` ya no corre al cambiar video/sesion; solo al pulsar
  el boton.
- `analyze_combat_session()` ya no corre al seleccionar sesion; el panel queda
  listo y espera accion del usuario.
- Se corrigio `ai_insights.analyze_event_frame()`: ahora define y consulta
  `cache_key` antes de llamar a Claude.
- Se corrigio `ai_insights.generate_athlete_note()`: se elimino un bloque
  erroneo de cache `event_frame` con variables inexistentes.
- Validacion en verde: compileall, prueba IA falsa, pytest, `test_app_flow.py`
  28/28, `test_s105_load.py --skip-video`, `git diff --check`.
- Servidor `8051` reiniciado limpio. Verificado: `HasDiag=False`,
  `HasTestCallback=False`, `HasAiButtonCallbacks=True`,
  `risky_unhashed_duplicate_bases=0`.

Regla operativa:

- IA pesada de Replay debe ser bajo demanda hasta tener jobs/background con
  progreso real. No dispararla automaticamente por seleccionar una sesion o
  cargar video.
- Cualquier diagnostico temporal de frontend debe retirarse antes de demo.

## Estado Actualizado 2026-05-24 - Logout Robusto

- Se corrigio el congelamiento/loading infinito al salir de sesion desde la
  barra lateral.
- Causa: el logout dependia de navegacion interna Dash (`dcc.Link`) y de un
  `dcc.Location` anidado dentro de `pages/logout.py`.
- Fix: `/logout` ahora es ruta Flask real; limpia `session` y redirige por HTTP
  a `/login`.
- Fix: el enlace "Salir" ahora usa `html.A` para forzar navegacion completa y
  evitar quedar atrapado en el router de Dash.
- `pages/logout.py` queda como fallback defensivo sin redireccion automatica
  anidada.
- Validacion: compileall OK, test client `302 /login`, sesion limpia,
  servidor vivo `8051` OK, `pytest`, `test_app_flow.py` 28/28 y
  `test_s105_load.py --skip-video` en verde.

Regla operativa:

- Logout y cambios criticos de autenticacion deben ser server-side HTTP
  redirects. No usar `dcc.Location` anidado como mecanismo principal de salida.

## Estado Actualizado 2026-05-24 - IA Bajo Demanda En Sesion/Ficha

- Se continuo la auditoria de congelamientos post-logout.
- Se confirmo que el boton interno "Cerrar sesion" de entrenamiento no ejecuta
  procesamiento pesado: solo cierra en DB y refresca seleccion.
- Se detectaron tres IA automaticas restantes en `app.py`:
  `generate_coaching_note()` al cambiar atleta, `generate_athlete_note()` al
  entrar a `/sesion` como atleta y `generate_team_summary()` al entrar a
  `/sesion` como coach.
- Se agregaron botones explicitos:
  - `btn-athlete-card-ai-note` en ficha de atleta.
  - `btn-sesion-ai-note` en `/sesion` atleta.
  - `btn-sesion-team-ai-note` en `/sesion` coach.
- Las tarjetas IA ahora muestran placeholders y no llaman a Claude por cambios
  de dropdown o navegacion.
- Validacion en verde: compileall, pytest, `/_dash-dependencies` sin duplicados
  peligrosos, `test_app_flow.py` 28/28, `test_s105_load.py --skip-video`,
  `git diff --check`.
- Servidor `8051` reiniciado con el codigo actual; verificado:
  `HasManualAiButtons=True`, `/logout` -> `302 /login`.

Regla operativa:

- IA potencialmente lenta debe ser bajo demanda o background job con progreso.
  No ejecutarla automaticamente al montar pagina o cambiar selector.

## Estado Actualizado 2026-05-24 - Hardening Ruta Legacy Pose

- Se revisaron rutas legacy/payloads dentro de la auditoria de congelamientos.
- `/upload-video` ya usa `fetch` multipart y no `dcc.Upload`, correcto para
  videos grandes.
- `/analyze-pose` es endpoint legacy autenticado y no parece usarse por la UI
  moderna de Biomecanica.
- Riesgo corregido: `/analyze-pose` aceptaba `max_frames` y `sample_every` sin
  limite defensivo suficiente.
- Fix: `_LEGACY_POSE_ROUTE_MAX_FRAMES = 1500`.
- Fix: `sample_every` queda limitado a `1..60`.
- Fix: `max_frames` queda limitado por
  `COMBATIQ_LEGACY_POSE_ROUTE_MAX_FRAMES` o `1500`.
- No se redujo potencia del flujo principal de Biomecanica/Yolo/MediaPipe en
  `views/signals_view.py`.
- Observacion: `data/uploads` contiene multiples videos duplicados de pruebas.
  No se eliminaron; requiere permiso explicito y listado/tamanos antes.
- Validacion en verde: compileall, pytest, `test_app_flow.py` 28/28,
  `test_s105_load.py --skip-video`, `/_dash-dependencies`
  `risky_unhashed_duplicate_bases=0`, prueba directa de cap legacy OK.
- Servidor `8051` verificado vivo despues de la interrupcion del arranque en
  primer plano: 147 dependencias, botones IA manuales presentes, `/logout`
  `302 /login`, cap legacy importado `1500`.

Regla operativa:

- Rutas legacy de analisis pesado deben tener caps defensivos aunque no sean el
  flujo principal de producto.

## Estado Actualizado 2026-05-24 - Inventario Uploads Duplicados

- Se auditaron `data/uploads`, `data/uploads_legacy` y `assets/uploads`.
- No se borro ni movio nada.
- Total: 56 archivos, ~4029.74 MB.
- Duplicados SHA-256: 3 grupos; espacio repetido estimado ~3816.16 MB.
- Grupos:
  - `20230325_213445*.mp4`: 24 copias identicas.
  - `videoplayback*.mp4`: 30 copias identicas.
  - `20260503_005430_07048cf5.mp4` y legacy `20260503_005430.mp4`: 2 copias
    identicas.
- DB no referencia directamente esos videos; solo aparecen ECG/IMU demo:
  `combat_12_wt_videoplayback.csv` y `combat_12_wt_videoplayback_imu`.
- Propuesta segura: mantener canónicos `20230325_213445.mp4`,
  `videoplayback.mp4` y `20260503_005430_07048cf5.mp4`; mover 53 duplicados a
  cuarentena `data/uploads_quarantine_20260524/`.
- No ejecutar limpieza sin permiso explicito.

## Estado Actualizado 2026-05-24 - Uploads En Cuarentena

- El usuario autorizo la limpieza controlada.
- Se movieron 53 duplicados a `data/uploads_quarantine_20260524/`.
- No se borro definitivamente ningun archivo.
- Se genero `data/upload_quarantine_20260524.json` como manifiesto de
  movimiento.
- `data/uploads` quedo con 3 canónicos:
  - `20230325_213445.mp4`
  - `videoplayback.mp4`
  - `20260503_005430_07048cf5.mp4`
- Espacio normal de uploads reducido a ~213.58 MB; cuarentena ~3816.16 MB.
- Se actualizaron aliases para que nombres antiguos sigan resolviendo a los
  canónicos.
- `check_videos.py` ahora usa `_resolve_uploaded_video()`.
- Validacion en verde: compileall, `check_videos.py`, pytest,
  `test_app_flow.py` 28/28, `test_s105_load.py --skip-video`,
  `/_dash-dependencies` sin duplicados peligrosos.

Regla operativa:

- No borrar `data/uploads_quarantine_20260524/` hasta completar validacion
  manual de Replay/Biomecanica y recibir permiso explicito.

## Estado Actualizado 2026-05-24 - Demo Login Robusto

- Se corrigio el flujo de entrada demo que podia quedar en `Loading...`.
- Antes: demo atleta/coach dependia de callbacks Dash que devolvian
  `dcc.Location`.
- Ahora: demo usa rutas Flask server-side:
  - `/demo/atleta`
  - `/demo/coach-tkd`
  - `/demo/coach-boxeo`
- Los accesos en `pages/auth_login.py` son enlaces `html.A` con `href` real.
- Las rutas crean sesion Flask y redirigen por HTTP a `/dashboard`.
- Se detectaron y detuvieron procesos duplicados en `8051`; quedo un unico
  servidor limpio.
- Validacion live:
  - `/demo/atleta` -> `302 /dashboard`.
  - `/logout` -> `302 /login`.
  - `/_dash-dependencies` -> 147 dependencias.
  - Layout vivo: `btn-demo-login` es componente `A` con `href="/demo/atleta"`.
- Validaciones en verde: compileall, pytest, `test_app_flow.py` 28/28,
  `test_s105_load.py --skip-video`.

Regla operativa:

- Entradas demo y autenticacion critica deben poder funcionar por HTTP directo,
  no solo por callbacks Dash.

## Estado Actualizado 2026-05-24 - Carga Doble Corregida

- Se corrigio una causa probable de "carga doble" tras entrar a demo.
- Causa raiz: el layout autenticado intentaba usar `request.path` como ruta
  inicial. En peticiones de Dash, ese valor puede ser `/_dash-layout` y no la
  ruta real del navegador.
- Fix en `app.py`: usuarios autenticados ya no reciben
  `dcc.Location(pathname=...)`; se usa `dcc.Location(id="url")` para que lea
  `window.location`.
- Limpieza en `pages/auth_login.py`: se quitaron contenedores `demo-redirect`
  obsoletos porque la entrada demo ya usa rutas Flask.
- Servidor `8051` reiniciado despues del cambio.
- Validacion en verde: compileall, pytest, `test_app_flow.py` 28/28,
  `test_s105_load.py --skip-video`.
- Validacion live: rutas demo redirigen a `/dashboard`, `/logout` redirige a
  `/login`, `/_dash-layout` con cookie demo no fuerza `pathname`,
  `/_dash-dependencies` tiene 144 dependencias y 0 outputs duplicados exactos.

Regla operativa:

- No usar `request.path` dentro de `serve_layout()` como fuente de verdad de la
  URL del navegador cuando el usuario ya esta autenticado.

## Estado Actualizado 2026-05-24 - Arranque Mas Estable Y Tema Persistente

- Se audito la capa de congelamientos/Loading posterior al fix de carga doble.
- Hallazgo: `app.index_string` limpiaba `sessionStorage` y claves de
  `localStorage` en cada carga, incluyendo `theme-store`.
- Riesgo: esa limpieza podia interferir con hidratacion Dash, persistencia de
  tema claro y percepcion de recarga doble.
- Fix en `app.py`: limpieza de service worker/cache ahora es versionada con
  `combatiq-sw-cache-cleanup-v2` y se ejecuta solo una vez.
- Ya no se limpia `sessionStorage`.
- Ya no se elimina `theme-store`, `ui-sidebar` ni `auth-store` en cada carga.
- Se reinicio `8051` y se eliminaron procesos duplicados.
- Validacion live: HTML sin `sessionStorage.clear`, sin eliminacion de
  `theme-store`, demo/login/logout correctos.
- Validacion en verde: compileall, pytest, `test_app_flow.py` 28/28,
  `test_s105_load.py --skip-video`, `/_dash-dependencies` 144 dependencias y
  0 outputs duplicados exactos.

Regla operativa:

- Persistencia visual/sesion no debe borrarse globalmente en cada page load; si
  hace falta limpiar caches, usar flags versionados e idempotentes.

## Estado Actualizado 2026-05-24 - SignalsView Mas Liviano Al Cargar

- Se redujo trabajo inicial en `views/signals_view.py`.
- Antes: 38 callbacks globales se disparaban al inicio.
- Ahora: 29 callbacks iniciales.
- Cambios: callbacks que solo renderizaban placeholders/estado vacio en Replay
  y señales pasaron a `prevent_initial_call=True`.
- Se mantuvo carga inicial de lo necesario:
  - sesiones Replay,
  - KPIs ECG/IMU/wellbeing,
  - selector de atleta,
  - ECG principal cuando corresponde.
- `replay-ai-panel` conserva mensaje inicial estatico sin callback.
- No se redujo potencia de analisis biomecanico, IA, sensores ni exports.
- Validacion en verde: compileall, pytest, `test_app_flow.py` 28/28,
  `test_s105_load.py --skip-video`.
- `/_dash-dependencies`: 144 dependencias, 0 outputs duplicados exactos,
  29 callbacks iniciales.
- Servidor `8051` reiniciado y verificado live.

Regla operativa:

- Diferir callbacks de estado vacio; mantener iniciales solo los que cargan
  datos reales o son necesarios para navegacion/seleccion.

## Estado Actualizado 2026-05-24 - Callbacks Iniciales En 24

- Segunda pasada de performance sobre callbacks iniciales.
- Callbacks iniciales: 29 -> 24.
- Global desde el inicio de esta etapa: 38 -> 24.
- Se difirieron callbacks seguros:
  - fuerza de password en registro,
  - campo "otro deporte" en registro,
  - store de fecha de peso/competencia,
  - progreso de checklist de competencia,
  - nota IA legacy de AnalysisView con Store inicial `None`.
- Se mantuvieron iniciales los callbacks que cargan datos reales: chat,
  compare, wellbeing, peso/nutricion, sensores, sidebar/router/tema.
- Validacion en verde: compileall, pytest, `test_app_flow.py` 28/28,
  `test_s105_load.py --skip-video`.
- `/_dash-dependencies`: 144 dependencias, 0 outputs duplicados exactos,
  24 callbacks iniciales.
- Servidor `8051` reiniciado y verificado live.

Regla operativa:

- La optimizacion de carga inicial debe ser conservadora: no diferir datos que
  el usuario espera ver al entrar a una pagina activa.

## Estado Actualizado 2026-05-24 - Auditoria Por Peso Real E Indices DB 200

- Se auditaron los callbacks restantes por costo real, no por cantidad.
- Paginas foco: bienestar, comparar, sensores, chat, peso/nutricion y senales.
- Hallazgo principal: en demo, DB no es el cuello; la mayor carga viene de
  renderizar/serializar figuras Plotly y bloques HTML.
- Ranking medido:
  - `wellbeing history render`: ~89 ms / ~24 KB.
  - `compare session charts`: ~87 ms / ~18.6 KB.
  - `peso view`: ~61 ms / ~19.1 KB.
  - `nutri view`: ~53 ms / ~20.6 KB.
  - `wellbeing trend only`: ~37 ms / ~9 KB.
- Se mantuvieron callbacks iniciales que cargan datos reales; no se recorto
  potencia de biomecanica, IA, sensores, senales ni exports.
- `db.py` ahora tiene migracion versionada `200` con indices de lectura para:
  cuestionarios, ECG, IMU, mensajes, peso y nutricion.
- DB local actualizada a `schema_version=200`.
- Validacion en verde: compileall, pytest, `test_app_flow.py` 28/28,
  `test_s105_load.py --skip-video`, `/_dash-dependencies` con 0 duplicados
  exactos y servidor `8051` vivo.

Regla operativa:

- Optimizar congelamientos por peso real: queries, render, payload y callbacks
  de pagina activa. No bajar callbacks por numero si eso retrasa datos utiles.
- No limitar historiales, informes o exports sin acuerdo explicito; si hace
  falta, primero optimizar render/lazy loading y mantener datos completos.

## Estado Actualizado 2026-05-24 - Compare Con Detalle Tecnico Bajo Demanda

- Se optimizo `views/compare_view.py` sin recortar funcionalidad.
- Antes, el callback `session_compare_all` calculaba ECG/IMU, tablas, badges y
  graficas aunque el detalle tecnico estuviera colapsado.
- Ahora el `html.Details` del bloque tecnico tiene `id="cmp-detail-toggle"` y el
  callback solo hace el calculo completo cuando `open=True` y el rol es `coach`.
- Para deportistas, los outputs ocultos siguen existiendo por compatibilidad,
  pero no disparan calculo pesado.
- Medicion:
  - Cerrado: ~0.85 ms / ~0.65 KB.
  - Abierto con datos reales: ~224 ms / ~18.6 KB.
- Validacion en verde: compileall, pytest, `test_app_flow.py` 28/28,
  `test_s105_load.py --skip-video`, dependencias Dash sin duplicados y servidor
  `8051` vivo con un solo listener real.

Regla operativa:

- Si una seccion pesada esta colapsada por defecto, cargarla bajo demanda.
- Mantener la potencia completa cuando el usuario abre el bloque.

## Estado Actualizado 2026-05-25 - Wellbeing History Separado Por Peso Real

- Se optimizo `pages/wellbeing.py` sin recortar historial ni exportes.
- El historico de wellbeing dejo de ser una sola respuesta pesada.
- Nuevo flujo:
  - `h-history-data` guarda datos primitivos ya autorizados.
  - `render_history_summary` pinta KPIs y tabla reciente.
  - `render_history_charts` pinta las dos graficas Plotly.
- El layout mantiene placeholders iniciales, asi que no queda pantalla vacia.
- Medicion:
  - Antes: ~89 ms / ~24 KB en un unico callback.
  - Ahora datos: ~7.3 ms / ~4 KB.
  - Ahora resumen/tabla: ~2.7 ms / ~5.5 KB.
  - Ahora graficas: ~72.7 ms / ~18.6 KB.
- Validacion en verde: compileall, pytest, `test_app_flow.py` 28/28,
  `test_s105_load.py --skip-video`, Dash sin duplicados y servidor `8051`
  vivo con un solo listener real.

Regla operativa:

- Separar informacion accionable ligera de graficas pesadas cuando una pagina
  mezcla ambas cosas.
- Mantener exportes e historiales completos salvo decision explicita.

## Estado Actualizado 2026-05-25 - Peso Y Nutricion Separados Por Peso Real

- Se optimizaron `peso` y `nutricion` en `app.py` sin cambiar calculos, guardado
  ni exportes.
- Peso:
  - `peso-data-store` contiene datos primitivos.
  - `render_peso_summary` pinta tabla, KPIs, alertas y ultimos registros.
  - `render_peso_graph` pinta solo la grafica.
- Nutricion:
  - `nutri-data-store` contiene datos primitivos.
  - `render_nutri_summary` pinta tabla, KPIs, insight y ultimos registros.
  - `render_nutri_graph` pinta solo la grafica.
- Medicion:
  - Peso datos ~4.5 ms, resumen ~5.3 ms, grafica ~41.3 ms.
  - Nutricion datos ~5.7 ms, resumen ~9.6 ms, grafica ~41.9 ms.
- Dash queda en 150 dependencias, 0 duplicados exactos y 24 callbacks iniciales.
- Validacion en verde: compileall, pytest, `test_app_flow.py` 28/28,
  `test_s105_load.py --skip-video`; servidor `8051` vivo con un solo listener.

Regla operativa:

- Dividir callbacks por funcion cuando la grafica domina el costo de una vista.
- La optimizacion no debe recortar historial, exportes ni recomendaciones.

## Estado Actualizado 2026-05-25 - Chat Poll Y Sensores Atleta

- Se continuo la auditoria por peso real en callbacks con polling.
- `pages/chat.py` ahora usa `chat-last-signature` y
  `_conversation_signature(messages)` para evitar re-renderizar mensajes si el
  polling no detecta cambios.
- `_chat_update` devuelve `PreventUpdate` en polling sin cambios y queda con
  `prevent_initial_call=True` porque el layout ya trae los mensajes iniciales.
- `_select_conversation` actualiza la firma al cambiar de atleta/conversacion.
- `views/sensors_view.py`: el intervalo de sensores en vista atleta queda
  desactivado porque solo actualizaba un contenedor oculto; las cards visibles
  se renderizan al entrar.
- El polling coach/admin de sensores se conserva porque si actualiza informacion
  visible del atleta seleccionado.
- `assets/chat_scroll.js`: el auto-scroll de chat deja de usar `setTimeout`
  permanente y pasa a `MutationObserver` de pagina.

Medicion:

- Chat antes: ~12.93 ms / ~2.11 KB cada 5 segundos aun sin cambios.
- Chat despues sin cambios: HTTP 204, ~5.57 ms / 0 KB.
- Envio vacio protegido: ~5.25 ms / ~2.215 KB.

Validacion:

- `compileall pages\chat.py views\sensors_view.py`: OK.
- `node --check assets\chat_scroll.js`: OK.
- `/assets/chat_scroll.js`: HTTP 200.
- `pytest -q`: `1 passed`.
- `test_app_flow.py`: `28/28`.
- `test_s105_load.py --skip-video`: OK.
- Dash dependencies: 150, 0 duplicados exactos, 23 callbacks iniciales.
- `q-gauge`, `q-explain` y `q-trend` aparecen en un solo callback; no hay
  duplicados `q-*`.
- Rutas live `/_dash-dependencies`, `/_dash-layout`, `/demo/atleta`,
  `/demo/coach-tkd` y `/logout` correctas.
- Servidor `8051` vivo con un solo listener real.

Regla operativa:

- Para congelamientos, auditar primero polling, render y payload. Si no hay
  cambios visibles, usar `PreventUpdate` o desactivar el intervalo.
- No eliminar potencia funcional para acelerar: biomecanica, IA, sensores,
  senales, historiales y exportes se preservan salvo decision explicita.

## Estado Actualizado 2026-05-25 - Auth, Registro Y Recuperacion De Contraseña

- Se corrigio la navegacion publica de autenticacion.
- Causa raiz del boton "Crear cuenta": el layout inicial sin sesion forzaba
  `dcc.Location(pathname="/login")`, por lo que `/registro` rebotaba a login.
- `app.py` ahora reconoce rutas publicas:
  `/login`, `/registro`, `/recuperar-password`, `/forgot-password`.
- En rutas publicas, `dcc.Location` lee la URL real del navegador y no se
  sobreescribe con `/login`.
- `pages/auth_login.py`: "Crear cuenta" y "Olvidaste tu contraseña" usan
  `dcc.Link`.
- `pages/auth_forgot.py`: nueva pantalla funcional para restablecer contraseña.
- `db.py`: migracion `210` crea `password_reset_tokens`.
- Tokens de recuperacion: hasheados, temporales, de un solo uso e invalidados
  tras cambio de contraseña.
- En local/demo se puede mostrar el codigo temporal para probar; en produccion
  debe enviarse por correo/API.
- `pages/auth_login.py` soporta bcrypt, PBKDF2 y SHA256 legacy al validar
  contraseña.

Validacion:

- `compileall app.py db.py pages\auth_login.py pages\auth_register.py pages\auth_forgot.py`: OK.
- DB local `schema_version=210`.
- Prueba DB: token incorrecto falla, correcto cambia password, reutilizacion
  falla.
- `pytest -q`: `1 passed`.
- `test_app_flow.py`: `28/28`.
- `test_s105_load.py --skip-video`: OK.
- Rutas live `/login`, `/registro`, `/recuperar-password`, `/forgot-password`
  responden 200 y no redirigen a `/login`.
- Router renderiza los IDs esperados: `login-email`, `reg-email`,
  `forgot-email`.
- Auth callbacks y `q-trend` sin duplicados especificos.
- Servidor `8051` vivo con un solo listener real.
- Confirmacion dirigida posterior: `15/15` validaciones en verde sobre rutas
  publicas, links, callback de registro, login, token de recuperacion, cambio
  de contraseña, bloqueo de reutilizacion y cleanup de usuario de prueba.

Regla operativa:

- Auth publico debe funcionar aunque no exista sesion.
- No dejar enlaces decorativos en auth: cada link visible debe navegar o ejecutar
  una accion real.
- El envio real de correos queda como integracion futura; la base segura ya esta
  preparada en DB.

## Estado Actualizado 2026-05-26 - Auditoria De Congelamientos

- Se auditaron congelamientos desde el enfoque de peso real: listeners, debug,
  rutas, callbacks, polling y layouts.
- Hallazgo principal: habia dos procesos escuchando en `8051` (`39024` y
  `40532`). Se detuvieron y se reinicio la app; quedo un solo listener
  (`35632`).
- `app.py` ya no arranca en `debug=True` por defecto.
- Nuevo flag: `COMBATIQ_DEBUG=0` por defecto; para desarrollo se puede activar
  con `COMBATIQ_DEBUG=1`.
- `dev_tools_hot_reload=False` queda explicito en `app.run`.
- El log del router baja de `INFO` a `DEBUG`.
- `.env.example` documenta `COMBATIQ_DEBUG=0`.

Medicion:

- Router no mostro freeze general: dashboard atleta ~290 ms promedio, analisis
  ~77 ms, comparar ~69 ms.
- Live tras reinicio limpio:
  - `/login` ~31.8 ms.
  - `/registro` ~14.9 ms.
  - `/recuperar-password` ~14.4 ms.
  - `/demo/atleta` ~52.5 ms y redirige a `/dashboard`.
  - `/_dash-layout` ~184.9 ms con sesion atleta.
  - `/_dash-dependencies` ~12.6 ms.
- Logout validado: demo atleta -> logout -> login; layout posterior ~14.5 ms.
- Callbacks especificos auth y `q-trend` aparecen una sola vez.

Validacion:

- `compileall app.py`: OK.
- `pytest -q`: `1 passed`.
- `test_app_flow.py`: `28/28`.
- `test_s105_load.py --skip-video`: OK.
- Un solo listener real en `8051`.

Regla operativa:

- Antes de culpar a callbacks, confirmar que solo exista un listener en el
  puerto de la app.
- Demo/inversores: debug y hot reload apagados por defecto.
- No optimizar congelamientos quitando potencia; primero eliminar ruido de
  runtime, procesos duplicados y logs innecesarios.

## Estado Actualizado 2026-05-26 - Asistente IA Sin Error Crudo

- Se corrigio el asistente IA flotante cuando Claude/API devuelve
  `Connection error`.
- `ai_insights.py` ahora tiene `_chat_local_fallback`.
- Si falta `ANTHROPIC_API_KEY` o falla la conexion externa, el asistente
  responde en modo local con datos internos.
- El fallback distingue rol:
  - Deportista: bienestar, tendencia, ultima sesion, ECG, competencia y plan de
    accion para hoy.
  - Coach: resumen de equipo, bienestar medio, atletas prioritarios y accion de
    carga/check-in.
- `app.py`: `send_chat_message` tiene un fallback adicional para evitar burbujas
  `Error: ...` si falla la llamada completa.

Validacion:

- `compileall ai_insights.py app.py`: OK.
- Test forzado con `Connection error`: atleta y coach devuelven
  `has_raw_error=False`.
- Callback flotante directo: `bubble_count=3`, `history_len=2`,
  `input_value=''`, `has_raw_error=False`.
- `pytest -q`: `1 passed`.
- `test_app_flow.py`: `28/28`.
- `test_s105_load.py --skip-video`: OK.
- Live server: rutas basicas 200, `ai-chat-messages` registrado una sola vez,
  un solo listener en `8051`.

Regla operativa:

- La capa de IA visible debe fallar de forma amable y accionable.
- No mostrar excepciones tecnicas al usuario final; usar logs y fallback local.

## Estado Actualizado 2026-05-26 - IA Externa Y Cuestionario

- Se confirmo que la IA externa si funciona: la key existe y los modelos
  responden cuando el proceso tiene red.
- Causa del `Connection error`: el servidor habia sido arrancado desde entorno
  con red restringida.
- Prueba Anthropic con red externa:
  - `claude-haiku-4-5`: OK.
  - `claude-haiku-4-5-20251001`: OK.
  - `claude-sonnet-4-6`: OK.
- La documentacion oficial confirma que `claude-haiku-4-5` es alias API del
  modelo fechado `claude-haiku-4-5-20251001`.
- Servidor reiniciado fuera de la restriccion: un solo listener en `8051`.
- Test live del asistente: status 200, ~6992 ms, sin `Connection error` y sin
  fallback local.

Cuestionario:

- Se corrigio `pages/wellbeing.py`.
- Causa: `save_wellbeing` no recibia el segundo `q-user` que estaba declarado
  como `State`, por lo que los argumentos quedaban desplazados.
- Error exacto: `"no"` llegaba a `Q.score_breakdown` como valor numerico y
  generaba `ValueError`.
- Fix: firma alineada:
  `save_wellbeing(input_user_id, n, user_id, session_id, competition, weight,
  injury, *values)`.
- Validacion POST real: antes 500; despues 200, DB +1 y `q-gauge` en respuesta.
- Registro sintetico eliminado.

Validacion:

- `compileall pages\wellbeing.py`: OK.
- `pytest -q`: `1 passed`.
- `test_app_flow.py`: `28/28`.
- `test_s105_load.py --skip-video`: OK.

Regla operativa:

- IA externa en demo requiere arrancar la app con red real.
- Los callbacks Dash con Input y State duplicados deben auditarse por orden de
  argumentos.

## Estado Actualizado 2026-05-26 - Export IMU De Sesion

- Se corrigio la exportacion IMU desde `Señales ECG / IMU` cuando la grafica se
  auto-carga desde una sesion Combat Monitor.
- Causa: `auto_load_imu_for_session` mostraba grafica/KPIs, pero no llenaba
  `imu-meta`; los botones de Excel/PDF dependian de ese store y asumian que no
  habia IMU analizado.
- `views/signals_view.py` ahora guarda metadata exportable para sidecars
  `data/ecg/*_imu.json` con `source=session_events` y `format=event_json`.
- Los exports IMU ahora aceptan:
  - CSV manual desde `data/imu`.
  - Eventos JSON de sesion desde `data/ecg`.
- Tambien existe fallback: si `imu-meta` esta vacio, el export reconstruye la
  metadata desde `signals-session`, `ecg-user` e `imu-tabs`.
- Validado con sesion `34`:
  - Excel: `CombatIQ_IMU_combat_12_wt_videoplayback_imu_eventos.xlsx`.
  - PDF: `CombatIQ_IMU_combat_12_wt_videoplayback_imu_informe.pdf`.
- Pruebas: `compileall views\signals_view.py`, import de app, callback directo,
  `pytest -q`, `test_app_flow.py` 28/28 y `test_s105_load.py --skip-video`.

Regla operativa:

- Si CombatIQ muestra una grafica a partir de datos de sesion, esa misma fuente
  debe ser exportable.
- No bloquear export por falta de `imu-meta` si la sesion seleccionada contiene
  IMU recuperable en DB/sidecar.

## Estado Actualizado 2026-05-26 - Sin Sesion Limpia ECG/IMU

- Se corrigio el estado visual de `Señales ECG / IMU` cuando el usuario limpia o
  cierra la sesion seleccionada.
- Antes, Dash conservaba la grafica y KPIs de la sesion anterior porque los
  callbacks devolvian `no_update` / `PreventUpdate`.
- `views/signals_view.py` ahora limpia:
  - `ecg-file`;
  - grafica ECG;
  - KPIs ECG;
  - grafica IMU;
  - KPIs IMU;
  - `imu-meta`.
- La UI muestra placeholders claros:
  - `Selecciona una sesión para ver ECG`.
  - `Selecciona una sesión para ver IMU`.
- No se modificaron calculos ni datos guardados; es una correccion de estado UI.
- Validado con callback directo, `compileall`, `pytest`, `test_app_flow.py`
  28/28 y `test_s105_load.py --skip-video`.

Regla operativa:

- Una seleccion vacia debe limpiar toda lectura dependiente; nunca conservar
  metricas antiguas que puedan parecer actuales.

## Estado Actualizado 2026-05-26 - Bienestar Guardado Rapido

- Se optimizo `Guardar cuestionario` en Bienestar.
- Antes, para bienestar bajo (`<65`), el callback llamaba IA externa dentro del
  mismo clic para generar una frase motivacional.
- Eso podia bloquear la respuesta hasta ~8 s si habia latencia o red restringida.
- `pages/wellbeing.py` ahora usa `_build_fast_wellbeing_message()`, una frase
  local instantanea basada en score, deporte, fortalezas y riesgos.
- El guardado conserva recomendacion accionable, pero ya no depende de
  Anthropic/red externa.
- Validado con `compileall`, busqueda de usos, `pytest`, `test_app_flow.py`
  28/28 y `test_s105_load.py --skip-video`.

Regla operativa:

- Ningun boton principal de guardado debe llamar APIs externas de forma
  sincronica.
- La IA externa debe ir bajo demanda, en segundo plano o con fallback rapido.

## Estado Actualizado 2026-05-26 - Lectura Biomecanica Con Evidencia

- Se mejoro la UX de graficas y lectura IA/coaching en `views/signals_view.py`.
- Todas las graficas relevantes de señales/replay/biomecanica tienen un
  desplegable `Cómo interpreto esta gráfica`.
- Ese desplegable solo explica la grafica: ejes, marcas, picos, curvas y
  limites de lectura.
- La lectura IA/coaching ahora incluye evidencia por frame cuando hay datos:
  - Modo rojo vs azul: distancia minima, intercambio, presion, pico angular.
  - Modo individual: amplitud, asimetria, landmarks dudosos, baja calidad.
- Los frames se muestran como `t=...s · frame ...` para que atleta/coach puedan
  buscar el momento en el video.
- Validado con `compileall`, import app, `pytest`, `test_app_flow.py` 28/28 y
  `test_s105_load.py --skip-video`.

Regla operativa:

- Grafica = interpretacion visual.
- IA/coaching = decision accionable + evidencia temporal.
- No mezclar recomendaciones dentro del desplegable de grafica.

Nota tecnica:

- Se retiro un BOM UTF-8 accidental de `views/signals_view.py` para evitar ruido
  de diff/encoding en el archivo critico de senales y biomecanica.
- Validado con `compileall views\signals_view.py`.

## Estado Actualizado 2026-05-27 - Biomecanica Persistente En Sesion

- Se corrigio el comportamiento de demo/inversores: un analisis biomecanico
  completado ya no debe desaparecer al cambiar de pestana y volver.
- `views/signals_view.py` conserva `pose-results` en `sessionStorage` y guarda el
  render visible de la lectura en cache server-side por `job_id`.
- Al volver a `Análisis Biomecánico`, `restore_pose_output()` reconstruye la
  vista desde cache si el usuario coincide.
- Si se cambia el objetivo (`Auto`, `Peto rojo`, `Peto azul`, `Rojo vs azul`,
  `Atleta izquierda`, `Atleta derecha`), la lectura se limpia para evitar datos
  incoherentes.
- `/logout` ahora redirige a `/login?logged_out=1` y
  `assets/60_pose_session_cleanup.js` limpia datos biomecanicos persistidos del
  navegador.
- TTL de cache de pose: 4 horas, acotado por maximo de items.

Regla operativa:

- Resultado biomecanico completado = persistente durante la sesion.
- Cambiar objetivo = invalidar resultado.
- Logout = limpiar resultado persistido.

## Estado Actualizado 2026-05-27 - Filtro Anti-Pose-Contaminada

- Se reforzo `pose_analyzer.py` contra cruces/oclusiones donde el peto rojo/azul
  se detecta, pero el esqueleto viene mezclado con otra persona.
- Nuevo concepto: `identity_quality`.
- Nuevo rechazo: `pose_contaminada`.
- La seleccion de objetivo ya no depende solo de color/casco/continuidad; tambien
  valida coherencia geometrica entre peto y torso/cadera/hombros.
- La confianza visible ahora combina:
  - calidad de frames aceptados (`selection_confidence_raw`);
  - cobertura (`coverage`);
  - continuidad.
- `views/signals_view.py` muestra esta idea como `Selección + cobertura` y
  expone `cobertura` en el resumen del objetivo.

Regla operativa:

- Si el sistema sospecha mezcla de cuerpos, baja confianza o descarta el frame.
- Mejor perder frames que presentar una metrica biomecanica falsa.

Refinamiento:

- `pose_analyzer.py` tambien penaliza solapamiento corporal:
  `cuerpo_cruzado` y `oclusion_parcial`.
- La galeria de momentos clave omite frames contaminados o parcialmente
  ocluidos; ya no debe usar esos frames como imagen fuerte de demo.
- `annotated_frames_meta` permite mostrar `t` y `score` debajo de cada frame
  seleccionado.

## Estado Actualizado 2026-05-27 - Falso Positivo Sin Atleta Claro

- Se cerro una segunda categoria de error biomecanico: no solo mezcla de cuerpos,
  tambien frames donde MediaPipe detecta una pose pero no hay evidencia clara de
  atleta rojo/azul.
- `pose_analyzer.py` agrega `_candidate_athlete_evidence()`.
- Rojo/azul ahora requieren evidencia minima de atleta: cuerpo visible,
  casco/peto compatible y descarte de arbitro/ruido.
- Si el pre-filtro de duelo no encuentra atletas claros, ya no usa candidatos
  crudos; devuelve `sin_evidencia_atleta`.
- Cruce corporal severo puede rechazar el frame como `cuerpo_cruzado`.
- `views/signals_view.py` muestra el chip `Sin atleta claro`.

Regla operativa:

- No fabricar atletas para mantener continuidad. La continuidad temporal ayuda,
  pero no sustituye la evidencia visual.
- Mejor una lectura conservadora con cobertura baja que una lectura visualmente
  convincente pero falsa.

## Estado Actualizado 2026-05-27 - Keyframes Defendibles

- Se agrego una capa extra para que la galeria no muestre frames debiles aunque
  algunas mediciones todavia sean usables.
- Nuevos conceptos:
  - `esqueleto_colapsado`: anatomia no plausible para lectura fuerte;
  - `casco_sin_peto_coherente`: cabeza/casco parece objetivo pero torso no
    acompana;
  - `cuerpo_recortado`: atleta demasiado pegado al borde.
- `esqueleto_colapsado` rechaza el frame para rojo/azul.
- `cuerpo_recortado` evita que el frame sea destacado visualmente.
- Version del analizador: `shape_guard_v3_2026_05_27`.
- Resultados antiguos en sessionStorage se invalidan y piden nuevo analisis.

Regla operativa:

- La galeria de momentos clave debe ser mas estricta que la serie numerica.
- Para inversores/coaches, nunca mostrar como "mejor frame" uno recortado,
  mezclado o dependiente de color de fondo.

## Estado Actualizado 2026-05-27 - Diagnostico De Version En Vivo

- Nueva ruta local: `/debug/analyzer-version`.
- Sirve para confirmar:
  - PID activo;
  - archivo `pose_analyzer.py` cargado;
  - version de `pose_analyzer`;
  - version de `signals_view`.
- La UI de biomecanica muestra version y tiempos de keyframes.
- El navegador limpia stores de pose si cambia la version del analizador.

Regla operativa:

- Si aparecen keyframes antiguos tras modificar biomecanica, primero revisar:
  `/debug/analyzer-version`, cantidad de procesos en `8051` y sessionStorage.
