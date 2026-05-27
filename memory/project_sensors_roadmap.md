# CombatIQ - Memoria Sensores, Hardware Y Mediciones

Fecha de inicio: 2026-05-21

## Objetivo

Revisar si CombatIQ esta preparado para hardware real y dejar una ruta clara
para sensores sin retrasar la demo.

## Estado Declarado Por El Usuario

- IMU custom BLE mediante `hub/`.
- API REST para sensores.
- CSV ECG/IMU.
- `sensor_sessions` y migracion declarada hasta 180.
- Pipeline de demo con `hub --demo`.
- Posible futuro: hardware comercial, prototipo ESP32/IMU o ruta hibrida.

## Archivos Criticos

- `sensors.py`
- `hub/config.py`
- `hub/ble_scanner.py`
- `hub/hub.py`
- `hub/imu_processor.py`
- `db.py`
- `app.py`
- `views/sensors_view.py`
- `views/signals_view.py`
- `test_sensor_hw.py`

## Checklist De Auditoria

- [ ] Confirmar sensores soportados hoy.
- [ ] Confirmar aliases y normalizacion.
- [ ] Confirmar API `/api/sensor-ping`.
- [ ] Confirmar API `/api/sensor-data`.
- [ ] Confirmar API `/api/sensor-status/<user_id>`.
- [ ] Revisar guardado de datos brutos vs procesados.
- [ ] Revisar latencia/frecuencia de muestreo.
- [ ] Revisar desconexion/reconexion.
- [ ] Revisar mensajes UI.
- [ ] Revisar plan B para demo.
- [ ] Revisar seguridad si se expone fuera de localhost.

## Decision De Producto Actual

Para inversores conviene mostrar la ruta mas estable y defendible, no la mas
ambiciosa:

- Demo controlada con IMU custom o simulador.
- CSV/video precargados como plan B.
- Hardware comercial solo si expone CSV/API fiable.
- Sensor propio si aporta diferenciacion real sin retrasar la demo.

## Riesgos

- Wearables cerrados pueden no dar acceso suficiente.
- BLE directo de bandas comerciales puede requerir adaptador especifico.
- Si se exponen endpoints sin token adecuado, hay riesgo de seguridad.
- Si se guardan metricas agregadas sin raw data, algunos analisis futuros
  pueden quedar limitados.

## 2026-05-21 - Estado Verificado Contra Prompt Maestro

Contexto:

- El prompt maestro actualizado marca Sprint 3 como completado.

Estado:

- Se mantiene como completado para demo controlada.
- Hardware listo hoy: IMU custom BLE/API/CSV y simulador/demo.
- Hardware parcialmente listo: ECG/HR por CSV o API de metricas.
- Hardware a evaluar: BLE comercial directo, wearables cerrados y estrategia
  hibrida con hardware comercial mientras madura sensor propio.

Validacion:

- `compileall` general: OK.
- `test_s105_load.py --skip-video`: OK en parse ECG y queries criticas DB.

Riesgo residual:

- No se probo hardware fisico real en esta verificacion.
- Antes de demo fuera de localhost, revisar token/API, red, permisos, bateria,
  dongle BLE y plan B con datos precargados.
