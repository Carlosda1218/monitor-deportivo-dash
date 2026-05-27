# CombatIQ - Registro Vivo De Cambios

## 2026-05-21 - Creacion De Memoria Operativa

Contexto:

- El usuario solicito que, desde este momento, cada modificacion relevante,
  observacion, optimizacion, area de mejora y decision quede registrada en la
  memoria del proyecto.
- Al iniciar la sesion, el prompt aportado por el usuario mencionaba archivos
  `memory/*.md`, pero la carpeta `memory/` no existia en esta copia local.

Cambios:

- Se creo la carpeta `memory/`.
- Se creo `memory/project_combatiq_master.md`.
- Se creo `memory/project_sprint_plan.md`.
- Se creo este registro `memory/project_change_log.md`.
- Se preparan archivos tematicos para biomecanica, replay/exportes, UI,
  coach sport filter y sensores.

Por que importa:

- Evita perder contexto entre sesiones.
- Permite auditar decisiones y no repetir trabajo.
- Reduce riesgo de actuar con un prompt maestro desactualizado.

Archivos implicados:

- `memory/project_combatiq_master.md`
- `memory/project_sprint_plan.md`
- `memory/project_change_log.md`

Validacion:

- Pendiente tras completar todos los archivos de memoria y actualizar
  `COMBATIQ_MASTER_PROMPT.md`.

Riesgo residual:

- Aun falta reconciliar completamente el prompt maestro raiz con el prompt
  actualizado aportado por el usuario el 2026-05-21.

## 2026-05-21 - Sincronizacion De Prompt Maestro Raiz

Contexto:

- `COMBATIQ_MASTER_PROMPT.md` existia en la raiz, pero estaba menos actualizado
  que el prompt aportado por el usuario el 2026-05-21.

Cambios:

- Se agrego una seccion `Actualizacion Operativa 2026-05-21`.
- Se documento la nueva regla de memoria.
- Se listaron los archivos `memory/*.md`.
- Se registro el orden de trabajo por sprints.

Archivos implicados:

- `COMBATIQ_MASTER_PROMPT.md`

Validacion:

- Pendiente en ese momento; completada despues con `compileall`, `pytest -q`
  y `test_app_flow.py`.

Riesgo residual:

- El prompt maestro raiz todavia conserva secciones antiguas y algunos textos
  con mojibake. No bloquea la app, pero debe limpiarse cuando toque auditoria
  de documentacion/encoding.

## 2026-05-21 - Correccion De Red De Pruebas Base

Contexto:

- `compileall` paso.
- `pytest -q` fallaba porque `test_app_flow.py` era recolectado por pytest,
  ejecutaba codigo en import y terminaba con `sys.exit`.
- Ese script operativo tambien esperaba la sesion demo `30`, pero el estado
  demo actual del atleta 21 usa sesiones `31`, `32`, `33` y `34`.

Cambios:

- `pytest.ini` ahora ignora scripts operativos que no son pruebas pytest:
  `test_app_flow.py`, `test_replay_session.py` y `test_claude.py`, ademas de
  los ya ignorados `test_s105_load.py` y `test_sensor_hw.py`.
- `test_app_flow.py` ahora apunta a la sesion demo vigente `34`.
- Los mensajes del script dejaron de mencionar de forma fija la sesion `30`.

Por que importa:

- Recupera una red de seguridad limpia para auditoria.
- Evita que pytest falle por scripts pensados para ejecutarse manualmente.
- Mantiene una prueba operativa separada para validar Replay, ECG e IMU contra
  la demo real.

Archivos implicados:

- `pytest.ini`
- `test_app_flow.py`

Validacion:

```powershell
.\.venv\Scripts\python.exe -m compileall app.py db.py analysis_engine.py ai_insights.py notifications.py pose_analyzer.py questionnaires.py report_utils.py sensors.py ui_charts.py yolo_tracker.py pages views hub
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe test_app_flow.py
```

Resultado:

- `compileall`: OK.
- `pytest -q`: `1 passed`.
- `test_app_flow.py`: `28/28` pruebas operativas pasaron.

Riesgo residual:

- `test_replay_session.py` y `test_claude.py` siguen siendo scripts operativos;
  deben ejecutarse manualmente cuando corresponda, no dentro de pytest.

## 2026-05-21 - Auditoria Y Correcciones Iniciales YOLO

Contexto:

- Se inicio Sprint 2 con `yolo_tracker.py` por ser nucleo del analisis
  YOLO/OpenVINO/ByteTrack.
- El usuario habia reportado lentitud/congelamientos y riesgo de deteccion
  incorrecta de personas en combate.

Cambios:

- `yolo_tracker.py` ya importa correctamente; se corrigio el `NameError` por
  constante usada antes de declararse.
- OpenVINO ahora tiene fallback GPU -> CPU.
- La lectura de video paso de seeks con `cap.set()` por muestra a lectura
  secuencial con salto por `sample_every`.
- Se agrego validacion de apertura de video.
- Se endurecio `_vest_color()` con clipping, ratio minimo y dominancia.
- El fallback por posicion ya no asigna color a una unica persona sin peto.
- `_postprocess()` conserva hasta 6 candidatos por frame para que arbitro o
  espectadores no oculten atletas reales.
- `analyze_duel_speeds()` conserva claves `azul`/`rojo` y agrega aliases
  `blue`/`red`.

Archivos implicados:

- `yolo_tracker.py`

Validacion:

- `python -m compileall yolo_tracker.py`: OK.
- `python -c "import yolo_tracker"`: OK.
- Prueba completa en `videoplayback.mp4`: 58.5s, sin error, azul 26 picos,
  rojo 16 picos, aliases `blue`/`red` poblados.

Riesgo residual:

- Rojo tiene menos frames/picos que el resultado historico. Puede ser porque
  ahora se evita ghost/referee, pero requiere revision visual antes de declarar
  la metrica final.

## 2026-05-21 - Blindaje De analyze_wt_deep.py

Contexto:

- Se ejecuto `analyze_wt_deep.py` como validacion, pero el script escribia
  archivos y DB por defecto.

Cambios:

- Se agrego `argparse`.
- Por defecto el script corre en DRY RUN.
- Para persistir datos ahora se requiere `--write`.

Archivos implicados:

- `analyze_wt_deep.py`

Validacion:

- `python -m compileall analyze_wt_deep.py yolo_tracker.py`: OK.
- `python analyze_wt_deep.py`: OK en modo seguro, no escribe.
- `python -m pytest -q`: `1 passed`.
- `python test_app_flow.py`: `28/28`.

Riesgo residual:

- Antes del blindaje, una ejecucion ya regenero archivos ECG/IMU de demo. No se
  revirtio sin permiso del usuario.

## 2026-05-21 - Regla De Optimizacion Sin Recorte Funcional

Contexto:

- El usuario aclaro que reducir congelamientos no significa quitar potencia a
  la aplicacion.
- El objetivo es limpiar lo que no se usa realmente, sobre todo en biomecanica,
  senales e IA.

Decision:

- Las optimizaciones deben conservar capacidades tecnicas utiles.
- No se deben desactivar analisis, IA, sensores ni graficas valiosas para ganar
  velocidad de forma artificial.
- Se priorizara limpieza real: codigo muerto, duplicados, recalculos, I/O
  repetido, scripts que escriben datos por accidente y callbacks pesados.

Archivos implicados:

- `memory/project_combatiq_master.md`
- `memory/project_change_log.md`

Validacion:

- Registro documental; sin impacto funcional.

## 2026-05-21 - Auditoria Y Limpieza De pose_analyzer.py

Contexto:

- Se continuo Sprint 2 sobre biomecanica MediaPipe.
- La optimizacion debia limpiar trabajo innecesario sin reducir potencia.

Cambios:

- Se elimino import no usado `sys`.
- Se agrego logging local para excepciones visuales no criticas.
- Se valido resultado de `cv2.imencode()` antes de base64.
- Se limito el pool de frames JPEG candidatos de galeria dual a 48 por defecto,
  configurable con `COMBATIQ_DUEL_KEYFRAME_CANDIDATES`.
- El modo `duel` ahora respeta `max_frames` y `max_seconds` si el caller los
  pasa explicitamente; solo autoescala cuando se usan defaults de modo
  individual.
- La simulacion ECG/IMU derivada de duelo ahora es determinista.

Archivos implicados:

- `pose_analyzer.py`

Validacion:

- `python -m compileall pose_analyzer.py`: OK.
- `python -c "import pose_analyzer"`: OK.
- Analisis individual azul corto: OK.
- Analisis dual corto con limites explicitos: OK, `frames_analyzed=40`.
- `python -m pytest -q`: `1 passed`.
- `python test_app_flow.py`: `28/28`.

Riesgo residual:

- Falta auditar `views/signals_view.py` para ver si el callback de biomecanica
  ejecuta MediaPipe/YOLO de forma bloqueante y si debe separar preview de
  analisis completo.

## 2026-05-21 - IA Por Rol En Biomecanica Y Replay

Contexto:

- El usuario detecto que la IA mezclaba mensaje para atleta y coach en una
  misma lectura, lo que hacia menos claro el analisis biomecanico y Replay.
- El objetivo era mejorar la traduccion deportiva de las graficas sin tocar la
  matematica de pose, tracking, rounds, ECG ni IMU.

Cambios:

- `generate_duel_insight()` ahora recibe audiencia, nombre del atleta y nombre
  del coach.
- El prompt de duelo rojo vs azul ahora obliga a hablar a una sola audiencia:
  atleta o coach.
- El fallback sin API tambien queda separado por rol:
  "Tu lectura del combate" para atleta y "Lectura tactica para coach" para
  coach.
- `analyze_combat_session()` ahora recibe audiencia y nombre del visor para
  que el panel Replay ECG/IMU no asuma siempre que habla al coach.
- `analyze_event_frame()` ahora acepta audiencia, atleta y visor para los
  analisis visuales de eventos.
- `signals_view.py` pasa el rol real de Flask session a la IA de biomecanica,
  Replay y evento visual.
- Las tarjetas de biomecanica agregan una frase puente distinta para coach y
  atleta antes de explicar la grafica.

Archivos implicados:

- `ai_insights.py`
- `views/signals_view.py`

Validacion:

- `python -m compileall ai_insights.py views/signals_view.py`: OK.
- Prueba local sin API: salida atleta y salida coach son distintas y no se
  mezclan.
- `python -m compileall app.py db.py analysis_engine.py ai_insights.py pose_analyzer.py yolo_tracker.py views pages hub`: OK.
- `python -m pytest -q`: `1 passed`.
- `python test_app_flow.py`: `28/28`.

Riesgo residual:

- Aun falta auditar a fondo si el callback de biomecanica debe dividirse en
  preview/proceso completo para evitar congelamientos.
- Queda pendiente revisar la calidad visual/manual de los textos en UI real,
  especialmente con API activa.

## 2026-05-21 - Verificacion Tras Actualizacion Del Proyecto

Contexto:

- El usuario actualizo el proyecto y pidio revisar el prompt maestro para saber
  desde donde retomar y comprobar si lo hecho esta correctamente registrado.

Hallazgos:

- `COMBATIQ_MASTER_PROMPT.md` esta mas actualizado que algunas memorias
  tematicas.
- El prompt maestro marca Sprints 2, 3 y 4 como completados, y Sprint 5 como
  siguiente foco.
- El codigo confirma que `detect_video_events()` ya acepta `target_vest` y que
  `signals_view.py` lo pasa desde `pose-target-select`.
- La memoria de Replay y el plan de sprints estaban desfasados y fueron
  sincronizados.

Archivos implicados:

- `COMBATIQ_MASTER_PROMPT.md`
- `memory/project_sprint_plan.md`
- `memory/project_replay_lessons.md`
- `memory/project_sensors_roadmap.md`
- `memory/project_change_log.md`

Validacion:

