# CombatIQ - Memoria Replay, Senales Y Exportes

Fecha de inicio: 2026-05-21

## Archivos Criticos

- `views/signals_view.py`
- `report_utils.py`
- `app.py`
- `ui_charts.py`
- `data/ecg/`
- `data/imu/`

## Reglas Aprendidas

- Las notas de sesion que deben aparecer en Replay deben empezar con
  `"Combat Monitor"`.
- Esa misma condicion tambien afecta carga automatica de IMU en replay.
- No tocar esa regla sin revisar ambos flujos.
- El cursor de sincronizacion video-sensores necesita placeholder estable si
  el JS espera `shapes[0]`.

## Exportes

Regla de calidad:

- PDF/XLSX/CSV son parte de la demo y deben verse profesionales.
- Deben incluir contexto, unidades, tablas claras y explicaciones.
- No deben inventar datos si faltan registros.
- Deben validar permisos en servidor.

Checklist de auditoria:

- [ ] PDF atleta.
- [ ] PDF equipo.
- [ ] PDF biomecanico individual.
- [ ] PDF rojo vs azul.
- [ ] XLSX/CSV ECG.
- [ ] XLSX/CSV IMU.
- [ ] Exportes historicos/comparativos.
- [ ] Dependencias opcionales: `reportlab`, `openpyxl`, `kaleido`.

## Riesgos

- `signals_view.py` es una de las vistas mas grandes y con mayor riesgo de
  lentitud.
- El usuario reporta congelamientos ocasionales.
- Graficas en modo claro pueden conservar fondos/leyendas oscuras.

## 2026-05-21 - Replay IA Por Rol

Contexto:

- Replay debe servir tanto al atleta como al coach.
- La IA de eventos, ECG e IMU no debe asumir siempre que habla al coach.

Cambios:

- `views/signals_view.py` obtiene el rol y nombre del visor desde Flask session
  antes de llamar a la IA de Replay.
- `ai_insights.analyze_combat_session()` acepta `audience` y `viewer_name`.
- `ai_insights.analyze_event_frame()` acepta `audience`, `athlete_name` y
  `viewer_name`.
- Los prompts ahora distinguen si la explicacion debe convertirse en plan para
  el atleta o en decisiones para el coach.

Validacion:

- `compileall`: OK.
- `pytest -q`: `1 passed`.
- `test_app_flow.py`: `28/28`.

Pendiente:

- Auditar si los eventos detectados deben incorporar de forma mas explicita la
  estructura real de rounds, descanso y objetivo analizado (rojo/azul) en el
  panel visible.
- Resuelto en codigo: `detect_video_events()` ya acepta `target_vest` y
  `signals_view.py` lo deriva de `pose-target-select` para pasar "rojo" o
  "azul" al prompt de vision.

## 2026-05-21 - Verificacion De Prompt Maestro Actualizado

Contexto:

- El usuario actualizo el proyecto y pidio revisar el prompt maestro para saber
  desde donde retomar.

Comprobacion:

- El prompt maestro raiz declara `detect_video_events()` con `target_vest`.
- El codigo confirma la implementacion:
  `ai_insights.detect_video_events(..., target_vest="azul")` y
  `views/signals_view.py` pasa el valor desde `pose-target-select`.
- Validaciones ejecutadas: `compileall`, `pytest -q`, `test_app_flow.py` y
  `test_s105_load.py --skip-video`, todas OK.

Riesgo residual:

- Validar visualmente con video real que el prompt de vision respeta rojo/azul.
- Integrar mejor rounds/descansos en el panel visible de eventos si se decide
  priorizar Replay durante Sprint 5 o Sprint 7.

## 2026-05-21 - Cache Ligera Compartida Con Biomecanica

Contexto:

- Biomecanica, Replay y exportes se cruzan en `signals_view.py`.
- Al optimizar congelamientos no se debe romper la descarga PDF ni la
  simulacion ECG/IMU derivada del analisis de pose.

Decision:

- El resultado pesado de pose vive temporalmente en servidor.
- `pose-results` conserva solo una version ligera con `job_id`.
- Cualquier callback que necesite el informe completo debe llamar al resolver
  de cache antes de trabajar.

Impacto:

- `populate_sim_from_pose` resuelve el reporte completo antes de generar la
  simulacion.
- `download_pose_report` resuelve el reporte completo antes del PDF.
- `save_pose_session` resuelve el reporte completo si esta disponible.

Riesgo residual:

- Si la cache expira o se reinicia el servidor, el usuario debe repetir el
  analisis para exportar desde ese resultado.
- En produccion conviene reemplazar la cache local por backend persistente de
  jobs.

## 2026-05-21 - Cache IA Vision Replay

Contexto:

- La IA de vision en Replay es util, pero cara/lenta si se repite sin necesidad.

Decision:

- Cachear deteccion de eventos de video por video, peto objetivo y eventos IMU.
- Cachear analisis de fotograma por hash del frame, evento y audiencia.

Impacto:

- Revisar el mismo evento o volver a seleccionar la misma sesion no deberia
  repetir la llamada de Claude mientras la cache siga viva.
- Mantiene el mismo resultado visible y el mismo lenguaje por rol.

Riesgo residual:

- TTL de la cache IA: 10 minutos.
- En produccion seria mejor distinguir metricas de cache hit/miss.

## 2026-05-22 - Procesamiento ECG Reutilizable

Contexto:

- Replay y Senales comparten archivos ECG de sesion.
- La lectura de archivo ya estaba cacheada, pero el suavizado y picos R se
  recalculaban en varias rutas con los mismos parametros.

Decision:

- Cachear el procesamiento ECG derivado por archivo/parametros, sin alterar
  datos ni umbrales.

Impacto:

- Mover la ventana del ECG, cargar una sesion y exportar con los mismos
  parametros debe evitar trabajo repetido.
- Mantiene el mismo resultado numerico y visual.

## 2026-05-22 - Fotogramas Replay Reutilizables

Contexto:

- El panel de IA visual extrae un frame del video al hacer clic en un evento.
- La respuesta de IA ya esta cacheada, pero extraer el frame repetido tambien
  costaba I/O y decodificacion.

Decision:

- Cachear el frame JPEG base64 por video, timestamp y estado del archivo.

Impacto:

- Repetir clic en la misma jugada evita reabrir el video.
- La cache se invalida si el archivo cambia por `mtime` o tamano.

Pendiente:

- Validar manualmente con video real subido, porque no habia archivo de video
  disponible en el workspace durante esta pasada.
