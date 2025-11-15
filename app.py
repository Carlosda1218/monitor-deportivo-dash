import os, io, base64, json, csv, webbrowser, importlib, traceback
from threading import Timer
from datetime import datetime

import numpy as np
import plotly.graph_objects as go

from flask import Flask, session
import dash
from dash import html, dcc, Input, Output, State, callback_context
from dash.dash_table import DataTable
from dash.exceptions import PreventUpdate

import db
import sensors as S
import questionnaires as Q

# ====== Flask + Dash ======
server = Flask(__name__)
server.secret_key = os.environ.get("POWERSYNC_SECRET", "dev-secret-change-me")

app = dash.Dash(
    __name__,
    server=server,
    title="PowerSync",
    suppress_callback_exceptions=True
)

# ====== DB init ======
db.init_db()

# ====== Estilos layout ======
SIDEBAR_STYLE = {
    "position":"fixed","top":0,"left":0,"bottom":0,"width":"260px",
    "padding":"18px 14px","background":"#121722","color":"#E7ECF3",
    "borderRight":"1px solid #1f2630"
}
PAGE_STYLE = {"marginLeft":"260px","padding":"18px"}
def h2(txt): return html.H2(txt, style={"margin":"6px 0 12px"})

# ====== Helpers de serialización ======
def _to_str(v):
    if isinstance(v, (bytes, bytearray)):
        try:
            return v.decode("utf-8")
        except Exception:
            return v.decode("latin1", "ignore")
    return v

# ====== Helpers ECG (sin pandas / sin scipy) ======
def read_ecg_csv(path:str, fs_default:int=250):
    with open(path, newline='', encoding='utf-8') as f:
        reader = csv.reader(f)
        rows = list(reader)
    if not rows:
        return np.array([]), np.array([]), fs_default

    header = [h.strip().lower() for h in rows[0]]
    has_header = any(header) and ("ecg" in header or "time" in header or "tiempo" in header)
    data_rows = rows[1:] if has_header else rows

    time_col = None
    ecg_col = None
    if has_header:
        for i, name in enumerate(header):
            if name in ("time","tiempo"): time_col = i
            if name == "ecg": ecg_col = i
    if ecg_col is None:
        ecg_col = 0

    x_vals, t_vals = [], []
    for r in data_rows:
        if not r or all((c or "").strip()=="" for c in r):
            continue
        try:
            x_vals.append(float(r[ecg_col]))
        except:
            continue
        if time_col is not None and time_col < len(r):
            try: t_vals.append(float(r[time_col]))
            except: t_vals.append(None)
        else:
            t_vals.append(None)

    x = np.array(x_vals, dtype=float)
    has_time = all(v is not None for v in t_vals) and len(t_vals)>1
    if has_time:
        t = np.array(t_vals, dtype=float)
        diffs = np.diff(t)
        fs = int(round(1.0/np.mean(diffs))) if np.all(diffs>0) else fs_default
    else:
        fs = fs_default
        t = np.arange(len(x))/fs
    return t, x, fs

def smooth(x:np.ndarray, win_ms:int, fs:int):
    win = max(3, int(round(win_ms*fs/1000)))
    if win % 2 == 0: win += 1
    if win >= len(x): win = max(3, len(x)//2*2+1)
    k = np.ones(win)/win
    return np.convolve(x, k, mode="same")

def _find_peaks_simple(s, height, distance):
    s = np.asarray(s); n = len(s)
    if n < 3: return np.array([], dtype=int)
    cand = np.where((s[1:-1] > s[:-2]) & (s[1:-1] >= s[2:]) & (s[1:-1] >= height))[0] + 1
    if cand.size == 0: return cand
    order = cand[np.argsort(s[cand])[::-1]]
    kept = []; blocked = np.zeros(n, dtype=bool)
    for idx in order:
        a = max(0, idx - distance); b = min(n, idx + distance + 1)
        if blocked[a:b].any(): continue
        kept.append(idx); blocked[a:b] = True
    return np.array(sorted(kept), dtype=int)

def detect_r_peaks(x:np.ndarray, fs:int, sens:float=0.6):
    z = (x - np.median(x))
    env = smooth(np.abs(z), win_ms=80, fs=fs)
    thr = np.quantile(env, sens)
    dist = int(0.25*fs)
    peaks = _find_peaks_simple(env, height=thr, distance=dist)
    return peaks

def ecg_metrics_from_peaks(peaks:np.ndarray, fs:int):
    if peaks is None or len(peaks) < 2:
        return 0.0, 0.0, 0.0
    rr = np.diff(peaks)/fs
    bpm = 60.0/np.mean(rr)
    sdnn = 1000*np.std(rr)
    rmssd = 1000*np.sqrt(np.mean(np.diff(rr)**2))
    return float(bpm), float(sdnn), float(rmssd)

def fig_ecg(t, x, peaks=None, title="ECG"):
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=t, y=x, mode="lines", name="ECG",
                             line=dict(color="#00f28a", width=2)))
    if peaks is not None and len(peaks)>0:
        fig.add_trace(go.Scatter(x=t[peaks], y=x[peaks], mode="markers", name="Picos R",
                                 marker=dict(size=7, symbol="x", color="#00f28a")))
    fig.update_layout(margin=dict(l=40,r=10,t=40,b=40), height=420,
                      template="plotly_dark", title=title, legend=dict(orientation="h"))
    return fig