- `python -m compileall app.py db.py analysis_engine.py ai_insights.py notifications.py pose_analyzer.py questionnaires.py report_utils.py sensors.py ui_charts.py yolo_tracker.py pages views hub`: OK.
- `python -m pytest -q`: `1 passed`.
- `python test_app_flow.py`: `28/28`.
- `python test_s105_load.py --skip-video`: OK.
- Prueba directa de cache: `cache_helpers_ok True`.

Riesgo residual:

- El arbol Git sigue muy modificado/no trackeado; no revertir ni limpiar sin
  permiso.
- Siguiente sprint recomendado: Sprint 5, rendimiento/congelamientos en
  `signals_view.py`.

## 2026-05-21 - Sprint 5 Cache Ligera Para Biomecanica

Contexto:

- Se inicio Sprint 5 con la regla del usuario: reducir congelamientos no debe
  quitar potencia a la app.
- `signals_view.py` ya tenia el analisis dividido en 3 callbacks, pero seguia
  usando `dcc.Store` para mover resultados muy pesados al navegador.

Hallazgo:

- Los stores intermedios/finales podian incluir frames, imagenes base64,
  lecturas completas de duelo y datos para PDF.
- Aunque MediaPipe, YOLO y la IA corrieran separados, serializar esos objetos
  al cliente podia congelar la pestana o hacerla lenta.

Cambios:

- Se agrego una cache temporal server-side para trabajos de pose con TTL de 45
  minutos y maximo 8 entradas.
- `pose-mediapipe-store` y `pose-speed-store` ahora guardan `job_id` y estado,
  no el payload completo.
- `pose-results` guarda un reporte ligero para la UI.
- PDF biomecanico, simulacion ECG/IMU derivada de pose y guardado de sesion
  resuelven el reporte completo desde la cache.

Archivos implicados:

- `views/signals_view.py`
- `memory/project_sprint_plan.md`
- `memory/project_change_log.md`

Validacion:

- `python -m compileall views\signals_view.py`: OK.
- `python -m pytest -q`: `1 passed`.
- `python test_app_flow.py`: `28/28`.
- `python test_s105_load.py --skip-video`: OK.

Riesgo residual:

- La cache es local al proceso. Para produccion/multi-worker conviene usar
  Redis, archivo temporal firmado o tabla de jobs.
- Si el servidor se reinicia o pasan 45 minutos, el usuario debe repetir el
  analisis para exportar el PDF desde ese resultado.
- Falta validacion manual con video largo real en la UI.

## 2026-05-21 - Sprint 5 Cache Para IA De Vision En Replay

Contexto:

- Replay puede analizar eventos visuales de video y fotogramas con IA.
- En una demo es comun cambiar de sesion, volver a tocar un evento o revisar el
  mismo video varias veces.

Hallazgo:

- `detect_video_events()` no tenia cache propia; una repeticion del mismo video,
  peto y eventos IMU podia volver a llamar a Claude Vision.
- `analyze_event_frame()` tampoco cacheaba por fotograma/evento/audiencia.

Cambios:

- `detect_video_events()` ahora cachea por ruta absoluta, `mtime`, tamano,
  deporte, parametros de muestreo, peto objetivo y firma de eventos IMU.
- `analyze_event_frame()` ahora cachea por hash del frame, evento, audiencia y
  nombres de atleta/visor.
- No se cambio el flujo visible ni el criterio deportivo; solo se evita repetir
  llamadas identicas.

Archivos implicados:

- `ai_insights.py`
- `memory/project_change_log.md`
- `memory/project_replay_lessons.md`
- `memory/project_sprint_plan.md`

Validacion:

- `python -m compileall ai_insights.py views\signals_view.py`: OK.
- `python -m pytest -q`: `1 passed`.
- `python test_app_flow.py`: `28/28`.

Riesgo residual:

- La cache global de IA sigue siendo en memoria y TTL 10 minutos.
- Si se desea control mas fino en produccion, conviene separar cache de texto,
  vision y trabajos largos.

## 2026-05-21 - Sprint 5 Eviccion Real De Cache IA

Contexto:

- La cache global de `ai_insights.py` tenia TTL, pero no expulsaba entradas
  antiguas si habia mas de 50 entradas aun vigentes.

Cambio:

- `_cache_set()` ahora elimina expiradas y, si todavia quedan mas de 50,
  expulsa las entradas mas antiguas.

Validacion:

- `python -m compileall ai_insights.py`: OK.
- Prueba directa: `ai_cache_eviction_ok 50`.
- `python -m pytest -q`: `1 passed`.
- `python test_app_flow.py`: `28/28`.

Riesgo residual:

- La cache sigue siendo local al proceso; produccion multi-worker necesita
  backend compartido si se busca consistencia entre workers.

## 2026-05-22 - Sprint 5 Cache De Procesamiento ECG

Contexto:

- En la pestana Senales ECG/IMU, mover la ventana del ECG podia recalcular
  suavizado y deteccion de picos R sobre toda la senal.
- Esto no rompia la app, pero podia sentirse lento en archivos largos o al
  mover sliders varias veces.

Cambio:

- Se agrego `_cached_ecg_process()` en `views/signals_view.py`.
- El cache se indexa por archivo, `mtime`, frecuencia, longitud de la senal,
  suavizado y sensibilidad.
- `render_ecg`, carga directa de ECG por sesion, export de picos y PDF ECG
  reutilizan el procesamiento cuando los parametros no cambian.

Archivos implicados:

- `views/signals_view.py`
- `memory/project_change_log.md`
- `memory/project_sprint_plan.md`
- `memory/project_replay_lessons.md`

Validacion:

- `python -m compileall views\signals_view.py`: OK.
- Prueba directa: `ecg_process_cache_ok 1500 4 0 1`.
- `python -m pytest -q`: `1 passed`.
- `python test_app_flow.py`: `28/28`.
- `python test_s105_load.py --skip-video`: OK.

Riesgo residual:

- La senal demo esta a 4 Hz, por eso no detecta picos R reales en esta prueba
  directa; el fallback de metricas almacenadas sigue cubriendo este caso.
- Seguir revisando archivos ECG de alta frecuencia para confirmar sensacion de
  fluidez al mover ventanas largas.

## 2026-05-22 - Sprint 5 Cache De Fotogramas Replay

Contexto:

- Al hacer clic varias veces sobre el mismo evento de Replay, la respuesta IA
  ya se cacheaba, pero todavia se podia volver a abrir el video para extraer el
  mismo frame.

Cambio:

- Se agrego cache temporal de fotogramas JPEG por video, timestamp, calidad,
  `mtime` y tamano.
- `analyze_vision_event()` usa `_extract_replay_frame_b64()` en vez de abrir
  manualmente el video en cada clic.
- Si el video no existe o no se puede abrir, se conserva el comportamiento
  seguro actual: no rompe y muestra el mensaje de fotograma no disponible.

Archivos implicados:

- `views/signals_view.py`
- `memory/project_change_log.md`
- `memory/project_sprint_plan.md`
- `memory/project_replay_lessons.md`

Validacion:

- `python -m compileall views\signals_view.py`: OK.
- Prueba boundary sin video: `replay_frame_cache_boundary_ok`.
- `python -m pytest -q`: `1 passed`.
- `python test_app_flow.py`: `28/28`.

Riesgo residual:

- No habia video `.mp4/.mov/.avi` disponible en el workspace para probar la
  extraccion real en esta pasada.
- Validar manualmente al subir un video y hacer clic dos veces en el mismo
  evento.

## 2026-05-22 - Observacion Ruta Legacy /analyze-pose

Contexto:

- Durante la pasada de rendimiento se busco si existian rutas que duplicaran
  analisis pesado fuera del flujo principal de `signals_view.py`.

Hallazgo:

- `app.py` conserva la ruta `POST /analyze-pose`, que ejecuta
  `pose_analyzer.analyze_video()` y devuelve el resultado completo como JSON.
- No se encontro uso directo desde `assets/video_upload.js`; el flujo actual de
  UI usa callbacks encadenados en `signals_view.py`.

Decision:

- No se modifico la ruta para no romper compatibilidad si algun cliente externo
  o script la usa.

Riesgo residual:

- Si alguien llama `/analyze-pose` con videos largos, puede devolver payloads
  grandes con frames/base64 y sentirse mas lento que el flujo optimizado de UI.
- Si se confirma que no se usa, conviene deprecarla o agregar modo `slim`/job
  server-side en un sprint posterior.

## 2026-05-23 - Bug Critico Botones Bloqueados Por Callback Duplicado

Contexto:

- El usuario reporto que no podia apretar ningun boton.
- La captura mostraba error Dash: `Duplicate callback outputs` para
  `q-gauge.figure`, `q-explain.children` y `q-trend.figure`.

Hallazgo:

- El problema venia de `pages/wellbeing.py`.
- Los callbacks globales de Dash para bienestar podian registrarse mas de una
  vez durante recarga/importacion, especialmente el callback de guardado del
  check-in (`btn-save-q`).
- No era buena solucion poner `allow_duplicate=True` a todo, porque podia
  ocultar el error y dejar doble guardado de check-ins.

Cambio:

- Se agrego `_callback_once()` en `pages/wellbeing.py` para evitar registro
  global duplicado de callbacks sensibles durante hot reload/importaciones.
- Se aplico a `load_q_trend` y `save_wellbeing`.
- Se limpio un `SyntaxWarning` en `app.py` reemplazando el regex JS con
  `u.split('/').pop()` y se quito whitespace final cercano.

Archivos implicados:

- `pages/wellbeing.py`
- `app.py`
- `memory/project_change_log.md`
- `memory/project_sprint_plan.md`
- `memory/project_combatiq_master.md`

Validacion:

- `python -m compileall app.py pages\wellbeing.py`: OK.
- Prueba reload: `wellbeing_callback_once_ok`.
- `/_dash-dependencies`: OK, solo 2 outputs relacionados con bienestar y sin
  duplicados.
- `python -m pytest -q`: `1 passed`.
- `python test_app_flow.py`: `28/28`.
- `python test_s105_load.py --skip-video`: OK.

Riesgo residual:

- El usuario debe recargar/reiniciar el servidor Dash para eliminar metadata
  vieja del navegador/proceso en ejecucion.
- Si aparece otro callback duplicado en una pagina distinta, aplicar el mismo
  criterio: no esconderlo con `allow_duplicate` si puede duplicar escrituras.

## 2026-05-23 - Refuerzo Callback Once Bienestar

Contexto:

- El usuario volvio a ver el mismo error `Duplicate callback outputs` con
  sufijo `@1bb7215b02c503fae26932545128b772`.
- Eso indica que el callback duplicado ya podia existir en `app.callback_map`
  y no solo en la lista global pendiente de Dash.

Cambio:

- `_callback_once()` ahora tambien usa un registro persistente en `builtins`
  para sobrevivir a `importlib.reload()` dentro del mismo proceso.
- Ademas revisa `dash.get_app().callback_map` y `_callback_list` si ya existe
  una app activa.
- Esto cubre recargas antes y despues de que Dash transfiera callbacks globales
  al mapa de la app.

Validacion:

- `python -m compileall pages\wellbeing.py app.py`: OK.
- Reload antes de setup: `wellbeing_callback_once_reload_before_setup_ok`.
- Reload despues de setup: `wellbeing_callback_once_reload_after_setup_ok`.
- Simulacion de proceso viejo sin sentinel: `wellbeing_callback_once_active_app_guard_ok`.
- `/_dash-dependencies`: OK, solo 2 dependencias de bienestar y sin duplicado.
- `python -m pytest -q`: `1 passed`.
- `python test_app_flow.py`: `28/28`.
- `python test_s105_load.py --skip-video`: OK.

Riesgo residual:

- Si el servidor ya estaba corriendo con callback duplicado registrado antes de
  este fix, hay que detenerlo por completo y arrancarlo de nuevo. Hot reload
  puede no eliminar el callback duplicado ya cargado en memoria.

## 2026-05-23 - Fix Definitivo Callback Bienestar Combinado

Contexto:

- El error persistio con el mismo hash:
  `q-gauge.figure@1bb...`, `q-explain.children@1bb...`,
  `q-trend.figure@1bb...`.
