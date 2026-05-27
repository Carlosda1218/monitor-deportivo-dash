# CombatIQ - Memoria De Auditoria Biomecanica

Fecha de inicio: 2026-05-21

## Objetivo

Reforzar el pilar tecnico de CombatIQ: analisis de combate, mediciones,
biomecanica, tracking, confianza e IA accionable para boxeo y taekwondo.

## Archivos Criticos

- `yolo_tracker.py`
- `pose_analyzer.py`
- `views/signals_view.py`
- `app.py`
- `ai_insights.py`
- `ui_charts.py`

## Estado Declarado Por El Usuario

YOLO tracker:

- Usa YOLOv8n-pose + OpenVINO + supervision ByteTrack.
- Funcion principal esperada: `analyze_duel_speeds("data/uploads/combate.mp4")`.
- Devuelve velocidades de patada, desplazamiento, picos y biomecanica por color.
- Tiene filtros anti-ghost recientes:
  - `_CONF_THR_PERSON = 0.45`
  - `_MIN_BBOX_AREA_PX2 = 3500`
  - `_MIN_KP_COUNT = 5`
  - `_MIN_TRACK_FRAMES = 3`
- El motivo declarado es evitar que ByteTrack asigne IDs de peto a arbitros o
  espaldas cuando el atleta sale brevemente del frame.

Biomecanica YOLO Phase 1:

- Articulaciones esperadas: rodilla L/R, cadera L/R, codo L/R.
- Funciones esperadas:
  - `_JOINT_ANGLES`
  - `_joint_angle`
  - `_angular_velocity`
  - `_compute_duel_biomech`
- UI esperada:
  - `_biomech_yolo_section(speed_data)`
  - KPIs ROM, tabla de asimetria, grafico de barras.

MediaPipe/pose analyzer:

- Selector de objetivo: automatico, peto rojo, peto azul, atleta izquierda,
  atleta derecha, rojo vs azul.
- Multipersona por frame.
- Filtro por color del peto.
- Seguimiento temporal.
- Limpieza de landmarks contaminados.
- Confianza por video y explicacion accionable.

## Validaciones Esperadas

- Compilar `yolo_tracker.py`, `pose_analyzer.py`, `views/signals_view.py`,
  `app.py`.
- Ejecutar `analyze_wt_deep.py` si el entorno y el tiempo lo permiten.
- Comparar resultado con `data/yolo_test_result.json` si aplica.
- Validar UI manual: Carlos Rios, sesion 34, video `videoplayback.mp4`.

## Puntos De Auditoria

- Confirmar que los filtros anti-ghost existen y se aplican en el orden
  correcto.
- Confirmar que los tracks inestables no contaminan metricas.
- Confirmar que velocidades cercanas al cap de 17 m/s no se presentan como
  precision absoluta si hay duda de calibracion.
- Confirmar que la IA diferencia dato confiable, estimado y descartado.
- Confirmar que el modo rojo vs azul no declara ganador ni puntos oficiales.
- Confirmar que graficas no saturan ni confunden.
- Confirmar que los textos usan lenguaje de coach/atleta, no laboratorio.

## Roadmap Phase 2 Pendiente

- `chamber_angle`: metrica prioritaria para coaches de taekwondo.
- Coordenada Z de MediaPipe.
- Optical Flow Lucas-Kanade para velocidades de patada mas precisas.
- Calibracion homografica con tatami WT 8x8m.
- Multi-camara y triangulacion 3D a futuro.

## Riesgos

- `kernel.errors.txt` contiene un error de kernel/Intel que podria afectar
  OpenVINO/GPU.
- Video ruidoso de peor caso debe usarse para probar que el sistema prefiere
  bajar confianza antes que inventar precision.
- El usuario reporta lentitud/congelamientos, posiblemente en tabs con video,
  graficas o analisis pesado.

## Auditoria 2026-05-21 - yolo_tracker.py

Hallazgos corregidos:

- Critico: `yolo_tracker.py` no importaba porque `_postprocess` usaba
  `_CONF_THR_PERSON` como default antes de que la constante existiera.
  Correccion: `conf_thr` ahora es `None` por default y toma la constante dentro
  de la funcion.
- Alto: OpenVINO intentaba GPU y si fallaba no caia a CPU. Correccion:
  `_get_model()` intenta GPU y luego CPU, registrando warning.
- Alto/rendimiento: el loop de video usaba `cap.set()` para cada frame
  muestreado. Correccion: lectura secuencial con `sample_every`, evitando seeks
  repetidos que pueden congelar videos largos.
- Medio: no se validaba `cap.isOpened()`. Correccion: error claro si el video
  no abre.
- Medio: `_vest_color()` podia clasificar ruido pequeno de color como peto.
  Correccion: bbox clipping, ratio minimo de color y dominancia rojo/azul.
- Medio: fallback por posicion podia asignar el color faltante a una unica
  persona sin peto visible, por ejemplo arbitro. Correccion: fallback solo si
  ningun color fue detectado en ese frame y hay al menos dos candidatos.
