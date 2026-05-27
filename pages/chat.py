"""
CombatIQ — Chat interno coach ↔ atleta
"""

from dash import html, dcc, Input, Output, State, no_update, ALL
from dash.exceptions import PreventUpdate
from flask import session
import db


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fmt_time(ts: str) -> str:
    if not ts:
        return ""
    try:
        return ts[11:16]
    except Exception:
        return ""


def _has_unread_from(messages: list, peer_id: int) -> bool:
    return any(m.get("sender_id") == peer_id and not m.get("read_at") for m in (messages or []))


def _conversation_signature(messages: list) -> str:
    """Firma ligera para evitar re-renderizar el chat si no cambió nada visible."""
    msgs = messages or []
    if not msgs:
        return "0"
    last = msgs[-1] or {}
    return "|".join([
        str(len(msgs)),
        str(last.get("id") or ""),
        str(last.get("sender_id") or ""),
        str(last.get("receiver_id") or ""),
        str(last.get("ts") or ""),
        str(len(last.get("body") or "")),
    ])


def _bubble(msg: dict, my_id: int) -> html.Div:
    sent = msg.get("sender_id") == my_id
    cls  = "msg-bubble msg-bubble--sent" if sent else "msg-bubble msg-bubble--recv"
    return html.Div(className=cls, children=[
        html.Div(msg.get("body", ""), className="msg-text"),
        html.Div(_fmt_time(msg.get("ts", "")), className="msg-time"),
    ])


def _avatar_node(avatar_url, name: str, base_cls: str) -> html.Div:
    """Círculo de avatar: foto real si existe, inicial si no."""
    if avatar_url:
        return html.Div(
            className=base_cls,
            children=[html.Img(src=avatar_url, className="avatar-img-fill")],
        )
    return html.Div((name or "?")[0].upper(), className=base_cls)


def _roster_item(uid: int, name: str, last_msg: str, last_ts: str,
                 unread: int, selected: bool, avatar_url=None) -> html.Div:
    cls  = "conv-item conv-item--active" if selected else "conv-item"
    name = name or "?"
    if last_msg:
        preview     = last_msg[:42] + ("…" if len(last_msg) > 42 else "")
        preview_cls = "conv-preview"
    else:
        preview     = "Iniciar conversación"
        preview_cls = "conv-preview conv-preview--new"
    time_str = _fmt_time(last_ts) if last_ts else ""
    return html.Div(
        id={"type": "conv-item", "user_id": uid},
        className=cls,
        n_clicks=0,
        children=[
            _avatar_node(avatar_url, name, "conv-avatar"),
            html.Div(className="conv-meta", children=[
                html.Div(className="conv-name-row", children=[
                    html.Span(name, className="conv-name"),
                    html.Span(time_str, className="conv-time"),
                ]),
                html.Div(className="conv-preview-row", children=[
                    html.Span(preview, className=preview_cls),
                    html.Span(
                        str(unread),
                        className="conv-badge",
                        style={"display": "inline-flex" if unread > 0 else "none"},
                    ),
                ]),
            ]),
        ],
    )


def _build_roster_list(uid_int: int, coach_sport, selected_peer) -> list:
    """All athletes: those with conversations first, then new ones."""
    conversations = db.list_conversations_for_coach(uid_int, sport=coach_sport)
    conv_map      = {c["user_id"]: c for c in conversations}
    roster        = db.list_roster_for_coach(uid_int, sport=coach_sport)

    items = []
    for c in conversations:
        cuid = c.get("user_id")
        if not cuid:
            continue
        items.append(_roster_item(
            uid=cuid,
            name=c.get("name") or "?",
            last_msg=c.get("last_msg", ""),
            last_ts=c.get("last_ts", ""),
            unread=c.get("unread", 0),
            selected=(cuid == selected_peer),
            avatar_url=c.get("avatar_url"),
        ))
    for a in roster:
        if a.get("id") not in conv_map:
            items.append(_roster_item(
                uid=a["id"],
                name=a.get("name") or "?",
                last_msg="",
                last_ts="",
                unread=0,
                selected=(a["id"] == selected_peer),
                avatar_url=a.get("avatar_url"),
            ))

    if not items:
        return [html.P("Sin atletas en tu equipo.", className="text-muted text-xs",
                       style={"padding": "16px"})]
    return items


def _peer_header_children(peer_name: str, avatar_url=None) -> list:
    return [
        _avatar_node(avatar_url, peer_name or "?", "chat-avatar chat-avatar--lg"),
        html.Div(peer_name or "Atleta", className="chat-peer-name"),
    ]


def _send_btn():
    return html.Button("➤", id="btn-chat-send", n_clicks=0, className="btn chat-send-btn")


# ── Layout ────────────────────────────────────────────────────────────────────