- La presencia del hash confirmo que `allow_duplicate=True` en `q-trend` era
  parte del problema visible para Dash.

Cambio:

- Se elimino el callback separado `load_q_trend`.
- Se elimino `allow_duplicate=True` de `q-trend`.
- `save_wellbeing()` ahora es el unico callback que escribe:
  `q-gauge.figure`, `q-explain.children` y `q-trend.figure`.
- El mismo callback distingue el trigger:
  - Si cambia `q-user`, solo actualiza tendencia.
  - Si se pulsa `btn-save-q`, guarda el check-in y actualiza gauge,
    explicacion y tendencia.
- Se agrego `_wellbeing_result_callback()` para purgar callbacks viejos de esos
  outputs y registrar el actual incluso si Dash ya hizo setup.

Validacion:

- `python -m compileall pages\wellbeing.py app.py`: OK.
- Reload global: `wellbeing_combined_global_reload_ok`.
- Reload con app activa: `wellbeing_combined_app_reload_ok`.
- `/_dash-dependencies`: OK, 1 sola dependencia:
  `..q-gauge.figure...q-explain.children...q-trend.figure..`, sin `@hash`.
- `python -m pytest -q`: `1 passed`.
- `python test_app_flow.py`: `28/28`.
- `python test_s105_load.py --skip-video`: OK.

Riesgo residual:

- Si el navegador o servidor conserva metadata vieja, hay que cerrar por
  completo el proceso Dash y hacer recarga dura del navegador.

## 2026-05-23 - Causa Raiz Real Callback Duplicado En Navegador

Contexto:

- El codigo ya estaba corregido y `/_dash-dependencies` desde test client no
  mostraba hashes `@...`, pero el navegador seguia mostrando:
  `q-gauge.figure@1bb...`, `q-explain.children@1bb...`,
  `q-trend.figure@1bb...`.
- Esto indicaba que el problema visible ya no venia del codigo actual, sino de
  un servidor Dash viejo o metadata cacheada.

Hallazgo:

- Habia procesos Python antiguos escuchando en `127.0.0.1:8051`.
- El servidor real del puerto `8051` seguia entregando dependencias antiguas:
  `HasOldHash=True`, `HasCombined=False`.

Accion:

- Se detuvieron los procesos Dash antiguos del puerto `8051`.
- Se arranco la app limpia desde `.\.venv\Scripts\python.exe app.py`.
- El servidor vivo quedo con un solo callback combinado para bienestar:
  `..q-gauge.figure...q-explain.children...q-trend.figure..`.

Validacion:

- `Invoke-WebRequest http://127.0.0.1:8051/_dash-dependencies`: OK.
- `HasOldHash=False`.
- `HasCombined=True`.
- `HasGauge=True`.
- `HasTrend=True`.
- Auditoria estricta de dependencias vivas: `risky_unhashed_duplicate_bases=0`.
- Hay duplicados intencionales con hash/`allow_duplicate=True` en Replay, IMU,
  Combat Monitor, equipo y tema; no son equivalentes al fallo de Bienestar.

Nota operativa:

- Si vuelve a aparecer este error con un hash `@...`, primero revisar procesos
  vivos en `8051` y hacer recarga dura del navegador antes de tocar codigo.

## 2026-05-24 - Auditoria De Congelamientos Replay/IA/Diagnostico

Contexto:

- El usuario reporto muchos congelamientos y pidio una auditoria exhaustiva.
- Se revisaron focos de carga en Dash: callbacks pesados, `dcc.Store`,
  polling, IA, Replay, ECG/IMU, diagnosticos temporales y servidor vivo.

Hallazgos reales:

- `app.py` conservaba diagnostico temporal de frontend:
  - Panel fijo `__diag`.
  - Interceptaba `console.error`.
  - Interceptaba `window.fetch`.
  - Caminaba internamente el arbol React/fiber para leer el store de Dash.
  - Ademas habia `__test-interval`, `__dash-test-btn` y `__dash-test-out`.
- Ese diagnostico ya no era util para producto/demo y podia contaminar UI,
  sumar callbacks y hacer que la app se sintiera pesada.
- En Replay, `detect_video_events()` se disparaba automaticamente al cambiar
  video o sesion. Con API activa podia abrir video, extraer frames y llamar a
  Claude sin que el usuario lo pidiera.
- En Replay, `analyze_combat_session()` tambien se disparaba automaticamente
  al seleccionar sesion. Usa Opus/tool-use y puede tardar bastante.
- `ai_insights.analyze_event_frame()` intentaba guardar en cache con
  `cache_key`, pero esa variable no existia dentro de la funcion.
- `ai_insights.generate_athlete_note()` tenia un bloque de cache de
  `event_frame` pegado por error, usando variables inexistentes como
  `frame_b64`, `audience`, `athlete_ref`, `viewer_ref`, `ev_type`, `ts` y `rn`.
  Con API activa podia romper la IA antes del `try`.

Cambios aplicados:

- Se elimino el diagnostico temporal completo de `app.py`.
- Se eliminaron `__test-interval`, `__dash-test-btn`, `__dash-test-out` y su
  callback.
- Replay ahora tiene boton explicito `Analizar IA video`.
- La deteccion visual de eventos ya no corre automaticamente por cambiar
  video/sesion; solo corre al pulsar el boton.
- Replay ahora tiene boton explicito `Generar lectura IA`.
- La lectura IA de combate ya no corre automaticamente al seleccionar sesion;
  muestra un mensaje ligero hasta que el usuario la pida.
- `analyze_event_frame()` ahora crea `cache_key` antes de llamar a Claude y
  reutiliza cache si existe.
- Se elimino el bloque erroneo de cache en `generate_athlete_note()`.

Validacion:

- `python -m compileall app.py ai_insights.py views\signals_view.py`: OK.
- Prueba con cliente IA falso:
  `generate_athlete_note()` -> `nota ok`.
- Prueba con cliente IA falso:
  `analyze_event_frame()` -> `nota ok`.
- `python -m pytest -q`: `1 passed`.
- `python test_app_flow.py`: `28/28`.
- `python test_s105_load.py --skip-video`: OK.
- `git diff --check -- app.py ai_insights.py views\signals_view.py`: sin errores
  reales; solo avisos CRLF.
- `/_dash-dependencies` con app importada: 147 dependencias,
  `risky_unhashed_duplicate_bases=0`.

Servidor vivo:

- El servidor viejo en `8051` aun servia `__diag` y callback de test.
- Se detuvieron procesos viejos y se arranco app limpia.
- Verificacion final en `http://127.0.0.1:8051/`:
  - `HasDiag=False`.
  - `HasTestCallback=False`.
  - `HasAiButtonCallbacks=True`.
  - `risky_unhashed_duplicate_bases=0`.

Regla nueva:

- IA pesada de Replay no debe dispararse automaticamente por seleccionar
  sesion/video. Debe ejecutarse por accion explicita del usuario, salvo que se
  implemente un backend de jobs/background con progreso real.
- No dejar diagnosticos visuales o interceptores globales (`fetch`,
  `console.error`, React fiber) en la app de demo/inversores.

## 2026-05-24 - Fix Logout Loading Infinito

Contexto:

- El usuario reporto que al tratar de salir de sesion la app se quedaba
  congelada en estado de carga.
- Se audito primero el flujo de logout global de la barra lateral.

Causa raiz:

- El enlace de "Salir" usaba navegacion interna de Dash (`dcc.Link`) hacia
  `/logout`.
- `pages/logout.py` limpiaba la sesion Flask y despues intentaba montar un
  `dcc.Location` anidado para redirigir a `/login`.
- Ese flujo podia quedar atrapado en el router de Dash o convivir con estado
  viejo del navegador (`auth-store`), dejando la pantalla en loading.

Cambios aplicados:

- `/logout` ahora es una ruta Flask real en `app.py`.
- La ruta Flask hace `session.clear()` y responde con redirect HTTP a `/login`.
- El enlace "Salir" ahora se renderiza como `html.A`, no como `dcc.Link`, para
  forzar una navegacion HTTP limpia.
- `pages/logout.py` queda solo como fallback defensivo: limpia sesion si hace
  falta y muestra un enlace manual para volver a iniciar sesion.
- Se elimino el uso de `dcc.Location(pathname="/login", id="redirect-logout")`
  en el fallback de logout.

Validacion:

- `python -m compileall app.py pages\logout.py`: OK.
- Flask test client: `GET /logout` devuelve `302` con `Location: /login`.
- Flask test client: despues de `/logout`, `session_user=None`.
- Servidor vivo `8051`: `GET /logout` devuelve `302` a `/login`.
- `rg`: no queda `redirect-logout`.
- `pytest -q`: `1 passed`.
- `test_app_flow.py`: `28/28`.
- `test_s105_load.py --skip-video`: OK.

Regla nueva:

- Logout/autenticacion debe resolverse del lado servidor con redirect HTTP, no
  con `dcc.Location` anidado dentro de una pagina Dash.
- Si hay loading infinito al salir, verificar primero `/logout` por HTTP,
  cookies/sesion y procesos Dash vivos antes de tocar biomecanica o IA.

## 2026-05-24 - IA Bajo Demanda En Sesion/Ficha Para Reducir Congelamientos

Contexto:

- Despues del fix de logout, se continuo la auditoria de congelamientos sin
  recortar potencia de biomecanica, senales ni IA.
- Se revisaron callbacks pesados de `app.py` y `views/signals_view.py`.

Hallazgos:

- Replay IA ya estaba corregido para ejecutarse por boton explicito.
- El boton interno "Cerrar sesion" de entrenamiento en `signals_view.py` no
  hace procesamiento pesado: cierra en DB, refresca opciones y limpia la
  seleccion.
- Persistian tres llamadas IA automaticas en `app.py`:
  - Ficha de atleta coach: `generate_coaching_note()` se disparaba al cambiar
    `athlete-select-v2`.
  - Vista `/sesion` atleta: `generate_athlete_note()` se disparaba al entrar a
    la pagina.
  - Vista `/sesion` coach: `generate_team_summary()` se disparaba al entrar a
    la pagina.
- Con `ANTHROPIC_API_KEY` activa, esas llamadas podian dejar la UI en loading
  durante navegacion o cambios de seleccion aunque el usuario no hubiera pedido
  analisis IA.

Cambios aplicados:

- Ficha de atleta: se agrego boton `btn-athlete-card-ai-note` con texto
  "Generar analisis IA".
- Ficha de atleta: `load_athlete_card_ai_note()` ya no llama a Claude al
  cambiar de atleta; solo muestra placeholder hasta que se pulse el boton.
- Vista `/sesion` atleta: se agrego boton `btn-sesion-ai-note`.
- Vista `/sesion` atleta: `load_sesion_ai_note()` ya no llama a Claude al
  navegar a la pagina; espera accion explicita.
- Vista `/sesion` coach: se agrego boton `btn-sesion-team-ai-note`.
- Vista `/sesion` coach: `load_sesion_team_ai_note()` ya no llama a Claude al
  navegar a la pagina; espera accion explicita.
- Las tres tarjetas ahora muestran placeholders claros para que no parezca que
  la app esta congelada o esperando una respuesta invisible.

Validacion:

- `python -m compileall app.py views\signals_view.py`: OK.
- `python -m pytest -q`: `1 passed`.
- `/_dash-dependencies`: 147 dependencias,
  `risky_unhashed_duplicate_bases=0`.
- `python test_app_flow.py`: `28/28`.
- `python test_s105_load.py --skip-video`: OK.
- `git diff --check -- app.py`: sin errores reales; solo aviso CRLF.
- Servidor `8051` reiniciado limpio despues del cambio.
- Servidor vivo: `/_dash-dependencies` devuelve 147 dependencias,
  `HasManualAiButtons=True`.
- Servidor vivo: `/logout` devuelve `302` con `Location: /login`.

Regla nueva:

- Cualquier IA que pueda tardar mas de unos segundos debe ser accion explicita
  del usuario o ejecutarse en jobs/background con progreso. No dispararla solo
  por navegar, cambiar dropdown o montar una pagina.