# ====== KPI UI ======
def kpi_card(label, value, suffix=""):
    return html.Div(className="kpi", children=[
        html.Div(label, className="kpi-label"),
        html.Div(f"{value}{suffix}", className="kpi-value"),
        html.Div(className="kpi-ecg-line")
    ])

def kpi_grid(bpm, sdnn, rmssd):
    return [kpi_card("BPM", f"{bpm:.0f}"),
            kpi_card("SDNN", f"{sdnn:.0f}", " ms"),
            kpi_card("RMSSD", f"{rmssd:.0f}", " ms")]

# ====== Sidebar ======
def _sidebar_links():
    logged = bool(session.get("user_id"))
    role = _to_str(session.get("role")) or "no autenticado"
    name = _to_str(session.get("name")) if session.get("name") else None
    session["role"] = role
    if name is not None:
        session["name"] = name

    if not logged:
        links = [
            dcc.Link("Iniciar sesión", href="/login", className="nav-link"),
            dcc.Link("Registrarse", href="/registro", className="nav-link"),
        ]
    else:
        links = [
            dcc.Link("Dashboard", href="/dashboard", className="nav-link"),
            dcc.Link("Usuarios", href="/usuarios", className="nav-link"),
            dcc.Link("Sensores", href="/sensores", className="nav-link"),
            dcc.Link("ECG", href="/ecg", className="nav-link"),
            dcc.Link("Cuestionario", href="/cuestionario", className="nav-link"),
            dcc.Link("Histórico", href="/historico", className="nav-link"),
            dcc.Link("Salir", href="/logout", className="nav-link"),
        ]
    role_badge = html.Div([
        html.Span("Rol: ", style={"color":"#9aa5b1"}),
        html.Span(role, className="badge-role")
    ], style={"margin":"12px 0 8px 4px"})
    return [role_badge] + links

sidebar = html.Div(id="sidebar", style=SIDEBAR_STYLE, children=[
    html.Div(style={"display":"flex","gap":"10px","alignItems":"center","fontWeight":700,"fontSize":"18px"}, children=[
        html.Img(src="/assets/logo_powersync.svg", style={"height":"22px"}),
        html.Span("PowerSync")
    ]),
    html.Hr(),
    html.Div(id="sidebar-links")
])
content = html.Div(id="page-content", style=PAGE_STYLE)

app.layout = html.Div([
    dcc.Location(id="url"),
    sidebar,
    content,
    dcc.Download(id="dl-png"),
    dcc.Download(id="dl-peaks"),
])

@app.callback(Output("sidebar-links","children"), Input("url","pathname"))
def _render_sidebar(_):
    return _sidebar_links()

# ====== IMPORT páginas externas ======
def _safe_import(modname: str):
    try:
        mod = importlib.import_module(modname)
        return mod, None
    except Exception:
        return None, traceback.format_exc()

page_login, err_login         = _safe_import("pages.auth_login")
page_register, err_register   = _safe_import("pages.auth_register")
page_dashboard, err_dashboard = _safe_import("pages.dashboard")
page_logout, err_logout       = _safe_import("pages.logout")

# =========================
#        VISTAS
# =========================

