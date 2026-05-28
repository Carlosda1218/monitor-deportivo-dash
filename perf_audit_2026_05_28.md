# CombatIQ — Auditoría de Performance / Congelamientos (2026-05-28)

**Síntoma reportado:** App tarda en cargar y se congela al cambiar de pestaña.

**Stack actual:**
- gunicorn 26.0.0 con `--workers 1 --threads 4 --timeout 300 --preload`
- Worker class: `gthread` (default)
- Backend: Supabase PostgreSQL Frankfurt (eu-central-1)
- Frontend: Dash 2.16 + Flask 3.0

---

## 🔴 CRÍTICO — La causa raíz

```
1 worker proceso + 4 threads gthread + GIL Python
                                +
       Callbacks SÍNCRONOS a Anthropic API (3-28 segundos cada uno)
                                +
       0 callbacks usan background_callback
                                =
       App congelada para TODOS los usuarios durante cada call IA
```

**Por qué sucede:**
- Gunicorn `gthread` libera el thread durante I/O socket, PERO requiere monkey-patching para liberarlo en HTTP libraries (anthropic, requests).
- Sin monkey-patch, cada call HTTP a Anthropic bloquea el thread.
- 4 threads disponibles → 4 calls IA simultáneos pueden congelar TODA la app.
- Con `--workers 1`, no hay otro worker que tome los requests.

---

## 📊 Resumen ejecutivo

| Categoría | Hallazgos | Impacto |
|-----------|-----------|---------|
| Worker config | gthread sin monkey-patch | 🔴 Causa principal del lag |
| Callbacks IA bloqueantes | 6 callbacks con calls síncronos | 🔴 Cada uno congela 3-28s |
| Imports pesados módulo-level | cv2, mediapipe, ultralytics, plotly | 🟡 Arranque lento (~7s) |
| Queries N+1 | Mayoría ya bulk-optimizadas | 🟢 OK |
| Cache TTL | full_report 5min, AI notes con cache | 🟢 OK |
| CSS bundles | 117KB + 37KB = 154KB | 🟡 OK desktop, mediano en mobile |
| Anti-patrones (sleep, sin timeout) | 1 en test, no en runtime | 🟢 OK |

---

## 🔴 CRITICAL — Worker config (CAUSA RAÍZ del lag)

### Problema
```python
# gunicorn config actual
--workers 1 --threads 4 --timeout 300 --preload
```

Cada call a `anthropic.Anthropic().messages.create()` usa `requests` o `httpx` SIN async. Gunicorn `gthread` NO libera el thread durante esos HTTP calls bloqueantes.

Resultado en producción:
- Usuario A pulsa "Generar análisis IA" → thread 1 bloqueado 20s
- Usuario B intenta navegar → si thread 2,3,4 también ocupados → **app se congela**
- Cambio de pestaña dispara callbacks (sidebar, layout) → si todos los threads ocupados → bloqueo

### Fix prioritario — Cambiar a `gevent`

```toml
# railway.toml + Procfile
startCommand = "gunicorn app:server --bind 0.0.0.0:$PORT --workers 1 --worker-class gevent --worker-connections 100 --timeout 300 --preload"
```

**Y al INICIO de `app.py` (línea 1):**
```python
# MUST be first import — monkey-patches stdlib for cooperative async
from gevent import monkey
monkey.patch_all()
```

**requirements.txt:** añadir `gevent>=23.0`

**Por qué funciona:**
- Gevent monkey-patcha `requests`, `socket`, `time.sleep` → I/O cooperativo
- 1 worker + 100 conexiones concurrentes (vs 4 hoy)
- HTTP calls a Anthropic ya NO bloquean otros requests
- Dash sigue funcionando con `--workers 1 --preload` (mantenemos su registro de componentes intacto)

**Riesgo:** Bajo, gevent es estable y ampliamente usado con Flask. El único cuidado es importar monkey antes de cualquier otra cosa.

---

## 🔴 HIGH — Callbacks IA bloqueantes (6 identificados)

| Callback | Archivo:Línea | Modelo | Timeout |
|----------|---------------|--------|---------|
| `load_athlete_card_ai_note` | app.py:2721 | Sonnet (note) + Haiku (alert) | 20s + 8s = **28s** |
| `_pose_step4_ai_duel` | views/signals_view.py:7315 | Sonnet | 20s (✅ ya async via Store) |
| Wellbeing save | pages/wellbeing.py:1315 | Sonnet | 20s |
| `_athlete_report` IA | app.py:10438 | Sonnet | 20s |
| Team summary IA | app.py:10583 | Haiku | 8s |
| `analyze_combat_session` | ai_insights.py:1143 | Opus (tool use) | **60s** |