## 2026-05-24 - Hardening Ruta Legacy `/analyze-pose`

Contexto:

- Se continuo auditoria de congelamientos revisando rutas legacy y payloads de
  video/pose.
- El flujo moderno de Biomecanica ya usa callbacks encadenados con `job_id` y
  cache temporal para no mandar frames/base64 completos al navegador.

Hallazgos:

- `/upload-video` ya sube videos por `fetch` multipart fuera de Dash, evitando
  mandar videos grandes por `dcc.Upload`.
- `/analyze-pose` no parece ser usado por la UI moderna, pero seguia expuesto
  como endpoint legacy autenticado.
- La ruta aceptaba `sample_every` y `max_frames` desde JSON/env sin limite
  superior efectivo. Una llamada accidental con `max_frames` enorme podia
  ocupar el proceso con MediaPipe durante demasiado tiempo.
- `data/uploads` contiene multiples videos duplicados de pruebas. No se
  eliminaron porque requieren permiso explicito del usuario, pero quedan como
  candidato de limpieza controlada.

Cambios aplicados:

- Se agrego `_LEGACY_POSE_ROUTE_MAX_FRAMES = 1500` en `app.py`.
- `/analyze-pose` ahora limita `sample_every` a rango `1..60`.
- `/analyze-pose` ahora limita `max_frames` a
  `COMBATIQ_LEGACY_POSE_ROUTE_MAX_FRAMES` o `1500` por defecto.
- No se toco el flujo principal de Biomecanica en `views/signals_view.py`.

Validacion:

- `python -m compileall app.py views\signals_view.py`: OK.
- `python -m pytest -q`: `1 passed`.
- `python test_app_flow.py`: `28/28`.
- `python test_s105_load.py --skip-video`: OK.
- `/_dash-dependencies`: 147 dependencias,
  `risky_unhashed_duplicate_bases=0`.
- Prueba directa `/analyze-pose`:
  - Sin login -> `401`.
  - Login + archivo inexistente -> `404`.
  - Con `pose_analyzer` simulado y `max_frames=999999` -> respuesta usa
    `max_frames=1500` y `sample_every=10`.
- `git diff --check -- app.py ...`: sin errores reales; solo aviso CRLF.
- Tras una interrupcion del arranque en primer plano, se verifico que el
  servidor quedo vivo en `8051`.
- Servidor vivo: `/_dash-dependencies` -> 147 dependencias,
  `HasManualAiButtons=True`; `/logout` -> `302 /login`.
- Import local confirma `_LEGACY_POSE_ROUTE_MAX_FRAMES=1500`.

Regla nueva:

- Endpoints legacy de analisis pesado deben tener limites defensivos aunque no
  los use la UI principal.
- La limpieza de `data/uploads` debe hacerse solo con permiso explicito y con
  listado previo de archivos/tamanos.

## 2026-05-24 - Inventario De Uploads Duplicados

Contexto:

- Se inicio limpieza controlada de uploads duplicados como siguiente bloque de
  performance/orden.
- No se borro ni movio ningun archivo en esta pasada.

Hallazgos:

- Carpetas revisadas:
  - `data/uploads`
  - `data/uploads_legacy`
  - `assets/uploads`
- Total actual: 56 archivos, ~4029.74 MB.
- Duplicados reales por SHA-256: 3 grupos.
- Espacio ocupado por copias repetidas: ~3816.16 MB.
- La DB `data/users.db` no referencia directamente esos videos duplicados. Solo
  aparecieron referencias a:
  - `combat_12_wt_videoplayback.csv`
  - `combat_12_wt_videoplayback_imu`
- `data/upload_aliases.json` referencia principalmente `20260503_005430.mp4`,
  que existe como legacy/canonico historico.

Grupos detectados:

- `20230325_213445*.mp4`: 24 archivos identicos, ~130.76 MB cada uno.
- `videoplayback*.mp4`: 30 archivos identicos, ~25.93 MB cada uno.
- `20260503_005430_07048cf5.mp4` y `data/uploads_legacy/20260503_005430.mp4`:
  2 archivos identicos, ~56.90 MB cada uno.

Propuesta segura:

- Mantener como canónicos:
  - `data/uploads/20230325_213445.mp4`
  - `data/uploads/videoplayback.mp4`
  - `data/uploads/20260503_005430_07048cf5.mp4`
- Mover 53 duplicados a una carpeta de cuarentena, por ejemplo:
  `data/uploads_quarantine_20260524/`.
- No eliminar definitivamente hasta validar que la app, Replay y pruebas siguen
  funcionando.

Regla:

- Para esta limpieza, preferir cuarentena/move antes que delete. Solo borrar
  despues de validacion y permiso explicito.

## 2026-05-24 - Uploads Duplicados Movidos A Cuarentena

Contexto:

- El usuario autorizo mover duplicados a cuarentena.
- No se elimino definitivamente ningun archivo.

Cambios aplicados:

- Se movieron 53 archivos duplicados a:
  `data/uploads_quarantine_20260524/`.
- Espacio movido a cuarentena: ~3816.16 MB.
- Se conservaron en `data/uploads`:
  - `20230325_213445.mp4`
  - `videoplayback.mp4`
  - `20260503_005430_07048cf5.mp4`
- `data/uploads` quedo con 3 archivos, ~213.58 MB.
- `data/uploads_legacy` y `assets/uploads` quedaron sin archivos de video
  duplicados.
- Se genero manifiesto:
  `data/upload_quarantine_20260524.json`.
- Se actualizaron aliases para compatibilidad:
  - 16 aliases existentes de `20260503_005430.mp4` pasaron a
    `20260503_005430_07048cf5.mp4`.
  - 53 aliases post-move permiten que nombres antiguos de duplicados sigan
    resolviendo al canonico correspondiente.
- `check_videos.py` ahora usa `_resolve_uploaded_video()` para reflejar el
  comportamiento real de la app y no fallar por rutas fisicas movidas.

Validacion:

- `_resolve_uploaded_video()` resuelve correctamente:
  - `videoplayback_871497d6.mp4` -> `data/uploads/videoplayback.mp4`
  - `20230325_213445_13607fef.mp4` -> `data/uploads/20230325_213445.mp4`
  - `20260503_005430.mp4` -> `data/uploads/20260503_005430_07048cf5.mp4`
- `python -m compileall app.py check_videos.py`: OK.
- `python check_videos.py`: OK, todos los nombres esperados abren por alias.
- `python test_app_flow.py`: `28/28`.
- `python -m pytest -q`: `1 passed`.
- `python test_s105_load.py --skip-video`: OK.
- `/_dash-dependencies`: 147 dependencias,
  `risky_unhashed_duplicate_bases=0`.
- `git diff --check`: sin errores reales; solo aviso CRLF.

Regla:

- La cuarentena no debe borrarse hasta validar manualmente Replay/Biomecanica
  con `videoplayback.mp4` y confirmar que no se necesita recuperar ningun
  archivo antiguo.

## 2026-05-24 - Fix Demo Atleta En Loading

Contexto:

- El usuario reporto que al intentar entrar como demo atleta la app quedaba en
  pantalla `Loading...`.
- Se priorizo como bug bloqueante de demo.

Hallazgos:

- El servidor entregaba `/`, `/login`, `/_dash-layout` y
  `/_dash-dependencies` correctamente.
- El callback Dash de `btn-demo-login` funcionaba por servidor: creaba sesion
  demo y devolvia `dcc.Location("/dashboard")`.
- Aun asi, depender de un callback para entrar a demo era fragil si el cliente
  quedaba atrapado en hidratacion o si habia procesos viejos sirviendo codigo.
- Se encontraron dos procesos escuchando en `8051` antes del reinicio limpio,
  lo que podia producir comportamiento fantasma.

Cambios aplicados:

- Se agregaron rutas Flask server-side:
  - `/demo/atleta`
  - `/demo/coach-tkd`
  - `/demo/coach-boxeo`
- Las rutas crean la sesion demo y redirigen por HTTP a `/dashboard`.
- En `pages/auth_login.py`, los accesos demo ahora son `html.A` con `href`
  real a esas rutas, no botones dependientes de callback.
- `assets/30_auth.css` ajusta `.auth-demo__pill` para que los enlaces se vean
  como los botones anteriores (`display: block`, sin subrayado).
- Se detuvieron los procesos viejos de `8051` y se levanto un unico servidor
  limpio.

Validacion:

- `python -m compileall app.py pages\auth_login.py`: OK.
- Flask test client:
  - `/demo/atleta` -> `302 /dashboard`, sesion `deportista`, `Demo Atleta`,
    `Taekwondo`.
  - `/demo/coach-tkd` -> `302 /dashboard`.
  - `/demo/coach-boxeo` -> `302 /dashboard`.
- Servidor vivo `8051`:
  - Un solo listener activo.
  - `/demo/atleta` -> `302 /dashboard`.
  - `/demo/coach-tkd` -> `302 /dashboard`.
  - `/demo/coach-boxeo` -> `302 /dashboard`.
  - `/logout` -> `302 /login`.
  - `/_dash-dependencies` -> 147 dependencias.
- Layout vivo confirma `btn-demo-login` como componente `A` con
  `href="/demo/atleta"`.
- `pytest -q`: OK.
- `test_app_flow.py`: `28/28`.
- `test_s105_load.py --skip-video`: OK.
- `git diff --check`: sin errores reales; solo aviso CRLF.

Regla:

- Accesos criticos de demo/autenticacion deben tener rutas HTTP server-side y
  no depender exclusivamente de callbacks Dash.

## 2026-05-24 - Fix Carga Doble Tras Entrar A Demo

Contexto:

- El usuario reporto que la app "carga doble" despues del fix de demo.
- Se investigo el arranque autenticado de Dash y los residuos del flujo demo
  anterior.

Hallazgo:

- `_initial_path_and_content()` intentaba preservar `request.path` para
  usuarios autenticados.
- En Dash, el layout suele pedirse desde `/_dash-layout`, no desde la URL real
  del navegador. Usar `request.path` ahi puede inyectar una ruta falsa en
  `dcc.Location` y provocar una segunda navegacion/hidratacion.
- Los accesos demo ya estaban convertidos a `html.A`, pero quedaban
  contenedores `demo-redirect` obsoletos en el layout de login.

Cambios aplicados:

- `app.py`: para usuarios autenticados, `_initial_path_and_content()` devuelve
  `(None, None)` y deja que `dcc.Location(id="url")` lea `window.location`.
- `pages/auth_login.py`: se eliminaron `demo-redirect`,
  `demo-coach-redirect` y `demo-coach-boxeo-redirect` porque ya no hay
  callbacks demo legacy.
- Se reinicio el servidor en `8051` para evitar seguir usando el proceso viejo
  con `use_reloader=False`.

Validacion:

- `python -m compileall app.py pages\auth_login.py`: OK.
- `pytest -q`: `1 passed`.
- `python test_app_flow.py`: `28/28`.
- `python test_s105_load.py --skip-video`: OK.
- Flask/live HTTP:
  - `/demo/atleta` -> `302 /dashboard`.
  - `/demo/coach-tkd` -> `302 /dashboard`.
  - `/demo/coach-boxeo` -> `302 /dashboard`.
  - `/logout` -> `302 /login`.
- Con cookie demo activa, `/_dash-layout` devuelve `url_props {'id': 'url'}`
  sin `pathname` forzado.
- `/_dash-dependencies`: 144 dependencias, `exact_duplicate_outputs=0`.
- `q-trend.figure` queda registrado una sola vez en el grupo
  `q-gauge/q-explain/q-trend`.

Regla:

- En layouts Dash autenticados, no inferir la ruta del navegador desde
  `request.path` si la peticion puede venir de `/_dash-layout`; preferir que
  `dcc.Location` lea la URL real del cliente.

## 2026-05-24 - Fix Limpieza Agresiva De Storage En Arranque

Contexto:

- Tras corregir la doble navegacion, se continuo la auditoria de congelamientos,
  "Loading..." y problemas de modo claro.
- Se revisaron callbacks iniciales, outputs duplicados, assets JS y el
  `index_string` global.

