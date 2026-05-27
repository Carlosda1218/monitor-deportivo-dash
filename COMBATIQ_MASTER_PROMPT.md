# CombatIQ Master Prompt

Este documento es la guia maestra de trabajo para CombatIQ. Su objetivo es
mantener continuidad entre sesiones, sprints y decisiones tecnicas sin perder
el foco: preparar la aplicacion para una presentacion seria ante deportistas,
coaches e inversores, conservando la logica funcional actual y reduciendo
riesgos de errores graves.

## Contexto Del Producto

CombatIQ es una aplicacion enfocada especificamente en boxeo y taekwondo.
No debe evolucionar como una app deportiva generica. Toda decision de producto,
interfaz, analisis y sensores debe poder explicarse desde esos dos deportes.

La app debe servir a tres perfiles principales:

- Atleta de boxeo y taekwondoin de alto rendimiento.
- Coach de boxeo y coach de taekwondo.
- Inversor o persona del medio evaluando potencial comercial, diferenciacion y madurez.

El objetivo de corto plazo es dejar una version limpia, estable, presentable y
creible para venta o demostracion. El objetivo de mediano plazo es fortalecer
la ventaja tecnica: sensores, graficas, analisis, pose_analyzer, IA y APIs.

## Principios No Negociables

- No romper logica que ya funciona.
- Tocar solo lo necesario para corregir errores, limpiar suciedad real o reducir riesgo.
- No hacer refactors grandes por gusto antes de tener una razon clara y validada.
- No eliminar cambios existentes del usuario ni datos locales sin permiso explicito.
- No esconder errores importantes con `except Exception` silenciosos si afectan datos, sensores, reportes o UX critica.
- Separar fallos reales de deuda estetica o texto corrupto que no rompe flujo.
- Priorizar estabilidad demostrable sobre perfeccion interna.
- Cada cambio relevante debe terminar con validacion ejecutada.
- Cada decision debe poder defenderse ante un inversor, un coach y un atleta.

## Fase 1: Auditoria Y Limpieza Tecnica

Esta fase busca hallar errores, discrepancias, codigo sucio y riesgos antes de
planear nuevas funciones biomecanicas.

Orden recomendado:

1. Revisar estructura, imports, dependencias y puntos de entrada.
2. Verificar compilacion e importacion de modulos principales.
3. Revisar `db.py`: migraciones, contratos de datos, permisos, integridad y queries.
4. Revisar `app.py`: rutas, callbacks globales, layout, auth, descargas, APIs y errores ocultos.
5. Revisar `views/`: signals, analysis, compare, sensors.
6. Revisar `pages/`: dashboard, wellbeing, sesiones, chat, onboarding, auth.
7. Revisar sensores y hub BLE.
8. Revisar `pose_analyzer.py`, `ai_insights.py`, `report_utils.py` y exportes.
9. Revisar assets criticos, PWA y archivos JS/CSS que afecten la demo.
10. Registrar hallazgos con severidad, impacto y propuesta de correccion.

Categorias de hallazgos:

- Bloqueador: impide arrancar, compilar, importar, guardar o exportar.
- Critico: puede perder datos, mentir al usuario, romper sensores o crear una mala demo.
- Alto: flujo importante degradado, permisos incorrectos, dependencia faltante, error silenciado.
- Medio: deuda tecnica visible, duplicacion riesgosa, texto corrupto importante, UX confusa.
- Bajo: limpieza, nombres, comentarios, pequenos detalles sin impacto inmediato.

Regla de rendimiento:

- En cada bloque se deben buscar causas de lentitud o congelamiento.
- Priorizar callbacks con loops, consultas N+1, lecturas repetidas de CSV/video, IA sin timeout y polling.
- Optimizar solo si es seguro: bulk queries, cache ligero, limites, validacion previa o evitar recalculo.
- No sacrificar exactitud biomecanica ni permisos por velocidad.
- Toda optimizacion debe conservar la salida funcional o documentar claramente el tradeoff.

Definicion de terminado en Fase 1:

- La app compila con el entorno `.venv`.
- Los modulos principales importan sin errores.
- Las pruebas disponibles corren o se documenta por que no aplican.
- Exportes PDF/XLSX criticos funcionan.
- Scripts operativos se distinguen de tests automatizados.
- Dependencias usadas por el codigo estan declaradas.
- Los errores corregidos tienen validacion concreta.
- Queda una lista clara de riesgos residuales.

Comandos base de validacion:

```powershell
.\.venv\Scripts\python.exe -m compileall app.py db.py analysis_engine.py ai_insights.py notifications.py pose_analyzer.py questionnaires.py report_utils.py sensors.py ui_charts.py pages views hub
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe test_s105_load.py --skip-video
```

## Fase 2: Simulacion De Roles Y Validacion De Producto

En esta fase se evalua la aplicacion como si fueramos usuarios reales y un
inversor. No se deben proponer funciones por moda; cada idea debe responder a
una necesidad concreta del perfil.

Roles a simular:

- Atleta de boxeo de alto rendimiento.
- Taekwondoin de alto rendimiento.
- Coach de boxeo.
- Coach de taekwondo.
- Inversor del medio deportivo, tecnologico o health/performance.

Preguntas para atleta:

- Entiendo rapidamente mi estado fisico y tecnico?
- Se que debo hacer hoy o manana para mejorar?
- Confio en que los datos no son ruido decorativo?
- Veo diferencia entre boxeo y taekwondo?
- La app me ahorra tiempo o me da claridad que no tenia?

Preguntas para coach:

- Puedo revisar a mi equipo rapido?
- Puedo detectar fatiga, riesgo o mejora tecnica sin abrir veinte pantallas?
- Puedo comparar sesiones y tomar decisiones de carga?
- Puedo explicar los datos al atleta sin sonar como tecnico de laboratorio?
- El flujo me sirve en entrenamiento real, no solo en oficina?

Preguntas para inversor:

- El producto se entiende en menos de cinco minutos?
- Hay diferenciacion clara frente a apps genericas de fitness?
- La demo muestra datos vivos, sensores, graficas y analisis accionable?
- Se ve escalable a gimnasios, academias, equipos o federaciones?
- La experiencia parece confiable, pulida y comercializable?

Salida esperada de Fase 2:

- Lista de fortalezas actuales.
- Lista de huecos por rol.
- Priorizacion de mejoras antes de demo.
- Decisiones sobre que se queda, que se simplifica y que se fortalece.
- Narrativa de producto para presentacion.

## Fase 3: Fortaleza Tecnica Y Diferenciacion

Esta fase fortalece lo que puede volver a CombatIQ dificil de copiar:

- Sensores reales y simulados.
- Graficas claras y accionables.
- Analisis tecnico por deporte.
- Pose analyzer para biomecanica.
- IA para feedback contextual.
- APIs externas solo si aportan valor demostrable.
- Reportes profesionales para coach, atleta e inversor.

Regla de implementacion:

- Primero estabilidad.
- Despues claridad de datos.
- Despues analisis accionable.
- Despues automatizacion con IA.
- Despues pulido visual y storytelling de demo.

## Arquitectura De Datos Y Migracion Futura

La app puede seguir usando SQLite durante la fase de estabilizacion y demo,
pero todas las decisiones nuevas deben tener en mente una migracion futura a
una base de datos mas robusta para produccion, por ejemplo PostgreSQL,
Supabase, Railway, Render Postgres u otro servicio SQL administrado.

Regla principal:

- No migrar la base de datos antes de terminar la limpieza critica y tener una
  razon operativa clara.
- No acoplar nuevas funciones a detalles innecesarios de SQLite.
- Mantener `db.py` como frontera de acceso a datos mientras no exista una capa
  formal de repositorios/servicios.
- Evitar escrituras directas a la DB desde vistas o callbacks si se puede
  encapsular en funciones de `db.py`.
- Toda migracion debe ser idempotente, versionada, reversible cuando sea
  razonable y probada contra copia de datos.
- Antes de migrar, definir estrategia de backup, seed/demo data, variables de
  entorno, pool/conexiones, indices y rollback.
- Priorizar SQL portable: tipos simples, queries parametrizadas, nombres de
  columnas claros y evitar dependencias innecesarias de PRAGMA/rowid.
- Identificar desde Fase 1 que tablas, indices y queries ya requieren ajuste
  para escalar a multiples coaches, equipos, sesiones, sensores y archivos.

Criterios para decidir migracion:

- La demo requiere multiples usuarios concurrentes o acceso remoto estable.
- SQLite empieza a generar locks, lentitud o limitaciones operativas.
- Necesitamos separar ambientes: demo, staging y produccion.
- Queremos auditoria, backups, dashboard de datos o integracion externa real.
- La carga de sensores/sesiones crece y exige indices, concurrencia o pooling.

No objetivo inmediato:

- No reescribir toda la capa de datos por arquitectura ideal.
- No cambiar esquema productivo sin respaldo y prueba de migracion.
- No bloquear la preparacion para inversores por una migracion prematura.

## Fase UI: Interfaz, Claridad Y Presentacion

Esta fase es condicionada: se ejecuta solo cuando una mejora visual o de
interaccion aporte claridad, confianza, impacto comercial o facilidad de uso.
La UI actual ya se considera una base valida: se ve digerible, los colores
concuerdan y no debe redisenarse por gusto.

Objetivo:

- Mejorar posicion, jerarquia, legibilidad o impacto de elementos especificos.
- Hacer mas clara la experiencia para atleta, coach e inversor.
- Aumentar la percepcion de producto profesional sin romper patrones actuales.
- Detectar pestanas, botones, graficas o tarjetas que puedan comunicar mejor.

Reglas:

- No redisenar pantallas completas salvo que haya una razon fuerte.
- Mantener el lenguaje visual actual si ya funciona.
- No cambiar colores, layouts o navegacion solo por novedad.
- Priorizar claridad sobre decoracion.
- Si una pestana, metrica o accion clave puede destacar mejor, proponerlo con razon.
- Toda mejora UI debe tener impacto explicable: escaneo mas rapido, menos confusion,
  mejor narrativa de demo, mejor lectura de datos o mayor confianza.

Checklist UI por rol:

- Atleta: entiende que debe mirar primero y que accion tomar.
- Coach: puede escanear equipo, alertas y sesiones rapidamente.
- Inversor: percibe foco, madurez, diferenciacion y producto vendible.

Preguntas antes de tocar UI:

- Esta pantalla ya cumple su funcion?
- Que elemento compite innecesariamente por atencion?
- Que dato deberia ser mas visible para boxeo o taekwondo?
- Esta mejora ayuda en una demo real?
- Hay riesgo de romper consistencia visual existente?

## Sprint Permanente: Calidad De Exportes E Informes