# ---- USUARIOS ----
def view_usuarios():
    users = db.list_users()
    sports_base = ["Taekwondo","Judo","Kickboxing","Box","Muay Thai","MMA","Karate","Sambo"]
    sports_opts = [{"label": s, "value": s} for s in sports_base] + [{"label":"Otra (especificar)","value":"OTRA"}]

    table = DataTable(
        id="tbl-users",
        data=users,
        columns=[
            {"name":"ID","id":"id"},
            {"name":"Nombre","id":"name"},
            {"name":"Rol","id":"role"},
            {"name":"Deporte","id":"sport"},
            {"name":"Alta","id":"created_at"}
        ],
        page_size=8, style_table={"overflowX":"auto"},
        style_cell={"background":"#151a21","color":"#E7ECF3","border":"1px solid #232a36"},
        sort_action="native", filter_action="native"
    )

    return html.Div([
        h2("Gestión de usuarios"),
        html.Div(
            style={"display":"grid","gridTemplateColumns":"1fr 220px 1fr 140px","gap":"8px","marginBottom":"10px"},
            children=[
                dcc.Input(id="in-name", type="text", placeholder="Nombre completo"),
                dcc.Dropdown(id="in-sport", options=sports_opts, placeholder="deporte"),
                html.Div(id="sport-custom-box", style={"display":"none"}, children=[
                    dcc.Input(id="in-sport-custom", type="text", placeholder="Especifica deporte/arte marcial")
                ]),
                html.Button("Añadir", id="btn-add", n_clicks=0, className="btn btn-primary"),
            ]
        ),
        html.Div(
            style={"display":"grid","gridTemplateColumns":"1fr 140px","gap":"8px","marginBottom":"10px"},
            children=[
                dcc.Dropdown(
                    id="in-del-user",
                    options=[{"label":f"{u['name']} ({u.get('role','?')})","value":u["id"]} for u in users],
                    placeholder="Selecciona usuario"
                ),
                html.Button("Eliminar", id="btn-del", n_clicks=0, className="btn btn-danger")
            ]
        ),
        table,
        html.Div(id="users-msg", style={"marginTop":"8px","color":"#FFB4B4"})
    ])

@app.callback(Output("sport-custom-box","style"), Input("in-sport","value"))
def toggle_custom_sport(selected):
    return {} if selected == "OTRA" else {"display":"none"}

@app.callback(
    Output("tbl-users","data", allow_duplicate=True),
    Output("in-del-user","options", allow_duplicate=True),
    Output("users-msg","children"),
    Input("btn-add","n_clicks"),
    Input("btn-del","n_clicks"),
    State("in-name","value"),
    State("in-sport","value"),
    State("in-sport-custom","value"),
    State("in-del-user","value"),
    prevent_initial_call=True
)
def user_actions(n_add, n_del, name, sport, sport_custom, del_user_id):
    trig = [t["prop_id"] for t in callback_context.triggered][0]
    msg = ""

    if "btn-add" in trig:
        if not name:
            msg = "Nombre requerido."
        else:
            if sport == "OTRA":
                if not (sport_custom and sport_custom.strip()):
                    msg = "Especifica el deporte en el campo 'Otro'."
                else:
                    db.add_user(name, sport_custom.strip(), role="deportista")
                    msg = "Usuario añadido."
            else:
                db.add_user(name, sport, role="deportista")
                msg = "Usuario añadido."

    elif "btn-del" in trig:
        if not del_user_id:
            msg = "Selecciona usuario a eliminar."
        else:
            db.delete_user(int(del_user_id))
            msg = "Usuario eliminado."

    users = db.list_users()
    options = [{"label":f"{u['name']} ({u.get('role','?')})","value":u["id"]} for u in users]
    return users, options, msg