Hallazgo:

- `app.index_string` ejecutaba en cada carga:
  - `sessionStorage.clear()`.
  - limpieza de claves de `localStorage` que incluian `theme-store`,
    `ui-sidebar` y `auth-store`.
  - unregister de service workers y borrado de caches en cada carga.
- Ese script corre despues de los scripts de Dash/renderer, por lo que podia
  interferir con hidratacion, persistencia de tema claro y sensacion de carga
  doble.

Cambios aplicados:

- `app.py`: se reemplazo la limpieza agresiva por una limpieza suave versionada
  de service worker/cache.
- Nuevo flag local: `combatiq-sw-cache-cleanup-v2`.
- Ya no se ejecuta `sessionStorage.clear()`.
- Ya no se elimina `theme-store`, por lo que el modo claro puede persistir.
- Si la limpieza ya se ejecuto en ese navegador/version, no se repite en cada
  carga.

Validacion:

- `python -m compileall app.py`: OK.
- HTML servido por `127.0.0.1:8051`:
  - `sessionStorage.clear` ausente.
  - no hay `removeItem` sobre `theme-store`.
  - `combatiq-sw-cache-cleanup-v2` presente.
- `/demo/atleta` -> `302 /dashboard`.
- `/logout` -> `302 /login`.
- `/_dash-dependencies`: 144 dependencias, `exact_duplicate_outputs=0`.
- `pytest -q`: `1 passed`.
- `python test_app_flow.py`: `28/28`.
- `python test_s105_load.py --skip-video`: OK.
- Se detuvieron procesos duplicados en `8051` y quedo un unico listener activo.

Regla:

- No limpiar `sessionStorage` ni `localStorage` globalmente en cada carga.
  Cualquier limpieza de caches/service workers debe ser versionada, idempotente
  y no tocar preferencias de usuario como `theme-store`.

## 2026-05-24 - SignalsView Callback Initial Load Reducido

Contexto:

- Se continuo la auditoria de congelamientos enfocada en callbacks que se
  disparan al montar la app.
- Antes del cambio habia 38 callbacks con `prevent_initial_call=False`.

Hallazgo:

- En `views/signals_view.py` varios callbacks renderizaban estados vacios al
  cargar, aunque el layout ya tenia placeholders:
  - lista de eventos vacia,
  - info de sesion sin seleccion,
  - panel IA sin sesion,
  - fila de renombrar oculta,
  - graficas vacias de ECG/IMU replay,
  - graficas vacias simuladas,
  - gating de sensores antes de tener `ecg-user`,
  - tabs/formato IMU antes de tener atleta,
  - selector de sesiones antes de tener atleta.

Cambios aplicados:

- `views/signals_view.py`: se paso a `prevent_initial_call=True` en esos
  callbacks no esenciales de arranque.
- Se agrego contenido inicial estatico en `replay-ai-panel` para conservar el
  mensaje visible sin disparar callback.
- No se tocaron los callbacks iniciales necesarios para cargar sesiones Replay,
  KPIs, selector de atleta ni ECG real.
- No se cambio logica de biomecanica, IA, sensores, exports ni DB.

Validacion:

- `python -m compileall views\signals_view.py`: OK.
- `/_dash-dependencies`: 144 dependencias.
- Callbacks iniciales bajaron de 38 a 29.
- `exact_duplicate_outputs=0`.
- `python -m pytest -q`: `1 passed`.
- `python test_app_flow.py`: `28/28`.
- `python test_s105_load.py --skip-video`: OK.
- Rutas demo/logout siguen correctas.
- Servidor `8051` reiniciado y verificado live.

Regla:

- Si un componente ya nace con placeholder y el callback solo repinta estado
  vacio, usar `prevent_initial_call=True`; preservar carga inicial solo cuando
  aporte datos reales o desbloquee flujo principal.

## 2026-05-24 - Callback Initial Load Segunda Pasada

Contexto:

- Tras reducir SignalsView, quedaban 29 callbacks iniciales.
- Se revisaron modulos restantes: registro, peso, competencia, AnalysisView,
  chat, sensores, wellbeing, comparacion y router.

Hallazgo:

- Muchos callbacks restantes si cargan datos reales y deben seguir iniciales:
  chat, compare, wellbeing, peso/nutricion, sensores, sidebar/router/tema.
- Se detectaron 5 callbacks seguros para diferir porque el layout ya tenia el
  estado inicial correcto o el Store inicia en `None`:
  - fuerza de password en registro,
  - campo "otro deporte" en registro,
  - store de fecha de competencia/peso,
  - progreso de checklist de competencia,
  - nota IA legacy de AnalysisView (`ai-report-store=None`).

Cambios aplicados:

- `pages/auth_register.py`: password strength y custom sport usan
  `prevent_initial_call=True`.
- `app.py`: `peso-comp-date-store` usa `prevent_initial_call=True`.
- `app.py`: `comp-checklist-progress` ahora trae texto inicial estatico y su
  callback usa `prevent_initial_call=True`.
- `views/analysis_view.py`: `_compat_ai_note` usa
  `prevent_initial_call=True`.

Validacion:

- `python -m compileall app.py pages\auth_register.py views\analysis_view.py`:
  OK.
- Callbacks iniciales bajaron de 29 a 24.
- `/_dash-dependencies`: 144 dependencias, `exact_duplicate_outputs=0`.
- Rutas demo/logout correctas.
- `python -m pytest -q`: `1 passed`.
- `python test_app_flow.py`: `28/28`.
- `python test_s105_load.py --skip-video`: OK.
- Servidor `8051` reiniciado y verificado live.

Regla:

- No diferir callbacks que cargan datos reales de una pagina activa. Diferir
  solo callbacks cuyo estado inicial ya exista en el layout o cuyo input
  arranque explicitamente vacio.

## 2026-05-24 - Auditoria De Callbacks Por Peso Real + Indices DB 200

Contexto:

- Se dejo de perseguir la cantidad de callbacks iniciales como metrica principal.
- La revision paso a medir costo real: queries de DB, construccion de figuras,
  tamano de payload y callbacks que se ejecutan al entrar a paginas como
  bienestar, comparar, sensores y chat.
- Objetivo: reducir congelamientos sin quitar potencia de analisis, sensores,
  biomecanica, IA ni exports.

Hallazgos:

- En datos demo, la DB no fue el cuello principal: las queries criticas suelen
  estar entre 2 ms y 10 ms.
- El peso real viene sobre todo de construir/serializar figuras Plotly y arboles
  HTML grandes.
- Callbacks mas pesados medidos:
  - `wellbeing history render`: ~89 ms, payload ~24.0 KB.
  - `compare session charts`: ~87 ms, payload ~18.6 KB.
  - `peso view`: ~61 ms, payload ~19.1 KB.
  - `nutri view`: ~53 ms, payload ~20.6 KB.
  - `wellbeing trend only`: ~37 ms, payload ~9.0 KB.
- Callbacks de chat, comparacion, bienestar, sensores y KPIs cargan datos reales;
  no conviene diferirlos solo para bajar el numero global.

Cambios aplicados:

- `db.py`: nueva migracion versionada `200`.
- Se agregaron indices seguros con `CREATE INDEX IF NOT EXISTS` para lecturas de
  dashboards, historicos, senales, chat, peso y nutricion:
  - `idx_questionnaires_user_id_desc`
  - `idx_questionnaires_session`
  - `idx_ecg_files_session_id_desc`
  - `idx_ecg_metrics_file_id_desc`
  - `idx_imu_metrics_session_ts`
  - `idx_imu_metrics_user_ts`
  - `idx_messages_pair_ts`
  - `idx_messages_receiver_sender_read`
  - `idx_weights_user_date_desc`
  - `idx_nutrition_user_date_desc`
- No se cambiaron pantallas, filtros, UX, analisis deportivo ni volumen de datos
  visible al usuario.

Validacion:

- `python -m compileall db.py`: OK.
- `db.init_db()` aplicado; `schema_version=200`.
- `PRAGMA index_list` confirmo indices nuevos en tablas objetivo.
- `EXPLAIN QUERY PLAN` confirmo uso de indices en lecturas de ECG/IMU clave.
- `git diff --check -- db.py`: OK, solo warning CRLF.
- `pytest -q`: `1 passed`.
- `test_app_flow.py`: `28/28`.
- `test_s105_load.py --skip-video`: OK.
- `/_dash-dependencies`: 144 dependencias, 0 outputs duplicados exactos,
  24 callbacks iniciales.
- Rutas live verificadas: `/demo/atleta`, `/demo/coach-tkd`,
  `/demo/coach-boxeo`, `/logout`, `/_dash-dependencies`.
- Servidor `8051` reiniciado con un unico listener activo.

Regla:

- Optimizar por peso real, no por cantidad de callbacks.
- Antes de limitar historiales o exports, confirmar decision de producto: la app
  debe seguir siendo potente y completa para coach, atleta e inversor.
- Si crecen mucho los datos, el siguiente frente debe ser render/serializacion
  de figuras o lazy render por pestana/ruta, no recortar informacion a ciegas.

## 2026-05-24 - Compare Lazy Render Del Detalle Tecnico

Contexto:

- `session_compare_all` era uno de los callbacks con mas peso real al entrar a
  Comparar.
- El bloque "Ver detalle tecnico de sensores" esta colapsado por defecto para
  coaches, pero el callback calculaba ECG/IMU y figuras aunque el usuario no lo
  hubiera abierto.
- Para deportistas, esos outputs viven ocultos por compatibilidad, asi que
  tampoco conviene calcularlos de inicio.

Cambios aplicados:

- `views/compare_view.py`: se agrego `id="cmp-detail-toggle"` al `html.Details`
  del detalle tecnico.
- `session_compare_all` ahora escucha `cmp-detail-toggle.open`.
- Si el rol no es `coach` o el detalle esta cerrado, devuelve placeholders
  ligeros sin hacer queries de sensores ni construir figuras.
- Cuando el coach abre el detalle, se mantiene el calculo completo de ECG/IMU,
  badges, resumen y recomendaciones.
- Se eliminaron referencias compartidas a un mismo `go.Figure()` vacio dentro
  del callback; los estados vacios ahora usan `placeholder_figure(380)` o una
  figura nueva cuando hace falta mensaje estilizado.

Medicion:

- Detalle cerrado: ~0.85 ms, payload ~0.65 KB.
- Detalle abierto con datos reales de Carlos Rios: ~224 ms, payload ~18.6 KB.
- Resultado: se elimina el costo pesado de entrada sin quitar potencia cuando el
  coach pide el detalle.

Validacion:

- `python -m compileall views\compare_view.py`: OK.
- `git diff --check -- views\compare_view.py`: OK, solo warning CRLF.
- `pytest -q`: `1 passed`.
- `test_app_flow.py`: `28/28`.
- `test_s105_load.py --skip-video`: OK.
- `/_dash-dependencies`: 144 dependencias, 0 duplicados exactos,
  24 callbacks iniciales, input `cmp-detail-toggle.open` presente.
- Rutas live: `/demo/atleta`, `/demo/coach-tkd`, `/logout` correctas.
- Servidor `8051` reiniciado con un solo listener real.

Regla:

- Los bloques colapsados por defecto no deben calcular graficas pesadas hasta
  que el usuario los abra.
- Lazy render si; recorte de datos no.

## 2026-05-25 - Wellbeing History Split Por Peso Real

Contexto:

- `wellbeing history render` era el otro callback pesado tras Comparar.
- Antes devolvia en una sola respuesta: KPIs de resumen, tabla reciente y dos
  figuras Plotly.
- Eso hacia que la lectura rapida tuviera que esperar a la construccion de
  graficas, aunque resumen/tabla fueran mucho mas ligeros.

Cambios aplicados:

- `pages/wellbeing.py`: se agrego `dcc.Store(id="h-history-data")`.
- El antiguo render monolitico se separo en tres pasos:
  - `load_history_data`: valida permisos, lee DB y prepara datos primitivos.
  - `render_history_summary`: pinta KPIs y tabla reciente desde el store.
  - `render_history_charts`: construye `h-wellness` y `h-load` desde el store.