Los archivos generados por CombatIQ son parte de la demo y de la confianza del
producto. Cada PDF, XLSX, CSV o imagen debe sentirse como una salida profesional
que un coach pueda entregar, un atleta pueda entender y un inversor pueda evaluar.

Reglas:

- Todo export debe validar permisos en servidor; no confiar solo en dropdowns o stores del cliente.
- Los Excel deben salir como tablas organizadas, con encabezados claros, filtros, columnas legibles, unidades y metadatos.
- Los PDFs deben explicar que mide cada grafica, que significan las metricas y que limitaciones tiene la lectura.
- Los nombres de archivo deben ser claros, consistentes y contener contexto suficiente sin exponer informacion innecesaria.
- Si un boton dice Excel, PDF, CSV o PNG, el archivo descargado debe coincidir con esa promesa.
- Si falta una dependencia opcional como `kaleido` o `reportlab`, el mensaje debe ser accionable y no parecer un fallo silencioso.
- Los exports de sensores deben aclarar si los datos son brutos, procesados, estimados o solo apoyo para decision del coach.
- No generar datos falsos para rellenar informes; si falta informacion, explicarlo con una fila o bloque de "sin registros".
- Mantener compatibilidad con Excel/LibreOffice cuando sea razonable.

Checklist minimo por export:

- Permisos: atleta propio, coach solo roster, admin segun corresponda, inversor sin datos privados salvo pantalla autorizada.
- Formato: tabla/filtros para XLSX; estructura con titulo, resumen, metricas, grafica y notas para PDF.
- Contexto: atleta, deporte, sesion o periodo, fecha de exportacion y fuente de datos.
- Calidad: columnas con unidades, textos legibles, sin labels que prometan otro formato.
- Validacion: abrir/generar bytes reales en prueba de humo y confirmar extension/contenido basico.

## Modo De Trabajo Por Sprints

Cada sprint debe tener:

- Objetivo unico y claro.
- Archivos o modulos a tocar.
- Riesgos conocidos.
- Criterio de terminado.
- Validaciones obligatorias.
- Resultado entendible para el usuario.

Formato recomendado de sprint:

```text
Sprint N - Nombre
Objetivo:
Alcance:
No alcance:
Archivos principales:
Riesgos:
Tareas:
Validacion:
Resultado esperado:
```

Reglas de ejecucion:

- Antes de editar, revisar contexto local.
- Antes de cambios grandes, explicar el plan.
- Durante el trabajo, informar avances breves.
- Despues de editar, validar.
- Al final, resumir cambios, pruebas y riesgos.
- Si se necesita informacion externa, pedir permiso y explicar por que.
- Cada cambio, correccion o decision relevante debe actualizar tambien este
  prompt maestro: checklist completado, estado actual, riesgos, observaciones
  o siguiente paso. La memoria del proyecto no debe quedar desfasada respecto
  al codigo real.

## Roadmap Operativo De Sprints Y Checklists

Esta seccion funciona como memoria practica del proyecto. El orden puede
ajustarse si aparece un bug critico, pero la prioridad general es: medicion
confiable, explicacion accionable, escalabilidad de sensores y demo clara.

### Sprint B0 - Blindaje De Biomecanica Individual

Objetivo:

- Que el analisis individual de un atleta no mida arbitro, publico ni puntos
  contaminados como si fueran del deportista.

Alcance:

- Selector de objetivo: Automatico, Peto rojo, Peto azul, Atleta izquierda,
  Atleta derecha.
- Deteccion multipersona por frame.
- Filtro por color de peto con OpenCV.
- Limpieza de landmarks sospechosos.
- Confianza por objetivo, frame y articulacion.

Checklist:

- [x] Agregar selector de objetivo antes de analizar.
- [x] Permitir `num_poses` mayor a 1.
- [x] Elegir pose por color/posicion/visibilidad.
- [x] Evitar falsos positivos de arbitro para peto rojo.
- [x] Evitar falsos positivos de arbitro para peto azul.
- [x] Ocultar landmarks contaminados por personas externas.
- [x] Excluir angulos corruptos del resumen.
- [x] Reforzar seguimiento temporal para no elegir desde cero cada frame.
- [x] Penalizar saltos bruscos de centro corporal o tamano de bbox.
- [ ] Mostrar en UI articulaciones confiables vs no confiables.
- [ ] Mostrar datos usados vs descartados de forma clara.
- [ ] Validar con videos malos: arbitro, publico, petos tapados, baja luz,
  camara movida y cuerpo parcialmente fuera de cuadro.

Criterio de terminado:

- El usuario puede elegir rojo/azul/izquierda/derecha y ver una lectura que
  prefiera descartar datos dudosos antes que entregar una medicion falsa.

Validacion:

- `python -m compileall pose_analyzer.py views/signals_view.py app.py`
- Ruta `/analyze-pose` con `target=red` y `target=blue`.
- `pytest -q`.
- Prueba visual del frame anotado.

### Sprint B1 - Seguimiento Temporal Fuerte

Objetivo:

- Mantener el mismo atleta durante todo el video usando continuidad temporal.

Alcance:

- Bloquear el objetivo despues de los primeros frames confiables.
- Usar centro del cuerpo, bbox, color del peto, visibilidad y continuidad.
- Descartar frames donde el objetivo salta a otra persona.
- Suavizar curvas sin ocultar cambios reales.

Checklist:

- [x] Crear score de continuidad entre frames.
- [x] Guardar `track_id` logico para el objetivo seleccionado.
- [x] Penalizar saltos de posicion imposibles.
- [x] Penalizar cambios bruscos de escala corporal.
- [x] Mantener lock por color de peto cuando haya oclusion breve.
- [x] Permitir recuperar el objetivo despues de perderlo.
- [x] Marcar segmentos con baja confianza, no mezclar con datos limpios.
- [x] Calcular confianza final por video: Alta, Media, Baja.

Criterio de terminado:

- En un video con arbitro y dos atletas, la seleccion no cambia de persona por
  frames aislados ni por oclusiones cortas.

### Sprint B2 - IA De Coaching Biomecanico Accionable

Objetivo:

- Que la IA explique la grafica en lenguaje deportivo y proponga acciones,
  ejercicios y limites de interpretacion.

Alcance:

- Explicacion de la grafica.
- Significado para boxeo o taekwondo.
- Recomendaciones concretas.
- Ejercicios de apoyo con dosis.
- Nivel de confianza y limitaciones del video.

Checklist:

- [x] Crear IA local de coaching biomecanico sin depender de API externa.
- [x] Mostrar confianza de lectura en UI.
- [x] Explicar frames usados, descartados y landmarks dudosos.
- [x] Recomendar ejercicios segun deporte y metrica.
- [x] Incluir la lectura de coaching en PDF.
- [ ] Separar salida por rol: atleta, coach y demo/inversor.
- [ ] Mejorar ejercicios por caso: chamber, retorno de guardia, base,
  transferencia de peso, movilidad, reactividad.
- [ ] Agregar micro-plan de 48-72h cuando la confianza sea suficiente.
- [ ] Integrar API de IA solo cuando los datos locales esten limpios y haya
  una razon clara de valor.

Regla de IA:

- La IA no debe sonar como laboratorio. Debe decir: que muestra la grafica,
  que significa en combate, que tan confiable es, que hacer ahora, que no se
  debe concluir con ese video y que ejercicio aplicar.

### Sprint B3 - Modo Combate Rojo Vs Azul

Objetivo:

- Analizar dos atletas a la vez para entender la relacion del combate, no solo
  la tecnica individual.

Alcance:

- Track simultaneo de peto rojo y peto azul.
- Comparacion de distancia, guardia, desplazamiento, simetria y amplitud.
- Deteccion de momentos de intercambio.
- Lectura tactica para coach.

Checklist:

- [x] Crear modo separado: "Analizar combate rojo vs azul".
- [x] Detectar y mantener dos tracks simultaneos.
- [x] Calcular distancia entre centros/torso.
- [x] Calcular avance, retroceso y cambios de distancia.
- [x] Detectar posibles intercambios por cercania y movimiento simultaneo.
- [x] Comparar amplitud de pierna y actividad por lado.
- [x] Comparar estabilidad/base despues de intercambio.
- [x] Generar lectura tipo: quien presiona, quien controla distancia, quien
  retrocede, cuando aparece intercambio.
- [x] Exportar resumen en PDF con grafica de distancia y eventos.

No alcance inicial:

- No declarar ganador ni juzgar puntos oficiales.
- No reemplazar analisis tactico del coach.
- No prometer precision de VAR con video no profesional.

Criterio de terminado:

- El modo explica de forma entendible la dinamica rojo vs azul y marca sus
  limitaciones segun calidad de video.

### Sprint B4 - Calidad Visual De Graficas Biomecanicas

Objetivo:

- Que la grafica ayude y no confunda.

Checklist:

- [x] Separar tren inferior y tren superior si demasiadas lineas saturan.
- [x] Marcar frames descartados o baja confianza.
- [x] Mostrar resumen textual al lado de la grafica.
- [x] Agregar leyenda corta: "que mirar primero".
- [x] Evitar que curvas incompletas parezcan error.
- [x] Permitir ver solo rodilla/cadera/codo/hombro.
- [x] En PDF, explicar la grafica antes de la tabla.

### Sprint H1 - Auditoria De Hardware Y Sensores

Objetivo:

- Confirmar si CombatIQ esta preparado para conectarse a hardware real sin
  retrasar el desarrollo de biomecanica.

Preguntas clave:

- Que sensores soporta hoy el codigo: BLE, IMU, ECG, archivos CSV, simulador?
- Que endpoints/API existen para conectar sensores?
- Que parte es demo/simulada y que parte es hardware real?
- Que latencia y frecuencia de muestreo soportamos?
- Donde se guardan los datos y como se sincronizan con sesiones?
- Que pasa si se desconecta un sensor en mitad de sesion?
- Hay permisos, emparejamiento, reconexion y mensajes claros?

Checklist:

- [x] Revisar `views/sensors_view.py`.
- [x] Revisar `hub/` y scripts BLE.
- [x] Revisar endpoints de sensores en `app.py`.
- [x] Revisar modelo de datos para sensores en `db.py`.
- [x] Probar flujo simulado de sensor.
- [x] Probar import/export de ECG/IMU.
- [x] Identificar hardware ya compatible o facilmente integrable.
- [x] Separar "hardware listo", "hardware experimental" y "solo demo".
- [x] Definir estrategia de fallback: CSV, simulador, video o datos grabados.
- [x] Crear checklist de demo en vivo con sensores: bateria, red, permisos,
  dongle BLE, plan B y datos precargados.

Opciones de hardware a evaluar:

- Hardware existente via BLE si expone datos accesibles y documentados.
- Sensores comerciales con export CSV/API si integran rapido.
- Prototipo propio ESP32/IMU si se necesita control total.
- Banda ECG/HR compatible si reduce riesgo frente a fabricar hardware.
- Estrategia hibrida: empezar con hardware comercial y dejar camino a sensor
  propio cuando el producto valide demanda.