# ---- SENSORES ----
def view_sensores():
    athletes = [u for u in db.list_users() if (u.get("role","deportista") == "deportista")]
    options_users = [{"label": f"{u['name']} · {u.get('sport','-')}", "value": u["id"]} for u in athletes]

    checklist = dcc.Checklist(
        id="chk-sensors",
        options=S.labels_for_checklist(),
        value=[],
        inputStyle={"marginRight":"8px"},
        labelStyle={"display":"block","marginBottom":"6px"}
    )
    info_box = html.Div(id="sensor-info", style={"marginTop":"12px"})

    return html.Div([
        h2("Sensores"),
        html.Small("Tip: aquí sólo aparecen deportistas (los coaches no se pueden seleccionar).", style={"opacity":0.8}),
        html.Br(),
        html.Div(style={"display":"grid","gridTemplateColumns":"1fr 1fr","gap":"18px"}, children=[
            html.Div(children=[
                html.Label("Deportista"),
                dcc.Dropdown(id="sel-user-sens", options=options_users, placeholder="Selecciona deportista..."),
                html.Br(),
                html.Label("Tipo de sensores asignados"),
                checklist,
                html.Br(),
                html.Button("Guardar asignación", id="btn-save-sens", className="btn btn-primary")
            ]),
            html.Div(children=[
                html.Label("Información de sensores asignados"),
                info_box
            ])
        ]),
        html.Div(id="sens-msg", style={"marginTop":"8px","color":"#FFB4B4"})
    ])

@app.callback(Output("chk-sensors","value"), Input("sel-user-sens","value"), prevent_initial_call=True)
def load_user_sensors(user_id):
    if not user_id: raise PreventUpdate
    return db.get_user_sensors(int(user_id))

@app.callback(
    Output("sens-msg","children"),
    Output("sensor-info","children"),
    Input("btn-save-sens","n_clicks"),
    State("sel-user-sens","value"),
    State("chk-sensors","value"),
    prevent_initial_call=True
)
def save_user_sensors(n, user_id, codes):
    if not user_id: return "Selecciona usuario.", []
    db.set_user_sensors(int(user_id), codes or [])
    cards = []
    last = db.get_last_ecg_metrics(int(user_id))
    for code in (codes or []):
        last_metric = f"{last['bpm']:.0f} BPM" if (code=="ECG" and last) else "—"
        cards.append(html.Div(style={"background":"#151a21","padding":"12px","borderRadius":"10px","marginBottom":"8px","border":"1px solid #232a36"}, children=[
            html.B(S.catalog()[code]["name"]),
            html.Div(S.description(code)),
            html.Small(" Última métrica: " + last_metric)
        ]))
    return "Asignación guardada.", cards

# ---- ECG ----
def view_ecg():
    athletes = [u for u in db.list_users() if (u.get("role","deportista") == "deportista")]
    options_users = [{"label": f"{u['name']} · {u.get('sport','-')}", "value": u["id"]} for u in athletes]

    return html.Div([
        h2("Monitorización ECG"),
        html.Small("Sólo deportistas pueden tener ficheros ECG.", style={"opacity":0.8}),
        html.Br(),
        html.Div(style={"display":"grid","gridTemplateColumns":"1fr 1fr","gap":"16px"}, children=[
            html.Div(children=[
                html.Label("Deportista"),
                dcc.Dropdown(id="ecg-user", options=options_users, placeholder="Selecciona deportista..."),
                html.Br(),
                html.Label("Subir archivo ECG (.csv)"),
                dcc.Upload(id="ecg-upload", children=html.Div("Arrastra o elige un archivo"),
                           multiple=False, style={"padding":"12px","border":"1px dashed #2b3a52","borderRadius":"10px"}),
                html.Button("Cargar ECG de ejemplo", id="btn-ecg-demo", style={"marginTop":"10px"}, className="btn btn-ghost"),
                html.Br(), html.Br(),
                html.Label("Ficheros ECG del usuario"),
                dcc.Dropdown(id="ecg-file", placeholder="No hay archivos aún..."),
                html.Br(),
                dcc.Checklist(options=[{"label":" Mostrar picos R","value":"r"}], value=[], id="ecg-showr"),
                html.Label("Sensibilidad picos (umbral)"),
                dcc.Slider(id="ecg-sens", min=0.3,max=0.95,step=0.05,value=0.6,tooltip={"placement":"bottom"}),
                html.Label("Suavizado (ms)"),
                dcc.Slider(id="ecg-smooth", min=20,max=120,step=5,value=40,tooltip={"placement":"bottom"}),
                html.Br(),
                html.Button("Descargar PNG", id="btn-dl-png", className="btn btn-primary"),
                html.Button("Descargar picos (CSV)", id="btn-dl-peaks", style={"marginLeft":"10px"}, className="btn btn-ghost"),
                html.Div(id="ecg-msg", style={"marginTop":"8px","color":"#FFB4B4"})
            ]),
            html.Div(children=[
                html.Div(id="ecg-kpis", className="kpis"),
                html.Div(className="ecg-divider"),
                dcc.Graph(id="ecg-graph", figure=go.Figure(), style={"height":"420px"})
            ])
        ])
    ])