**Mitigación con gevent + threads:** Estos siguen tardando lo mismo individualmente, pero NO bloquean otros usuarios.

**Mejora adicional opcional:** Convertir a "lazy on click" donde el usuario inicia explícitamente la IA (ya hecho en `load_athlete_card_ai_note` con botón "btn-athlete-card-ai-note").

---

## 🟡 MEDIUM — Imports pesados a nivel módulo

```python
# app.py:25 - PLOTLY se importa siempre (~1.2s)
import plotly.graph_objects as go

# pose_analyzer.py:16-19 - 3 librerías pesadas
import cv2                          # ~2s, 60MB resident
import mediapipe as mp              # ~3s, 200MB resident
from mediapipe.tasks.python import vision as mp_vision

# yolo_tracker.py:15
import cv2

# Total arranque Railway: ~7-10 segundos
```

**Impacto:** Solo afecta el arranque del worker (1 vez). NO afecta latencia de requests.

**Fix opcional (no urgente):** Lazy imports de `pose_analyzer` y `yolo_tracker`:
```python
def analyze_video(...):
    import cv2  # lazy: solo cuando se llama, no en arranque
    import mediapipe as mp
    ...
```

Resultado: arranque baja de ~10s a ~3s. Pero las dependencias siguen siendo necesarias cuando se usa biomecánica.

---

## 🟡 MEDIUM — Dashboard hace 10 queries serie

`pages/dashboard.py` rama atleta carga:
```
1. db.get_user_by_id
2. db.get_athlete_profile
3. db.list_questionnaires
4. db.get_last_ecg_metrics
5. db.get_weekly_load_summary
6. db.get_load_history
7. db.get_notification_prefs
8. db.get_readiness_score
9. db.list_competition_results
10. db.list_competition_events
```

Cada query Supabase Frankfurt desde Railway ≈ 30-50ms. Total: **300-500ms** por carga de dashboard.

**Fix opcional:** El primer load es lo más lento. Como `full_report` ya tiene cache TTL 5min, segunda visita es instantánea.

**No urgente** porque está dentro de rango aceptable (<1s).

---

## 🟢 GOOD — Lo que está bien

1. **`analysis_engine.full_report`** tiene cache TTL 5min — recargar dashboard es instantáneo.
2. **`generate_coaching_note`** tiene cache propio por payload.
3. **`list_questionnaires_bulk`** y similares ya usadas — sin N+1 en coach.
4. **`auto_select_ecg_for_session`** renderiza ECG sin chain — eficiente.
5. **`Perf-2`** ya convirtió duel AI a async via Store — ese ya no bloquea.
6. **CSS minificable** pero ya razonable (154KB total).

---

## 🎯 Plan de implementación recomendado

### Fase A — Fix crítico (15 min, riesgo bajo)
1. `pip install gevent`
2. Añadir `from gevent import monkey; monkey.patch_all()` al inicio de `app.py`
3. Cambiar gunicorn a `--worker-class gevent --worker-connections 100`
4. Push y test en Railway

**Resultado esperado:** Lag desaparece. 100 conexiones simultáneas por worker. Cambio de pestaña instantáneo aunque haya IA corriendo en otro tab.

### Fase B — Optimización opcional (30 min, después de validar A)
5. Lazy imports en `pose_analyzer.py` y `yolo_tracker.py` (arranque 10s → 3s)
6. Considerar `--workers 2` (Railway lo soporta, requiere validar Dash registry compartido)

### Fase C — Background callbacks reales (futuro, requiere Redis/Celery)
7. Sprint dedicado para mover IA a background callbacks Dash con DiskCache backend

---

## 📁 Archivos a tocar (Fase A)

```
requirements.txt    → +gevent>=23.0
app.py              → +from gevent import monkey; monkey.patch_all() (LÍNEA 1)
Procfile            → --worker-class gevent --worker-connections 100
railway.toml        → mismo cambio en startCommand
```

**Riesgo:** Bajo. Gevent es estable, compatible con Flask. El único requisito es que `monkey.patch_all()` sea LA PRIMERA importación.

**Verificación post-deploy:**
- Login funciona
- Cambio de pestaña inmediato
- "Generar análisis IA" tarda como antes (20s) pero NO congela navegación de otros tabs
