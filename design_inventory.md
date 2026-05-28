# CombatIQ — Inventario visual del código (2026-05-28)

Documento de referencia para rediseño Figma — **cada elemento aquí EXISTE en el código**.

---

## 🔑 LOGIN (`pages/auth_login.py`)

### Layout: 2 paneles horizontales (`auth-wrap`)

#### Panel izquierdo (`auth-left`)
**Branding (`auth-left__brand`):**
- Logo SVG (`/assets/logo_combatiq.svg`) en `auth-left__mark`
- Nombre "CombatIQ" (`auth-left__name`)
- Tag "Combat Sports Performance" (`auth-left__tag`)

**Cuerpo (`auth-left__body`):**
- Headline H2: "Monitoreo de rendimiento para taekwondo y boxeo" (con `taekwondo y boxeo` en span destacado)
- Subtítulo: "Carga, bienestar y análisis cardiovascular en un solo lugar. Para atletas y coaches que toman decisiones con datos."
- **Lista de 4 features** (`auth-left__features` con dots):
  1. "Semáforo semanal de carga y bienestar"
  2. "Análisis ECG / HRV por sesión"
  3. "Vista de equipo para coaches — por deporte"
  4. "Taekwondo y Boxeo — análisis especializado por deporte"

**Footer:** "© 2026 CombatIQ" (`auth-left__footer`)

#### Panel derecho (`auth-right`)
**Card de formulario (`auth-card`):**
- H2 "Bienvenido de vuelta" (`auth-title`)
- Subtítulo "Inicia sesión para acceder a tu panel." (`auth-subtitle`)
- Field email: label "Correo electrónico" + input `tu@correo.com`
- Field password: label "Contraseña" + input `••••••••`
- Row remember (`auth-remember`):
  - Checkbox " Recordarme"
  - Link "¿Olvidaste tu contraseña?" → `/recuperar-password`
- Botón primary "Entrar" (`auth-btn-primary`)
- `login-msg` (mensaje error/éxito)
- Switch (`auth-switch`): "¿No tienes cuenta? Crear cuenta" → `/registro`

**Bloque demo (`auth-demo`):**
- Título "Explorar sin cuenta"
- **3 pills (`auth-demo__pill`):**
  - "Atleta\nTaekwondo" → `/demo/atleta`
  - "Coach\nTaekwondo" → `/demo/coach-tkd`
  - "Coach\nBoxeo" → `/demo/coach-boxeo`
- Hint "Datos realistas de ejemplo · sin registro"

### Elementos globales
- Botón toggle de tema (`btn-auth-theme`) — esquina con icono "☀"

---

## 🥋 ATHLETE HOME (`pages/home.py` — rama `else` del rol)

### 1. Hero card (`home-hero`)

**`home-hero__main` (lado izquierdo):**
- **Hero badges** (`home-badges`): array de spans con clase `home-badge`
  - Para atleta: `["Deportista", sport or "Preparación diaria"]`
- **`home-header`:**
  - H1 `f"{greeting}, {first_name}"` (greeting calculado por hora — "Buenos días/tardes/noches")
  - P meta: fecha + deporte + rol unidos con " - "
- **`home-lead`** (hero_copy contextual por deporte):
  - TKD: "Revisa si llegas explosivo y sin molestias antes del primer round de hoy."
  - Boxeo: "Revisa si llegas con manos libres y carga manejable antes de subir al saco."
  - Genérico: "Aquí puedes ver cómo llegas hoy, qué registros tienes recientes y qué te conviene revisar primero."

**`home-hero__side` (lado derecho):**
- Título "Por dónde empezar" (`home-flow__title`)
- Intro "Si no sabes qué mirar primero, este orden suele funcionar bien." (`home-flow__intro`)
- **3 flow items (`home-flow` con `_flow_item`):**
  - **TKD:**
    1. "Mira tu estado" — "Ve si llegas con piernas, sin fatiga y listo para patear a ritmo." → /dashboard
    2. "Abre tu sesión" — "Ajusta el trabajo de distancia y patadas al objetivo del día." → /sesion
    3. "Compara cuando lo necesites" — "Confirma tendencias de explosividad antes de un bloque competitivo." → /comparar
  - **Boxeo:** versión similar con foco en manos/saco
  - **Genérico:** versión similar genérica

### 2. Rec banner condicional (`_rec_banner`)

Solo aparece si el atleta tiene check-in hoy o si NO tiene check-in.

**Si NO tiene check-in:**
- Estado pendiente con link a `/cuestionario`
- Icono "○", label "Check-in pendiente", text "Completa tu check-in del día para ver qué tipo de sesión te conviene hoy."