def _list_ecg_options(user_id:int):
    files = db.list_ecg_files(user_id)
    return [{"label":f["filename"],"value":f["id"]} for f in files]

@app.callback(Output("ecg-file","options"), Input("ecg-user","value"), prevent_initial_call=True)
def refresh_user_files(user_id):
    if not user_id: raise PreventUpdate
    return _list_ecg_options(int(user_id))

@app.callback(
    Output("ecg-file","value", allow_duplicate=True),
    Output("ecg-msg","children", allow_duplicate=True),
    Input("btn-ecg-demo","n_clicks"),
    State("ecg-user","value"),
    prevent_initial_call=True
)
def load_demo(n, user_id):
    if not user_id: return dash.no_update, "Selecciona usuario."
    demo_path = os.path.join("data","ecg","ecg_example.csv")
    if not os.path.exists(demo_path):
        return dash.no_update, "No encuentro data/ecg/ecg_example.csv"
    ecg_id = db.add_ecg_file(int(user_id), "ecg_example.csv", 250)
    return ecg_id, "ECG de ejemplo asociado."

@app.callback(
    Output("ecg-file","options", allow_duplicate=True),
    Output("ecg-file","value", allow_duplicate=True),
    Output("ecg-msg","children"),
    Input("ecg-upload","contents"),
    State("ecg-upload","filename"),
    State("ecg-user","value"),
    prevent_initial_call=True
)
def on_upload(content, filename, user_id):
    if not user_id: return dash.no_update, dash.no_update, "Selecciona usuario antes de subir."
    if content is None: raise PreventUpdate
    header, b64 = content.split(",")
    data = base64.b64decode(b64)
    save_path = os.path.join("data","ecg", filename)
    with open(save_path, "wb") as f: f.write(data)
    ecg_id = db.add_ecg_file(int(user_id), filename, 250)
    opts = _list_ecg_options(int(user_id))
    return opts, ecg_id, f"Archivo {filename} guardado."

@app.callback(
    Output("ecg-graph","figure"),
    Output("ecg-kpis","children"),
    Input("ecg-file","value"),
    Input("ecg-showr","value"),
    Input("ecg-sens","value"),
    Input("ecg-smooth","value"),
    State("ecg-user","value"),
    prevent_initial_call=True
)
def render_ecg(ecg_id, showr_list, sens, smooth_ms, user_id):
    if not (user_id and ecg_id): raise PreventUpdate
    files = db.list_ecg_files(int(user_id))
    row = next((f for f in files if f["id"]==ecg_id), None)
    if not row: raise PreventUpdate
    path = os.path.join("data","ecg", row["filename"])
    if not os.path.exists(path):
        return go.Figure(), []
    t, x, fs = read_ecg_csv(path, fs_default=row.get("fs",250))
    xs = smooth(x, smooth_ms, fs) if smooth_ms and smooth_ms>0 else x
    peaks = detect_r_peaks(xs, fs, sens) if ("r" in (showr_list or [])) else None
    bpm, sdnn, rmssd = ecg_metrics_from_peaks(peaks if peaks is not None else np.array([]), fs)
    if peaks is not None and len(peaks)>1:
        db.save_ecg_metrics(ecg_id, bpm, sdnn, rmssd, int(len(peaks)))
    fig = fig_ecg(t, xs, peaks if ("r" in (showr_list or [])) else None, title=row["filename"])
    kpis = kpi_grid(bpm, sdnn, rmssd)
    return fig, kpis

@app.callback(
    Output("dl-png","data"),
    Input("btn-dl-png","n_clicks"),
    State("ecg-graph","figure"),
    prevent_initial_call=True
)
def download_png(n, fig_dict):
    fig = go.Figure(fig_dict)
    try:
        buf = fig.to_image(format="png", scale=2)
    except Exception:
        return dcc.send_string("Instala 'kaleido' para exportar PNG", "README.txt")
    return dcc.send_bytes(lambda b: b.write(buf), "ecg.png")