Criterio de decision:

- Para demo/inversores conviene la ruta mas estable y demostrable, no la mas
  ambiciosa. Si hardware comercial fiable permite mostrar valor antes, se
  prioriza. Sensor propio solo si aporta diferenciacion real y no retrasa la
  demo.

Resultado H1:

- Hardware listo hoy: IMU custom por BLE Nordic UART mediante `hub/`, API REST
  `/api/sensor-ping` y `/api/sensor-data`, importacion CSV de ECG/IMU, exportes
  XLSX/PDF y simulador `test_sensor_hw.py`.
- Hardware parcialmente listo: ECG/HR por CSV o API de metricas; BLE directo de
  bandas comerciales tipo Polar/Garmin/Apple requiere adaptador especifico.
- Hardware experimental: `HR_WRIST` y cualquier wearable cerrado sin CSV/API
  accesible.
- Solo demo/fallback: `hub --demo`, archivos precargados en `data/ecg` y
  `data/imu`, replay de video, y datos de combate ya guardados.
- Se corrigio una discrepancia de aliases: `IMU_GLOVE`, `IMU_ARM` e `IMU` se
  normalizan a `IMU_WRIST`; `IMU_ANKLE`, `IMU_LEG` e `IMU_KICK` a `IMU_FOOT`;
  `HR`/`HEART_RATE` a `HR_WRIST`.
- Cuando el API recibe un sensor conocido, ahora lo asigna automaticamente al
  deportista para que el hardware no quede oculto en la UI de sensores.
- Para demo con inversores: llevar sensor IMU custom o simulador controlado,
  tener token API configurado si se expone en red, confirmar bateria/dongle BLE,
  probar ping antes de presentar y tener CSV/video precargado como plan B.

### Sprint H2 - Arquitectura Escalable De Datos De Sensores

Objetivo:

- Evitar que ECG/IMU/video crezcan como archivos sueltos imposibles de auditar.

Checklist:

- [ ] Definir entidad de sesion sensorial: atleta, coach, deporte, fecha,
  dispositivo, archivo fuente, calidad y metadatos.
- [ ] Separar datos brutos, procesados y resumen.
- [ ] Indexar por atleta, sesion, fecha y tipo de senal.
- [ ] Preparar migracion futura a Postgres/Supabase/Railway sin romper SQLite.
- [ ] Definir retencion, backup y limpieza de uploads.
- [ ] Asegurar que reportes indiquen fuente de datos.

## Politica De Cambios

Cambios permitidos sin consulta previa:

- Correcciones de sintaxis.
- Imports rotos.
- Dependencias faltantes ya usadas por el codigo.
- Pruebas de humo no invasivas.
- Mensajes de error mas claros.
- Manejo de errores que evita datos falsos o silencios peligrosos.
- Limpieza puntual de encoding cuando afecta UI, reportes o prompts.

Cambios que requieren pausa o confirmacion:

- Redisenar pantallas completas.
- Cambiar esquema de base de datos de forma irreversible.
- Eliminar datos, archivos de usuario o sesiones.
- Cambiar logica deportiva o umbrales biomecanicos.
- Introducir una API externa con coste, credenciales o dependencia fuerte.
- Cambiar el flujo de login, permisos o roles.
- Reemplazar la arquitectura de callbacks o vistas.

## Criterios De Calidad Para Inversores

La app debe transmitir:

- Estabilidad: no se rompe en flujos basicos.
- Claridad: cada grafica o metrica tiene utilidad evidente.
- Foco: boxeo y taekwondo se sienten especificos, no pegados encima.
- Profesionalismo: exportes, textos y UI no parecen prototipo descuidado.
- Trazabilidad: los datos guardados se pueden consultar, exportar y explicar.
- Diferenciacion: sensores, analisis y biomecanica son el centro, no adorno.
- Confianza: errores visibles se explican; errores ocultos se minimizan.

## Estado Actual Conocido

Actualizacion reciente de biomecanica y demo:

- La app ya usa la marca CombatIQ y assets de logo actualizados.
- El limite de video fue ampliado para permitir pruebas con videos mas grandes.
- `pose_analyzer.py` ahora soporta selector de objetivo: Automatico, Peto rojo,
  Peto azul, Atleta izquierda y Atleta derecha.
- El analizador ya pide multiples poses por frame y selecciona objetivo con
  color de peto, posicion, visibilidad y continuidad basica.
- Se corrigieron falsos positivos donde el arbitro era seleccionado como peto
  rojo o peto azul.
- Se agrego limpieza de landmarks contaminados por personas externas; los
  segmentos dudosos no se dibujan y sus angulos no contaminan el resumen.
- La salida biomecanica ya incluye confianza de seleccion, calidad de pose,
  frames usados, candidatos vistos, misses y advertencias de landmarks.
- Sprint B1 agrego seguimiento temporal fuerte con `track_id`, continuidad,
  rechazo de saltos de posicion/escala, razones de rechazo y continuidad en
  UI/PDF.
- La UI de biomecanica muestra objetivo analizado, confianza de seleccion y
  una tarjeta de IA de coaching.
- La IA local de coaching biomecanico explica la grafica, indica confianza,
  traduce significado deportivo, propone acciones y recomienda ejercicios.
- El PDF biomecanico ya incluye objetivo analizado y lectura de IA de coaching.
- Sprint B4 separo las graficas biomecanicas en tren inferior y tren superior,
  agrego guia de lectura, frames usados, continuidad, landmarks limpiados y
  rechazos de tracking para que los huecos no parezcan errores visuales.
- El PDF biomecanico explica como leer las graficas antes de mostrar la tabla
  de angulos.
- Sprint B3 agrego modo "Rojo vs azul" como selector de biomecanica: mantiene
  tracks simultaneos para ambos petos, calcula distancia normalizada, avances,
  retrocesos, posibles intercambios, tendencia de presion, amplitud comparada
  y una lectura tactica que no declara ganador ni puntos oficiales.
- El PDF del modo rojo vs azul incluye metricas tacticas, IA tactica, grafica
  de distancia con posibles intercambios y comparativa rojo/azul.
- En el video ruidoso de peor caso, el modo rojo vs azul funciona pero reporta
  confianza baja cuando solo logra pocos frames pareados; esto se considera
  comportamiento correcto porque evita maquillar datos insuficientes.
- Sprint H1 audito hardware/sensores y dejo el flujo mas honesto: IMU custom
  por BLE/API/CSV esta listo para demo controlada; ECG/HR comercial funciona
  por CSV/API de metricas pero BLE directo requiere adaptador; wearables cerrados
  quedan como experimental.
- Se agrego normalizacion de aliases de hardware y auto-asignacion de sensores
  conocidos al llegar ping/datos por API, evitando que un dispositivo real guarde
  datos pero no aparezca en la UI.
- El video ruidoso usado como peor caso queda como caso de prueba principal
  para seguir endureciendo mediciones.

Validaciones recientes confirmadas:

- `python -m compileall pose_analyzer.py views/signals_view.py app.py`.
- Ruta `/analyze-pose` probada con `target=red` y `target=blue`.
- Ruta `/analyze-pose` devuelve `track_id`, continuidad temporal y rechazos
  por tracking/color para `target=red` y `target=blue`.
- `pytest -q` pasa con la prueba de humo disponible.
- Pruebas visuales con video real confirmaron mejor seleccion de peto rojo y
  peto azul, y limpieza de landmarks contaminados.

Trabajo inmediato pendiente:

- Mostrar articulaciones confiables vs no confiables en UI de forma mas clara.
- Mejorar visualizacion de graficas para que no saturen al usuario.
- Separar lectura de IA por rol: atleta, coach y narrativa de demo.
- Auditar hardware/sensores para decidir entre hardware comercial, BLE/API,
  CSV, prototipo propio o estrategia hibrida.

Primera pasada quirurgica realizada:

- `report_utils.py` fue reconstruido porque no compilaba y bloqueaba exportes.
- `hub` fue ajustado para importar como paquete y manejar BLE de forma mas robusta.
- `requirements.txt` fue alineado con dependencias usadas por el codigo.
- `pytest.ini` y `test_smoke.py` fueron agregados para validacion minima estable.
- `views/analysis_view.py` ahora reporta advertencias si una sesion se guarda parcialmente.

Validaciones confirmadas:

- Compilacion de modulos clave.
- Importacion de app y modulos principales.
- `pytest -q` con prueba de humo.
- `test_s105_load.py --skip-video`.
- Generacion real de PDF y XLSX desde `report_utils.py`.

Riesgos residuales conocidos:

- Hay texto corrupto o mojibake en varios archivos.
- Hay muchos `except Exception` defensivos que pueden ocultar fallos.
- `app.py` y `db.py` son grandes y requieren revision linea por linea por bloques.
- Los scripts de prueba de hardware son operativos, no unit tests convencionales.
- La demo completa con servidor y video upload requiere validacion separada.
- Seguir auditando rendimiento en tabs pesadas: signals, compare, analysis, dashboard y wellbeing.
- Revisar endpoints/API de sensores antes de exposicion fuera de entorno local.

## Prompt Operativo Para Futuras Sesiones

Usar este prompt al retomar trabajo:

```text
Estamos trabajando en CombatIQ, una aplicacion para boxeo y taekwondo que debe
quedar lista para presentacion ante atletas de alto rendimiento, coaches e
inversores. La prioridad actual es estabilidad, limpieza y credibilidad sin
romper la logica funcional existente.

Trabaja por sprints. Antes de implementar, revisa el contexto local. Toca solo
lo necesario. No hagas refactors grandes sin razon. No borres datos ni cambios
del usuario. Clasifica hallazgos por severidad. Valida cada cambio con comandos
concretos. Mantén siempre presentes los roles: atleta de boxeo, taekwondoin,
coach de boxeo, coach de taekwondo e inversor.

Primero seguimos con Fase 1: auditoria linea por linea por bloques, correccion
de errores, limpieza funcional y puntos de lentitud/congelamiento. Despues pasaremos a Fase 2: simulacion de
roles y decisiones de producto. Luego Fase 3: sensores, graficas, analisis,
pose_analyzer e IA. La UI se revisara como fase condicionada cuando aporte
claridad, impacto de demo o confianza comercial sin redisenar por gusto.

Consulta `COMBATIQ_MASTER_PROMPT.md` al iniciar cada sesion.
```

## Actualizacion Operativa 2026-05-21

El usuario solicito una nueva ronda de auditorias exhaustivas, con revision
linea a linea y foco reforzado en el pilar central de CombatIQ:

- Analisis de combates.
- Sensores y mediciones.
- Biomecanica YOLO/OpenVINO/MediaPipe.
- IA contextual.
- Calidad de graficas, reportes y exportes.
- Rendimiento y congelamientos.
- Modo claro en UI, especialmente graficas o secciones que permanecen oscuras.

### Regla Nueva De Memoria

Desde 2026-05-21, cada modificacion, decision, observacion relevante,
optimizacion, riesgo, area de mejora o validacion debe registrarse tambien en
la memoria del proyecto.

La carpeta `memory/` fue creada porque el prompt actualizado del usuario ya la
mencionaba, pero no existia en esta copia local al iniciar la sesion.

Archivos de memoria operativa:

- `memory/project_combatiq_master.md`: reglas, estado general y decisiones.
- `memory/project_sprint_plan.md`: sprints activos, tareas y checklists.
- `memory/project_change_log.md`: cambios realizados, validaciones y riesgos.
- `memory/project_biomech_audit.md`: biomecanica, YOLO, MediaPipe, tracking e IA tactica.
- `memory/project_replay_lessons.md`: replay, senales, sincronizacion y exportes.
- `memory/project_ui_modernization.md`: UI, tema claro, graficas y demo.
- `memory/project_coach_sport_filter.md`: permisos/filtro por deporte de coaches.
- `memory/project_sensors_roadmap.md`: sensores, hardware, APIs, BLE, CSV y roadmap.

Formato minimo de registro:

- Fecha.
- Que se hizo o decidio.
- Por que importa.
- Archivos implicados.
- Validacion ejecutada o pendiente.
- Riesgo residual si existe.

### Estado Declarado Por El Usuario Para Verificar En Codigo

El usuario aporto un prompt maestro actualizado con estas declaraciones de
estado. Deben tratarse como contexto de trabajo y verificarse contra el codigo
durante la auditoria:

- Stack actual: Dash + Flask + SQLite, Python, OpenVINO, MediaPipe y
  supervision ByteTrack.
- `yolo_tracker.py` usa YOLOv8n-pose + OpenVINO + ByteTrack para velocidades
  y biomecanica Phase 1.
- `pose_analyzer.py` mantiene analisis individual y modo duel.
- `ai_insights.py` usa Claude API con coaching contextual y cache.
- `analysis_engine.py` cubre ACWR, HRV readiness y alertas cruzadas.
- `sensors.py` y `hub/` cubren IMU custom BLE y pipeline de sensores.
- `signals_view.py` concentra Replay, senales ECG/IMU y biomecanica.
- Las sesiones demo de Carlos Rios, id 21, son el caso principal de validacion.
- Las notas de sesion que deben aparecer en Replay deben empezar con
  `"Combat Monitor"`.
- La app puede ir lenta o congelarse en algunas pestanas.
- El modo claro puede dejar algunas graficas o secciones en oscuro.

### Siguiente Orden De Trabajo Acordado

1. Sprint 0: sincronizar memoria y prompt maestro.
2. Sprint 1: validacion base con compilacion y tests.
3. Sprint 2: auditoria biomecanica/combate linea a linea.
4. Sprint 3: auditoria sensores, hardware y mediciones.
5. Sprint 4: auditoria IA contextual.
6. Sprint 5: rendimiento, callbacks pesados y congelamientos.
7. Sprint 6: UI modo claro y pulido de demo.
8. Sprint 7: exportes profesionales PDF/XLSX/CSV.
9. Sprint 8: DB, roles, permisos y escalabilidad futura.

Si durante cualquier sprint aparece un bug real, se pausa el resto y se corrige
antes de continuar.

### Estado de sprints (actualizado 2026-05-21)

Sprint 0 (memoria + sync): COMPLETADO
Sprint 1 (compilacion + tests): COMPLETADO. compileall OK, pytest 1/1, test_app_flow 28/28.
Sprint 2 (biomecanica/combate): COMPLETADO.
  - yolo_tracker.py: import fix, GPU fallback, lectura secuencial video, validaciones VideoCapture,
    filtro de color de peto mas estricto, fallback posicion conservador, aliases azul/rojo.
  - pose_analyzer.py: import limpio, pool de frames en modo dual, max_frames/max_seconds respetados.
  - ai_insights.py: role separation completa (audience/viewer_name en todas las funciones),
    tier de modelos Opus/Sonnet/Haiku, timeouts configurables, detect_video_events con target_vest.
  - signals_view.py: audience y viewer_name pasados desde Flask session a todas las llamadas IA.
  - Pendiente arquitectonico registrado: congelamientos por MediaPipe+YOLO+Claude bloqueantes en serie.
    No se reduce funcionalidad. Fix correcto es separar en callbacks con dcc.Store (Sprint 5).
Sprint 3 (sensores/hardware): COMPLETADO. sensors.py, hub/, sensors_view.py todos limpios.
Sprint 4 (IA contextual): COMPLETADO. ai_insights.py auditado completamente.
  - Codigo limpio en general. 3 funciones _legacy nunca llamadas (deuda tecnica baja, no urgente).
Sprint 5 (rendimiento/congelamientos): EN PROGRESO.
  - Fix aplicado: cache temporal server-side para analisis pesado de pose en
    signals_view.py. Los dcc.Store ya no transportan frames/base64/reportes
    completos, sino job_id y un reporte ligero.
  - Fix aplicado: cache IA de vision en `ai_insights.py` para no repetir
    deteccion del mismo video/evento/fotograma dentro del TTL.
  - Fix aplicado: eviccion real de cache IA al superar 50 entradas vivas.
  - Fix aplicado: cache de procesamiento ECG para reutilizar suavizado y picos
    R al mover ventanas/exportar con los mismos parametros.
  - Fix aplicado: cache de fotogramas Replay por video/timestamp para reducir
    I/O al repetir eventos visuales.
  - Observacion: `/analyze-pose` en `app.py` sigue como ruta legacy de payload
    completo; no se modifico por compatibilidad.
  - No se redujo calidad ni alcance de MediaPipe, YOLO o IA.

### Incidente 2026-05-23 - Botones Bloqueados

- Error reportado: `Duplicate callback outputs` para `q-gauge.figure`,
  `q-explain.children` y `q-trend.figure`.
- Causa: callbacks de bienestar podian registrarse duplicados durante
  hot reload/importacion.
- Fix: `_callback_once()` en `pages/wellbeing.py` para `load_q_trend` y
  `save_wellbeing`.
- Refuerzo: `_callback_once()` revisa callbacks globales, sentinel persistente
  en `builtins` y `dash.get_app().callback_map`.
- Fix definitivo: se elimino el callback separado de tendencia y se combino
  con `save_wellbeing`, eliminando `allow_duplicate=True` de `q-trend`.
- Importante: no se resolvio agregando `allow_duplicate=True` a todo porque eso
  podia causar doble guardado de check-ins.
- Validado con `/_dash-dependencies`, compileall, pytest, `test_app_flow.py` y
  `test_s105_load.py --skip-video`.
  - Pendiente: validacion manual con video largo en UI y siguiente auditoria de
    pestanas pesadas.
Sprint 6 (UI modo claro): PENDIENTE.
Sprint 7 (exportes PDF/XLSX/CSV): PENDIENTE.
Sprint 8 (DB/roles/permisos/escalabilidad): PENDIENTE.

### Validacion Base 2026-05-21

Resultado de Sprint 0 y Sprint 1:

- Se creo la carpeta `memory/` y sus archivos operativos.
- Se actualizo este prompt maestro con la regla de memoria.
- `compileall` de modulos criticos paso correctamente.
- `pytest -q` paso con `1 passed`.
- `test_app_flow.py` paso como script operativo con `28/28`.
- Se corrigio una discrepancia de pruebas: el flujo operativo esperaba la
  sesion `30`, pero la demo actual de Carlos Rios usa sesiones `31-34`; ahora
  valida la sesion `34`.
- `pytest.ini` ignora scripts operativos que ejecutan `sys.exit` o dependen de
  API/hardware para que la suite de humo sea estable.

Riesgo residual:

- Los scripts operativos deben ejecutarse manualmente cuando aplique.
- La auditoria linea a linea de biomecanica, sensores, IA, UI y rendimiento
  todavia esta pendiente.

### Avance Sprint 2 2026-05-21 - YOLO

`yolo_tracker.py` recibio una primera auditoria linea a linea. Correcciones
aplicadas:

- Import roto por constante usada antes de declararse.
- Fallback OpenVINO GPU -> CPU.
- Lectura secuencial de video en vez de `cap.set()` por cada muestra.
- Validacion clara si `cv2.VideoCapture` no abre.
- Filtro de color de peto mas estricto.
- Fallback por posicion mas conservador para no convertir arbitros en atletas.
- Mas candidatos por frame antes de tracking/color.
- Aliases `blue`/`red` ademas de `azul`/`rojo`.

Tambien se detecto que `analyze_wt_deep.py` escribia archivos y DB por defecto.
Ahora corre en modo seguro y solo persiste si se usa `--write`.

Validaciones:

- `compileall`: OK.
- `pytest -q`: `1 passed`.
- `test_app_flow.py`: `28/28`.
- `analyze_duel_speeds` completo con `videoplayback.mp4`: 58.5s, sin error.

Riesgo residual:

- La deteccion roja quedo mas conservadora que el historico; requiere revision
  visual para asegurar que no se perdio atleta real al reducir ghosts.

### Regla De Optimizacion Aclarada 2026-05-21

Reducir congelamientos no significa quitar potencia a la app. Las mejoras de
rendimiento deben venir de limpieza real y arquitectura mas segura, no de
desactivar capacidades utiles.

En biomecanica, senales e IA:

- No bajar precision por velocidad sin razon validada.
- No desactivar YOLO, MediaPipe, sensores o IA solo para que cargue mas rapido.
- No quitar graficas o analisis que aporten valor a atleta, coach o demo.
- Si una optimizacion implica analizar menos datos, debe ser opcion controlada
  y explicita.
- Priorizar limpieza: codigo muerto, duplicados, I/O repetido, callbacks
  sobrecargados, caches faltantes y scripts que escriben datos sin advertir.

### Avance Sprint 2 2026-05-21 - MediaPipe Pose Analyzer

`pose_analyzer.py` recibio una pasada de auditoria enfocada en limpieza real y
rendimiento sin recortar potencia:

- Se elimino import no usado.
- Se agrego logging debug en excepciones visuales no criticas.
- Se valida `cv2.imencode()` antes de generar base64.
- El modo dual ya no codifica cientos/miles de JPEGs si solo se muestran hasta
  6 frames: mantiene un pool configurable de candidatos.
- El modo `duel` respeta `max_frames` y `max_seconds` cuando el caller los pasa
  explicitamente; solo autoescala cuando se dejaron defaults individuales.
- La simulacion ECG/IMU desde duelo es determinista.

Validaciones:

- `compileall`: OK.
- `pytest -q`: `1 passed`.
- `test_app_flow.py`: `28/28`.
- Prueba individual azul corta: OK.
- Prueba dual corta: OK, respeta `frames_analyzed=40`.

Siguiente foco:

- Auditar `signals_view.py`, porque probablemente ahi se sienten los
  congelamientos si MediaPipe/YOLO/IA se ejecutan en el callback principal sin
  separar preview, progreso o analisis completo.

### Avance Sprint 2 2026-05-21 - IA Por Rol En Biomecanica Y Replay

Se corrigio la mezcla de mensajes atleta/coach en lecturas IA sin tocar los
calculos de pose, tracking, rounds, ECG ni IMU:

- `generate_duel_insight()` recibe audiencia, atleta y coach.
- El prompt rojo vs azul habla a una sola audiencia por respuesta.
- El fallback sin API separa "Tu lectura del combate" para atleta y "Lectura
  tactica para coach" para coach.
- `analyze_combat_session()` y `analyze_event_frame()` reciben audiencia y
  nombre del visor.
- `signals_view.py` pasa el rol real de Flask session a la IA de biomecanica,
  Replay y evento visual.
- Las tarjetas biomecanicas agregan una frase puente distinta para coach y
  atleta antes de explicar la grafica.

Validaciones:

- `compileall`: OK.
- `pytest -q`: `1 passed`.
- `test_app_flow.py`: `28/28`.
- Prueba sin API: atleta y coach reciben salidas distintas.

Pendiente:

- Validar con API activa que Claude no mezcle roles.
- `detect_video_events()`: RESUELTO 2026-05-21. Ahora acepta `target_vest` y
  `scan_video_events` en `signals_view.py` lo pasa desde `pose-target-select`.
- Congelamientos `signals_view.py`: FIX PARCIAL APLICADO 2026-05-21.
  Causa: MediaPipe (~60s) + YOLO (~120s para 6min video) + Claude (~40s)
  corren en serie bloqueante en `analyze_pose_callback`. No se reduce
  `max_duration_s` ni ningun parametro de calidad — la regla es avanzar, no
  retroceder. El fix correcto es arquitectonico: separar los tres analisis en
  callbacks independientes con dcc.Store intermedio y feedback de progreso.
  Estado actual: el flujo ya esta separado en callbacks y se agrego cache
  server-side para no serializar frames/base64/reportes completos hacia el
  navegador. Pendiente validar manualmente video largo y revisar otras pestanas
  pesadas.

### Regla Operativa 2026-05-23 - Dash Hot Reload Y Callbacks Duplicados

Si Dash muestra `Duplicate callback outputs` con outputs terminados en hash
`@...`, no asumir inmediatamente que el codigo actual sigue roto.

Procedimiento obligatorio:

- Verificar `http://127.0.0.1:8051/_dash-dependencies`.
- Si aparece el hash viejo, revisar procesos Python vivos en el puerto `8051`.
- Detener procesos Dash antiguos antes de seguir editando codigo.
- Arrancar limpio desde `.\.venv\Scripts\python.exe app.py`.
- Confirmar que `/_dash-dependencies` muestre el callback combinado esperado y
  sin hash viejo.

Caso resuelto:

- Error: `q-gauge.figure@1bb...`, `q-explain.children@1bb...`,
  `q-trend.figure@1bb...`.
- Causa raiz visible: servidor Dash antiguo sirviendo metadata vieja en
  `8051`, no el codigo final de `pages/wellbeing.py`.
- Validacion final: `HasOldHash=False`, `HasCombined=True`.
- Auditoria extra: no hay duplicados peligrosos sin hash
  (`risky_unhashed_duplicate_bases=0`). Los duplicados restantes detectados son
  intencionales con `allow_duplicate=True`.

### Actualizacion 2026-05-24 - Auditoria De Congelamientos

Cambios confirmados:

- Eliminado diagnostico temporal de `app.py`:
  - Panel `__diag`.
  - Interceptor global de `console.error`.
  - Interceptor global de `window.fetch`.
  - Lectura interna de React fiber/Dash store.
  - `__test-interval`, `__dash-test-btn`, `__dash-test-out` y callback.
- Replay eventos IA:
  - Nuevo boton `Analizar IA video`.
  - `detect_video_events()` ya no corre automaticamente por seleccionar
    sesion/video.
- Replay lectura IA:
  - Nuevo boton `Generar lectura IA`.
  - `analyze_combat_session()` ya no corre automaticamente al seleccionar
    sesion.
- IA:
  - `analyze_event_frame()` ya define/cachea `cache_key` correctamente.
  - `generate_athlete_note()` ya no contiene bloque erroneo `event_frame` con
    variables inexistentes.
- Servidor `8051` reiniciado limpio y verificado.

Validaciones:

- `compileall app.py ai_insights.py views\signals_view.py`: OK.
- Cliente IA falso: `generate_athlete_note()` y `analyze_event_frame()` OK.
- `pytest -q`: `1 passed`.
- `test_app_flow.py`: `28/28`.
- `test_s105_load.py --skip-video`: OK.
- `/_dash-dependencies`: `risky_unhashed_duplicate_bases=0`.
- Live `8051`: `HasDiag=False`, `HasTestCallback=False`,
  `HasAiButtonCallbacks=True`.

Regla nueva:

- No ejecutar IA pesada automaticamente en Replay por cambios de sesion/video.
  Debe ser accion explicita del usuario mientras no exista backend de jobs con
  progreso/cancelacion.
- No dejar interceptores globales ni paneles diagnosticos visibles en demo.

### Actualizacion 2026-05-24 - Logout Robusto

Se corrigio el congelamiento al salir de sesion:

- `/logout` ahora es una ruta Flask real en `app.py`.
- La ruta limpia `session` y redirige por HTTP a `/login`.
- El enlace "Salir" de la barra lateral ahora usa `html.A`, no `dcc.Link`.
- `pages/logout.py` queda como fallback defensivo y ya no monta
  `dcc.Location(pathname="/login", id="redirect-logout")`.

Validaciones:

- `compileall app.py pages\logout.py`: OK.
- Flask test client: `GET /logout` -> `302 /login`, sesion limpia.
- Servidor vivo `8051`: `GET /logout` -> `302 /login`.
- `pytest -q`: `1 passed`.
- `test_app_flow.py`: `28/28`.
- `test_s105_load.py --skip-video`: OK.

Regla nueva:

- Logout/autenticacion debe ser server-side HTTP redirect. No usar
  `dcc.Location` anidado como mecanismo principal para cerrar sesion.

### Actualizacion 2026-05-24 - IA Bajo Demanda En Sesion/Ficha

Se redujeron congelamientos potenciales sin eliminar capacidades:

- La ficha de atleta coach ya no genera `generate_coaching_note()` al cambiar
  atleta. Ahora usa boton `Generar analisis IA`.
- La vista `/sesion` atleta ya no genera `generate_athlete_note()` al entrar a
  la pagina. Ahora usa boton `Generar lectura IA`.
- La vista `/sesion` coach ya no genera `generate_team_summary()` al entrar a
  la pagina. Ahora usa boton `Generar resumen IA`.
- Las tres tarjetas muestran placeholders claros para que la interfaz no
  parezca congelada.
- Se reviso el boton interno "Cerrar sesion" de entrenamiento: no ejecuta IA ni
  biomecanica; solo cierra en DB y refresca opciones.

Validaciones:

- `compileall app.py views\signals_view.py`: OK.
- `pytest -q`: `1 passed`.
- `/_dash-dependencies`: `risky_unhashed_duplicate_bases=0`.
- `test_app_flow.py`: `28/28`.
- `test_s105_load.py --skip-video`: OK.
- `git diff --check -- app.py`: OK.
- Servidor `8051` reiniciado y verificado: botones IA manuales presentes y
  `/logout` responde `302 /login`.

Regla nueva:

- Cualquier IA que pueda tardar mas de unos segundos debe ejecutarse por accion
  explicita del usuario o mediante jobs/background con progreso.

### Actualizacion 2026-05-24 - Hardening Ruta Legacy `/analyze-pose`

Se revisaron rutas antiguas de video/pose para reducir congelamientos:

- `/upload-video` ya sube videos por `fetch` multipart, correcto para evitar
  payloads gigantes dentro de Dash.
- `/analyze-pose` es endpoint legacy autenticado y no parece usarse por la UI
  moderna.
- Riesgo corregido: aceptaba `sample_every` y `max_frames` sin limite defensivo
  suficiente.
- Nuevo limite: `_LEGACY_POSE_ROUTE_MAX_FRAMES = 1500`.
- `sample_every` queda limitado a `1..60`.
- `max_frames` queda limitado por `COMBATIQ_LEGACY_POSE_ROUTE_MAX_FRAMES` o
  `1500` por defecto.
- No se redujo potencia del flujo principal de Biomecanica en
  `views/signals_view.py`.
- Observacion: `data/uploads` contiene muchos videos duplicados de pruebas. No
  eliminar sin permiso explicito y sin listar tamanos antes.

Validaciones:

- `compileall app.py views\signals_view.py`: OK.
- `pytest -q`: OK.
- `test_app_flow.py`: `28/28`.
- `test_s105_load.py --skip-video`: OK.
- `/_dash-dependencies`: `risky_unhashed_duplicate_bases=0`.
- Prueba simulada: `max_frames=999999` en `/analyze-pose` se recorta a `1500`.

Regla nueva:

- Todo endpoint legacy de analisis pesado debe tener caps defensivos, aunque la
  UI principal no lo use.

### Actualizacion 2026-05-24 - Inventario Uploads Duplicados

Se revisaron uploads sin borrar ni mover archivos:

- Total: 56 archivos, ~4029.74 MB.
- Duplicados reales por SHA-256: 3 grupos.
- Espacio repetido estimado: ~3816.16 MB.
- Grupos duplicados:
  - `20230325_213445*.mp4`: 24 archivos identicos.
  - `videoplayback*.mp4`: 30 archivos identicos.
  - `20260503_005430_07048cf5.mp4` y
    `data/uploads_legacy/20260503_005430.mp4`: identicos.
- La DB no referencia directamente esos videos; solo contiene referencias
  demo ECG/IMU (`combat_12_wt_videoplayback.csv`,
  `combat_12_wt_videoplayback_imu`).

Propuesta pendiente:

- Mantener canónicos:
  - `data/uploads/20230325_213445.mp4`
  - `data/uploads/videoplayback.mp4`
  - `data/uploads/20260503_005430_07048cf5.mp4`
- Mover 53 duplicados a `data/uploads_quarantine_20260524/`.
- Validar app/tests antes de eliminar definitivamente.

Regla:

- Limpieza de archivos de usuario: primero cuarentena, no delete directo; pedir
  confirmacion explicita antes de mover o borrar.