- El layout conserva placeholders iniciales para que no aparezcan huecos vacios.
- No se limitaron historiales ni exportes. El Excel sigue leyendo DB completa
  con su filtro de periodo.
- Se mantuvo UI en espanol correcto con acentos en textos visibles.

Medicion:

- Antes: `wellbeing history render` ~89 ms / ~24 KB en una sola respuesta.
- Ahora:
  - `load_history_data`: ~7.3 ms / ~4.0 KB.
  - `render_history_summary`: ~2.7 ms / ~5.5 KB.
  - `render_history_charts`: ~72.7 ms / ~18.6 KB.
- Resultado: la informacion accionable aparece separada de las graficas pesadas;
  la carga total conserva potencia y datos completos.

Validacion:

- `python -m compileall pages\wellbeing.py`: OK.
- `git diff --check -- pages\wellbeing.py`: OK, solo warning CRLF.
- `pytest -q`: `1 passed`.
- `test_app_flow.py`: `28/28`.
- `test_s105_load.py --skip-video`: OK.
- `/_dash-dependencies`: 146 dependencias, 0 duplicados exactos,
  24 callbacks iniciales.
- Rutas live: `/demo/atleta`, `/demo/coach-tkd`, `/logout` correctas.
- Servidor `8051` reiniciado con un solo listener real.

Regla:

- Si una respuesta mezcla datos ligeros accionables con figuras pesadas,
  separar en Store + callbacks especializados.
- No recortar datos de historiales/exportes sin decision de producto.

## 2026-05-25 - Peso Y Nutricion Split Por Peso Real

Contexto:

- Tras `compare` y `wellbeing`, los siguientes callbacks pesados eran
  `peso view` y `nutri view`.
- Ambos mezclaban en una sola respuesta: datos de tabla, KPIs, estados visuales,
  insight/alertas y figura Plotly.
- Objetivo: mejorar percepcion de carga sin recortar historial, exportes ni
  calculos deportivos/nutricionales.

Cambios aplicados:

- `app.py`: `view_peso()` agrega `dcc.Store(id="peso-data-store")`.
- `update_peso_view` se separo en:
  - `load_peso_data`: lee DB y prepara filas primitivas.
  - `render_peso_summary`: tabla, KPIs, alertas y tabla reciente.
  - `render_peso_graph`: solo construye la grafica Plotly.
- `app.py`: `view_nutricion()` agrega `dcc.Store(id="nutri-data-store")`.
- `update_nutri_view` se separo en:
  - `load_nutri_data`: lee DB y prepara filas primitivas.
  - `render_nutri_summary`: tabla, KPIs, insight bienestar-nutricion y tabla
    reciente.
  - `render_nutri_graph`: solo construye la grafica Plotly.
- No se cambiaron guardados, validaciones de formulario, exportes Excel/CSV ni
  reglas de calculo.

Medicion con Carlos Rios:

- Peso:
  - datos: ~4.5 ms / ~2.7 KB.
  - resumen/tabla/alertas: ~5.3 ms / ~9.6 KB.
  - grafica: ~41.3 ms / ~9.6 KB.
- Nutricion:
  - datos: ~5.7 ms / ~4.4 KB.
  - resumen/insight/tabla: ~9.6 ms / ~10.2 KB.
  - grafica: ~41.9 ms / ~10.5 KB.

Validacion:

- `python -m compileall app.py`: OK.
- `git diff --check -- app.py`: OK, solo warning CRLF.
- `pytest -q`: `1 passed`.
- `test_app_flow.py`: `28/28`.
- `test_s105_load.py --skip-video`: OK.
- Dash dependencies: 150, 0 duplicados exactos, 24 callbacks iniciales.
- Rutas live: `/demo/atleta`, `/demo/coach-tkd`, `/logout` correctas.
- Servidor `8051` reiniciado con un solo listener real.

Regla:

- En vistas con KPIs/tabla + grafica, separar datos y render pesado si no cambia
  la experiencia ni los datos.
- No optimizar eliminando historial ni exportes completos.

## 2026-05-25 - Chat Poll Signature Y Sensores Atleta Sin Polling Oculto

Contexto:

- Tras optimizar compare, wellbeing, peso y nutricion, se auditaron callbacks
  con polling real.
- `pages/chat.py` actualizaba el DOM completo cada 5 segundos aunque no hubiera
  mensajes nuevos.
- `views/sensors_view.py` tenia un intervalo en vista atleta que actualizaba un
  contenedor oculto; no cambiaba nada visible para el usuario.
- Se mantuvo el polling visible de coach/admin en sensores porque si alimenta
  el estado de atleta seleccionado.

Cambios aplicados:

- `pages/chat.py`: nuevo helper `_conversation_signature(messages)`.
- `chat-last-signature` guarda una firma liviana de la conversacion visible.
- `_chat_update` ahora usa `PreventUpdate` si el polling no detecta cambios.
- El layout ya trae mensajes iniciales, por lo que `_chat_update` queda con
  `prevent_initial_call=True`.
- `_select_conversation` actualiza tambien la firma cuando el coach cambia de
  atleta.
- `views/sensors_view.py`: el intervalo de la vista atleta queda desactivado
  porque sus cards se renderizan al entrar y no dependian de ese polling.
- `assets/chat_scroll.js`: se reemplazo el `setTimeout` permanente cada 1.5 s
  por un `MutationObserver` de pagina; mantiene auto-scroll sin polling global.

Medicion:

- Chat antes: polling cada 5 s con ~12.93 ms y ~2.11 KB aunque no hubiera
  cambios.
- Chat despues sin cambios: HTTP 204, ~5.57 ms y 0 KB de payload.
- Envio vacio sigue protegido: ~5.25 ms y respuesta normal sin romper UI.
- Sensores atleta: se elimina polling oculto no visible.
- Sensores coach/admin: se conserva polling funcional cada 15 s.

Validacion:

- `python -m compileall pages\chat.py views\sensors_view.py`: OK.
- `node --check assets\chat_scroll.js`: OK.
- `/assets/chat_scroll.js`: HTTP 200.
- `git diff --check -- pages\chat.py views\sensors_view.py`: OK, solo warning
  CRLF en `views\sensors_view.py`.
- `pytest -q`: `1 passed`.
- `test_app_flow.py`: `28/28`.
- `test_s105_load.py --skip-video`: OK.
- Dash dependencies: 150, 0 duplicados exactos, 23 callbacks iniciales.
- Verificacion especifica: `q-gauge`, `q-explain` y `q-trend` aparecen en un
  solo callback; no hay duplicados `q-*`.
- Rutas live: `/_dash-dependencies`, `/_dash-layout`, `/demo/atleta`,
  `/demo/coach-tkd` y `/logout` correctas.
- Servidor `8051` vivo con un solo listener real.

Regla:

- Si un polling no cambia informacion visible, debe eliminarse o desactivarse.
- Si un polling si es necesario, primero evitar re-render/payload cuando no hay
  cambios antes de recortar funcionalidad.
- No reducir congelamientos quitando potencia de biomecanica, IA, sensores,
  senales, historiales o exportes.

## 2026-05-25 - Auth Public Routes Y Recuperacion De Contraseña

Contexto:

- El boton "Crear cuenta" desde login no navegaba correctamente a registro.
- Causa raiz: `serve_layout()` pre-cargaba login y forzaba
  `dcc.Location(pathname="/login")` cuando no habia sesion. Eso hacia que
  `/registro` rebotara a `/login`.
- El enlace "Olvidaste tu contraseña" apuntaba a `href="#"`, por lo que era
  decorativo y no tenia flujo real.

Cambios aplicados:

- `app.py`: nuevas rutas publicas de auth:
  `/login`, `/registro`, `/recuperar-password`, `/forgot-password`.
- `app.py`: `_initial_path_and_content()` ya no fuerza pathname a `/login` en
  rutas publicas; deja que `dcc.Location` lea la URL real del navegador.
- `app.py`: se importa y enruta `pages.auth_forgot`.
- `pages/auth_login.py`: "Crear cuenta" y "Olvidaste tu contraseña" ahora usan
  `dcc.Link` para navegacion interna Dash.
- `pages/auth_login.py`: `_check_pw` ahora tambien soporta hashes PBKDF2 de
  `db.py`, ademas de bcrypt y SHA256 legacy.
- `pages/auth_register.py`: link de regreso a login migrado a `dcc.Link`.
- `pages/auth_forgot.py`: nueva pantalla de recuperacion con solicitud de
  codigo temporal, validacion de contraseña y redireccion a login.
- `db.py`: migracion versionada `210` con tabla `password_reset_tokens`.
- `db.py`: helpers `create_password_reset_token`,
  `reset_password_with_token` y `update_user_password`.

Seguridad y producto:

- La pantalla muestra mensaje generico para no revelar si un correo existe.
- Los tokens se guardan hasheados, tienen caducidad y quedan invalidados al
  usarse.
- En local/demo se puede mostrar el codigo temporal en pantalla para probar el
  flujo; en produccion debe enviarse por correo/API y no mostrarse.

Validacion:

- `compileall app.py db.py pages\auth_login.py pages\auth_register.py pages\auth_forgot.py`: OK.
- `db.init_db()`: DB local en `schema_version=210`.
- Tabla `password_reset_tokens`: creada.
- Prueba DB: token incorrecto falla, token correcto actualiza contraseña,
  reutilizar token falla.
- Usuario sintetico de prueba eliminado al terminar.
- `pytest -q`: `1 passed`.
- `test_app_flow.py`: `28/28`.
- `test_s105_load.py --skip-video`: OK.
- Rutas live: `/login`, `/registro`, `/recuperar-password`,
  `/forgot-password`, `/_dash-layout`, `/_dash-dependencies`: HTTP 200.
- Router interno: `/login` renderiza `login-email`, `/registro` renderiza
  `reg-email`, `/recuperar-password` renderiza `forgot-email`.
- Verificacion callbacks: `login-msg`, `reg-msg`, `forgot-request-msg`,
  `forgot-reset-msg` y `q-trend` aparecen una sola vez cada uno.
- Servidor `8051` vivo con un solo listener real.
- Validacion dirigida posterior: `15/15`.
  - `/registro` y `/recuperar-password` no fuerzan `/login`.
  - Router renderiza `reg-email` y `forgot-email`.
  - Links de login apuntan a `/registro` y `/recuperar-password`.
  - Registro crea cuenta y redirige a `/onboarding`.
  - Login con password original funciona.
  - Forgot genera mensaje seguro y token temporal.
  - Token incorrecto falla, token correcto cambia password, token usado no se
    reutiliza.
  - Login con nueva password funciona.
  - Usuario sintetico de prueba eliminado al terminar.

Regla:

- Las rutas publicas de autenticacion no deben forzar pathname a `/login`.
- Todo enlace interno de auth debe usar `dcc.Link` salvo que sea una ruta Flask
  intencional.
- Recuperacion de contraseña queda lista para conectar correo externo en un
  sprint posterior sin cambiar el modelo de datos.

## 2026-05-26 - Auditoria De Congelamientos: Debug Y Doble Listener

Contexto:

- Se inicio una auditoria especifica de congelamientos, "Loading..." y carga
  doble.
- Objetivo: no recortar potencia de biomecanica, IA, sensores, senales,
  historiales ni exportes; buscar causas reales de bloqueo o peso innecesario.

Hallazgos:

- Habia dos procesos `python` escuchando en `8051` al mismo tiempo:
  `39024` y `40532`.
- Esto puede explicar doble carga, rutas que parecen quedarse en loading y
  comportamiento intermitente al navegar o cerrar sesion.
- `app.py` arrancaba con `debug=True`. Aunque `use_reloader=False` evitaba el
  reloader, debug mantiene herramientas de desarrollo/overlays y trabajo extra
  del navegador que no conviene en demo.
- El router escribia cada navegacion en log `INFO`, lo que mete ruido y puede
  penalizar si hay muchas navegaciones/callbacks.