def layout():
    uid  = session.get("user_id")
    role = str(session.get("role") or "")

    if not uid:
        return html.Div([
            html.H2("Chat"),
            html.P("Inicia sesión para acceder al chat.", className="text-muted"),
        ], className="page-content")

    uid_int = int(uid)

    # ── Deportista ────────────────────────────────────────────────────────
    if role == "deportista":
        coach = db.get_user_coach(uid_int)
        if not coach:
            return html.Div([
                html.Div(className="page-head", children=[
                    html.H2("Chat con tu coach"),
                    html.P("Aún no tienes un coach asignado.", className="text-muted"),
                ]),
            ], className="page-content")

        peer_id    = coach["id"]
        peer_name  = coach.get("name") or "Coach"
        peer_sport = coach.get("sport") or ""

        msgs = db.list_conversation(uid_int, peer_id) or []
        if _has_unread_from(msgs, peer_id):
            db.mark_messages_read(uid_int, peer_id)

        return html.Div(className="page-content chat-shell", children=[
            dcc.Store(id="chat-peer-id", data=peer_id),
            dcc.Store(id="chat-last-signature", data=_conversation_signature(msgs)),
            dcc.Interval(id="chat-poll", interval=5_000, n_intervals=0),

            html.Div(className="page-head", children=[
                html.H2("Chat"),
                html.P(
                    f"Conversación con {peer_name}" +
                    (f" · {peer_sport.title()}" if peer_sport else ""),
                    className="text-muted",
                ),
            ]),
            html.Div(className="ecg-divider", style={"marginBottom": "16px"}),

            html.Div(className="chat-single",
                     style={"height": "calc(100vh - 185px)", "minHeight": "560px"},
                     children=[
                html.Div(className="chat-peer-header", children=[
                    _avatar_node(coach.get("avatar_url"), peer_name, "chat-avatar chat-avatar--lg"),
                    html.Div(children=[
                        html.Div(peer_name, className="chat-peer-name"),
                        html.Div("Coach · CombatIQ", className="text-muted text-xs"),
                    ]),
                ]),
                html.Div(
                    id="chat-messages",
                    className="chat-messages",
                    children=[_bubble(m, uid_int) for m in msgs],
                ),
                html.Div(className="chat-input-row", children=[
                    dcc.Input(id="chat-input", type="text", n_submit=0,
                              placeholder="Escribe un mensaje…",
                              className="chat-textarea", debounce=False),
                    _send_btn(),
                ]),
                html.Div(id="chat-send-err", className="text-muted chat-send-err"),
            ]),
            # Placeholders para que los callbacks del coach no fallen
            html.Div(style={"display": "none"}, children=[
                html.Div(id="chat-peer-header"),
                html.Div(id="conv-list"),
            ]),
        ])

    # ── Coach ─────────────────────────────────────────────────────────────
    if role == "coach":
        coach_sport    = str(session.get("sport") or "").strip() or None
        conversations  = db.list_conversations_for_coach(uid_int, sport=coach_sport)
        default_peer   = conversations[0]["user_id"] if conversations else None
        msgs_init      = []
        peer_name_init = ""

        peer_avatar_init = None
        if default_peer:
            msgs_init = db.list_conversation(uid_int, default_peer) or []
            if _has_unread_from(msgs_init, default_peer):
                db.mark_messages_read(uid_int, default_peer)
            p = db.get_user_by_id(default_peer) or {}
            peer_name_init  = p.get("name") or "Atleta"
            peer_avatar_init = p.get("avatar_url")

        coach_sport  = str(session.get("sport") or "").strip() or None
        roster_items = _build_roster_list(uid_int, coach_sport, default_peer)

        return html.Div(className="page-content chat-shell", children=[
            dcc.Store(id="chat-peer-id", data=default_peer),
            dcc.Store(id="chat-last-signature", data=_conversation_signature(msgs_init)),
            dcc.Interval(id="chat-poll", interval=5_000, n_intervals=0),

            html.Div(className="page-head", children=[
                html.H2("Chat con atletas"),
                html.P("Mensajes directos con tu equipo.", className="text-muted"),
            ]),
            html.Div(className="ecg-divider", style={"marginBottom": "16px"}),

            html.Div(className="chat-coach-layout",
                     style={"height": "calc(100vh - 185px)", "minHeight": "560px"},
                     children=[

                # ── Sidebar ──────────────────────────────────────────────
                html.Div(className="chat-sidebar", children=[
                    html.Div(className="chat-sidebar-header", children=[
                        html.Span("Mensajes", className="chat-sidebar-title"),
                    ]),
                    html.Div(id="conv-list", className="conv-list", children=roster_items),
                ]),

                # ── Panel ────────────────────────────────────────────────
                html.Div(className="chat-panel", children=[
                    html.Div(
                        id="chat-peer-header",
                        className="chat-peer-header",
                        children=(
                            _peer_header_children(peer_name_init, peer_avatar_init) if peer_name_init else
                            [html.Span("Selecciona un atleta", className="text-muted")]
                        ),
                    ),
                    html.Div(
                        id="chat-messages",
                        className="chat-messages",
                        children=[_bubble(m, uid_int) for m in msgs_init],
                    ),
                    html.Div(className="chat-input-row", children=[
                        dcc.Input(id="chat-input", type="text", n_submit=0,
                                  placeholder="Escribe un mensaje…",
                                  className="chat-textarea", debounce=False),
                        _send_btn(),
                    ]),
                    html.Div(id="chat-send-err", className="text-muted chat-send-err"),
                ]),
            ]),
        ])

    return html.Div([
        html.H2("Chat"),
        html.P("No disponible para este rol.", className="text-muted"),
    ], className="page-content")