@app.callback(
    Output("dl-peaks","data"),
    Input("btn-dl-peaks","n_clicks"),
    State("ecg-file","value"),
    State("ecg-user","value"),
    State("ecg-sens","value"),
    State("ecg-smooth","value"),
    prevent_initial_call=True
)
def download_peaks(n, ecg_id, user_id, sens, smooth_ms):
    if not (user_id and ecg_id): raise PreventUpdate
    files = db.list_ecg_files(int(user_id))
    row = next((f for f in files if f["id"]==ecg_id), None)
    if not row: raise PreventUpdate
    path = os.path.join("data","ecg", row["filename"])
    t, x, fs = read_ecg_csv(path, fs_default=row.get("fs",250))
    xs = smooth(x, smooth_ms, fs)
    peaks = detect_r_peaks(xs, fs, sens)

    sio = io.StringIO()
    w = csv.writer(sio)
    w.writerow(["time_s","value"])
    if peaks is not None and len(peaks)>0:
        for idx in peaks:
            w.writerow([f"{t[idx]:.6f}", f"{xs[idx]:.6f}"])
    csv_str = sio.getvalue()
    return dcc.send_bytes(lambda b: b.write(csv_str.encode("utf-8")), "r_peaks.csv")

# ---- CUESTIONARIO ----
def view_cuestionario():
    athletes = [u for u in db.list_users() if (u.get("role","deportista") == "deportista")]
    options_users = [{"label": f"{u['name']} · {u.get('sport','-')}", "value": u["id"]} for u in athletes]

    items = []
    for key,label in Q.questions():
        if key in ("sueno_horas","duracion","golpes_cabeza"):
            if key=="sueno_horas": rmin,rmax,step,init = 0,12,1,7
            elif key=="duracion":  rmin,rmax,step,init = 0,240,5,60
            else:                  rmin,rmax,step,init = 0,20,1,0
            items.append(html.Div([html.Label(label), dcc.Slider(id=f"q-{key}", min=rmin,max=rmax,step=step,value=init,tooltip={"placement":"bottom"})], style={"marginBottom":"8px"}))
        else:
            items.append(html.Div([html.Label(label), dcc.Slider(id=f"q-{key}", min=1,max=10,step=1,value=5,tooltip={"placement":"bottom"})], style={"marginBottom":"8px"}))
    return html.Div([
        h2("Cuestionario de Autopercepción"),
        html.Small("Sólo aplicable a deportistas.", style={"opacity":0.8}),
        html.Br(),
        html.Label("Deportista"),
        dcc.Dropdown(id="q-user", options=options_users, placeholder="Selecciona deportista..."),
        html.Br(),
        html.Div(children=items),
        html.Button("Guardar cuestionario", id="btn-save-q", className="btn btn-primary"),
        html.Br(), html.Br(),
        dcc.Graph(id="q-gauge", figure=go.Figure(), style={"height":"420px"}),
        html.Div(id="q-explain")
    ])

@app.callback(
    Output("q-gauge","figure"),
    Output("q-explain","children"),
    Input("btn-save-q","n_clicks"),
    State("q-user","value"),
    *[State(f"q-{k}", "value") for k,_ in Q.questions()],
    prevent_initial_call=True
)
def save_q(n, user_id, *values):
    if not user_id: raise PreventUpdate
    ans = {k:v for (k,_),v in zip(Q.questions(), values)}
    wellness = Q.wellness_score(ans)
    db.save_questionnaire(int(user_id), ans, wellness, ans.get("rpe"), ans.get("duracion"))
    fig = go.Figure(go.Indicator(mode="gauge+number", value=wellness,
                                 gauge={"axis":{"range":[0,100]},
                                        "bar":{"color":"#34D7E0"},
                                        "steps":[{"range":[0,60],"color":"#43141a"},
                                                 {"range":[60,80],"color":"#3a2f16"},
                                                 {"range":[80,100],"color":"#103530"}]},
                                 title={"text":"Wellness (0-100)"}))
    fig.update_layout(height=420, template="plotly_dark", margin=dict(l=40,r=10,t=40,b=40))
    txt = ("Interpretación: >80 listo para entrenar; 60–80 atención; <60 considera reducir carga. "
           "Integra fatiga/DOMS/estrés (restan), sueño y ánimo (suman), golpes a la cabeza y sRPE.")
    return fig, txt