- Medicion de layouts por router no mostro un freeze general:
  - Dashboard atleta: ~290 ms promedio, pico ~458 ms.
  - Analisis: ~77 ms.
  - Comparar: ~69 ms.
  - ECQ/Replay layout: ~4 ms de router, payload mayor por estructura.

Cambios aplicados:

- `app.py`: `COMBATIQ_DEBUG = _env_flag("COMBATIQ_DEBUG", "0")`.
- `app.py`: `app.run(debug=COMBATIQ_DEBUG, ..., dev_tools_hot_reload=False)`.
- `app.py`: el log del router baja de `INFO` a `DEBUG`.
- `.env.example`: documenta `COMBATIQ_DEBUG=0`.
- Reinicio limpio: se detuvieron los listeners `39024` y `40532`; queda un solo
  listener real en `8051` (`35632`).

Medicion live tras el fix:

- `/login`: 200, ~31.8 ms.
- `/registro`: 200, ~14.9 ms.
- `/recuperar-password`: 200, ~14.4 ms.
- `/demo/atleta`: 200, ~52.5 ms, final `/dashboard`.
- `/dashboard`: 200, ~14.1 ms.
- `/_dash-layout`: 200, ~184.9 ms con sesion atleta.
- `/_dash-dependencies`: 200, ~12.6 ms, 152 callbacks live.
- Flujo logout: `/demo/atleta` -> `/logout` -> `/login`; layout posterior
  ~14.5 ms.
- Verificacion callbacks: `q-trend`, `login-msg`, `reg-msg`,
  `forgot-request-msg` y `forgot-reset-msg` aparecen una sola vez.

Validacion:

- `compileall app.py`: OK.
- Import config: `COMBATIQ_DEBUG False`, `AUTO_OPEN True`.
- `pytest -q`: `1 passed`.
- `test_app_flow.py`: `28/28`.
- `test_s105_load.py --skip-video`: OK.
- `git diff --check -- app.py .env.example`: OK, solo warnings CRLF.

Regla:

- En demo/inversores la app debe correr sin debug/hot reload salvo que se active
  explicitamente `COMBATIQ_DEBUG=1`.
- Si aparece doble listener en `8051`, limpiar procesos antes de diagnosticar
  callbacks.
- Logs de navegacion masiva deben quedar en `DEBUG`, no en `INFO`.

## 2026-05-26 - Asistente IA Flotante Con Fallback Local

Contexto:

- En el asistente flotante aparecia `Error: Connection error`.
- El callback si funcionaba; el problema era que `ai_insights.generate_chat_response`
  devolvia el error crudo cuando la API externa no respondia.
- Resultado para usuario: burbuja de error sin ayuda accionable.

Cambios aplicados:

- `ai_insights.py`: nuevo `_chat_local_fallback(message, context, reason)`.
- Si no hay `ANTHROPIC_API_KEY`, el asistente responde en modo local con datos
  cargados y recomendacion, en vez de mostrar error.
- Si hay fallo de conexion/API, se registra warning y se responde en modo local.
- `app.py`: el callback flotante `send_chat_message` ahora tiene fallback
  defensivo si falla la importacion/llamada completa.
- El fallback diferencia atleta vs coach.

Validacion:

- `compileall ai_insights.py app.py`: OK.
- Test con cliente IA forzado a `Connection error`: atleta y coach devuelven
  `has_raw_error=False`.
- Callback flotante directo: `bubble_count=3`, `history_len=2`,
  `input_value=''`, `has_raw_error=False`.
- `pytest -q`: `1 passed`.
- `test_app_flow.py`: `28/28`.
- `test_s105_load.py --skip-video`: OK.
- Servidor reiniciado; rutas live basicas: HTTP 200.
- `/_dash-dependencies`: 152 callbacks live; `ai-chat-messages` aparece en un
  solo callback.
- `8051`: un solo listener real.

Regla:

- La IA externa puede fallar; la UI nunca debe mostrar errores crudos al usuario.
- Toda IA visible debe tener una respuesta local util basada en datos internos.
- Los errores tecnicos se registran en logs, no en burbujas de usuario.

## 2026-05-26 - IA Externa Validada Y Guardar Cuestionario Corregido

Contexto:

- El usuario pregunto por que la IA externa no conectaba y reporto que el boton
  "Guardar cuestionario" no funcionaba.

IA externa:

- `ANTHROPIC_API_KEY` esta presente, con prefijo correcto `sk-ant-` y longitud
  esperada; no se imprimio la clave.
- Paquete `anthropic`: `0.96.0`.
- Modelos configurados:
  - `_MODEL_HAIKU = claude-haiku-4-5`
  - `_MODEL_SONNET = claude-sonnet-4-6`
  - `_MODEL_OPUS = claude-opus-4-7`
- Documentacion oficial Anthropic/Claude confirma:
  - API IDs: `claude-opus-4-7`, `claude-sonnet-4-6`,
    `claude-haiku-4-5-20251001`.
  - Alias API: `claude-haiku-4-5`.
- Prueba normal desde sandbox: todos daban `APIConnectionError Connection error`.
- Prueba con red externa permitida: `claude-haiku-4-5`,
  `claude-haiku-4-5-20251001` y `claude-sonnet-4-6` respondieron `OK`.
- Causa: la app/proceso habia sido arrancado desde entorno con red restringida,
  no era fallo de key ni modelo.
- Se reinicio la app fuera de la restriccion de red; queda un solo listener en
  `8051`.
- Test live del asistente:
  - `/demo/atleta` + POST a `ai-chat-messages`.
  - Status 200.
  - Latencia ~6992 ms.
  - `has_connection_error=False`.
  - `has_modo_local=False` (respondio IA externa real).

Guardar cuestionario:

- Causa raiz: firma desalineada del callback `save_wellbeing`.
- `q-user` estaba como `Input` y tambien como `State`, pero la funcion no
  recibia el segundo valor.
- Eso desplazaba argumentos: `q-session`, `q-competition`, `q-weight`,
  `q-injury` entraban corridos y `"no"` terminaba como respuesta numerica.
- Error observado: `ValueError: could not convert string to float: 'no'`.
- Fix: `save_wellbeing(input_user_id, n, user_id, session_id, competition,
  weight, injury, *values)` y uso de `input_user_id` solo para la tendencia
  cuando el trigger no es guardar.
- Test POST real a `/_dash-update-component`:
  - Antes: status 500, delta DB 0.
  - Despues: status 200, delta DB +1, respuesta incluye `q-gauge`.
  - Registro sintetico eliminado al terminar.
- Test live contra servidor reiniciado:
  - status 200.
  - DB delta +1.
  - `has_500=False`.
  - cleanup ejecutado.

Validacion:

- `compileall pages\wellbeing.py`: OK.
- `pytest -q`: `1 passed`.
- `test_app_flow.py`: `28/28`.
- `test_s105_load.py --skip-video`: OK.
- `git diff --check -- pages\wellbeing.py ai_insights.py app.py`: OK,
  solo warnings CRLF.

Regla:

- Cuando un callback usa el mismo componente como Input y State, la firma debe
  reflejar ambos argumentos en orden exacto.
- Para probar IA externa desde este entorno, distinguir sandbox de red vs app
  arrancada fuera de sandbox.
- Si la demo requiere IA externa real, arrancar la app desde terminal normal o
  con permisos de red, no desde el sandbox restringido.

## 2026-05-26 - Export IMU Desde Sesiones Combat Monitor Corregido

Contexto:

- El usuario reporto que en `Señales ECG / IMU` se veian KPIs y grafica IMU,
  pero al pulsar `Descargar informe (PDF)` aparecia:
  `Carga o analiza un archivo IMU antes de exportar el informe.`
- Caso reproducible: sesion `34` de Carlos Rios, con sidecar
  `data/ecg/combat_12_wt_videoplayback_imu.json`.

Causa raiz:

- `auto_load_imu_for_session` renderizaba la grafica y KPIs desde la sesion
  Combat Monitor, pero no escribia `imu-meta`.
- Los exports `download_imu_data` y `download_imu_report` dependian de
  `imu-meta`, que solo se llenaba al subir/analizar manualmente un CSV IMU en
  `data/imu`.
- Resultado: la UI mostraba datos IMU reales de sesion, pero los botones de
  export creian que no habia fuente exportable.

Fix:

- Archivo: `views/signals_view.py`.
- `auto_load_imu_for_session` ahora tambien actualiza `imu-meta` con:
  - `source=session_events`
  - `format=event_json`
  - ruta segura al sidecar JSON en `data/ecg`
  - `uid`, `session_id`, `kind`, deporte y metricas de DB.
- Se añadieron helpers locales para reconstruir metadata IMU desde la sesion
  seleccionada:
  - `_build_session_imu_meta`
  - `_session_imu_meta_from_state`
  - `_load_imu_event_sidecar`
  - `_imu_sidecar_path_from_row`
- `download_imu_data` ahora soporta dos fuentes:
  - CSV manual (`data/imu`) como antes.
  - Eventos JSON de sesion (`data/ecg/*_imu.json`) exportados como tabla Excel.
- `download_imu_report` ahora soporta dos fuentes:
  - CSV manual con magnitud continua.
  - Eventos JSON de sesion con intensidad en g y metricas guardadas.
- Se agrego fallback por `signals-session` + `ecg-user` + `imu-tabs` para que el
  export funcione aunque `imu-meta` venga vacio por estado viejo del navegador.

Validacion:

- `compileall views\signals_view.py`: OK.
- Import de `app.py`: OK, 135 callbacks registrados; sin error de callbacks
  duplicados.
- Validacion directa de callbacks con sesion `34` y `imu-meta=None`:
  - Excel generado:
    `CombatIQ_IMU_combat_12_wt_videoplayback_imu_eventos.xlsx`.
  - PDF generado:
    `CombatIQ_IMU_combat_12_wt_videoplayback_imu_informe.pdf`.
  - Mensaje PDF vacio, sin error.
- `pytest -q`: `1 passed`.
- `test_app_flow.py`: `28/28`.
- `test_s105_load.py --skip-video`: OK.

Regla:

- Si una grafica se renderiza desde una fuente de datos, los exports deben usar
  esa misma fuente o reconstruirla desde el estado de sesion.
- No hacer que el usuario suba de nuevo un archivo si la sesion ya contiene IMU
  exportable.
- Diferenciar claramente IMU manual CSV (`data/imu`) de IMU de sesion Combat
  Monitor (`data/ecg/*_imu.json`).

## 2026-05-26 - Limpieza Visual Al Quitar Sesion ECG/IMU

Contexto:

- El usuario reporto que al quitar la sesion en `Señales ECG / IMU`, la UI
  seguia mostrando la grafica, KPIs y metricas de la sesion anterior.
- Visualmente parecia que habia una sesion activa aunque el dropdown estuviera
  vacio.

Causa raiz:

- Al quedar `signals-session=None`, los callbacks de ECG/IMU hacian
  `PreventUpdate` o devolvian `no_update`.
- Dash conservaba los outputs anteriores: grafica ECG, tarjetas KPI, grafica
  IMU, tarjetas KPI e `imu-meta`.

Fix:

- Archivo: `views/signals_view.py`.
- `auto_select_ecg_for_session(None, ...)` ahora:
  - limpia `ecg-file` con `None`;
  - muestra placeholder `Selecciona una sesión para ver ECG`;
  - vacia KPIs;
  - muestra mensaje claro.
- `auto_load_imu_for_session(None, ...)` ahora:
  - muestra placeholder `Selecciona una sesión para ver IMU`;
  - vacia KPIs;
  - borra `imu-meta`;
  - muestra mensaje claro.
- El cambio no toca calculos ECG/IMU ni datos de DB; solo corrige estado visual
  cuando no hay sesion seleccionada.

Validacion:

- `compileall views\signals_view.py`: OK.
- Import de `app.py`: OK, 135 callbacks registrados.
- Test directo con `signals-session=None`:
  - ECG: `ecg-file=None`, KPIs `0`, figura sin trazas y con placeholder.
  - IMU: KPIs `0`, `imu-meta=None`, figura sin trazas y con placeholder.