# ── Callbacks ─────────────────────────────────────────────────────────────────

def register_callbacks(app):

    # ── Enviar + polling ──────────────────────────────────────────────────
    @app.callback(
        Output("chat-messages", "children"),
        Output("chat-send-err", "children"),
        Output("chat-input", "value"),
        Output("chat-last-signature", "data"),
        Input("btn-chat-send", "n_clicks"),
        Input("chat-input", "n_submit"),
        Input("chat-poll", "n_intervals"),
        State("chat-input", "value"),
        State("chat-peer-id", "data"),
        State("chat-last-signature", "data"),
        prevent_initial_call=True,
    )
    def _chat_update(n_send, n_submit, _tick, text, peer_id, current_signature):
        from dash import ctx
        from flask import session as _sess

        uid = _sess.get("user_id")
        if not uid or not peer_id:
            return [], no_update, no_update, no_update

        uid_int  = int(uid)
        peer_int = int(peer_id)
        is_send  = ctx.triggered_id in ("btn-chat-send", "chat-input")

        err = ""
        if is_send:
            if not (text or "").strip():
                msgs = db.list_conversation(uid_int, peer_int)
                if _has_unread_from(msgs, peer_int):
                    db.mark_messages_read(uid_int, peer_int)
                return (
                    [_bubble(m, uid_int) for m in (msgs or [])],
                    "Escribe algo antes de enviar.",
                    no_update,
                    _conversation_signature(msgs),
                )
            try:
                db.send_message(uid_int, peer_int, text.strip())
                import threading as _thr
                import notifications as _N
                if _N.is_configured():
                    sender_info   = db.get_user_by_id(uid_int) or {}
                    receiver_info = db.get_user_by_id(peer_int) or {}
                    recv_email    = receiver_info.get("email") or ""
                    if recv_email and "@" in recv_email:
                        _thr.Thread(
                            target=_N.notify_new_message,
                            args=(recv_email,
                                  receiver_info.get("name", "Usuario"),
                                  sender_info.get("name", "Tu contacto"),
                                  text.strip()),
                            daemon=True,
                        ).start()
            except Exception as exc:
                err = f"Error al enviar: {exc}"

        msgs = db.list_conversation(uid_int, peer_int) or []
        sig = _conversation_signature(msgs)
        if not is_send and sig == current_signature:
            raise PreventUpdate
        if _has_unread_from(msgs, peer_int):
            db.mark_messages_read(uid_int, peer_int)
        new_input_val = "" if is_send and not err else no_update
        return [_bubble(m, uid_int) for m in msgs], err, new_input_val, sig

    # ── Seleccionar / iniciar conversación ───────────────────────────────
    @app.callback(
        Output("chat-peer-id", "data"),
        Output("chat-messages", "children", allow_duplicate=True),
        Output("chat-peer-header", "children"),
        Output("conv-list", "children"),
        Output("chat-last-signature", "data", allow_duplicate=True),
        Input({"type": "conv-item", "user_id": ALL}, "n_clicks"),
        State("chat-peer-id", "data"),
        prevent_initial_call=True,
    )
    def _select_conversation(clicks, current_peer):
        from dash import ctx
        from flask import session as _sess

        uid = _sess.get("user_id")
        if not uid or not clicks or not any(c for c in clicks if c):
            return no_update, no_update, no_update, no_update, no_update

        triggered = ctx.triggered_id
        if not triggered:
            return no_update, no_update, no_update, no_update, no_update

        uid_int  = int(uid)
        new_peer = int(triggered["user_id"])

        if current_peer is not None and new_peer == int(current_peer):
            return no_update, no_update, no_update, no_update, no_update

        msgs      = db.list_conversation(uid_int, new_peer) or []
        if _has_unread_from(msgs, new_peer):
            db.mark_messages_read(uid_int, new_peer)
        peer_info   = db.get_user_by_id(new_peer) or {}
        peer_name   = peer_info.get("name") or "Atleta"
        peer_avatar = peer_info.get("avatar_url")

        coach_sport  = str(_sess.get("sport") or "").strip() or None
        roster_items = _build_roster_list(uid_int, coach_sport, new_peer)

        return (
            new_peer,
            [_bubble(m, uid_int) for m in msgs],
            _peer_header_children(peer_name, peer_avatar),
            roster_items,
            _conversation_signature(msgs),
        )