### Actualizacion 2026-05-24 - Duplicados Movidos A Cuarentena

El usuario autorizo la limpieza controlada:

- Se movieron 53 duplicados a `data/uploads_quarantine_20260524/`.
- No se borro definitivamente ningun archivo.
- `data/uploads` conserva 3 canónicos:
  - `20230325_213445.mp4`
  - `videoplayback.mp4`
  - `20260503_005430_07048cf5.mp4`
- `data/uploads` quedo en ~213.58 MB.
- La cuarentena contiene ~3816.16 MB.
- Se genero manifiesto: `data/upload_quarantine_20260524.json`.
- Se actualizaron aliases para que nombres antiguos de duplicados sigan
  resolviendo al archivo canonico.
- `check_videos.py` fue actualizado para usar `_resolve_uploaded_video()`.

Validaciones:

- `_resolve_uploaded_video()` resuelve nombres antiguos como
  `videoplayback_871497d6.mp4`, `20230325_213445_13607fef.mp4` y
  `20260503_005430.mp4`.
- `compileall app.py check_videos.py`: OK.
- `check_videos.py`: OK.
- `test_app_flow.py`: `28/28`.
- `pytest -q`: OK.
- `test_s105_load.py --skip-video`: OK.
- `/_dash-dependencies`: `risky_unhashed_duplicate_bases=0`.

Regla:

- No eliminar `data/uploads_quarantine_20260524/` sin validacion manual previa
  de Replay/Biomecanica y permiso explicito.

### Actualizacion 2026-05-24 - Fix Demo Atleta En Loading

Se corrigio la entrada demo que podia quedar en `Loading...`:

- Se agregaron rutas Flask:
  - `/demo/atleta`
  - `/demo/coach-tkd`
  - `/demo/coach-boxeo`
- Cada ruta crea la sesion demo correspondiente y redirige por HTTP a
  `/dashboard`.
- Los controles demo en `pages/auth_login.py` ahora son `html.A` con `href`
  real, no dependen exclusivamente de un callback Dash.
- `.auth-demo__pill` en `assets/30_auth.css` se ajusto para que los enlaces se
  vean como botones.
- Se detectaron procesos duplicados en `8051`; se detuvieron y quedo un unico
  servidor limpio.

Validaciones:

- `/demo/atleta` -> `302 /dashboard`, sesion `Demo Atleta`/`deportista`.
- `/demo/coach-tkd` -> `302 /dashboard`.
- `/demo/coach-boxeo` -> `302 /dashboard`.
- `/logout` -> `302 /login`.
- `/_dash-dependencies` -> 147 dependencias.
- Layout vivo: `btn-demo-login` es `A` con `href="/demo/atleta"`.
- `pytest -q`, `test_app_flow.py` y `test_s105_load.py --skip-video`: OK.

Regla:

- Todo acceso critico de demo/autenticacion debe tener ruta HTTP server-side
  robusta. No depender solo de hidratacion/callbacks Dash.

### Actualizacion 2026-05-24 - Carga Doble Demo Corregida

Se corrigio la doble hidratacion/navegacion percibida tras entrar a demo:

- Causa: el layout autenticado usaba `request.path` para construir
  `dcc.Location(pathname=...)`.
- En Dash, el layout puede renderizarse durante `/_dash-layout`, por lo que
  `request.path` no siempre representa la URL real del navegador.
- Fix: en `app.py`, usuarios autenticados usan `dcc.Location(id="url")` sin
  `pathname` forzado.
- Limpieza: en `pages/auth_login.py` se eliminaron los `demo-redirect`
  obsoletos.
- Servidor `8051` reiniciado para cargar el codigo nuevo.

Validaciones:

- `compileall app.py pages\auth_login.py`: OK.
- `pytest -q`: OK.
- `test_app_flow.py`: `28/28`.
- `test_s105_load.py --skip-video`: OK.
- Rutas demo: `/demo/atleta`, `/demo/coach-tkd`, `/demo/coach-boxeo` ->
  `302 /dashboard`.
- `/logout` -> `302 /login`.
- `/_dash-layout` con cookie demo no fuerza `pathname`.
- `/_dash-dependencies`: 144 dependencias, 0 outputs duplicados exactos.

Regla:

- En Dash, no tomar `request.path` de `serve_layout()` como ruta de navegador
  para usuarios autenticados; dejar que `dcc.Location` lea `window.location`.

### Actualizacion 2026-05-24 - Arranque Estable Y Tema Claro Persistente

Se corrigio una fuente probable de congelamientos/carga doble y del bug de modo
claro:

- Causa: `app.index_string` limpiaba `sessionStorage` y claves de
  `localStorage` en cada carga.
- Entre las claves eliminadas estaba `theme-store`, por lo que el modo claro
  podia perderse o verse inconsistente.
- Esa limpieza corria despues de cargar Dash, por lo que podia interferir con
  hidratacion.
- Fix: en `app.py`, la limpieza de service worker/cache queda versionada con
  `combatiq-sw-cache-cleanup-v2`.
- Ya no se ejecuta `sessionStorage.clear()`.
- Ya no se elimina `theme-store` en cada carga.
- Se reinicio el servidor `8051` dejando un unico listener.

Validaciones:

- HTML servido sin `sessionStorage.clear`.
- HTML servido sin borrado de `theme-store`.
- `/demo/atleta` -> `302 /dashboard`.
- `/logout` -> `302 /login`.
- `/_dash-dependencies`: 144 dependencias, 0 outputs duplicados exactos.
- `compileall app.py`: OK.
- `pytest -q`: OK.
- `test_app_flow.py`: `28/28`.
- `test_s105_load.py --skip-video`: OK.

Regla:

- No borrar storage global en cada carga. Cualquier limpieza de cache/PWA debe
  ser idempotente, versionada y no tocar preferencias de usuario.

### Actualizacion 2026-05-24 - Optimización De Callbacks Iniciales En Signals

Se optimizo la carga inicial de Replay/ECG/IMU sin recortar funcionalidad:

- Archivo: `views/signals_view.py`.
- Antes: 38 callbacks globales con `prevent_initial_call=False`.
- Despues: 29 callbacks iniciales.
- Diferidos:
  - render de eventos vacios,
  - info de sesion sin seleccion,
  - panel IA sin sesion,
  - fila de renombrar oculta,
  - graficas vacias de Replay ECG/IMU,
  - graficas simuladas vacias,
  - gating/tabs/sesiones antes de tener atleta seleccionado.
- Conservados:
  - carga de sesiones Replay,
  - KPIs principales,
  - selector de atleta,
  - ECG principal cuando corresponde.
- `replay-ai-panel` ahora trae mensaje inicial estatico sin necesitar callback.
- No se toco la logica deportiva, biomecanica, IA, sensores ni exportes.

Validaciones:

- `compileall views\signals_view.py`: OK.
- `pytest -q`: OK.
- `test_app_flow.py`: `28/28`.
- `test_s105_load.py --skip-video`: OK.
- `/_dash-dependencies`: 144 dependencias, 0 outputs duplicados exactos,
  29 callbacks iniciales.
- Rutas demo/logout correctas.
- Servidor `8051` reiniciado.

Regla:

- Callbacks que solo repintan placeholders deben ser diferidos. La carga inicial
  debe reservarse para datos reales o acciones necesarias del flujo.

### Actualizacion 2026-05-24 - Segunda Pasada De Callbacks Iniciales

Se continuo la optimizacion de congelamientos/carga:

- Callbacks iniciales globales antes de esta pasada: 29.
- Callbacks iniciales despues: 24.
- Reduccion total en esta etapa: 38 -> 24.
- Diferidos seguros:
  - password strength en registro,
  - campo custom de deporte en registro,
  - store de fecha de peso/competencia,
  - progreso de checklist de competencia,
  - nota IA legacy de AnalysisView cuando `ai-report-store` inicia en `None`.
- Se conservaron iniciales los callbacks que si cargan datos reales de pagina:
  chat, comparacion, wellbeing, peso/nutricion, sensores, sidebar, tema y
  router.

Validaciones:

- `compileall app.py pages\auth_register.py views\analysis_view.py`: OK.
- `pytest -q`: OK.
- `test_app_flow.py`: `28/28`.
- `test_s105_load.py --skip-video`: OK.
- Rutas demo/logout correctas.
- `/_dash-dependencies`: 144 dependencias, 0 outputs duplicados exactos,
  24 callbacks iniciales.
- Servidor `8051` reiniciado.

Regla:

- No perseguir numeros a costa de UX. Solo diferir callbacks cuando el layout
  ya contiene el estado inicial correcto o no hay datos reales que cargar.

### Actualizacion 2026-05-24 - Auditoria De Callbacks Por Peso Real + DB 200

Se paso a la auditoria correcta para congelamientos: medir peso real, no solo
cantidad de callbacks.

Hallazgos:

- En datos demo, la DB no fue el cuello principal: lecturas criticas estuvieron
  normalmente entre 2 ms y 10 ms.
- El peso visible viene de construir/serializar figuras Plotly y HTML grande.
- Callbacks mas pesados medidos:
  - `wellbeing history render`: ~89 ms, payload ~24 KB.
  - `compare session charts`: ~87 ms, payload ~18.6 KB.
  - `peso view`: ~61 ms, payload ~19.1 KB.
  - `nutri view`: ~53 ms, payload ~20.6 KB.
  - `wellbeing trend only`: ~37 ms, payload ~9 KB.
- Chat, compare, wellbeing, sensores y KPIs cargan datos reales; no deben
  diferirse solo para bajar una metrica numerica.

Cambio aplicado:

- `db.py`: migracion versionada `200` con indices de lectura para dashboards e
  historicos:
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

Validaciones:

- `compileall db.py`: OK.
- DB local en `schema_version=200`.
- `PRAGMA index_list` y `EXPLAIN QUERY PLAN` verificaron indices objetivo.
- `pytest -q`: OK.
- `test_app_flow.py`: `28/28`.
- `test_s105_load.py --skip-video`: OK.
- `/_dash-dependencies`: 144 dependencias, 0 outputs duplicados exactos,
  24 callbacks iniciales.
- Servidor `8051` reiniciado y verificado live.

Regla:

- Optimizar por peso real: queries, render, payload y pagina activa.
- No recortar potencia de la app para reducir congelamientos.
- No limitar historiales ni exports sin acuerdo explicito. Si los datos crecen,
  priorizar lazy rendering, cache defensivo o optimizacion de figuras manteniendo
  informacion completa.

### Actualizacion 2026-05-24 - Comparar: Detalle Tecnico Bajo Demanda

Se redujo costo real de entrada a Comparar sin quitar potencia:

- Archivo: `views/compare_view.py`.
- Callback afectado: `session_compare_all`.
- Antes: calculaba ECG/IMU, tablas, badges, resumen, recomendaciones y dos
  graficas aunque el bloque tecnico estuviera cerrado.
