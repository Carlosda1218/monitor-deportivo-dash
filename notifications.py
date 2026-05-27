"""
CombatIQ — Notificaciones por email
====================================
Configuración via variables de entorno (o archivo .env):

  MAIL_SERVER   SMTP host          (ej. smtp.gmail.com)
  MAIL_PORT     Puerto SMTP        (ej. 587)
  MAIL_USER     Usuario/remitente  (ej. combatiq@gmail.com)
  MAIL_PASS     Contraseña / app-password
  MAIL_FROM     Nombre visible     (ej. CombatIQ <combatiq@gmail.com>)

Si las variables no están configuradas el módulo opera en modo silencioso
(log a consola) y nunca lanza excepciones al caller.
"""

import os
import smtplib
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime

log = logging.getLogger("combatiq.notifications")


# ── Configuración ────────────────────────────────────────────────────────────

def _cfg():
    return {
        "server": os.environ.get("MAIL_SERVER", ""),
        "port":   int(os.environ.get("MAIL_PORT", 587)),
        "user":   os.environ.get("MAIL_USER", ""),
        "pw":     os.environ.get("MAIL_PASS", ""),
        "from":   os.environ.get("MAIL_FROM", os.environ.get("MAIL_USER", "CombatIQ")),
    }


def is_configured() -> bool:
    c = _cfg()
    return bool(c["server"] and c["user"] and c["pw"])


# ── Núcleo ───────────────────────────────────────────────────────────────────

def send_email(to: str | list, subject: str, html_body: str, text_body: str = "") -> bool:
    """
    Envía un email. Devuelve True si se envió, False si no está configurado
    o si ocurrió un error (nunca propaga excepciones).
    """
    if not is_configured():
        log.debug("Notificaciones no configuradas — email omitido: %s", subject)
        return False

    if isinstance(to, str):
        to = [to]
    to = [addr for addr in to if addr and "@" in addr]
    if not to:
        return False

    c = _cfg()
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = c["from"]
        msg["To"]      = ", ".join(to)

        if text_body:
            msg.attach(MIMEText(text_body, "plain", "utf-8"))
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        with smtplib.SMTP(c["server"], c["port"], timeout=10) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.login(c["user"], c["pw"])
            smtp.sendmail(c["user"], to, msg.as_string())

        log.info("Email enviado → %s | %s", to, subject)
        return True

    except Exception as exc:
        log.warning("Error enviando email: %s", exc)
        return False


# ── Plantilla base HTML ──────────────────────────────────────────────────────