- Medio/contrato: el resultado documentado esperaba `blue`/`red`, pero la UI
  usa `azul`/`rojo`. Correccion: se mantienen claves en espanol y aliases
  `blue`/`red`.
- Medio: `_postprocess` conservaba solo 2 detecciones por frame; un arbitro
  grande podia ocultar a un atleta real. Correccion: se conservan hasta 6
  candidatos para que color/tracking decidan.

Validaciones:

- `python -m compileall yolo_tracker.py`: OK.
- `python -c "import yolo_tracker"`: OK.
- `analyze_duel_speeds(... max_duration_s=30)`: OK, aliases sincronizados.
- `analyze_duel_speeds(... sample_every=3, max_duration_s=600)`: OK en 58.5s.
- Resultado completo actual: azul 26 picos, rojo 16 picos, max azul 16.94 m/s,
  max rojo 16.68 m/s, frames biomecanicos azul 5219, rojo 1541.

Comparacion contra `data/yolo_test_result.json`:

- Historico: azul 38 picos, rojo 32 picos, frames azul 2725, rojo 2653.
- Actual: menos picos y menos frames rojos, pero con filtros mas conservadores
  contra arbitro/ghost.

Riesgo residual:

- Requiere validacion visual del atleta rojo para confirmar que la reduccion de
  picos no elimina tecnica real. Si falta rojo real, el siguiente ajuste debe
  mejorar seleccion por color sin reintroducir arbitros.

## Auditoria 2026-05-21 - analyze_wt_deep.py

Hallazgo:

- Alto: el script parecia una validacion, pero por defecto escribia CSV/JSON y
  actualizaba DB. Durante esta auditoria se ejecuto una vez antes de detectar
  el riesgo y regenero `combat_12_wt_videoplayback.csv` y
  `combat_12_wt_videoplayback_imu.json`.

Correccion:

- Se agrego modo seguro por defecto. Ahora `analyze_wt_deep.py` analiza y
  muestra resultados sin escribir archivos ni DB.
- Para persistir resultados se debe usar `--write`.

Validacion:

- `python -m compileall analyze_wt_deep.py yolo_tracker.py`: OK.
- `python analyze_wt_deep.py`: corre en DRY RUN y no persiste.

Riesgo residual:

- La corrida previa ya cambio archivos demo de ECG/IMU. No se revirtio porque
  no se deben eliminar ni restaurar datos sin permiso. El flujo actual sigue
  pasando `test_app_flow.py`.

## Auditoria 2026-05-21 - pose_analyzer.py

Contexto:

- El usuario aclaro que optimizar no debe significar quitar potencia.
- Se reviso `pose_analyzer.py` despues de `yolo_tracker.py`, con foco en
  biomecanica MediaPipe, modo dual, consumo de memoria y limites de analisis.

Hallazgos corregidos:

- Bajo/limpieza: `sys` estaba importado sin uso. Se elimino.
- Medio: excepciones no criticas en dibujo/bbox se silenciaban con `pass`.
  Ahora se registran como `debug`, sin romper el flujo visual.
- Medio: `cv2.imencode()` no verificaba si la codificacion JPEG habia sido
  exitosa antes de generar base64. Ahora valida `ok_frame`/`ok_kf`.
- Alto/rendimiento: en modo dual se podian codificar y guardar en memoria
  cientos o miles de JPEGs candidatos para la galeria, aunque la UI solo
  muestra hasta 6. Ahora se mantiene un pool acotado de candidatos
  (`COMBATIQ_DUEL_KEYFRAME_CANDIDATES`, default 48). Esto no toca los calculos
  biomecanicos ni las graficas, solo evita trabajo visual innecesario.
- Alto/rendimiento: `analyze_video(... target="duel", max_frames=..., max_seconds=...)`
  elevaba siempre los limites a defaults largos de combate, incluso cuando el
  caller pasaba limites explicitos. Ahora solo autoescala si el caller dejo los
  defaults de modo individual; previews/tests respetan limites explicitos.
- Medio/reproducibilidad: `simulate_duel_ecg_imu()` usaba aleatoriedad global.
  Ahora usa un generador local determinista para que la misma entrada produzca
  la misma simulacion ECG/IMU.

Validaciones:

- `python -m compileall pose_analyzer.py`: OK.
- `python -c "import pose_analyzer"`: OK.
- Analisis individual azul, `sample_every=12`, `max_frames=20`,
  `max_seconds=20`: OK, 15 frames, confianza 1.0, 0.64s.
- Analisis dual explicito, `sample_every=12`, `max_frames=40`,
  `max_seconds=30`: OK, respeta `frames_analyzed=40`, 9 frames pareados,
  2 frames anotados, 1.48s.
- `compileall` general: OK.
- `pytest -q`: `1 passed`.
- `test_app_flow.py`: `28/28`.

Riesgo residual:

- El analisis dual completo por defecto sigue siendo pesado por diseno porque
  conserva potencia para combates completos. El siguiente foco debe ser revisar
  `signals_view.py` para confirmar si lo lanza de forma bloqueante y si conviene
  separar preview/procesamiento completo en UI sin quitar capacidades.

## Auditoria 2026-05-21 - IA Biomecanica Por Rol

Contexto:

- El usuario reporto que la IA de biomecanica mezclaba lectura para estudiante
  y coach en el mismo bloque.
- La correccion no debia cambiar calculos biomecanicos ni tracking.

Cambios:

- `ai_insights.generate_duel_insight()` ahora acepta:
  `audience`, `athlete_name`, `coach_name`.
- El prompt rojo vs azul exige una sola audiencia por respuesta.
- El fallback local sin API diferencia atleta y coach.
- `views/signals_view.py` envia el rol real de la sesion al generar la lectura
  IA del duelo.
- Las tarjetas de lectura biomecanica agregan un puente textual segun rol para
  que el usuario entienda si la recomendacion es para ejecutar o para entrenar
  a otro atleta.

Validacion:

- `compileall` de `ai_insights.py` y `views/signals_view.py`: OK.
- Prueba sin API: atleta recibe "Tu lectura del combate"; coach recibe
  "Lectura tactica para coach".
- `pytest -q`: `1 passed`.
- `test_app_flow.py`: `28/28`.

Riesgo residual:

- Validar con API activa que Claude respeta la audiencia y no vuelve a mezclar
  ambos roles.
- Revisar en UI real que el texto no sature la tarjeta ni repita informacion.

## Auditoria 2026-05-21 - Sprint 5 Signals View Cache

Contexto:

- El usuario pidio optimizar congelamientos sin quitar potencia del analisis.
- La zona critica era la pestana de biomecanica en `views/signals_view.py`.

Hallazgo:

- El flujo ya estaba separado en 3 callbacks, pero los stores seguian moviendo
  payloads grandes al navegador.
- El problema no era solo el tiempo de MediaPipe/YOLO/Claude; tambien era la
  transferencia/serializacion de frames y reportes completos en JSON.

Correccion:

- Se agrego cache server-side para resultados pesados de pose.
- Los stores de Dash ahora llevan referencias ligeras (`job_id`) y estado.
- El reporte completo se mantiene en servidor para PDF, simulacion y guardado.
- El reporte visible en `pose-results` queda reducido a resumen, biomecanica,
  objetivo, metadatos y bandera de duelo.

Validacion:

- `python -m compileall views\signals_view.py`: OK.
- `python -m pytest -q`: OK.
- `python test_app_flow.py`: OK.
- Prueba directa de helpers: el reporte ligero no contiene frames y
  `_resolve_pose_report_data()` recupera el reporte completo desde cache.

Riesgo residual:

- Cache en memoria: adecuada para demo local, no suficiente como arquitectura
  final multi-worker.
- Falta prueba manual completa con `videoplayback.mp4` y exportar PDF despues
  del analisis para confirmar experiencia de usuario.

## Auditoria 2026-05-27 - Anti-Pose-Contaminada

Contexto:

- Nuevas capturas del usuario mostraron ID switch/mezcla corporal: el peto se
  detecta, pero el esqueleto puede tomar extremidades/torso de otra persona
  durante cruces.

Cambios:

- `pose_analyzer.py`:
  - `_target_body_consistency()` valida alineacion peto-torso-hombros-cadera;
  - `_describe_pose()` calcula `red_identity_quality` y
    `blue_identity_quality`;
  - `_select_pose()` rechaza identidad baja como `pose_contaminada`;
  - modo individual/dual guardan `identity_quality` e `identity_warnings`;
  - confianza visible se pondera por `coverage`.
- `views/signals_view.py`:
  - etiquetas `Selección + cobertura`;
  - resumen de objetivo con `cobertura`;
  - chips para `pose_contaminada`, `color_contrario_en_pose`,
    `peto_no_aislado`, `casco_contrario`.

Validacion:

- `compileall pose_analyzer.py views\signals_view.py`: OK.
- Import app: OK, 137 callbacks.
- `pytest -q`: OK.
- `test_app_flow.py`: 28/28.
- `test_s105_load.py --skip-video`: OK.

Riesgo residual:

- En videos con muchos cruces puede bajar mucho la cobertura de rojo. Es
  preferible a una medicion falsa, pero requiere explicacion clara en UI y,
  como siguiente paso, suavizado temporal mas fuerte.

Refinamiento tras revisar frames del usuario:

- Se agrego overlap corporal en `pose_analyzer.py`.
- Los frames destacados ahora excluyen frames contaminados/oclusos.
- Validacion larga con `videoplayback.mp4` selecciono keyframes limpios en:
  `48.5s`, `86.0s`, `95.0s`, `127.5s`, `136.5s`, `201.0s`.
- Los momentos ~`241.7s` y ~`289.2s` quedaron fuera de la galeria destacada.