- Ahora: el `html.Details` del detalle tecnico tiene `id="cmp-detail-toggle"` y
  el callback escucha su propiedad `open`.
- Si `open=False` o el rol no es `coach`, devuelve placeholders ligeros.
- Si el coach abre el bloque, se ejecuta el analisis completo.

Medicion:

- Detalle cerrado: ~0.85 ms / ~0.65 KB.
- Detalle abierto con datos reales: ~224 ms / ~18.6 KB.

Validacion:

- `compileall views\compare_view.py`: OK.
- `pytest -q`: OK.
- `test_app_flow.py`: `28/28`.
- `test_s105_load.py --skip-video`: OK.
- Dash dependencies: 144, 0 outputs duplicados exactos, 24 callbacks iniciales,
  input `cmp-detail-toggle.open` presente.
- Rutas demo/logout correctas.
- Servidor `8051` reiniciado con un solo listener real.

Regla:

- Todo bloque tecnico pesado que este colapsado por defecto debe cargarse bajo
  demanda.
- No confundir optimizacion con quitar potencia: el analisis completo sigue
  disponible cuando el usuario lo solicita.

### Actualizacion 2026-05-25 - Historico Wellbeing Separado Por Peso Real

Se optimizo el historico de bienestar sin recortar datos:

- Archivo: `pages/wellbeing.py`.
- Antes: un solo callback devolvia resumen, tabla reciente y dos graficas.
- Ahora:
  - `load_history_data` carga datos autorizados a `h-history-data`.
  - `render_history_summary` pinta KPIs y tabla reciente.
  - `render_history_charts` pinta `h-wellness` y `h-load`.
- El layout conserva placeholders iniciales.
- Export Excel/CSV no se limita ni se modifica.
- Textos visibles se mantuvieron en espanol con acentos.

Medicion:

- Antes: ~89 ms / ~24 KB.
- Ahora:
  - datos: ~7.3 ms / ~4 KB.
  - resumen/tabla: ~2.7 ms / ~5.5 KB.
  - graficas: ~72.7 ms / ~18.6 KB.

Validacion:

- `compileall pages\wellbeing.py`: OK.
- `pytest -q`: OK.
- `test_app_flow.py`: `28/28`.
- `test_s105_load.py --skip-video`: OK.
- Dash dependencies: 146, 0 outputs duplicados exactos, 24 callbacks iniciales.
- Rutas demo/logout correctas.
- Servidor `8051` reiniciado con un solo listener real.

Regla:

- Cuando una pagina mezcle informacion ligera accionable y graficas pesadas,
  separar datos/render en callbacks especializados.
- No limitar historiales ni exportes para mejorar performance sin aprobacion.

### Actualizacion 2026-05-25 - Peso Y Nutricion Separados Por Peso Real

Se optimizaron las vistas de peso y nutricion sin recortar datos:

- Archivo: `app.py`.
- Peso:
  - `load_peso_data` carga DB a `peso-data-store`.
  - `render_peso_summary` pinta tabla, KPIs, alertas y ultimos registros.
  - `render_peso_graph` pinta solo la grafica.
- Nutricion:
  - `load_nutri_data` carga DB a `nutri-data-store`.
  - `render_nutri_summary` pinta tabla, KPIs, insight bienestar-nutricion y
    ultimos registros.
  - `render_nutri_graph` pinta solo la grafica.
- No se tocaron formularios, guardados, reglas de calculo ni exportes Excel/CSV.

Medicion:

- Peso:
  - datos: ~4.5 ms / ~2.7 KB.
  - resumen: ~5.3 ms / ~9.6 KB.
  - grafica: ~41.3 ms / ~9.6 KB.
- Nutricion:
  - datos: ~5.7 ms / ~4.4 KB.
  - resumen/insight: ~9.6 ms / ~10.2 KB.
  - grafica: ~41.9 ms / ~10.5 KB.

Validacion:

- `compileall app.py`: OK.
- `pytest -q`: OK.
- `test_app_flow.py`: `28/28`.
- `test_s105_load.py --skip-video`: OK.
- Dash dependencies: 150, 0 outputs duplicados exactos, 24 callbacks iniciales.
- Rutas demo/logout correctas.
- Servidor `8051` reiniciado con un solo listener real.

Regla:

- Optimizar por percepcion y peso real: datos ligeros primero, graficas despues.
- No recortar historial, exportes ni recomendaciones salvo decision explicita.

### Actualizacion 2026-05-25 - Chat Poll Signature Y Sensores Atleta

Se optimizaron callbacks con polling sin recortar funcionalidad:

- Archivo: `pages/chat.py`.
- Nuevo `chat-last-signature` para guardar una firma liviana de la conversacion.
- `_chat_update` usa `PreventUpdate` cuando el polling no detecta cambios.
- `_chat_update` queda con `prevent_initial_call=True` porque el layout ya trae
  mensajes iniciales.
- `_select_conversation` actualiza la firma al cambiar de conversacion.
- Archivo: `views/sensors_view.py`.
- El intervalo de sensores en vista atleta queda desactivado porque solo
  actualizaba un contenedor oculto.
- Se conserva el polling coach/admin de sensores porque actualiza informacion
  visible del atleta seleccionado.
- Archivo: `assets/chat_scroll.js`.
- El auto-scroll del chat deja de usar `setTimeout` permanente cada 1.5 s y
  pasa a `MutationObserver` de pagina.

Medicion:

- Chat antes: ~12.93 ms / ~2.11 KB cada 5 segundos sin cambios.
- Chat despues sin cambios: HTTP 204, ~5.57 ms / 0 KB.
- Envio vacio protegido: ~5.25 ms / ~2.215 KB.

Validacion:

- `compileall pages\chat.py views\sensors_view.py`: OK.
- `node --check assets\chat_scroll.js`: OK.
- `/assets/chat_scroll.js`: HTTP 200.
- `pytest -q`: OK.
- `test_app_flow.py`: `28/28`.
- `test_s105_load.py --skip-video`: OK.
- Dash dependencies: 150, 0 outputs duplicados exactos, 23 callbacks iniciales.
- `q-gauge`, `q-explain` y `q-trend` aparecen en un solo callback; no hay
  duplicados `q-*`.
- Rutas `/_dash-dependencies`, `/_dash-layout`, `/demo/atleta`,
  `/demo/coach-tkd` y `/logout` correctas.
- Servidor `8051` vivo con un solo listener real.

Regla:

- Polling sin cambios visibles debe responder sin re-render ni payload.
- Si un intervalo no actualiza nada visible, debe desactivarse.
- No optimizar congelamientos eliminando potencia de biomecanica, IA, sensores,
  senales, historiales o exportes.

### Actualizacion 2026-05-25 - Auth Publico Y Recuperacion De Contraseña

Se corrigio el flujo de autenticacion:

- Archivo: `app.py`.
- Causa raiz: sin sesion, el layout inicial forzaba `dcc.Location` a `/login`.
  Eso impedia que `/registro` se mantuviera como ruta real.
- Nuevas rutas publicas respetadas:
  `/login`, `/registro`, `/recuperar-password`, `/forgot-password`.
- Archivo: `pages/auth_login.py`.
- "Crear cuenta" y "Olvidaste tu contraseña" usan `dcc.Link`.
- `_check_pw` soporta bcrypt, PBKDF2 y SHA256 legacy.
- Archivo: `pages/auth_register.py`.
- Link de regreso a login migrado a `dcc.Link`.
- Archivo: `pages/auth_forgot.py`.
- Nueva pantalla para solicitar codigo temporal y cambiar contraseña.
- Archivo: `db.py`.
- Migracion `210`: tabla `password_reset_tokens`.
- Helpers: `create_password_reset_token`, `reset_password_with_token`,
  `update_user_password`.

Seguridad:

- Mensaje generico para no revelar si el correo existe.
- Token guardado como hash, con caducidad y un solo uso.
- En local/demo el codigo puede mostrarse para probar; en produccion debe
  enviarse por correo/API.

Validacion:

- `compileall app.py db.py pages\auth_login.py pages\auth_register.py pages\auth_forgot.py`: OK.
- DB local en `schema_version=210`.
- Token incorrecto falla, token correcto actualiza password y reutilizar token
  falla.
- `pytest -q`: OK.
- `test_app_flow.py`: `28/28`.
- `test_s105_load.py --skip-video`: OK.
- Rutas `/login`, `/registro`, `/recuperar-password`, `/forgot-password`,
  `/_dash-layout`, `/_dash-dependencies`: HTTP 200.
- Router interno confirma layouts correctos: `login-email`, `reg-email`,
  `forgot-email`.
- Callbacks auth y `q-trend` aparecen una sola vez cada uno.
- Servidor `8051` vivo con un solo listener real.
- Confirmacion dirigida posterior: `15/15`.
  - Rutas publicas sin rebote.
  - Registro crea usuario y redirige.
  - Forgot password genera token seguro.
  - Token malo falla, token bueno cambia contraseña, token usado no se reutiliza.
  - Login con nueva contraseña funciona.
  - Usuario de prueba eliminado.

Regla:

- No dejar links visibles sin accion real.
- Rutas publicas de auth no deben rebotar a login por el layout inicial.
- Integracion de correo queda pendiente, pero el modelo seguro ya esta listo.

### Actualizacion 2026-05-26 - Auditoria De Congelamientos

Se realizo una auditoria dirigida de congelamientos:

- Hallazgo fuerte: habia dos procesos `python` escuchando en `8051`
  (`39024` y `40532`).
- Se limpiaron ambos y se reinicio la app; quedo un solo listener real
  (`35632`).
- Archivo: `app.py`.
- `debug=True` se reemplazo por `COMBATIQ_DEBUG`, apagado por defecto.
- `dev_tools_hot_reload=False` queda explicito.
- El log del router baja de `INFO` a `DEBUG`.
- Archivo: `.env.example`.
- Se documenta `COMBATIQ_DEBUG=0`.

Medicion:

- Router no mostro congelamiento global: dashboard atleta ~290 ms promedio,
  analisis ~77 ms, comparar ~69 ms.
- Live tras fix:
  - `/login`: ~31.8 ms.
  - `/registro`: ~14.9 ms.
  - `/recuperar-password`: ~14.4 ms.
  - `/demo/atleta`: ~52.5 ms, final `/dashboard`.
  - `/_dash-layout`: ~184.9 ms con sesion atleta.
  - `/_dash-dependencies`: ~12.6 ms.
- Logout validado: demo atleta -> logout -> login; layout posterior ~14.5 ms.
- `q-trend` y callbacks auth aparecen una sola vez.

Validacion:

