# Sprint TKD-Pulido — Auditoría y mejoras (2026-05-29)

**Contexto:** decisión estratégica del usuario — foco exclusivo en Taekwondo para demo Julio 2026 en México. Boxeo y demás artes marciales se escalarán post-Julio.

**Regla activa:** [project_focus_tkd_first](memory/project_focus_tkd_first.md)

---

## ✅ Mejoras aplicadas

### 1. Home atleta TKD (`pages/home.py`)
**Hero copy** — más TKD-purista:
- ❌ "Revisa si llegas explosivo y sin molestias antes del primer round de hoy."
- ✅ "Revisa si llegas con piernas frescas y sin molestias antes del entrenamiento de hoy."

**Flow items (3 pasos guiados)** — terminología WT real:
- ❌ "Ve si llegas con piernas, sin fatiga y listo para patear a ritmo."
- ✅ "Comprueba fatiga de pierna, recuperación y carga acumulada antes de subir al tatami."

- ❌ "Ajusta el trabajo de distancia y patadas al objetivo del día."
- ✅ "Define el foco: distancia y bandal, contraataque o trabajo de Poomsae."

- ❌ "Confirma tendencias de explosividad antes de un bloque competitivo."
- ✅ "Confirma tendencias de explosividad y precisión antes de un torneo o selectivo."

### 2. Home coach TKD (`pages/home.py`)
**Hero copy** — orientado a decisión táctica:
- ❌ "Revisa quién llega con energía para patear y quién necesita un día más controlado."
- ✅ "Revisa quién llega con piernas para Kyorugi y quién necesita técnica controlada hoy."

**Flow items** — más específicos para coach TKD:
- ✅ "Detecta quién llega sin explosividad, con molestias de cadera o rodilla, o con baja recuperación."
- ✅ "Define si toca distancia y bandal, entrada-salida con contraataque, o simulación de combate con peto."
- ✅ "Usa análisis biomecánico (cámara y velocidad de pateo) e histórico para decisiones tácticas."

### 3. Recomendación post-checkin (`pages/home.py:_home_rec_banner`)
**Score ≥80 (TKD):**
- ❌ "Sparring técnico o simulación de combate — tienes explosividad y energía para sostenerlo."
- ✅ "Kyorugi con peto y contacto — tienes piernas y explosividad para sostener rondas a ritmo de competición."

**Score ≥65 (TKD):**
- ❌ "Técnica de patada con calidad — distancia y precisión sobre explosividad hoy."
- ✅ "Patada técnica en distancia — dollyo y bandal con precisión, sin contacto pleno hoy."

**Score ≥50 (TKD):**
- ❌ "Técnica suave sin contacto — evita exigencia de explosividad máxima."
- ✅ "Sin contacto en peto — trabajo de Poomsae o pateo en peteca, cuida cadera y rodilla."

**Score <50 (TKD):**
- ❌ "Sin impacto hoy — movilidad, estiramientos y habla con tu coach antes de entrenar."
- ✅ "Sin tatami hoy — movilidad de cadera, estiramiento de isquiosurales y habla con tu coach antes de patear."

### 4. Biomecánica TKD (`views/signals_view.py`)
**Título del bloque** — más profesional:
- ❌ "⚡ Cámara de pateo (TKD)"
- ✅ "⚡ Cámara y velocidad de pateo · WT"

**Subtítulo** — explica ambas métricas juntas:
- ❌ "Ángulo de rodilla al momento de máxima flexión pre-extensión. Referencia WT élite: < 85° (pierna dominante)."
- ✅ "Ángulo de rodilla en máxima flexión antes de la extensión (chamber) y velocidad de tobillo al impacto. Referencia élite WT: cámara < 85° y velocidad ≥ 10 m/s en Dollyo-chagi competitivo."

**KPI labels** — más españolizados:
- ❌ "Total kicks" / "kick(s)" / "Eventos detectados"
- ✅ "Patadas detectadas" / "patada(s)" / "Eventos en el video"

**Velocidad pico subtítulo** — más TKD:
- ❌ "Tobillo en extensión"
- ✅ "Tobillo en impacto"

**Ref. WT élite contexto:**
- ❌ "Dollyo-chagi competición"
- ✅ "Dollyo competición"

---

## ✅ Validado sin cambios necesarios

| Pantalla | Estado | Razón |
|----------|--------|-------|
| Login | OK | Ya tiene "Taekwondo y Boxeo — análisis especializado" + demo pills TKD/Box |
| Onboarding | OK | Ya tiene 8+ ramas TKD-específicas con tips contextuales |
| Sesiones de combate | OK | KPIs Rounds/BPM/Impactos/Dados/Recibidos universales y útiles |
| IMU profile | OK | 3 tabs TKD (pierna/cintura/peto) con copy TKD-purista |
| Wellbeing | OK | `_build_fast_wellbeing_message` tiene rama TKD |
| ECG fallback | OK | Fix anterior: muestra KPIs guardados si CSV falta |
| PDF report_utils | OK | Motor genérico — recibe datos TKD del caller |
| Chat | OK | Genérico coach↔atleta, sport filter activo |

---

## 📊 Datos demo verificados en producción

```
ATLETA carlos.tkd@demo.combatiq:
  ✓ 14 check-ins de wellbeing
  ✓ 7 archivos ECG (con métricas guardadas BPM/SDNN/RMSSD)
  ✓ 2 sesiones de combate
  ✓ 7 IMU metrics
  ✓ Coach asignado: Ana Morales (TKD)

COACH ana.coach.tkd@demo.combatiq:
  ✓ 3 atletas TKD en roster
  ✓ 1 conversación activa
```

**Suficiente para demo Julio.** Quizás generar 5-10 sesiones más históricas si hay tiempo.

---

## 🎯 Filosofía del Sprint

El usuario decidió "foco exclusivo en TKD". Esto se traduce en:

1. **NO eliminar** lógica de Boxeo existente
2. **SÍ enriquecer** copy/UX TKD para que se sienta hecho POR un coach TKD
3. **Terminología WT real** en lugar de español deportivo genérico:
   - Kyorugi (combate de competición)
   - Dollyo / Bandal chagi (técnicas de pateo)
   - Hogu / peto (chaleco protector)
   - Tatami (área de combate)
   - Poomsae (formas técnicas)
   - Selectivo / torneo (eventos competitivos)
4. **Métricas biomecánicas TKD destacadas** — chamber + kick speed con referencia WT

---

## 🚀 Próximos sprints camino a Julio

1. **Sprint Design-1 TKD** — Coach Home + Biomec TKD en Figma (paridad código)
2. **Sprint Mobile-1** — responsive real para coaches en gimnasio
3. **Sprint Demo-1** — script narrativo TKD-only + cuentas perfectas
4. **Sprint Polish-1** — custom domain combatiq.app

**Diferido post-Julio:** Sprint Biomech-P3 (Boxeo) y multi-sport.