**Si SÍ tiene check-in (con score):**
- Borde izquierdo color por nivel (`borderLeftColor`)
- Icono + label + texto contextual por deporte y score:
  - ≥80: "Listo para exigencia alta" (teal, ▲)
  - ≥65: "Intensidad controlada" (amber, ●)
  - ≥50: "Ajusta la carga hoy" (red, ▼)
  - <50: "Recuperación prioritaria" (punch, ⚠)
- Texto cambia por deporte (TKD/Boxeo/genérico)
- Score grande a la derecha: `XX/100`

### 3. Streak widget (`streak-widget`) — solo si NO es coach

**`streak-widget__left`:**
- Emoji + número grande de días + " días"
- Color dinámico por nivel (🔥 ≥7, ✓ =1, ○ =0)

**`streak-widget__right`:**
- Mensaje por nivel:
  - ≥30: "¡Racha legendaria! X días seguidos."
  - ≥14: "¡Racha increíble! X días sin fallar."
  - ≥7: "Una semana seguida. ¡Sigue así!"
  - ≥3: "X días consecutivos. Buen ritmo."
  - =1: "Hoy ya está. Vuelve mañana para extender la racha."
  - =0: "Empieza el check-in de hoy para activar tu racha."
- Próximo hito: "X días para el hito de Y 🏆" (hitos: 3, 7, 14, 21, 30, 60, 90)
- Mejor racha: "Mejor racha: X días"

### 4. Grid overview (`home-overview-grid`)

#### Summary card "Estado de hoy" (`_summary_card`)
- Title: "Estado de hoy"
- Subtitle: "Una vista rápida para entender tu estado del día sin tener que ir pantalla por pantalla."
- **3 KPIs (`_summary_kpi`):**
  1. "Último check-in" → wellness.value (ej. "82/100") + sub "Último check-in"
  2. "Últimos 7 días" → checkins_7d count + "Cuestionarios respondidos"
  3. "Último ECG" → ecg.value + ecg.sub
- Detail (note text): wellness.detail + ecg.detail + context_note

#### Chart card "Tendencia de bienestar" (`_chart_card`)
- Title: "Tendencia de bienestar"
- Subtitle: "Sirve para ver si vienes estable o si tu estado ha cambiado en los últimos días."
- Plotly figure: línea de wellness score últimos 14-30 días con zonas (verde ≥70, ámbar 50-69, rojo <50)
- Empty state: "Todavía no hay suficientes cuestionarios para mostrar una tendencia útil."

### 5. Tool groups collapsible (`tiles-section collapsible-card`)

**Header (Summary):**
- Label "Herramientas frecuentes"
- Copy "Accesos rápidos — el menú lateral sigue siendo la navegación principal."
- Chevron "⌄"

**Body — 2 grupos × 3 tiles cada uno:**

**Grupo 1: "Seguimiento"**
- Subtitle: "Aquí tienes a mano las vistas que más te ayudan a seguir tu evolución."
- Tiles:
  - "Señales ECG / IMU" → /ecg (icono signals.svg)
  - "Comparar sesiones" → /comparar (compare.svg)
  - "Historial de wellbeing" → /historico (history.svg)

**Grupo 2: "Apoyo del día"**
- Subtitle: "Puedes usar estas herramientas cuando quieras llevar mejor tu control diario."
- Tiles:
  - "Peso" → /peso (weight.svg)
  - "Nutrición" → /nutricion (nutrition.svg)
  - "Chat con el coach" → /chat (team.svg)

---

## Notas importantes

1. **Variantes por deporte (TKD / Boxeo / genérico):** Cada lugar con `hero_copy`, `flow_items`, `rec_banner` text tiene 3 versiones. Diseñar al menos 2 mockups (TKD y Boxeo) para validar.

2. **Variantes por rol (atleta / coach / admin / inversor):** Este documento cubre **atleta**. El coach tiene otro flujo completo en el mismo `pages/home.py` (rama `if role == "coach"`).

3. **Estados condicionales:**
   - Sin user_id → pantalla de bienvenida con botones Login/Registro
   - Sin checkin hoy → rec_banner "pendiente"
   - Con checkin hoy → rec_banner contextual
   - Streak = 0 → mensaje motivacional diferente
   - Sin datos ECG → "Sin datos"

4. **Diseño DEBE preservar:**
   - El streak widget como **tarjeta separada** (no fundido en KPIs)
   - Los 3 flow items con título + descripción + link
   - Los 6 tool tiles con sus iconos SVG
   - El rec banner con 4 niveles de severidad
   - Las 3 demo pills del login
   - El toggle de tema del login