def _html_wrap(title: str, body_html: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="es">
<head><meta charset="utf-8"><title>{title}</title></head>
<body style="margin:0;padding:0;background:#161f29;font-family:sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0">
    <tr><td align="center" style="padding:32px 16px;">
      <table width="560" cellpadding="0" cellspacing="0"
             style="background:#202c3a;border-radius:14px;overflow:hidden;">
        <!-- Header -->
        <tr>
          <td style="background:#1a2c3d;padding:20px 28px;border-bottom:1px solid #31445f;">
            <span style="color:#2fb7c4;font-size:20px;font-weight:800;">CombatIQ</span>
            <span style="color:#a7b1bc;font-size:12px;margin-left:12px;">Performance Monitor</span>
          </td>
        </tr>
        <!-- Body -->
        <tr><td style="padding:24px 28px;color:#f2f5fa;font-size:14px;line-height:1.6;">
          {body_html}
        </td></tr>
        <!-- Footer -->
        <tr>
          <td style="padding:16px 28px;border-top:1px solid #31445f;">
            <p style="margin:0;color:#a7b1bc;font-size:11px;">
              CombatIQ · {datetime.now().strftime("%d/%m/%Y")} ·
              <a href="#" style="color:#2fb7c4;">Gestionar notificaciones</a>
            </p>
          </td>
        </tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""


# ── Notificaciones específicas ───────────────────────────────────────────────

def notify_new_message(
    to_email: str,
    to_name: str,
    from_name: str,
    message_preview: str,
) -> bool:
    """
    Notifica por email cuando se recibe un mensaje en el chat interno.
    Se envía solo cuando el receptor no está activo en ese momento.
    """
    from html import escape as _esc
    preview   = _esc((message_preview or "")[:200])
    safe_name = _esc(to_name or "")
    safe_from = _esc(from_name or "")
    body = f"""
      <h2 style="margin:0 0 8px;color:#f2f5fa;">Nuevo mensaje en CombatIQ</h2>
      <p style="margin:0 0 16px;color:#a7b1bc;">Hola {safe_name}, tienes un mensaje nuevo.</p>
      <table width="100%" cellpadding="16" cellspacing="0"
             style="background:#1a2535;border-radius:10px;border-left:4px solid #2fb7c4;margin-bottom:16px;">
        <tr><td>
          <div style="font-size:13px;font-weight:700;color:#a7b1bc;margin-bottom:6px;">{safe_from}</div>
          <div style="font-size:14px;color:#f2f5fa;line-height:1.55;">{preview}</div>
        </td></tr>
      </table>
      <p style="color:#a7b1bc;font-size:13px;">
        Abre CombatIQ → Chat para responder.
      </p>
    """
    subject = f"CombatIQ — Mensaje de {safe_from}"
    return send_email(
        to_email,
        subject,
        _html_wrap(subject, body),
        f"Mensaje de {from_name}: {preview}",
    )


def notify_coach_low_wellness(
    athlete_name: str,
    athlete_sport: str,
    score: float,
    coach_email: str,
    coach_name: str = "Coach",
) -> bool:
    """
    Alerta al coach cuando un atleta registra bienestar < 50.
    """
    from html import escape as _esc
    score_int = int(score)
    color = "#e45a5a" if score < 50 else "#f0a832"
    safe_athlete = _esc(athlete_name or "")
    safe_sport   = _esc((athlete_sport or "").title())
    safe_coach   = _esc(coach_name or "Coach")
    body = f"""
      <h2 style="margin:0 0 8px;color:#f2f5fa;">Alerta de bienestar</h2>
      <p style="margin:0 0 16px;color:#a7b1bc;">Hola {safe_coach}, uno de tus atletas necesita atención hoy.</p>
      <table width="100%" cellpadding="12" cellspacing="0"
             style="background:#1a2535;border-radius:10px;margin-bottom:16px;">
        <tr>
          <td style="text-align:center;">
            <div style="font-size:42px;font-weight:900;color:{color};">{score_int}</div>
            <div style="color:#a7b1bc;font-size:12px;margin-top:4px;">/ 100</div>
          </td>
          <td style="padding-left:16px;">
            <div style="font-size:16px;font-weight:700;color:#f2f5fa;">{safe_athlete}</div>
            <div style="color:#a7b1bc;font-size:13px;margin-top:4px;">{safe_sport}</div>
            <div style="color:{color};font-size:12px;margin-top:8px;font-weight:600;">
              {"Estado comprometido — revisar carga de la sesión" if score < 50 else "Atención — supervisar hoy"}
            </div>
          </td>
        </tr>
      </table>
      <p style="color:#a7b1bc;font-size:13px;">
        Entra en CombatIQ para ver el detalle del check-in y ajustar la planificación si es necesario.
      </p>
    """
    subject = f"⚠ Bienestar bajo — {safe_athlete} ({score_int}/100)"
    return send_email(
        coach_email,
        subject,
        _html_wrap(subject, body),
        f"Alerta CombatIQ: {athlete_name} registró bienestar {score_int}/100 hoy.",
    )


def notify_athlete_new_announcement(
    athlete_emails: list,
    sport: str,
    ann_title: str,
    ann_body: str,
    coach_name: str = "Tu coach",
) -> bool:
    """
    Notifica a los atletas de un deporte cuando el coach publica un comunicado.
    """
    from html import escape as _esc
    safe_coach  = _esc(coach_name or "Tu coach")
    safe_sport  = _esc((sport or "").title())
    safe_title  = _esc(ann_title or "")
    preview     = _esc((ann_body or "")[:200])
    preview_raw = (ann_body or "")[:200]
    body = f"""
      <h2 style="margin:0 0 8px;color:#f2f5fa;">Nuevo comunicado</h2>
      <p style="margin:0 0 16px;color:#a7b1bc;">{safe_coach} ha publicado un aviso para el equipo de {safe_sport}.</p>
      <table width="100%" cellpadding="16" cellspacing="0"
             style="background:#1a2535;border-radius:10px;border-left:4px solid #2fb7c4;margin-bottom:16px;">
        <tr><td>
          <div style="font-size:15px;font-weight:700;color:#f2f5fa;margin-bottom:8px;">{safe_title}</div>
          {"<div style='color:#a7b1bc;font-size:13px;line-height:1.6;'>" + preview + ("…" if len(ann_body or "") > 200 else "") + "</div>" if preview else ""}
        </td></tr>
      </table>
      <p style="color:#a7b1bc;font-size:13px;">
        Abre CombatIQ → Contacto con mi coach para ver todos los comunicados.
      </p>
    """
    subject = f"📢 Comunicado: {safe_title}"
    return send_email(
        athlete_emails,
        subject,
        _html_wrap(subject, body),
        f"Nuevo comunicado de {coach_name}: {ann_title}\n{preview_raw}",
    )


def notify_coach_weekly_digest(
    coach_email: str,
    coach_name: str,
    stats: dict,
) -> bool:
    """
    Resumen semanal del equipo para el coach.
    stats keys: total_athletes, active_7d, checkins_7d, avg_wellness,
                red_athletes (list), top_athletes (list), sport
    """
    total    = stats.get("total_athletes", 0)
    active   = stats.get("active_7d", 0)
    checkins = stats.get("checkins_7d", 0)
    avg_w    = stats.get("avg_wellness")
    red_list = stats.get("red_athletes", [])
    top_list = stats.get("top_athletes", [])
    sport    = (stats.get("sport") or "").title()

    avg_w_str   = f"{avg_w:.0f}/100" if avg_w is not None else "—"
    avg_w_color = (
        "#2fb7c4" if avg_w and avg_w >= 75 else
        "#f0a832" if avg_w and avg_w >= 60 else
        "#e45a5a" if avg_w else "#a7b1bc"
    )

    kpi_row = f"""
      <table width="100%" cellpadding="12" cellspacing="0"
             style="background:#1a2535;border-radius:10px;margin-bottom:16px;">
        <tr>
          <td style="text-align:center;border-right:1px solid #31445f;">
            <div style="font-size:28px;font-weight:900;color:#f2f5fa;">{active}</div>
            <div style="color:#a7b1bc;font-size:11px;">activos / {total}</div>
          </td>
          <td style="text-align:center;border-right:1px solid #31445f;">
            <div style="font-size:28px;font-weight:900;color:#f2f5fa;">{checkins}</div>
            <div style="color:#a7b1bc;font-size:11px;">check-ins 7d</div>
          </td>
          <td style="text-align:center;">
            <div style="font-size:28px;font-weight:900;color:{avg_w_color};">{avg_w_str}</div>
            <div style="color:#a7b1bc;font-size:11px;">bienestar prom.</div>
          </td>
        </tr>
      </table>"""

    red_html = ""
    if red_list:
        rows_html = "".join(
            f"<tr><td style='padding:8px 12px;color:#e45a5a;font-weight:600;'>{a['name']}</td>"
            f"<td style='padding:8px 12px;color:#a7b1bc;font-size:12px;'>{str(a.get('sport') or '').title()}</td>"
            f"<td style='padding:8px 12px;text-align:right;color:#e45a5a;'>{int(a.get('last_score', 0))}/100</td></tr>"
            for a in red_list
        )
        red_html = f"""
          <div style="margin-bottom:16px;">
            <div style="font-size:14px;font-weight:700;color:#e45a5a;margin-bottom:8px;">
              &#9888; Atletas con alerta roja ({len(red_list)})
            </div>
            <table width="100%" cellpadding="0" cellspacing="0"
                   style="background:#1a2535;border-radius:8px;overflow:hidden;">
              {rows_html}
            </table>
          </div>"""

    top_html = ""
    if top_list:
        rows_html = "".join(
            f"<tr><td style='padding:8px 12px;color:#f2f5fa;'>{a['name']}</td>"
            f"<td style='padding:8px 12px;text-align:right;color:#2fb7c4;font-weight:700;'>{int(a.get('wellness', 0))}/100</td></tr>"
            for a in top_list[:3]
        )
        top_html = f"""
          <div style="margin-bottom:16px;">
            <div style="font-size:14px;font-weight:700;color:#f2f5fa;margin-bottom:8px;">
              Top atletas esta semana
            </div>
            <table width="100%" cellpadding="0" cellspacing="0"
                   style="background:#1a2535;border-radius:8px;overflow:hidden;">
              {rows_html}
            </table>
          </div>"""

    body = f"""
      <h2 style="margin:0 0 4px;color:#f2f5fa;">Resumen semanal del equipo</h2>
      <p style="margin:0 0 20px;color:#a7b1bc;">
        Hola {coach_name}, aquí tienes el estado de tu equipo{(' de ' + sport) if sport else ''} esta semana.
      </p>
      {kpi_row}
      {red_html}
      {top_html}
      <p style="color:#a7b1bc;font-size:13px;margin-top:8px;">
        Entra en CombatIQ para ver el detalle de cada atleta y ajustar la carga de la próxima semana.
      </p>"""

    subject = f"CombatIQ — Resumen semanal{(' de ' + sport) if sport else ''}"
    return send_email(
        coach_email,
        subject,
        _html_wrap(subject, body),
        f"Resumen semanal CombatIQ: {active}/{total} activos, {checkins} check-ins, bienestar {avg_w_str}.",
    )


def notify_weekly_summary_athlete(
    to_email: str,
    name: str,
    sport: str,
    streak: int,
    avg_wellness,
    load_7d: int,
    next_comp: dict | None = None,
) -> bool:
    """
    Resumen semanal de progreso propio al atleta.
    next_comp: dict con keys 'event_name', 'event_date', 'days' (int).
    """
    streak_color = "#2fb7c4" if streak >= 5 else "#f0a832" if streak >= 2 else "#a7b1bc"
    w_color = (
        "#2fb7c4" if avg_wellness and avg_wellness >= 75 else
        "#f0a832" if avg_wellness and avg_wellness >= 55 else
        "#e45a5a" if avg_wellness else "#a7b1bc"
    )
    w_str = f"{avg_wellness:.0f}/100" if avg_wellness is not None else "—"
    load_str = f"{load_7d:,}" if load_7d else "—"

    kpi_row = f"""
      <table width="100%" cellpadding="12" cellspacing="0"
             style="background:#1a2535;border-radius:10px;margin-bottom:16px;">
        <tr>
          <td style="text-align:center;border-right:1px solid #31445f;">
            <div style="font-size:28px;font-weight:900;color:{streak_color};">{streak}</div>
            <div style="color:#a7b1bc;font-size:11px;">días de racha</div>
          </td>
          <td style="text-align:center;border-right:1px solid #31445f;">
            <div style="font-size:28px;font-weight:900;color:{w_color};">{w_str}</div>
            <div style="color:#a7b1bc;font-size:11px;">bienestar 7d</div>
          </td>
          <td style="text-align:center;">
            <div style="font-size:28px;font-weight:900;color:#f2f5fa;">{load_str}</div>
            <div style="color:#a7b1bc;font-size:11px;">carga 7d (UA)</div>
          </td>
        </tr>
      </table>"""

    comp_html = ""
    if next_comp:
        days = next_comp.get("days", 0)
        days_color = "#e45a5a" if days <= 7 else "#f0a832" if days <= 21 else "#2fb7c4"
        comp_html = f"""
          <div style="background:#1a2535;border-radius:10px;padding:14px 16px;
                      margin-bottom:16px;border-left:4px solid {days_color};">
            <div style="font-size:13px;color:#a7b1bc;margin-bottom:4px;">Próxima competencia</div>
            <div style="font-size:15px;font-weight:700;color:#f2f5fa;">
              {next_comp.get('event_name', '—')}
            </div>
            <div style="font-size:13px;color:{days_color};margin-top:4px;font-weight:600;">
              {days} días restantes · {next_comp.get('event_date', '')}
            </div>
          </div>"""

    body = f"""
      <h2 style="margin:0 0 4px;color:#f2f5fa;">Tu semana en {sport.title()}</h2>
      <p style="margin:0 0 20px;color:#a7b1bc;">
        Hola {name}, aquí tienes un vistazo de tu semana en CombatIQ.
      </p>
      {kpi_row}
      {comp_html}
      <p style="color:#a7b1bc;font-size:13px;margin-top:8px;">
        Entra en CombatIQ para ver el detalle de tu progreso y planificar la próxima semana.
      </p>"""

    subject = f"CombatIQ — Tu semana en {sport.title()}"
    return send_email(
        to_email,
        subject,
        _html_wrap(subject, body),
        f"Resumen semanal CombatIQ — {name}: {streak} días de racha, bienestar {w_str}, carga {load_str} UA.",
    )


def notify_athlete_checkin_reminder(
    athlete_email: str,
    athlete_name: str,
    sport: str,
) -> bool:
    """
    Recordatorio diario de check-in (llamar desde un scheduler externo o cron).
    """
    body = f"""
      <h2 style="margin:0 0 8px;color:#f2f5fa;">¿Cómo llegas hoy?</h2>
      <p style="margin:0 0 16px;color:#a7b1bc;">Hola {athlete_name}, aún no has registrado tu check-in de hoy.</p>
      <table width="100%" cellpadding="16" cellspacing="0"
             style="background:#1a2535;border-radius:10px;margin-bottom:16px;">
        <tr><td style="text-align:center;">
          <div style="color:#a7b1bc;font-size:13px;margin-bottom:12px;">
            El check-in de {sport.title()} tarda menos de 2 minutos y le da a tu coach
            el contexto que necesita para planificar tu sesión de hoy.
          </div>
          <a href="#checkin"
             style="display:inline-block;background:#2fb7c4;color:#161f29;
                    font-weight:700;font-size:14px;padding:10px 24px;
                    border-radius:8px;text-decoration:none;">
            Hacer check-in ahora
          </a>
        </td></tr>
      </table>
    """
    subject = f"CombatIQ — Pendiente tu check-in de hoy"
    return send_email(
        athlete_email,
        subject,
        _html_wrap(subject, body),
        f"Hola {athlete_name}, aún no has hecho el check-in de hoy en CombatIQ.",
    )
