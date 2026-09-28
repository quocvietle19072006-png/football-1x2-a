import os
import math
import requests
import numpy as np
import pandas as pd
import streamlit as st
from sklearn.linear_model import LogisticRegression

st.set_page_config(
    page_title="Football 1X2 AI",
    page_icon="⚽",
    layout="centered",
)

st.markdown("""
<style>
.block-container {padding: 1rem 0.8rem 2rem 0.8rem; max-width: 760px;}
h1 {font-size: 2rem !important;}
div[data-testid="stMetric"] {border: 1px solid #ddd; border-radius: 12px; padding: 10px;}
</style>
""", unsafe_allow_html=True)

TOKEN = st.secrets.get("FOOTBALL_DATA_TOKEN", os.getenv("FOOTBALL_DATA_TOKEN", ""))
BASE = "https://api.football-data.org/v4"

COMPETITIONS = {
    "Premier League": "PL",
    "La Liga": "PD",
    "Bundesliga": "BL1",
    "Serie A": "SA",
    "Ligue 1": "FL1",
    "Champions League": "CL",
    "Eredivisie": "DED",
    "Primeira Liga": "PPL",
}

@st.cache_data(ttl=900)
def api_get(path, params=None):
    if not TOKEN:
        return {}
    r = requests.get(
        BASE + path,
        headers={"X-Auth-Token": TOKEN},
        params=params or {},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()

def form_features(matches, team):
    past = [m for m in matches if m.get("status") == "FINISHED" and
            (m.get("homeTeam", {}).get("name") == team or
             m.get("awayTeam", {}).get("name") == team)]
    past = sorted(past, key=lambda x: x.get("utcDate", ""))[-5:]
    pts, gf, ga = [], [], []
    for m in past:
        hs = (m.get("score", {}).get("fullTime", {}) or {}).get("home")
        aws = (m.get("score", {}).get("fullTime", {}) or {}).get("away")
        if hs is None or aws is None:
            continue
        home = m.get("homeTeam", {}).get("name") == team
        scored, conceded = (hs, aws) if home else (aws, hs)
        gf.append(scored); ga.append(conceded)
        pts.append(3 if scored > conceded else 1 if scored == conceded else 0)
    return (
        float(np.mean(pts)) if pts else 1.0,
        float(np.mean(gf)) if gf else 1.0,
        float(np.mean(ga)) if ga else 1.0,
    )

def build_training(matches):
    rows, y = [], []
    for m in matches:
        if m.get("status") != "FINISHED":
            continue
        hs = (m.get("score", {}).get("fullTime", {}) or {}).get("home")
        aws = (m.get("score", {}).get("fullTime", {}) or {}).get("away")
        if hs is None or aws is None:
            continue
        home = m.get("homeTeam", {}).get("name")
        away = m.get("awayTeam", {}).get("name")
        if not home or not away:
            continue
        hp, hgf, hga = form_features(matches, home)
        ap, agf, aga = form_features(matches, away)
        rows.append([hp-ap, hgf-agf, hga-aga, hgf-aga, 0.15])
        y.append(0 if hs == aws else 1 if hs > aws else 2)
    return np.asarray(rows), np.asarray(y)

def poisson_probs(lh, la):
    vals = np.zeros(3)
    for h in range(8):
        ph = math.exp(-lh) * lh**h / math.factorial(h)
        for a in range(8):
            pa = math.exp(-la) * la**a / math.factorial(a)
            p = ph * pa
            vals[0] += p if h > a else 0
            vals[1] += p if h == a else 0
            vals[2] += p if h < a else 0
    s = vals.sum()
    return vals / s if s else np.array([1/3, 1/3, 1/3])

def predict(home, away, matches):
    hp, hgf, hga = form_features(matches, home)
    ap, agf, aga = form_features(matches, away)

    X, y = build_training(matches)
    if len(X) >= 30 and len(np.unique(y)) >= 3:
        model = LogisticRegression(max_iter=2000)
        model.fit(X, y)
        x = np.array([[hp-ap, hgf-agf, hga-aga, hgf-aga, 0.15]])
        ml = np.zeros(3)
        raw = model.predict_proba(x)[0]
        for cls, p in zip(model.classes_, raw):
            ml[int(cls)] = p
    else:
        ml = np.array([0.40, 0.28, 0.32])

    home_xg = max(0.25, min(4.0, 1.45 + 0.28*(hgf-agf) - 0.18*(hga-aga)))
    away_xg = max(0.20, min(4.0, 1.15 - 0.20*(hgf-agf) + 0.18*(hga-aga)))
    poi = poisson_probs(home_xg, away_xg)
    final = 0.72 * ml + 0.28 * poi
    final = final / final.sum()
    return final, home_xg, away_xg

st.title("⚽ Football 1X2 AI")
st.caption("Dự đoán xác suất 1X2 từ dữ liệu trận đấu")

if not TOKEN:
    st.error("Chưa có FOOTBALL_DATA_TOKEN trong Secrets.")
    st.stop()

competition = st.selectbox("Giải đấu", list(COMPETITIONS.keys()))
code = COMPETITIONS[competition]

try:
    finished_data = api_get(f"/competitions/{code}/matches", {"status": "FINISHED"})
    upcoming_data = api_get(f"/competitions/{code}/matches", {"status": "SCHEDULED"})
    finished = finished_data.get("matches", [])
    upcoming = upcoming_data.get("matches", [])
except Exception as e:
    st.error(f"Không lấy được dữ liệu bóng đá: {e}")
    st.stop()

if not finished:
    st.warning("Giải đấu chưa có đủ dữ liệu lịch sử.")
    st.stop()

teams = sorted({
    t
    for m in finished
    for t in [
        m.get("homeTeam", {}).get("name"),
        m.get("awayTeam", {}).get("name")
    ]
    if t
})

st.subheader("Chọn trận")
home = st.selectbox("Đội nhà", teams, index=0)
away_options = [t for t in teams if t != home]
away = st.selectbox("Đội khách", away_options, index=0)

if st.button("🔮 Dự đoán 1X2", use_container_width=True):
    p, xg_h, xg_a = predict(home, away, finished)

    c1, c2, c3 = st.columns(3)
    c1.metric("1 — Chủ nhà", f"{p[0]*100:.1f}%")
    c2.metric("X — Hòa", f"{p[1]*100:.1f}%")
    c3.metric("2 — Khách", f"{p[2]*100:.1f}%")

    st.subheader("⚽ Kỳ vọng bàn thắng")
    c1, c2 = st.columns(2)
    c1.metric(home, f"{xg_h:.2f} xG")
    c2.metric(away, f"{xg_a:.2f} xG")

    labels = ["1 — Chủ nhà", "X — Hòa", "2 — Khách"]
    result = labels[int(np.argmax(p))]
    st.info(f"Xác suất cao nhất theo mô hình: **{result}**")

st.divider()
st.caption(f"Dữ liệu: Football-Data.org • {competition}")
