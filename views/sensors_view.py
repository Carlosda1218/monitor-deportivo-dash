# views/sensors_view.py

from flask import session
from dash import html, dcc, Input, Output, State
from dash.exceptions import PreventUpdate


class SensorsView:
    """
    Vista 'Sensores del producto'.

    - Para COACH / ADMIN:
        * Seleccionar deportista
        * Asignar sensores
        * Diferenciar sensores principales vs sensores opcionales / laboratorio

    - Para DEPORTISTA:
        * Ver 'Mis sensores' con explicación breve
        * Entender qué sensores forman el seguimiento principal
    """

    _callbacks_registered = False  # ✅ evita registro doble

    CORE_CODES = ["ECG", "IMU_GLOVE", "IMU_HEAD"]
    LAB_CODES = ["EMG_ARM", "EMG_LEG", "RESP_BELT"]

    def __init__(self, app, db, sensors_module):
        self.app = app
        self.db = db
        self.S = sensors_module

        # ✅ registra callbacks solo una vez
        if not SensorsView._callbacks_registered:
            self._register_callbacks()
            SensorsView._callbacks_registered = True

    # ---------- Layout principal ----------

    def layout(self):
        if not session.get("user_id"):
            return html.Div("Inicia sesión para ver esta página.")

        role = str(session.get("role") or "no autenticado")

        if role in ("coach", "admin"):
            return self._layout_coach_admin(role)

        if role == "deportista":
            return self._layout_athlete()

        return html.Div("No tienes permisos para ver esta página.")

    # ---------- Helpers UI ----------

    def _split_codes(self, codes):
        codes = codes or []
        core = [c for c in codes if c in self.CORE_CODES]
        lab = [c for c in codes if c in self.LAB_CODES]
        other = [c for c in codes if c not in self.CORE_CODES + self.LAB_CODES]
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
                html.Div(sub, className="kpi-sub"),
                html.Div(className="kpi-ecg-line"),
            ],
        )

    def _assignment_summary(self, codes):
        core_codes, lab_codes, other_codes = self._split_codes(codes or [])
        has_ecg = "ECG" in core_codes
        has_imu = any(code.startswith("IMU") for code in core_codes)

        if has_ecg and has_imu:
            base_value = "Base lista"
            base_sub = "ECG + IMU preparados para seguimiento"
        elif core_codes:
            base_value = "Base parcial"
            base_sub = self._sensor_names(core_codes)
        else:
            base_value = "Sin base"
            base_sub = "Falta definir la base principal"

        lab_value = str(len(lab_codes)) if lab_codes else "0"
        lab_sub = self._sensor_names(lab_codes) if lab_codes else "Sin apoyo adicional"

        other_value = str(len(other_codes)) if other_codes else "0"
        other_sub = self._sensor_names(other_codes) if other_codes else "Sin sensores extra"

        return html.Div(
            className="kpis kpis--tight",
            children=[
                self._summary_kpi("Seguimiento base", base_value, base_sub),
                self._summary_kpi("Apoyo opcional", lab_value, lab_sub),
                self._summary_kpi("Otros sensores", other_value, other_sub),
            ],
        )

    def _empty_hint(self, title, detail):
        return html.Div(
            className="inner-card",
            style={"padding": "24px", "textAlign": "center"},
            children=[
                html.P(title, style={"fontWeight": "600", "marginBottom": "6px"}),
                html.P(detail, className="text-muted"),
            ],
        )

    def _catalog_info_block(self):
        core_cards = self._build_sensor_cards(self.CORE_CODES)
        lab_cards = self._build_sensor_cards(self.LAB_CODES)
        return html.Div(
            style={"display": "grid", "gap": "16px"},
            children=[
                self._group_block(
                    "Base principal recomendada",
                    "Deja esta base visible para que el seguimiento diario se entienda rápido.",
                    core_cards,
                ),
                self._collapsible_group(
                    "Sensores opcionales / laboratorio",
                    "Ábrelo solo cuando necesites apoyo adicional o una sesión más controlada.",
                    [html.Div(lab_cards, style={"display": "flex", "flexDirection": "column", "gap": "12px"})],
                    open=False,
                ),
            ],
        )

    def _build_sensor_cards(self, codes, user_id_for_last_metrics=None):
        """
        Construye cards de sensores usando clases CSS (sin estilos inline)
        para mantener consistencia visual a nivel app.
        """
        S = self.S
        db = self.db

        try:
            last_ecg = db.get_last_ecg_metrics(int(user_id_for_last_metrics)) if user_id_for_last_metrics else None
        except Exception:
            last_ecg = None

        cards = []
        for code in (codes or []):
            info = S.catalog().get(code, {})
            name = info.get("name", code)
            desc = S.description(code)
            signals = S.pretty_signals_for(code)
            metrics = S.metrics_for(code)

            if code == "ECG" and last_ecg:
                try:
                    last_metric = f"{float(last_ecg.get('bpm', 0) or 0):.0f} BPM"
                except Exception:
                    last_metric = "—"
            else:
                last_metric = "—"

            _is_core = code in self.CORE_CODES
            _badge_cls = "sensor-badge sensor-badge--core" if _is_core else "sensor-badge sensor-badge--lab"
            _badge_lbl = "Principal" if _is_core else "Opcional"

            cards.append(
                html.Div(
                    className="card",
                    style={"padding": "16px"},
                    children=[
                        html.Div(
                            style={"display": "flex", "justifyContent": "space-between", "alignItems": "flex-start", "marginBottom": "8px"},
                            children=[
                                html.H4(name, className="card-title", style={"margin": 0}),
                                html.Span(_badge_lbl, className=_badge_cls),
                            ],
                        ),
                        html.P(desc, className="text-muted", style={"marginBottom": "10px"}),
                        html.P(f"Señales: {signals}", className="sensor-meta"),
                        html.P(f"Métricas: {', '.join(metrics) if metrics else '—'}", className="sensor-meta"),
                        html.P(f"Última métrica: {last_metric}", className="sensor-meta"),
                    ],
                )
            )
        return cards

    def _hidden_placeholders_for_callbacks(self):
        """
        Evita errores si Dash valida callbacks y no encuentra IDs
        cuando el layout del deportista NO incluye componentes del coach/admin.
        """
        return html.Div(
            style={"display": "none"},
            children=[
                dcc.Dropdown(id="sel-user-sens"),
                dcc.Checklist(id="chk-sensors"),
                html.Button(id="btn-save-sens"),
                html.Div(id="sens-msg"),
                html.Div(id="sensor-info"),
            ],
        )

    # ---------- Layout COACH / ADMIN ----------

    def _layout_coach_admin(self, role: str):
        user_id = session.get("user_id")

        if role == "coach" and user_id:
            athletes = self.db.list_athletes_for_coach(int(user_id))
        else:  # admin
            athletes = [
                u for u in self.db.list_users()
                if (u.get("role", "deportista") == "deportista")
            ]

        options_users = [
            {"label": f"{u['name']} · {u.get('sport', '-')}", "value": u["id"]}
            for u in athletes
        ]

        checklist = dcc.Checklist(
            id="chk-sensors",
            options=self.S.labels_for_checklist(),
            value=[],
            inputStyle={"marginRight": "8px"},
            labelStyle={"display": "block", "marginBottom": "6px"},
        )

        return html.Div([
            html.Div(className="page-head", children=[
                html.H2("Sensores"),
                html.P(
                    "Aquí defines qué base de seguimiento usará cada deportista y qué apoyo extra solo conviene abrir en casos puntuales.",
                    className="text-muted",
                ),
            ]),
            html.Div(className="ecg-divider"),
            self._catalog_info_block(),
            html.Div(
                className="grid-2col",
                style={"marginTop": "20px"},
                children=[
                    html.Div(className="card", children=[
                        html.H4("Asignar sensores", className="card-title"),
                        html.P(
                            "Selecciona al deportista y deja lista una base clara para el seguimiento del día a día.",
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
                                "Deja ECG + IMU como base. Lo demás solo si realmente aporta a esa sesión.",
                                className="text-muted",
                                style={"fontSize": "12px", "marginBottom": "10px"},
                            ),
                            checklist,
                        ]),
                        html.Div(
                            style={"display": "flex", "alignItems": "center", "gap": "12px"},
                            children=[
                                html.Button("Guardar asignación", id="btn-save-sens", className="btn btn-primary"),
                                html.Div(id="sens-msg", className="text-muted"),
                            ],
                        ),
                    ]),
                    html.Div(className="card", children=[
                        html.H4("Vista previa", className="card-title"),
                        html.P(
                            "Aquí puedes comprobar rápido si la base quedó clara antes de guardar.",
                            className="text-muted",
                            style={"marginBottom": "12px"},
                        ),
                        html.Div(
                            id="sensor-info",
                            children=[
                                self._empty_hint(
                                    "Selecciona un deportista para ver su base de seguimiento.",
                                    "Cuando elijas uno, aquí verás qué queda como principal y qué pasa a apoyo opcional.",
                                )
                            ],
                        ),
                    ]),
                ],
            ),
        ])

    # ---------- Layout DEPORTISTA ----------

    def _layout_athlete(self):
        user_id = session.get("user_id")
        if not user_id:
            return html.Div("No se encontró tu sesión de deportista.")

        try:
            codes = self.db.get_user_sensors(int(user_id)) or []
        except Exception:
            codes = []

        core_codes, lab_codes, other_codes = self._split_codes(codes)

        blocks = []
        summary = self._assignment_summary(codes)
        if core_codes:
            blocks.append(
                self._group_block(
                    "Mis sensores principales",
                    "Esta es la base que usarás con más frecuencia para seguir tu rendimiento.",
                    self._build_sensor_cards(core_codes, user_id_for_last_metrics=int(user_id)),
                )
            )
        if lab_codes:
            blocks.append(
                self._collapsible_group(
                    "Sensores opcionales / laboratorio",
                    "Ábrelo solo si quieres revisar sensores que se usan en sesiones más controladas.",
                    [html.Div(self._build_sensor_cards(lab_codes, user_id_for_last_metrics=int(user_id)), style={"display": "flex", "flexDirection": "column", "gap": "12px"})],
                    open=False,
                )
            )
        if other_codes:
            blocks.append(
                self._collapsible_group(
                    "Otros sensores asignados",
                    "Aquí quedan sensores extra asociados a tu perfil.",
                    [html.Div(self._build_sensor_cards(other_codes, user_id_for_last_metrics=int(user_id)), style={"display": "flex", "flexDirection": "column", "gap": "12px"})],
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
                    "Aquí ves tu base de seguimiento actual y los sensores extra que solo se usan cuando hace falta más contexto.",
                    className="text-muted",
                ),
            ]),
            html.Div(className="ecg-divider"),
            summary,
            html.Div(style={"height": "16px"}),
            html.Div(blocks, style={"display": "flex", "flexDirection": "column", "gap": "16px"}),
            # placeholders invisibles para evitar errores de callbacks en modo deportista
            self._hidden_placeholders_for_callbacks(),
        ])

    # ---------- Callbacks ----------

    def _register_callbacks(self):
        app = self.app
        db = self.db

        @app.callback(
            Output("chk-sensors", "value"),
            Input("sel-user-sens", "value"),
            prevent_initial_call=True,
        )
        def load_user_sensors(user_id):
            if not user_id:
                raise PreventUpdate
            try:
                return db.get_user_sensors(int(user_id)) or []
            except Exception:
                return []

        # ✅ Panel en vivo (sin guardar)
        @app.callback(
            Output("sensor-info", "children"),
            Input("sel-user-sens", "value"),
            Input("chk-sensors", "value"),
            prevent_initial_call=True,
        )
        def live_info_box(user_id, codes):
            if not user_id:
                return []
            try:
                core_codes, lab_codes, other_codes = self._split_codes(codes or [])
                groups = [self._assignment_summary(codes or [])]
                if core_codes:
                    groups.append(
                        self._group_block(
                            "Base principal seleccionada",
                            "Esta base es la que debería quedar más clara y más estable para ese deportista.",
                            self._build_sensor_cards(core_codes, user_id_for_last_metrics=int(user_id)),
                        )
                    )
                if lab_codes:
                    groups.append(
                        self._collapsible_group(
                            "Sensores opcionales / laboratorio",
                            "Ábrelo solo si necesitas revisar el apoyo adicional que quedará asociado.",
                            [html.Div(self._build_sensor_cards(lab_codes, user_id_for_last_metrics=int(user_id)), style={"display": "flex", "flexDirection": "column", "gap": "12px"})],
                            open=False,
                        )
                    )
                if other_codes:
                    groups.append(
                        self._collapsible_group(
                            "Otros sensores",
                            "Aquí quedan sensores adicionales asociados al perfil.",
                            [html.Div(self._build_sensor_cards(other_codes, user_id_for_last_metrics=int(user_id)), style={"display": "flex", "flexDirection": "column", "gap": "12px"})],
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
                return groups
            except Exception:
                return []

        # ✅ Guardar asignación (SOLO msg) -> evita outputs duplicados
        @app.callback(
            Output("sens-msg", "children"),
            Input("btn-save-sens", "n_clicks"),
            State("sel-user-sens", "value"),
            State("chk-sensors", "value"),
            prevent_initial_call=True,
        )
        def save_user_sensors(n, user_id, codes):
            role = str(session.get("role") or "no autenticado")

            if role not in ("coach", "admin"):
                return "No tienes permisos para modificar sensores."

            if not user_id:
                return "Selecciona usuario."

            try:
                db.set_user_sensors(int(user_id), codes or [])
            except Exception:
                return "Error guardando sensores (DB)."

            return "Asignación guardada."