- `pytest -q`: `1 passed`.
- `test_app_flow.py`: `28/28`.
- `test_s105_load.py --skip-video`: OK.

Regla:

- Cuando una seleccion maestra queda vacia, ningun KPI/grafica/export debe seguir
  apuntando a la lectura anterior.
- En Dash, evitar `PreventUpdate` para estados que deben limpiar UI; devolver
  placeholders explicitos.

## 2026-05-26 - Guardar Cuestionario Sin Bloqueo De IA Externa

Contexto:

- El usuario noto que `Guardar cuestionario` en Bienestar tardaba demasiado y
  podia sentirse como congelamiento.

Causa raiz:

- En el flujo de atleta, cuando `wellness < 65`, el callback de guardado llamaba
  `ai_insights.generate_wellbeing_message()`.
- Esa llamada usa IA externa y puede esperar hasta el timeout ligero (~8 s) si
  hay latencia o red restringida.
- Al estar dentro del mismo callback del boton, la UI queda esperando antes de
  devolver gauge, explicacion y tendencia.

Fix:

- Archivo: `pages/wellbeing.py`.
- Se agrego `_build_fast_wellbeing_message()`, una frase local instantanea
  basada en:
  - score de bienestar;
  - deporte;
  - principales positivos;
  - principales riesgos.
- El callback `save_wellbeing` ya no llama IA externa al guardar.
- Se conserva la lectura accionable para el atleta, pero sin depender de red ni
  de Anthropic en el boton critico.

Validacion:

- `compileall pages\wellbeing.py`: OK.
- `rg generate_wellbeing_message pages\wellbeing.py`: sin usos.
- `pytest -q`: `1 passed`.
- `test_app_flow.py`: `28/28`.
- `test_s105_load.py --skip-video`: OK.

Regla:

- Botones criticos de guardado no deben depender de APIs externas.
- IA externa puede enriquecer analisis, pero debe ejecutarse en flujos separados,
  bajo demanda o con fallback/asinc, no bloqueando persistencia de datos.

## 2026-05-26 - Biomecanica: Interpretacion De Graficas Y Evidencia Por Frame

Contexto:

- El usuario pidio cuidar dos detalles de UX/analisis:
  - Debajo de cada grafica debe existir un desplegable `Cómo interpreto esta
    gráfica`.
  - La lectura IA/coaching debe decir en que frame o segundo se vio la evidencia
    que sostiene el consejo, y por que.

Fix:

- Archivo: `views/signals_view.py`.
- Se agrego helper reutilizable `_graph_interpretation()` para desplegables bajo
  graficas.
- Se agregaron helpers:
  - `_frame_ref()` para mostrar `t=...s · frame ...`.
  - `_duel_frame_evidence()` para extraer evidencia de modo rojo vs azul:
    distancia mas corta, posible intercambio, presion y pico de velocidad
    angular.
  - `_single_frame_evidence()` para extraer evidencia de modo individual:
    mayor amplitud, asimetria, landmarks dudosos y baja calidad de pose.
  - `_evidence_list()` para renderizar la evidencia en tarjetas IA/coaching.
- Se añadieron desplegables `Cómo interpreto esta gráfica` en:
  - ECG/IMU de señales.
  - ECG/IMU del replay.
  - ECG/IMU simulados desde movimiento.
  - Grafica de distancia rojo vs azul.
  - Velocidad de pateo.
  - Velocidad de desplazamiento.
  - ROM YOLO.
  - Tren inferior.
  - Tren superior.
- La tarjeta de lectura tactica y la lectura IA de combate ahora incluyen
  evidencia por frame cuando hay datos duales.
- La tarjeta de lectura biomecanica individual ahora incluye evidencia por frame
  para todas las modalidades de objetivo individual.

Validacion:

- `compileall views\signals_view.py`: OK.
- Import de `app.py`: OK, 135 callbacks registrados.
- `pytest -q`: `1 passed`.
- `test_app_flow.py`: `28/28`.
- `test_s105_load.py --skip-video`: OK.

Regla:

- El desplegable bajo una grafica debe explicar solo la grafica, sin meter
  recomendaciones de entrenamiento.
- Las recomendaciones IA/coaching deben intentar citar tiempo/frame y motivo
  cuando haya frames disponibles.
- La IA no reemplaza medicion biomecanica; traduce e interpreta la evidencia que
  calculan MediaPipe/YOLO.

Limpieza tecnica adicional:

- Se retiro el BOM UTF-8 accidental al inicio de `views/signals_view.py`.
- Primeros bytes verificados despues de la limpieza: `35 32 118 105...`
  (`# views/...`).
- `compileall views\signals_view.py`: OK despues de la limpieza.

## 2026-05-27 - Biomecanica Persistente Para Demo

Contexto:

- El usuario pidio que, despues de completar un analisis biomecanico, cambiar de
  pestana no borre la lectura.
- Regla de producto para demo/inversores: la lectura debe permanecer visible
  hasta que el usuario cambie el tipo/objetivo de analisis o cierre sesion.

Fix:

- Archivo: `views/signals_view.py`.
- `pose-results` ahora usa `storage_type="session"` para conservar la referencia
  ligera del analisis durante la sesion del navegador.
- El selector de objetivo y numero de rounds usan persistencia de sesion para no
  desalinear la UI del resultado mostrado.
- Al terminar el render de biomecanica, CombatIQ guarda tambien el componente
  visible en cache server-side (`rendered_output`) junto al `job_id`.
- Nuevo callback `restore_pose_output()`:
  - al volver a `tab-biomech`, recupera el render desde cache si existe;
  - valida que el `user_id` del analisis coincida con el usuario actual;
  - si la cache expiro, muestra una tarjeta de referencia preservada y pide
    repetir el analisis para reconstruir graficas/imagenes.
- Nuevo callback `reset_pose_when_target_changes()`:
  - si el usuario cambia `Auto`, `Peto rojo`, `Peto azul`, `Rojo vs azul`,
    `Atleta izquierda` o `Atleta derecha`, se limpian stores/resultados;
  - se evita mostrar una lectura vieja con un objetivo nuevo.
- TTL de cache de pose ampliado de 45 min a 4 h, acotado por maximo de items,
  para cubrir presentaciones largas sin quitar potencia al analisis.

Logout:

- Archivo: `app.py`: `/logout` redirige a `/login?logged_out=1`.
- Archivo nuevo: `assets/60_pose_session_cleanup.js`.
- El JS limpia llaves `pose-*` de `sessionStorage/localStorage` al visitar
  `/logout` o `/login?logged_out=1`, incluyendo navegacion interna Dash.

Validacion:

- `compileall views\signals_view.py app.py`: OK.
- Import de app: OK, `137 callbacks`.
- Prueba directa de cache: `job_id` recupera `rendered_output`.
- `pytest -q`: `1 passed`.
- `test_app_flow.py`: `28/28`.
- `test_s105_load.py --skip-video`: OK.
- `git diff --check`: sin errores reales; solo avisos CRLF esperados en Windows.

Regla:

- Un analisis biomecanico completado no debe desaparecer por navegar entre
  pestanas.
- Cambiar objetivo de analisis invalida la lectura anterior.
- Cerrar sesion debe limpiar referencias biomecanicas persistidas en navegador.

## 2026-05-27 - Biomecanica: Filtro Anti-Pose-Contaminada

Contexto:

- El usuario mostro frames donde el reconocimiento rojo/azul ya detectaba
  personas, pero mezclaba cuerpos durante cruces/oclusiones: peto rojo con
  esqueleto parcialmente tomado de otro atleta/arbitro.
- Riesgo: una metrica falsa puede aparecer con confianza visual alta, algo
  grave para demo, coach e inversores.

Fix:

- Archivo: `pose_analyzer.py`.
- Se agrego `_target_body_consistency()`:
  - valida que hombros/cadera/torso esten geometricamente alineados con el
    color del peto objetivo;
  - penaliza `color_contrario_en_pose`, `casco_contrario`, `peto_no_aislado`,
    `posible_arbitro`, torso desalineado o demasiado ancho;
  - devuelve `identity_quality` y notas explicables.
- `_describe_pose()` ahora calcula calidad de identidad para rojo y azul.
- `_select_pose()` ahora rechaza candidatos con identidad baja como
  `pose_contaminada`.
- La confianza del frame aceptado se reduce si `identity_quality` es baja.
- Modo individual y dual guardan:
  - `identity_quality`;
  - `identity_warnings`;
  - `pose_contaminada` dentro de `landmark_warnings` cuando aplica.
- La confianza visible del objetivo ahora considera cobertura:
  - `selection_confidence_raw` = calidad media de frames aceptados;
  - `coverage` = frames aceptados / frames muestreados;
  - `confidence` visible = confianza ponderada por cobertura.
- `views/signals_view.py` cambia etiquetas a `Selección + cobertura` y muestra
  `cobertura` en el resumen del objetivo.
- La fila de confiabilidad ahora entiende:
  - `pose_contaminada`;
  - `color_contrario_en_pose`;
  - `peto_no_aislado`;
  - `casco_contrario`.

Validacion:

- `compileall pose_analyzer.py views\signals_view.py`: OK.
- Import de app: OK, `137 callbacks`.
- Prueba dual acotada con `data/uploads/videoplayback.mp4`:
  - error: `None`;
  - 90 frames muestreados;
  - 15 frames pareados;
  - cobertura dual: `0.167`;
  - confianza visible dual: `0.697`;
  - rojo: confianza visible `0.56`, raw `0.929`, cobertura `0.278`,
    rechazos `54`;
  - azul: confianza visible `0.833`, raw `0.942`, cobertura `0.789`,
    rechazos `4`;
  - rechazos rojo: `pose_contaminada`, `color_insuficiente`,
    `continuidad_baja`.
- Prueba individual acotada:
  - rojo: selected `19`, coverage `0.271`, confidence `0.557`, raw `0.929`,
    rejections `44`.
  - azul: selected `55`, coverage `0.786`, confidence `0.832`, raw `0.943`,
    rejections `8`.
- `pytest -q`: `1 passed`.
- `test_app_flow.py`: `28/28`.
- `test_s105_load.py --skip-video`: OK.
- `git diff --check`: sin errores reales; solo CRLF esperado.

Observacion:

- En videos ruidosos, rojo puede bajar cobertura de forma notable. Esto es
  correcto: CombatIQ prefiere decir "no confio en este frame" antes que fabricar
  una biomecanica falsa.
- Los logs de MediaPipe sobre `clearcut` son intentos externos de telemetria sin
  red; no bloquearon la validacion.

Regla:

- Frame con peto visible pero esqueleto contaminado no debe mostrarse como
  medicion confiable.
- La confianza visible siempre debe considerar cobertura, no solo frames buenos.

Refinamiento posterior con frames del usuario:

- Se agrego penalizacion por solapamiento corporal entre candidatos:
  - `cuerpo_cruzado` si el overlap es alto;
  - `oclusion_parcial` si el overlap es medio.
- Los porcentajes dibujados en frames duales ahora usan confianza efectiva del
  frame (`selection_confidence * pose_quality`), no solo confianza de color.
- Los frames destacados de la galeria dual se eligen solo si estan limpios:
  - sin `pose_contaminada`;
  - sin `cuerpo_cruzado`;
  - sin `oclusion_parcial`;
  - sin color/casco contrario;
  - con confianza minima suficiente.
- Se agrego `annotated_frames_meta` con `t` y `score`; la UI muestra debajo de
  cada imagen el tiempo y score del frame destacado.

Validacion adicional:

- Corrida larga acotada con `videoplayback.mp4`, `sample_every=12`,
  `max_frames=540`:
  - keyframes seleccionados: `48.5s`, `86.0s`, `95.0s`, `127.5s`, `136.5s`,
    `201.0s`;
  - los frames problematicos alrededor de `241.7s` y `289.2s` ya no quedan como
    frames destacados.
- `test_app_flow.py`: `28/28`.
- `test_s105_load.py --skip-video`: OK.
- Import app: `137 callbacks`.
