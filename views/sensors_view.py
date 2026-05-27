# views/sensors_view.py

from flask import session
from datetime import datetime
from dash import html, dcc, Input, Output, State
from dash.exceptions import PreventUpdate


class SensorsView:
    """
    Vista 'Sensores del producto'.

    - Para COACH / ADMIN:
        * Catálogo de sensores (CORE vs LAB)
        * Asignar sensores a deportistas
        * Parear dispositivos físicos (registro en sensor_devices)
        * Ver estado de conexión en tiempo real (polling vía dcc.Interval)

    - Para DEPORTISTA:
        * Ver 'Mis sensores' con explicación breve
        * Estado de conexión de sus dispositivos emparejados
        * Polling automático de estado cada 15 segundos
    """

    _callbacks_registered = False

    ALL_CODES  = ["ECG", "IMU_WRIST", "IMU_FOOT", "IMU_HEAD", "HR_WRIST"]
    CORE_CODES = {"ECG", "IMU_WRIST", "IMU_FOOT", "IMU_HEAD"}   # base ECG + IMU

    # Etiquetas legibles de estado
    _STATUS_LABEL = {
        "connected": "Conectado",
        "idle":      "Inactivo",
        "offline":   "Sin señal",
        "paired":    "Emparejado",
    }

    def __init__(self, app, db, sensors_module):
        self.app = app
        self.db  = db
        self.S   = sensors_module

        if not SensorsView._callbacks_registered:
            self._register_callbacks()
            SensorsView._callbacks_registered = True

    def _safe_int(self, value):
        try:
            return int(value)
        except Exception:
            return None

    def _coach_sport(self):
        return str(session.get("sport") or "").strip() or None

    def _can_access_athlete(self, athlete_id: int) -> bool:
        aid = self._safe_int(athlete_id)
        actor_id = self._safe_int(session.get("user_id"))
        role = str(session.get("role") or "")
        if not (aid and actor_id):
            return False
        if role == "admin":
            return True
        if role == "deportista":
            return aid == actor_id
        if role == "coach":
            try:
                return bool(self.db.coach_has_athlete(actor_id, aid, sport=self._coach_sport()))
            except Exception:
                try:
                    roster = self.db.list_roster_for_coach(actor_id, sport=self._coach_sport()) or []
                    return any(self._safe_int(a.get("id")) == aid for a in roster)
                except Exception:
                    return False
        return False

    def _clean_sensor_codes(self, codes):
        allowed = set((self.S.catalog() or {}).keys())
        clean = []
        for code in codes or []:
            try:
                c = self.S.normalize_code(code)
            except Exception:
                c = str(code or "").strip().upper()
            if c in allowed and c not in clean:
                clean.append(c)
        return clean

    # ------------------------------------------------------------------ #
    #  Layout principal                                                    #
    # ------------------------------------------------------------------ #

    def layout(self):
        if not session.get("user_id"):
            return html.Div("Inicia sesión para ver esta página.")

        role = str(session.get("role") or "")

        if role in ("coach", "admin"):
            return self._layout_coach_admin_v2(role)
        if role == "deportista":
            return self._layout_athlete()

        return html.Div("No tienes permisos para ver esta página.")

    # ------------------------------------------------------------------ #
    #  Helpers de estado de conexión                                       #
    # ------------------------------------------------------------------ #

    def _status_dot(self, status: str):
        """Punto de color según estado del dispositivo."""
        return html.Span(className=f"device-status-dot device-status-dot--{status}")

    def _device_status_row(self, device: dict):
        """
        Fila compacta de estado que se incrusta al fondo de una sensor card.
        """
        status  = device.get("computed_status", "paired")
        label   = self._STATUS_LABEL.get(status, status.capitalize())
        d_label = device.get("device_label") or device.get("device_id", "")
        fw      = device.get("firmware_version", "")
        ls      = device.get("last_seen", "")
        ls_str  = ""
        if ls:
            try:
                dt = datetime.fromisoformat(ls)
                ls_str = dt.strftime("%H:%M:%S")
            except Exception:
                ls_str = ls[:16]

        children = [
            self._status_dot(status),
            html.Span(label, className=f"device-status-row__label device-status-row__label--{status}"),
        ]
        if d_label:
            children.append(html.Span(f"· {d_label}", style={"fontSize": "11px"}))
        if fw:
            children.append(html.Span(fw, className="firmware-chip"))
        if ls_str:
            children.append(html.Span(f"última señal {ls_str}", className="device-status-row__meta"))

        return html.Div(className="device-status-row", children=children)

    # ------------------------------------------------------------------ #
    #  Helpers de catálogo de sensores                                     #
    # ------------------------------------------------------------------ #

    def _split_codes(self, codes):
        codes = codes or []
        core = [c for c in codes if c in self.CORE_CODES]
        lab = [c for c in codes if c in self.ALL_CODES and c not in self.CORE_CODES]
        other = [c for c in codes if c not in self.ALL_CODES]
        return core, lab, other

    def _group_block(self, title, subtitle, cards):
        return html.Div(
            className="card",
            children=[
                html.H4(title, className="card-title"),
                html.P(subtitle, className="text-muted", style={"marginBottom": "14px"}),
                html.Div(
                    cards if cards else [html.P("Sin datos disponibles.", className="text-muted")],
                    style={"display": "flex", "flexDirection": "column", "gap": "12px"},
                ),
            ],
        )

    def _collapsible_group(self, title, subtitle, body, open=False):
        return html.Details(
            className="card collapsible-card",
            open=open,
            children=[
                html.Summary(
                    className="collapsible-card__summary",
                    children=[
                        html.Div(
                            [
                                html.H4(title, className="card-title"),
                                html.P(subtitle, className="text-muted"),
                            ],
                            className="collapsible-card__head",
                        ),
                        html.Span(">", className="collapsible-card__chevron"),
                    ],
                ),
                html.Div(className="collapsible-card__body", children=body),
            ],
        )

    def _sensor_names(self, codes):
        names = []
        for code in codes or []:
            info = self.S.catalog().get(code, {})
            names.append(info.get("name", code))
        return ", ".join(names) if names else "Sin sensores asignados"

    def _summary_kpi(self, label, value, sub):
        return html.Div(
            className="kpi kpi--mini",
            children=[
                html.Div(label, className="kpi-label"),
                html.Div(value, className="kpi-value"),
                html.Div(sub,   className="kpi-sub"),
                html.Div(className="kpi-ecg-line"),
            ],
        )

    def _assignment_summary(self, codes):
        core_codes, lab_codes, other_codes = self._split_codes(codes or [])
        has_ecg = "ECG" in core_codes
        has_imu = any(c.startswith("IMU") for c in core_codes)

        if has_ecg and has_imu:
            base_value = "Base lista"
            base_sub   = "ECG + IMU preparados para seguimiento"
        elif core_codes:
            base_value = "Base parcial"
            base_sub   = self._sensor_names(core_codes)
        else:
            base_value = "Sin base"
            base_sub   = "Falta definir la base principal"

        lab_value = str(len(lab_codes)) if lab_codes else "0"
        lab_sub   = self._sensor_names(lab_codes) if lab_codes else "Sin apoyo adicional"

        other_value = str(len(other_codes)) if other_codes else "0"
        other_sub   = self._sensor_names(other_codes) if other_codes else "Sin sensores extra"

        return html.Div(
            className="kpis kpis--tight",
            children=[
                self._summary_kpi("Seguimiento base", base_value, base_sub),
                self._summary_kpi("Apoyo opcional",   lab_value,  lab_sub),
                self._summary_kpi("Otros sensores",   other_value, other_sub),
            ],
        )

    def _empty_hint(self, title, detail):
        return html.Div(
            className="inner-card",
            style={"padding": "24px", "textAlign": "center"},
            children=[
                html.P(title,  style={"fontWeight": "600", "marginBottom": "6px"}),
                html.P(detail, className="text-muted"),
            ],
        )

    def _coach_sensor_snapshot(self, athletes):
        total = len(athletes or [])
        ready = 0
        partial = 0
        missing = 0
        hardware = 0
        focus_names = []
        athlete_ids = [
            int(aid) for aid in (self._safe_int(a.get("id")) for a in (athletes or [])) if aid
        ]
        sensors_bulk = {}
        devices_bulk = {}
        if athlete_ids and hasattr(self.db, "get_user_sensors_bulk"):
            try:
                sensors_bulk = self.db.get_user_sensors_bulk(athlete_ids) or {}
            except Exception:
                sensors_bulk = {}
        if athlete_ids and hasattr(self.db, "get_user_devices_bulk"):
            try:
                devices_bulk = self.db.get_user_devices_bulk(athlete_ids) or {}
            except Exception:
                devices_bulk = {}

        for athlete in athletes or []:
            athlete_id = athlete.get("id")
            athlete_name = athlete.get("name") or "Atleta"
            if not athlete_id:
                continue

            try:
                aid_int = int(athlete_id)
                codes = sensors_bulk.get(aid_int)
                if codes is None:
                    codes = self.db.get_user_sensors(aid_int) or []
            except Exception:
                codes = []

            try:
                aid_int = int(athlete_id)
                devices = devices_bulk.get(aid_int)
                if devices is None:
                    devices = self.db.get_user_devices(aid_int) or []
            except Exception:
                devices = []

            known_codes, _, _ = self._split_codes(codes)
            has_ecg = "ECG" in known_codes
            has_imu = any(str(code).startswith("IMU") for code in known_codes)

            if has_ecg and has_imu:
                ready += 1
            elif known_codes:
                partial += 1
                if len(focus_names) < 3:
                    focus_names.append(athlete_name)
            else:
                missing += 1
                if len(focus_names) < 3:
                    focus_names.append(athlete_name)

            if devices:
                hardware += 1

        return {
            "total": total,
            "ready": ready,
            "partial": partial,
            "missing": missing,
            "hardware": hardware,
            "focus_names": focus_names,
        }

    def _catalog_info_block_v2(self):
        all_cards = self._build_sensor_cards(self.ALL_CODES)
        return html.Div(
            style={"display": "grid", "gap": "12px"},
            children=all_cards,
        )

    def _build_sensor_cards(self, codes, devices_by_code: dict = None):
        """
        Construye cards de sensores.
        devices_by_code: dict { sensor_code: [device, ...] } para inyectar
        el estado de conexión de hardware real en cada card.
        """
        S  = self.S
        db = self.db
        devices_by_code = devices_by_code or {}

        cards = []
        for code in (codes or []):
            info    = S.catalog().get(code, {})
            name    = info.get("name", code)
            desc    = S.description(code)
            signals = S.pretty_signals_for(code)
            metrics = S.metrics_for(code)
            readiness = info.get("readiness")
            ingestion = info.get("ingestion")
            hardware_meta = []
            if readiness:
                hardware_meta.append(html.P(f"Estado hardware: {readiness}", className="sensor-meta"))
            if ingestion:
                hardware_meta.append(html.P(f"Ingesta: {ingestion}", className="sensor-meta"))

            _is_core    = code in self.CORE_CODES
            _badge_cls  = "sensor-badge sensor-badge--core" if _is_core else "sensor-badge sensor-badge--lab"
            _badge_lbl  = "Principal" if _is_core else "Opcional"

            # Filas de estado para cada dispositivo emparejado a este sensor
            device_rows = []
            for dev in devices_by_code.get(code, []):
                device_rows.append(self._device_status_row(dev))

            # Si no hay dispositivos, mostrar row vacío en modo "sin hardware"
            if not device_rows:
                device_rows = [
                    html.Div(
                        className="device-status-row",
                        children=[
                            self._status_dot("paired"),
                            html.Span("Sin hardware emparejado",
                                      className="device-status-row__label device-status-row__label--paired"),
                            html.Span("Usa 'Parear dispositivo' para conectar hardware real",
                                      className="device-status-row__meta"),
                        ],
                    )
                ]

            cards.append(
                html.Div(
                    className="card",
                    style={"padding": "16px"},
                    children=[
                        html.Div(
                            style={"display": "flex", "justifyContent": "space-between",
                                   "alignItems": "flex-start", "marginBottom": "8px"},
                            children=[
                                html.H4(name, className="card-title", style={"margin": 0}),
                                html.Span(_badge_lbl, className=_badge_cls),
                            ],
                        ),
                        html.P(desc, className="text-muted", style={"marginBottom": "10px"}),
                        *hardware_meta,
                        html.P(f"Señales: {signals}", className="sensor-meta"),
                        html.P(f"Métricas: {', '.join(metrics) if metrics else '—'}", className="sensor-meta"),
                        *device_rows,
                    ],
                )
            )
        return cards

    # ------------------------------------------------------------------ #
    #  Sección "Parear dispositivo"                                        #
    # ------------------------------------------------------------------ #

    def _pair_device_section(self, user_id: int):
        """
        Formulario para registrar un dispositivo físico (MAC/UUID BLE)
        y asociarlo a un sensor de un deportista.
        Solo visible para coach/admin.
        """
        sensor_opts = [
            {"label": f"{v.get('short', k)} ({k})", "value": k}
            for k, v in self.S.catalog().items()
        ]


        def _step(num, title, hint, control):
            return html.Div(className="pair-wizard-step", children=[
                html.Div(className="pair-wizard-step__left", children=[
                    html.Div(str(num), className="pair-device-step__num"),
                    html.Div(className="pair-wizard-step__line") if num < 3 else None,
                ]),
                html.Div(className="pair-wizard-step__body", children=[
                    html.Div(title, className="pair-device-step__title"),
                    html.Div(hint,  className="pair-device-step__text"),
                    html.Div(control, style={"marginTop": "10px"}),
                ]),
            ])

        return html.Details(
            className="card collapsible-card",
            open=False,
            children=[
                html.Summary(
                    className="collapsible-card__summary",
                    children=[
                        html.Div(className="collapsible-card__head", children=[
                            html.H4("Parear dispositivo físico", className="card-title"),
                            html.P("Vincula un sensor de hardware real a uno de tus deportistas.", className="text-muted"),
                        ]),
                        html.Span(">", className="collapsible-card__chevron"),
                    ],
                ),
                html.Div(
                    className="collapsible-card__body",
                    children=[
                        html.P(
                            "Completa los tres pasos en orden. Puedes registrar el dispositivo ahora y conectarlo más adelante.",
                            className="text-muted",
                            style={"fontSize": "13px", "marginBottom": "18px"},
                        ),

                        # ── Paso 1 ───────────────────────────────────────
                        _step(
                            1,
                            "¿Para qué deportista es el dispositivo?",
                            "El sensor quedará asociado a esta persona y su coach podrá ver su estado.",
                            dcc.Dropdown(
                                id="pair-user-select",
                                options=[],
                                placeholder="Selecciona deportista...",
                            ),
                        ),

                        # ── Paso 2 ───────────────────────────────────────
                        _step(
                            2,
                            "¿Qué tipo de sensor es?",
                            "Indica si es un ECG, IMU de guante, IMU de cabeza u otro del catálogo.",
                            dcc.Dropdown(
                                id="pair-sensor-code",
                                options=sensor_opts,
                                placeholder="Selecciona el tipo de sensor...",
                            ),
                        ),

                        # ── Paso 3 ───────────────────────────────────────
                        _step(
                            3,
                            "¿Cuál es el identificador del dispositivo?",
                            "Es el código único del hardware — suele ser una dirección MAC BLE (ej. AA:BB:CC:DD:EE:FF). Lo encontrarás en la etiqueta del sensor o en su app de configuración.",
                            html.Div([
                                dcc.Input(
                                    id="pair-device-id",
                                    type="text",
                                    placeholder="AA:BB:CC:DD:EE:FF",
                                    style={"width": "100%"},
                                ),
                                html.Div(className="filters-bar filters-bar--2", style={"marginTop": "10px"}, children=[
                                    html.Div(className="filter-item", children=[
                                        html.Label("Nombre para identificarlo (opcional)"),
                                        dcc.Input(
                                            id="pair-device-label",
                                            type="text",
                                            placeholder="Ej. Banda ECG Carlos #1",
                                            style={"width": "100%"},
                                        ),
                                    ]),
                                    html.Div(className="filter-item", children=[
                                        html.Label("Versión de firmware (opcional)"),
                                        dcc.Input(
                                            id="pair-firmware",
                                            type="text",
                                            placeholder="Ej. 1.2.3",
                                            style={"width": "100%"},
                                        ),
                                    ]),
                                ]),
                            ]),
                        ),

                        # ── CTA ──────────────────────────────────────────
                        html.Div(
                            style={"display": "flex", "alignItems": "center", "gap": "12px", "marginTop": "20px"},
                            children=[
                                html.Button("Registrar dispositivo", id="btn-pair-device",
                                            n_clicks=0, className="btn btn-primary"),
                                html.Div(id="pair-device-msg", className="text-muted"),
                            ],
                        ),

                    ],
                ),
            ],
        )

    def _layout_coach_admin_v2(self, role: str):
        user_id = session.get("user_id")
        coach_sport = str(session.get("sport") or "").strip() or None
        sport_label = coach_sport or "Deporte de combate"

        if role == "coach" and user_id:
            athletes = self.db.list_roster_for_coach(int(user_id), sport=coach_sport)
        else:
            athletes = [u for u in self.db.list_users() if u.get("role") == "deportista"]

        options_users = [
            {"label": f"{u['name']} | {u.get('sport', '-')}", "value": u["id"]}
            for u in athletes
        ]
        snapshot = self._coach_sensor_snapshot(athletes)
        focus_preview = ", ".join(snapshot["focus_names"]) if snapshot["focus_names"] else "El equipo ya tiene una base bastante ordenada."

        checklist = dcc.Checklist(
            id="chk-sensors",
            options=self.S.labels_for_checklist(),
            value=[],
            inputStyle={"marginRight": "8px"},
            labelStyle={"display": "block", "marginBottom": "6px"},
        )

        return html.Div([
            dcc.Interval(id="sens-poll-interval", interval=15_000, n_intervals=0),
            html.Div(className="profile-hero-grid", children=[
                html.Div(className="page-head profile-hero", children=[
                    html.Div(className="session-pill-row", children=[
                        html.Span(sport_label, className="session-pill"),
                        html.Span("Coach", className="session-pill session-pill--muted"),
                    ]),
                    html.H2("Sensores del equipo"),
                    html.P(
                        "Aquí defines la base de seguimiento del equipo antes de bajar al detalle de hardware o laboratorio.",
                        className="text-muted",
                    ),
                ]),
                html.Div(className="card profile-focus-card", children=[
                    html.H4("Qué conviene revisar primero", className="card-title"),
                    html.P(
                        "Empieza por quienes todavía no tienen una base clara con ECG e IMU, y luego confirma si hace falta apoyo extra.",
                        className="text-muted",
                    ),
                    html.Ul([
                        html.Li([html.Strong("Base lista: "), f"{snapshot['ready']} de {snapshot['total']}"]),
                        html.Li([html.Strong("Pendientes: "), f"{snapshot['missing'] + snapshot['partial']} del equipo"]),
                        html.Li([html.Strong("Hardware vinculado: "), f"{snapshot['hardware']} de {snapshot['total']}"]),
                        html.Li([html.Strong("Revisar primero: "), focus_preview]),
                    ], className="list-compact"),
                ]),
            ]),
            html.Div(className="kpis profile-kpis", children=[
                self._summary_kpi(
                    "Configuración completa",
                    f"{snapshot['ready']} / {snapshot['total']}" if snapshot["total"] else "0",
                    "Atletas con ECG + IMU listos para seguimiento completo",
                ),
                self._summary_kpi(
                    "Configuración parcial",
                    str(snapshot["partial"]),
                    "Tienen algún sensor pero les falta completar la base",
                ),
                self._summary_kpi(
                    "Sin configurar",
                    str(snapshot["missing"]),
                    "Atletas sin sensores asignados todavía",
                ),
                self._summary_kpi(
                    "Hardware vinculado",
                    f"{snapshot['hardware']} / {snapshot['total']}" if snapshot["total"] else "0",
                    "Dispositivos físicos ya emparejados en el sistema",
                ),
            ]),
            html.Div(className="profile-main-grid", children=[
                html.Div(className="profile-stack", children=[
                    html.Div(className="card", children=[
                        html.H4("Asignar base de seguimiento", className="card-title"),
                        html.P(
                            "Selecciona al deportista y deja clara su base principal antes de pensar en sensores extra.",
                            className="text-muted",
                            style={"marginBottom": "14px"},
                        ),
                        html.Div(className="filter-item", style={"marginBottom": "14px"}, children=[
                            html.Label("Deportista"),
                            dcc.Dropdown(
                                id="sel-user-sens",
                                options=options_users,
                                placeholder="Selecciona deportista...",
                            ),
                        ]),
                        html.Div(className="filter-item", style={"marginBottom": "16px"}, children=[
                            html.Label("Sensores asignados"),
                            html.P(
                                "Deja ECG + IMU como base. Suma apoyo extra solo si esa sesión realmente lo necesita.",
                                className="text-muted",
                                style={"fontSize": "12px", "marginBottom": "10px"},
                            ),
                            checklist,
                        ]),
                        html.Div(
                            style={"display": "flex", "alignItems": "center", "gap": "12px", "flexWrap": "wrap"},
                            children=[
                                html.Button("Guardar asignación", id="btn-save-sens", className="btn btn-primary"),
                                html.Div(id="sens-msg", className="text-muted"),
                            ],
                        ),
                    ]),
                    self._collapsible_group(
                        "Guía de base recomendada",
                        "Repasa aquí la base principal y el apoyo adicional cuando necesites afinar la configuración.",
                        [self._catalog_info_block_v2()],
                        open=False,
                    ),
                ]),
                html.Div(className="profile-stack", children=[
                    html.Div(className="card", children=[
                        html.H4("Lectura del deportista seleccionado", className="card-title"),
                        html.P(
                            "Aquí confirmas su base asignada y el estado del hardware antes de seguir.",
                            className="text-muted",
                            style={"marginBottom": "12px"},
                        ),
                        html.Div(
                            id="sensor-info",
                            children=[
                                self._empty_hint(
                                    "Selecciona un deportista para ver su base actual.",
                                    "Cuando elijas uno, aquí verás sus sensores y si ya hay hardware vinculado.",
                                )
                            ],
                        ),
                    ]),
                    self._pair_device_section(int(user_id) if user_id else 0),
                ]),
            ]),
        ], className="page-content profile-shell")

    # ------------------------------------------------------------------ #
    #  Layout DEPORTISTA                                                   #
    # ------------------------------------------------------------------ #

    def _layout_athlete(self):
        user_id = session.get("user_id")
        if not user_id:
            return html.Div("No se encontró tu sesión de deportista.")

        try:
            codes = self.db.get_user_sensors(int(user_id)) or []
        except Exception:
            codes = []

        # Dispositivos emparejados (con estado calculado)
        try:
            devices = self.db.get_user_devices(int(user_id)) or []
        except Exception:
            devices = []

        devices_by_code = {}
        for dev in devices:
            try:
                code = self.S.normalize_code(dev.get("sensor_code"))
            except Exception:
                code = dev.get("sensor_code")
            if code:
                devices_by_code.setdefault(code, []).append(dev)

        core_codes, lab_codes, other_codes = self._split_codes(codes)

        blocks = []
        summary = self._assignment_summary(codes)

        if core_codes:
            blocks.append(
                self._group_block(
                    "Mis sensores principales",
                    "Esta es la base que usarás con más frecuencia para seguir tu rendimiento.",
                    self._build_sensor_cards(core_codes, devices_by_code),
                )
            )
        if lab_codes:
            blocks.append(
                self._collapsible_group(
                    "Sensores opcionales / laboratorio",
                    "Ábrelo solo si quieres revisar sensores de sesiones más controladas.",
                    [html.Div(
                        self._build_sensor_cards(lab_codes, devices_by_code),
                        style={"display": "flex", "flexDirection": "column", "gap": "12px"},
                    )],
                    open=False,
                )
            )
        if other_codes:
            blocks.append(
                self._collapsible_group(
                    "Otros sensores asignados",
                    "Sensores extra asociados a tu perfil.",
                    [html.Div(
                        self._build_sensor_cards(other_codes, devices_by_code),
                        style={"display": "flex", "flexDirection": "column", "gap": "12px"},
                    )],
                    open=False,
                )
            )

        if not blocks:
            blocks = [
                self._empty_hint(
                    "Todavía no tienes sensores asignados.",
                    "Tu coach puede configurarlos desde su panel de sensores.",
                )
            ]

        return html.Div([
            html.Div(className="page-head", children=[
                html.H2("Mis sensores"),
                html.P(
                    "Aquí ves tu base de seguimiento actual y el estado de conexión de los dispositivos emparejados.",
                    className="text-muted",
                ),
            ]),
            html.Div(className="ecg-divider"),

            # La vista atleta renderiza sus cards al entrar; se evita polling oculto.
            dcc.Interval(id="sens-poll-interval", interval=15_000, n_intervals=0, disabled=True),

            summary,
            html.Div(style={"height": "16px"}),
            html.Div(blocks, style={"display": "flex", "flexDirection": "column", "gap": "16px"}),

            # Placeholders invisibles para callbacks del coach
            self._hidden_placeholders_for_callbacks(),
        ])

    # ------------------------------------------------------------------ #
    #  Placeholders invisibles                                             #
    # ------------------------------------------------------------------ #

    def _hidden_placeholders_for_callbacks(self):
        return html.Div(
            style={"display": "none"},
            children=[
                dcc.Dropdown(id="sel-user-sens"),
                dcc.Checklist(id="chk-sensors"),
                html.Button(id="btn-save-sens"),
                html.Div(id="sens-msg"),
                html.Div(id="sensor-info"),
                dcc.Input(id="pair-device-id"),
                dcc.Dropdown(id="pair-sensor-code"),
                dcc.Input(id="pair-device-label"),
                dcc.Input(id="pair-firmware"),
                dcc.Dropdown(id="pair-user-select"),
                html.Button(id="btn-pair-device"),
                html.Div(id="pair-device-msg"),
            ],
        )

    # ------------------------------------------------------------------ #
    #  Callbacks                                                           #
    # ------------------------------------------------------------------ #

    def _register_callbacks(self):
        app = self.app
        db  = self.db

        # ── Cargar sensores del deportista seleccionado ──────────────────
        @app.callback(
            Output("chk-sensors", "value"),
            Input("sel-user-sens", "value"),
            prevent_initial_call=True,
        )
        def load_user_sensors(user_id):
            if not user_id:
                raise PreventUpdate
            if not self._can_access_athlete(user_id):
                raise PreventUpdate
            try:
                return db.get_user_sensors(int(user_id)) or []
            except Exception:
                return []

        # ── Vista previa en vivo (asignación + dispositivos) ─────────────
        @app.callback(
            Output("sensor-info", "children"),
            Input("sel-user-sens", "value"),
            Input("chk-sensors",   "value"),
            Input("sens-poll-interval", "n_intervals"),
            prevent_initial_call=True,
        )
        def live_info_box(user_id, codes, _n):
            if not user_id:
                return []
            if not self._can_access_athlete(user_id):
                return self._empty_hint(
                    "No tienes permisos para ver este deportista.",
                    "Selecciona un atleta de tu roster para revisar sensores.",
                )
            try:
                # Dispositivos del usuario seleccionado
                devices = db.get_user_devices(int(user_id)) or []
                devices_by_code = {}
                for dev in devices:
                    try:
                        code = self.S.normalize_code(dev.get("sensor_code"))
                    except Exception:
                        code = dev.get("sensor_code")
                    if code:
                        devices_by_code.setdefault(code, []).append(dev)

                clean_codes = self._clean_sensor_codes(codes or [])
                core_codes, lab_codes, other_codes = self._split_codes(clean_codes)
                groups = [self._assignment_summary(clean_codes)]

                if core_codes:
                    groups.append(
                        self._group_block(
                            "Base principal seleccionada",
                            "Esta base es la que debería quedar más clara y más estable para ese deportista.",
                            self._build_sensor_cards(core_codes, devices_by_code),
                        )
                    )
                if lab_codes:
                    groups.append(
                        self._collapsible_group(
                            "Sensores opcionales / laboratorio",
                            "Ábrelo solo si necesitas revisar el apoyo adicional que quedará asociado.",
                            [html.Div(
                                self._build_sensor_cards(lab_codes, devices_by_code),
                                style={"display": "flex", "flexDirection": "column", "gap": "12px"},
                            )],
                            open=False,
                        )
                    )
                if other_codes:
                    groups.append(
                        self._collapsible_group(
                            "Otros sensores",
                            "Aquí quedan sensores adicionales asociados al perfil.",
                            [html.Div(
                                self._build_sensor_cards(other_codes, devices_by_code),
                                style={"display": "flex", "flexDirection": "column", "gap": "12px"},
                            )],
                            open=False,
                        )
                    )

                if not any([core_codes, lab_codes, other_codes]):
                    groups.append(
                        self._empty_hint(
                            "Todavía no hay sensores asignados para este deportista.",
                            "Puedes dejar lista una base simple con ECG + IMU y guardar cuando lo tengas claro.",
                        )
                    )

                # ── Historial: sensor_sessions por sesión de entrenamiento ──
                try:
                    recent_sessions = db.list_sessions(int(user_id), limit=10) or []
                    ss_cards = []
                    for s in recent_sessions[:5]:
                        ss_summary = db.get_sensor_data_summary(s["id"])
                        if not ss_summary:
                            continue
                        ts_label = (s.get("ts_start") or "")[:16].replace("T", " ") or "Sin fecha"
                        kpi_items = [
                            html.Span(
                                [html.Strong(f"{code}: "), f"{info['sample_count']} paq."],
                                style={"marginRight": "14px", "fontSize": "12px"},
                            )
                            for code, info in ss_summary.items()
                        ]
                        ss_cards.append(
                            html.Div(
                                style={"borderBottom": "1px solid var(--line)",
                                       "paddingBottom": "8px", "marginBottom": "8px"},
                                children=[
                                    html.Div([
                                        html.Strong(ts_label, style={"fontSize": "13px"}),
                                        html.Span(f" · {s.get('sport') or ''}", className="text-muted",
                                                  style={"fontSize": "12px"}),
                                    ]),
                                    html.Div(kpi_items, style={"marginTop": "4px"}),
                                ],
                            )
                        )
                    if ss_cards:
                        groups.append(
                            html.Div(
                                className="card",
                                style={"marginTop": "16px"},
                                children=[
                                    html.H5("Actividad de sensores por sesión",
                                            className="card-title",
                                            style={"marginBottom": "8px"}),
                                    html.P("Paquetes recibidos por sensor en las últimas sesiones.",
                                           className="text-muted",
                                           style={"fontSize": "12px", "marginBottom": "10px"}),
                                    *ss_cards,
                                ],
                            )
                        )
                except Exception:
                    pass

                return groups
            except Exception:
                return []

        # ── Guardar asignación ───────────────────────────────────────────
        @app.callback(
            Output("sens-msg", "children"),
            Input("btn-save-sens", "n_clicks"),
            State("sel-user-sens", "value"),
            State("chk-sensors",   "value"),
            prevent_initial_call=True,
        )
        def save_user_sensors(n, user_id, codes):
            role = str(session.get("role") or "")
            if role not in ("coach", "admin"):
                return "No tienes permisos para modificar sensores."
            if not user_id:
                return "Selecciona un deportista."
            if not self._can_access_athlete(user_id):
                return "No tienes permisos para modificar sensores de este deportista."
            clean_codes = self._clean_sensor_codes(codes or [])
            try:
                db.set_user_sensors(int(user_id), clean_codes)
            except Exception:
                return "Error guardando sensores (DB)."
            return "Asignación guardada."

        # ── Rellenar dropdown de deportistas en el formulario de parear ──
        @app.callback(
            Output("pair-user-select", "options"),
            Input("sens-poll-interval", "n_intervals"),
            prevent_initial_call=False,
        )
        def fill_pair_user_opts(_n):
            if _n:
                raise PreventUpdate
            role    = str(session.get("role") or "")
            user_id = session.get("user_id")
            if role == "coach" and user_id:
                coach_sport = str(session.get("sport") or "").strip() or None
                athletes = db.list_roster_for_coach(int(user_id), sport=coach_sport)
            elif role == "admin":
                athletes = [u for u in db.list_users() if u.get("role") == "deportista"]
            else:
                athletes = []
            return [
                {"label": f"{u['name']} · {u.get('sport', '-')}", "value": u["id"]}
                for u in athletes
            ]

        # ── Registrar / emparejar dispositivo ────────────────────────────
        @app.callback(
            Output("pair-device-msg", "children"),
            Input("btn-pair-device", "n_clicks"),
            State("pair-device-id",    "value"),
            State("pair-sensor-code",  "value"),
            State("pair-device-label", "value"),
            State("pair-firmware",     "value"),
            State("pair-user-select",  "value"),
            prevent_initial_call=True,
        )
        def register_device(n, device_id, sensor_code, label, firmware, target_user_id):
            role = str(session.get("role") or "")
            if role not in ("coach", "admin"):
                return "No tienes permisos."
            if not device_id or not device_id.strip():
                return "Introduce el ID del dispositivo (MAC / UUID)."
            try:
                clean_sensor_code = self.S.normalize_code(sensor_code)
            except Exception:
                clean_sensor_code = str(sensor_code or "").strip().upper()
            if not clean_sensor_code:
                return "Selecciona el tipo de sensor."
            if clean_sensor_code not in set((self.S.catalog() or {}).keys()):
                return "Selecciona un tipo de sensor válido."
            if not target_user_id:
                return "Selecciona el deportista al que asociar el dispositivo."
            if not self._can_access_athlete(target_user_id):
                return "No tienes permisos para emparejar dispositivos a este deportista."
            clean_device_id = device_id.strip()[:128]
            try:
                db.register_device(
                    int(target_user_id),
                    clean_sensor_code,
                    clean_device_id,
                    device_label=(label or "").strip()[:120] or None,
                    firmware_version=(firmware or "").strip()[:80] or None,
                )
                return f"Dispositivo '{clean_device_id}' emparejado con éxito."
            except Exception as exc:
                return f"Error al registrar: {exc}"
