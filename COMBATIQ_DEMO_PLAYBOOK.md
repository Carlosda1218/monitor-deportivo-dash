# CombatIQ Demo Playbook

Guia externa para presentar CombatIQ sin crear una pestana de inversor dentro
de la app. La app debe hablar como producto para atletas y coaches; la lectura
comercial se explica fuera, usando las pantallas actuales como evidencia.

## Principios de demo

- No mostrar "inversor" como rol o seccion de producto.
- Hablar de valor con pantallas reales: equipo, check-in, sensores, analisis,
  historial, reportes y comunicacion.
- Separar siempre datos demo de datos reales: usar "datos realistas de ejemplo"
  cuando sea una cuenta de demostracion.
- No prometer diagnostico clinico. CombatIQ apoya decisiones de entrenamiento.
- No vender IA como magia: venderla como lectura contextual de senales, historial
  y carga.
- Si preguntan por base de datos, responder que SQLite sirve para demo/MVP y que
  la migracion se hara cuando el producto requiera concurrencia, backups,
  multi-sede o despliegue estable.

## Ruta sugerida de presentacion

1. Login
   - Mostrar foco: taekwondo y boxeo.
   - Entrar con Coach Boxeo o Coach Taekwondo.

2. Panel del coach
   - Explicar que el coach no mira numeros sueltos: mira a quien revisar,
     quien llega listo y quien necesita ajuste.
   - Mostrar plantilla, check-ins, alertas, competencias y sensores.

3. Competencias
   - Mostrar agenda del equipo.
   - Explicar taper y preparacion por deportista.

4. Monitor de combate
   - Mostrar rounds oficiales por deporte.
   - Explicar ECG + IMU como base de lectura durante combate/sesion.

5. Senales ECG / IMU
   - Mostrar replay, eventos e interpretacion.
   - Enfatizar que la lectura gana valor con video, sensores y contexto.

6. Historial y reportes
   - Mostrar sesiones guardadas, export Excel/PDF e informes.
   - Mensaje comercial: el dato sale ordenado, compartible y explicable.

7. Vista atleta
   - Mostrar check-in, dashboard, sesion del dia y comunicacion con coach.
   - Mensaje: el atleta entiende que hacer hoy sin leer un tablero tecnico.

8. Sobre CombatIQ
   - Cerrar con posicionamiento: no es fitness generico; es una app para
     taekwondo y boxeo con seguimiento de rendimiento.

## Lectura por rol

Atleta de alto rendimiento:
- Quiere saber como llega hoy, que debe ajustar y si su cuerpo responde mejor.
- Valora claridad, tendencia y feedback que pueda aplicar antes de entrenar.

Coach:
- Quiere decidir rapido a quien apretar, a quien cuidar y que revisar.
- Valora plantilla, check-ins, sensores, comunicacion y reportes compartibles.

Inversor:
- No necesita una pestana propia.
- Necesita ver foco, diferenciacion, datos capturados, repeticion de uso,
  reportes profesionales, potencial de sensores y ruta clara a escalabilidad.

## Puntos fuertes actuales

- Foco deportivo claro: taekwondo y boxeo.
- Flujos separados para atleta y coach.
- Monitor de combate con rounds por deporte.
- ECG/IMU, replay, historial y exports.
- Comunicacion coach-atleta y comunicados de equipo.
- Datos demo realistas para mostrar escenarios concretos.

## Riesgos que no debemos esconder

- Sensores reales y hardware requieren validacion de entorno antes de demo en vivo.
- SQLite es suficiente para esta fase, pero no debe venderse como arquitectura
  final multi-cliente.
- La IA debe presentarse como apoyo explicativo, no como diagnostico ni sustituto
  del criterio del coach.
- Si la demo sera remota o con varios usuarios simultaneos, conviene probar
  servidor, red, carga y subida de video por separado.

## Siguiente sprint recomendado

1. Revisar flujo atleta completo con el mismo rigor usado en coach.
2. Revisar reportes PDF/XLSX desde el punto de vista de entrega comercial.
3. Fortalecer sensores, replay y pose analyzer como nucleo diferencial.
4. Preparar checklist tecnico para demo en vivo: servidor, red, datos, videos,
   cuentas demo, exports y plan de contingencia.