# ---- HISTÓRICO ----
def rolling_mean(y, window:int):
    y = list(map(float, y)); n = len(y)
    if window <= 1 or n == 0: return y
    if n < window: m = sum(y)/n; return [m]*n
    cumsum = [0.0]
    for val in y: cumsum.append(cumsum[-1] + val)
    res = []
    for i in range(1, n+1):
        a = max(0, i-window); b = i; w = b - a
        res.append( (cumsum[b]-cumsum[a]) / w )
    return res

def view_historico():
    athletes = [u for u in db.list_users() if (u.get("role","deportista") == "deportista")]
    options_users = [{"label": f"{u['name']} · {u.get('sport','-')}", "value": u["id"]} for u in athletes]
    return html.Div([
        h2("Histórico de Cuestionarios"),
        html.Small("Sólo deportistas con cuestionarios previos aparecerán en las gráficas.", style={"opacity":0.8}),
        html.Br(),
        html.Label("Deportista"),
        dcc.Dropdown(id="h-user", options=options_users, placeholder="Selecciona deportista..."),
        html.Br(),
        dcc.Graph(id="h-wellness", figure=go.Figure(), style={"height":"420px"}),
        dcc.Graph(id="h-load", figure=go.Figure(), style={"height":"420px"}),
    ])

@app.callback(Output("h-wellness","figure"), Output("h-load","figure"), Input("h-user","value"), prevent_initial_call=True)
def render_history(user_id):
    if not user_id: raise PreventUpdate
    rows = list(reversed(db.list_questionnaires(int(user_id))))
    if not rows: return go.Figure(), go.Figure()

    ts, wel, rpe_vals, dur_vals = [], [], [], []
    for r in rows:
        try: ts.append(datetime.fromisoformat(r["ts"]))
        except: ts.append(r["ts"])
        wel.append(float(r["wellness_score"]))
        rpe_vals.append(float(r["rpe"]) if r.get("rpe") is not None else 0.0)
        d = r.get("duration_min", r.get("duration", 0))
        dur_vals.append(float(d) if d is not None else 0.0)

    ma = rolling_mean(wel, 7)

    f1 = go.Figure()
    f1.add_trace(go.Scatter(x=ts, y=wel, mode="lines+markers", name="Wellness"))
    f1.add_trace(go.Scatter(x=ts, y=ma, mode="lines", name="Media móvil 7"))
    f1.update_layout(template="plotly_dark", height=420, margin=dict(l=40,r=10,t=40,b=40), title="Wellness vs tiempo")

    load = (np.array(rpe_vals) * np.array(dur_vals)).tolist()
    f2 = go.Figure(); f2.add_trace(go.Bar(x=ts, y=load, name="sRPE"))
    f2.update_layout(template="plotly_dark", height=420, margin=dict(l=40,r=10,t=40,b=40), title="Carga interna (RPE × duración)")
    return f1, f2

# ====== ROUTER ======
@app.callback(Output("page-content","children"), Input("url","pathname"))
def router(path):
    def errbox(title, err):
        return html.Div([
            h2(title),
            html.Pre(err, style={
                "whiteSpace":"pre-wrap","background":"#2b1f23",
                "border":"1px solid #4a2b31","padding":"12px",
                "borderRadius":"10px","color":"#FFB4B4","overflow":"auto"
            })
        ])

    if path in ("/", "/usuarios"): return view_usuarios()
    if path == "/sensores":        return view_sensores()
    if path == "/ecg":             return view_ecg()
    if path == "/cuestionario":    return view_cuestionario()
    if path == "/historico":       return view_historico()

    mod, err = None, None
    if path == "/login":     mod, err = page_login, err_login
    if path == "/registro":  mod, err = page_register, err_register
    if path == "/dashboard": mod, err = page_dashboard, err_dashboard
    if path == "/logout":    mod, err = page_logout, err_logout

    if err:   return errbox(f"Error importando {path}", err)
    if not mod: return html.Div("Vista no disponible.")
    return mod.layout() if callable(getattr(mod,"layout", None)) else mod.layout

# ====== MAIN ======
if __name__ == "__main__":
    PORT = int(os.environ.get("PORT", 8050))
    URL  = f"http://127.0.0.1:{PORT}/"
    if os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        Timer(1.0, lambda: webbrowser.open_new(URL)).start()
    app.run(debug=True, port=PORT)