- `compileall app.py`: OK.
- `COMBATIQ_DEBUG False` al importar.
- `pytest -q`: OK.
- `test_app_flow.py`: `28/28`.
- `test_s105_load.py --skip-video`: OK.
- Un solo listener en `8051`.

Regla:

- En demo/inversores, debug/hot reload apagados salvo `COMBATIQ_DEBUG=1`.
- Siempre revisar listeners duplicados antes de diagnosticar congelamientos de
  callbacks.
- No resolver congelamientos quitando funcionalidades centrales; primero
  limpiar runtime, procesos duplicados, polling/logs y payload real.

### Actualizacion 2026-05-26 - Asistente IA Flotante Con Fallback Local

Se corrigio el error visible del asistente:

- Problema: la burbuja mostraba `Error: Connection error`.
- Causa: `ai_insights.generate_chat_response` devolvia excepciones crudas cuando
  la API externa no respondia.
- Archivo: `ai_insights.py`.
- Nuevo `_chat_local_fallback` para responder con datos internos si falla Claude
  o falta `ANTHROPIC_API_KEY`.
- Archivo: `app.py`.
- `send_chat_message` tiene fallback defensivo adicional.

Comportamiento esperado:

- Atleta: si falla IA externa, recibe lectura local con bienestar, tendencia,
  ultima sesion, ECG, competencia y plan de accion.
- Coach: si falla IA externa, recibe resumen local de equipo, bienestar medio,
  atletas prioritarios y accion sugerida.
- El usuario no debe ver `Error: Connection error`; el detalle tecnico queda en
  logs.

Validacion:

- `compileall ai_insights.py app.py`: OK.
- Test con `Connection error` forzado: atleta y coach `has_raw_error=False`.
- Callback flotante directo: burbujas normales, historial actualizado e input
  limpio.
- `pytest -q`: OK.
- `test_app_flow.py`: `28/28`.
- `test_s105_load.py --skip-video`: OK.
- Server live: rutas 200, `ai-chat-messages` registrado una sola vez, un solo
  listener en `8051`.

Regla:

- Toda IA visible necesita fallback local accionable.
- No mostrar excepciones tecnicas al usuario final.
- Si se requiere IA externa real para demo, validar key/conectividad/modelo antes
  de la presentacion.

### Actualizacion 2026-05-26 - IA Externa Validada Y Cuestionario Corregido

IA externa:

- `ANTHROPIC_API_KEY` esta presente y con formato correcto.
- El paquete `anthropic` instalado es `0.96.0`.
- Modelos validados con red externa:
  - `claude-haiku-4-5`: OK.
  - `claude-haiku-4-5-20251001`: OK.
  - `claude-sonnet-4-6`: OK.
- Documentacion oficial: `claude-haiku-4-5` es alias API de
  `claude-haiku-4-5-20251001`; `claude-opus-4-7` y `claude-sonnet-4-6` son IDs
  actuales.
- Causa del fallo: proceso de la app arrancado desde entorno con red restringida,
  no key ni modelo.
- App reiniciada fuera de restriccion; un solo listener en `8051`.
- Test live del asistente: status 200, ~6992 ms, sin `Connection error` y sin
  fallback local.

Cuestionario:

- Archivo: `pages/wellbeing.py`.
- Causa: el callback `save_wellbeing` tenia `q-user` como Input y State, pero la
  firma no recibia ambos valores.
- Efecto: los argumentos se desplazaban y `"no"` llegaba como respuesta numerica,
  causando `ValueError`.
- Fix: firma alineada:
  `save_wellbeing(input_user_id, n, user_id, session_id, competition, weight,
  injury, *values)`.
- Test POST real: status 200, DB +1, `q-gauge` en respuesta.
- Test live: status 200, DB +1, sin 500; cleanup ejecutado.

Validacion:

- `compileall pages\wellbeing.py`: OK.
- `pytest -q`: OK.
- `test_app_flow.py`: `28/28`.
- `test_s105_load.py --skip-video`: OK.

Regla:

- En Dash, el orden de argumentos debe coincidir exactamente con Inputs y States,
  incluso si se repite el mismo componente.
- Para demo con IA externa real, arrancar la app desde terminal normal o proceso
  con red real.

### Actualizacion 2026-05-26 - Export IMU Desde Sesion Combat Monitor

- Problema corregido: la UI podia mostrar grafica y KPIs IMU de una sesion, pero
  el PDF/Excel no exportaban porque `imu-meta` solo existia para CSVs subidos
  manualmente.
- Archivo tocado: `views/signals_view.py`.
- `auto_load_imu_for_session` ahora guarda metadata exportable para sidecars
  `data/ecg/*_imu.json`.
- Los exports IMU soportan:
  - CSV manual en `data/imu`.
  - Eventos de sesion en JSON (`source=session_events`, `format=event_json`).
- Fallback añadido: si `imu-meta` esta vacio, el export reconstruye metadata
  desde `signals-session`, `ecg-user` e `imu-tabs`.
- Validado con sesion `34`:
  - Excel: `CombatIQ_IMU_combat_12_wt_videoplayback_imu_eventos.xlsx`.
  - PDF: `CombatIQ_IMU_combat_12_wt_videoplayback_imu_informe.pdf`.
- Pruebas en verde: `compileall views\signals_view.py`, import app,
  callback directo, `pytest -q`, `test_app_flow.py` 28/28,
  `test_s105_load.py --skip-video`.

Regla:

- Toda grafica visible debe tener una ruta de exportacion coherente con su
  fuente real de datos.
- No exigir carga manual si la sesion ya tiene datos IMU exportables.

### Actualizacion 2026-05-26 - Limpieza Visual Al Quitar Sesion

- Problema corregido: al limpiar/cerrar la sesion en `Señales ECG / IMU`, la app
  dejaba pegadas grafica y metricas de la sesion anterior.
- Archivo tocado: `views/signals_view.py`.
- `auto_select_ecg_for_session` limpia `ecg-file`, grafica y KPIs cuando
  `signals-session=None`.
- `auto_load_imu_for_session` limpia grafica, KPIs e `imu-meta` cuando
  `signals-session=None`.
- La UI muestra placeholders explicitos en lugar de datos viejos.
- Validado con callback directo, `compileall`, `pytest -q`,
  `test_app_flow.py` 28/28 y `test_s105_load.py --skip-video`.

Regla:

- Si una seleccion maestra queda vacia, todas las lecturas dependientes deben
  limpiarse. No usar `PreventUpdate` cuando el resultado correcto es borrar UI.

### Actualizacion 2026-05-26 - Guardar Cuestionario No Bloquea Por IA

- Problema corregido: `Guardar cuestionario` en Bienestar podia tardar y sentirse
  congelado.
- Causa: para `wellness < 65`, el callback llamaba IA externa dentro del mismo
  save.
- Archivo tocado: `pages/wellbeing.py`.
- Se agrego `_build_fast_wellbeing_message()` como recomendacion local
  instantanea.
- `save_wellbeing` ya no llama `generate_wellbeing_message` al guardar.
- Validado con `compileall`, `pytest -q`, `test_app_flow.py` 28/28 y
  `test_s105_load.py --skip-video`.

Regla:

- Guardar datos debe ser rapido y no depender de red externa.
- IA externa debe ser bajo demanda, asincrona o con fallback local inmediato.

### Actualizacion 2026-05-26 - Graficas Interpretables Y Frames En IA

- Archivo tocado: `views/signals_view.py`.
- Se agrego `Cómo interpreto esta gráfica` bajo graficas relevantes.
- Ese desplegable solo interpreta la grafica; no recomienda ejercicios ni toma
  decisiones de entrenamiento.
- La lectura IA/coaching ahora agrega evidencia por tiempo/frame:
  - En rojo vs azul: distancia minima, intercambio, presion y pico de velocidad.
  - En analisis individual: amplitud, asimetria, landmarks dudosos y menor
    calidad de pose.
- Esto aplica a modalidades de objetivo individual y dual.
- Validado con `compileall views\signals_view.py`, import app, `pytest -q`,
  `test_app_flow.py` 28/28 y `test_s105_load.py --skip-video`.

Regla:

- Cualquier consejo biomecanico debe poder apuntar a un momento del video cuando
  haya frames disponibles.
- La IA interpreta/traduce datos; MediaPipe/YOLO siguen siendo el nucleo de
  medicion.

Nota tecnica:

- Se retiro BOM UTF-8 accidental de `views/signals_view.py`.
- Validado con `compileall views\signals_view.py`.

### Actualizacion 2026-05-27 - Persistencia Del Analisis Biomecanico

- Objetivo de demo: si el analisis biomecanico termina y el usuario cambia de
  pestana, la lectura debe seguir presente al volver.
- `views/signals_view.py`:
  - `pose-results` usa `storage_type="session"`;
  - selector de objetivo y rounds persisten durante la sesion;
  - el render completo queda guardado en cache server-side por `job_id`;
  - `restore_pose_output()` restaura la vista al volver a la pestana;
  - `reset_pose_when_target_changes()` limpia resultados si se cambia objetivo.
- `app.py`: `/logout` redirige a `/login?logged_out=1`.
- `assets/60_pose_session_cleanup.js`: limpia llaves `pose-*` de navegador al
  cerrar sesion.
- TTL de cache de pose ampliado a 4 h para cubrir presentaciones largas.

Regla:

- El resultado biomecanico vive hasta cambiar objetivo o cerrar sesion.
- Nunca mostrar una lectura vieja asociada a un objetivo nuevo.
- Si la cache visual expira, mostrar referencia preservada y pedir repetir el
  analisis para reconstruir graficas/imagenes.

### Actualizacion 2026-05-27 - Filtro Anti-Pose-Contaminada

- Se reforzo el analisis biomecanico ante frames donde el peto correcto aparece,
  pero MediaPipe puede mezclar articulaciones de otra persona por cruce/oclusion.
- `pose_analyzer.py`:
  - nuevo helper `_target_body_consistency()`;
  - nuevo campo `identity_quality`;
  - nuevo rechazo `pose_contaminada`;
  - campos `identity_warnings`, `coverage`, `selection_confidence_raw`;
  - confianza visible ponderada por cobertura.
- `views/signals_view.py`:
  - confianza mostrada como `Selección + cobertura`;
  - resumen de objetivo incluye `cobertura`;
  - chips de confiabilidad para pose mezclada/color cruzado/peto parcial/casco
    cruzado.

Regla:

- En biomecanica, si hay duda seria de identidad corporal, se descarta o baja
  confianza. No se debe presentar un frame contaminado como metrica fuerte.

Refinamiento:

- Penalizar overlap corporal (`cuerpo_cruzado`, `oclusion_parcial`).
- Los frames de galeria/preview deben ser limpios; si estan contaminados pueden
  afectar confianza, pero no deben presentarse como mejores ejemplos visuales.
- La galeria dual muestra tiempo y score del frame destacado.
