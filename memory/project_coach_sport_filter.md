# CombatIQ - Memoria Coach Sport Filter

Fecha de inicio: 2026-05-21

## Regla Principal

Un coach debe ver solo atletas de su mismo deporte cuando el flujo sea de
equipo/entrenamiento. CombatIQ no debe mezclar boxeo y taekwondo como si fuera
fitness generico.

## Roles

- Atleta de boxeo.
- Taekwondoin.
- Coach de boxeo.
- Coach de taekwondo.
- Admin/demo.
- Inversor solo si el flujo lo permite explicitamente y sin exponer datos
  privados innecesarios.

## Checklist De Auditoria

- [ ] Revisar vistas de coach.
- [ ] Revisar queries de roster.
- [ ] Revisar comparativas multi-atleta.
- [ ] Revisar exports de equipo.
- [ ] Revisar home/dashboard para que el rol inversor no aparezca como
  deportista por accidente.
- [ ] Confirmar que dropdowns no filtran solo en cliente cuando el servidor
  debe validar permisos.

## Riesgos

- El filtro por deporte es parte de la credibilidad del producto.
- Un fallo de permisos o privacidad seria critico para demo e inversion.
