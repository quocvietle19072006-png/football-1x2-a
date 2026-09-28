import os
import math
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import requests
import streamlit as st
from sklearn.linear_model import LogisticRegression

st.set_page_config(
    page_title="Football 1X2 AI",
    page_icon="⚽",
    layout="centered",
)

st.markdown("""
<style>
.block-container {max-width: 760px; padding-top: 1rem; padding-bottom: 2rem;}
h1 {font-size: 2rem !important;}
div[data-testid="stMetricValue"] {font-size: 1.45rem;}
.small-note {font-size: .85rem; opacity: .75;}
</style>
""", unsafe_allow_html=True)

BASE_URL = "https://v3.football.api-sports.io"
API_KEY = st.secrets.get("API_FOOTBALL_KEY", os.getenv("API_FOOTBALL_KEY", ""))

if not API_KEY:
    st.title("⚽ Football 1X2 AI")
    st.error("Chưa có API_FOOTBALL_KEY trong Secrets.")
    st.info("Vào Streamlit → Settings/Manage app → Secrets và thêm: API_FOOTBALL_KEY = \"API_KEY_CUA_BAN\"")
    st.stop()


def api_get(endpoint, params=None):
    headers = {"x-apisports-key": API_KEY}
    try:
        r = requests.get(
            f"{BASE_URL}/{endpoint}",
            headers=headers,
            params=params or {},
            timeout=30,
        )
        remaining = r.headers.get("x-ratelimit-requests-remaining", "?")
        if r.status_code != 200:
            try:
                body = r.json()
                errors = body.get("errors", {})
            except Exception:
                errors = r.text[:300]
            raise RuntimeError(f"HTTP {r.status_code} — {errors} — còn khoảng {remaining} request hôm nay.")
        data = r.json()
        if data.get("errors"):
            raise RuntimeError(f"{data['errors']} — còn khoảng {remaining} request hôm nay.")
        return data, remaining
    except requests.RequestException as e:
        raise RuntimeError(f"Không kết nối được API: {e}")


@st.cache_data(ttl=86400, show_spinner=False)
def load_leagues():
    data, remaining = api_get("leagues", {"current": "true"})
    rows = []
    for item in data.get("response", []):
        league = item.get("league", {})
        country = item.get("country", {})
        seasons = item.get("seasons") or []
        if league.get("type") != "League":
            continue
        current_seasons = [s for s in seasons if s.get("current")]
        season_obj = current_seasons[-1] if current_seasons else (seasons[-1] if seasons else {})
        year = season_obj.get("year")
        if not league.get("id") or not league.get("name") or not year:
            continue
        rows.append({
            "id": int(league["id"]),
            "name": league["name"],
            "country": country.get("name") or "Other",
            "flag": country.get("flag") or "",
            "season": int(year),
        })
    df = pd.DataFrame(rows).drop_duplicates(subset=["id"])
    if not df.empty:
        df = df.sort_values(["country", "name"]).reset_index(drop=True)
    return df, remaining


@st.cache_data(ttl=1800, show_spinner=False)
def load_finished(league_id, season):
    data, remaining = api_get(
        "fixtures",
        {"league": int(league_id), "season": int(season), "status": "FT-AET-PEN"},
    )
    rows = []
    for x in data.get("response", []):
        status = x.get("fixture", {}).get("status", {}).get("short")
        if status not in {"FT", "AET", "PEN"}:
            continue
        home = x.get("teams", {}).get("home", {})
        away = x.get("teams", {}).get("away", {})
        goals = x.get("goals", {})
        if not home.get("id") or not away.get("id"):
            continue
        gh, ga = goals.get("home"), goals.get("away")
        if gh is None or ga is None:
            continue
        rows.append({
            "date": x.get("fixture", {}).get("date", ""),
            "home_id": int(home["id"]),
            "away_id": int(away["id"]),
            "home": home.get("name", "Home"),
            "away": away.get("name", "Away"),
            "gh": int(gh),
            "ga": int(ga),
        })
    df = pd.DataFrame(rows)
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"], utc=True, errors="coerce")
        df = df.sort_values("date")
    return df, remaining


@st.cache_data(ttl=900, show_spinner=False)
def load_next(league_id, season):
    data, remaining = api_get(
        "fixtures",
        {"league": int(league_id), "season": int(season), "next": 30},
    )
    rows = []
    for x in data.get("response", []):
        status = x.get("fixture", {}).get("status", {}).get("short")
        if status not in {"NS", "TBD"}:
            continue
        home = x.get("teams", {}).get("home", {})
        away = x.get("teams", {}).get("away", {})
        if not home.get("id") or not away.get("id"):
            continue
        rows.append({
            "fixture_id": int(x.get("fixture", {}).get("id")),
            "date": x.get("fixture", {}).get("date", ""),
            "home_id": int(home["id"]),
            "away_id": int(away["id"]),
            "home": home.get("name", "Home"),
            "away": away.get("name", "Away"),
        })
    df = pd.DataFrame(rows)
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"], utc=True, errors="coerce")
        df = df.sort_values("date")
    return df, remaining


def team_stats(history, team_id, before_date=None, window=12):
    h = history
    if before_date is not None and not h.empty:
        h = h[h["date"] < before_date]
    if h.empty:
        return {"matches": 0, "gf": 1.25, "ga": 1.25, "points": 1.2, "win": .33, "draw": .34, "loss": .33}

    mask = (h["home_id"] == team_id) | (h["away_id"] == team_id)
    t = h.loc[mask].tail(window)
    if t.empty:
        return {"matches": 0, "gf": 1.25, "ga": 1.25, "points": 1.2, "win": .33, "draw": .34, "loss": .33}

    gf, ga, pts = [], [], []
    wins = draws = losses = 0
    for _, r in t.iterrows():
        if int(r.home_id) == team_id:
            a, b = int(r.gh), int(r.ga)
        else:
            a, b = int(r.ga), int(r.gh)
        gf.append(a)
        ga.append(b)
        if a > b:
            wins += 1; pts.append(3)
        elif a == b:
            draws += 1; pts.append(1)
        else:
            losses += 1; pts.append(0)

    n = len(t)
    return {
        "matches": n,
        "gf": float(np.mean(gf)),
        "ga": float(np.mean(ga)),
        "points": float(np.mean(pts)),
        "win": wins / n,
        "draw": draws / n,
        "loss": losses / n,
    }


def poisson_probs(lam_home, lam_away, max_goals=8):
    ph = [math.exp(-lam_home) * lam_home**k / math.factorial(k) for k in range(max_goals + 1)]
    pa = [math.exp(-lam_away) * lam_away**k / math.factorial(k) for k in range(max_goals + 1)]
    p1 = px = p2 = 0.0
    for i, a in enumerate(ph):
        for j, b in enumerate(pa):
            p = a * b
            if i > j: p1 += p
            elif i == j: px += p
            else: p2 += p
    total = p1 + px + p2
    return np.array([p1, px, p2]) / total


def build_ml(history):
    if len(history) < 30:
        return None

    rows, y = [], []
    for _, r in history.iterrows():
        hs = team_stats(history, int(r.home_id), r.date, 10)
        aas = team_stats(history, int(r.away_id), r.date, 10)
        rows.append([
            hs["gf"] - aas["ga"],
            aas["gf"] - hs["ga"],
            hs["points"] - aas["points"],
            hs["win"] - aas["win"],
            hs["draw"] - aas["draw"],
            hs["loss"] - aas["loss"],
        ])
        if r.gh > r.ga: y.append(0)
        elif r.gh == r.ga: y.append(1)
        else: y.append(2)

    if len(set(y)) < 3:
        return None

    model = LogisticRegression(max_iter=1000, multi_class="auto")
    model.fit(np.asarray(rows), np.asarray(y))
    return model


def predict(history, home_id, away_id, fixture_date=None):
    hs = team_stats(history, home_id, fixture_date, 12)
    aas = team_stats(history, away_id, fixture_date, 12)

    # Conservative attack/defence blend + small home advantage.
    home_xg = max(0.20, 0.58 * hs["gf"] + 0.42 * aas["ga"] + 0.18)
    away_xg = max(0.20, 0.58 * aas["gf"] + 0.42 * hs["ga"])

    poi = poisson_probs(home_xg, away_xg)

    model = build_ml(history)
    if model is not None:
        feat = np.array([[
            hs["gf"] - aas["ga"],
            aas["gf"] - hs["ga"],
            hs["points"] - aas["points"],
            hs["win"] - aas["win"],
            hs["draw"] - aas["draw"],
            hs["loss"] - aas["loss"],
        ]])
        ml_raw = model.predict_proba(feat)[0]
        ml = np.zeros(3)
        for cls, p in zip(model.classes_, ml_raw):
            ml[int(cls)] = p
        final = 0.72 * ml + 0.28 * poi
    else:
        final = poi

    final = final / final.sum()
    return final, home_xg, away_xg, hs, aas


st.title("⚽ Football 1X2 AI")
st.caption("Dữ liệu API-Football • mô hình ML + Poisson • tối ưu cho điện thoại")

try:
    leagues, remaining = load_leagues()
except Exception as e:
    st.error(f"Không tải được danh sách giải: {e}")
    st.stop()

if leagues.empty:
    st.warning("API không trả về giải đấu đang hoạt động.")
    st.stop()

c1, c2 = st.columns(2)
with c1:
    countries = ["Tất cả"] + sorted(leagues["country"].dropna().unique().tolist())
    country = st.selectbox("🌍 Quốc gia", countries)
with c2:
    search = st.text_input("🔎 Tìm giải", placeholder="National League...")

filtered = leagues.copy()
if country != "Tất cả":
    filtered = filtered[filtered["country"] == country]
if search.strip():
    q = search.strip().lower()
    filtered = filtered[
        filtered["name"].str.lower().str.contains(q, na=False)
        | filtered["country"].str.lower().str.contains(q, na=False)
    ]

if filtered.empty:
    st.warning("Không tìm thấy giải phù hợp.")
    st.stop()

filtered["label"] = filtered.apply(
    lambda r: f"{r['flag']} {r['country']} — {r['name']} ({r['season']})".strip(),
    axis=1,
)
choice = st.selectbox("🏆 Chọn giải đấu", filtered["label"].tolist())
league_row = filtered[filtered["label"] == choice].iloc[0]
league_id = int(league_row["id"])
season = int(league_row["season"])

st.caption(f"League ID: {league_id} • Season: {season} • API còn khoảng: {remaining} request/ngày")

try:
    with st.spinner("Đang tải dữ liệu trận đấu..."):
        history, rem1 = load_finished(league_id, season)
        upcoming, rem2 = load_next(league_id, season)
except Exception as e:
    st.error(f"Không tải được dữ liệu giải này: {e}")
    st.stop()

if upcoming.empty:
    st.warning("Chưa có trận sắp tới trong dữ liệu API cho giải này.")
    st.stop()

upcoming["label"] = upcoming.apply(
    lambda r: f"{r['date'].strftime('%d/%m %H:%M UTC')} — {r['home']} vs {r['away']}",
    axis=1,
)
fixture_choice = st.selectbox("⚽ Chọn trận", upcoming["label"].tolist())
fixture = upcoming[upcoming["label"] == fixture_choice].iloc[0]

probs, xg_h, xg_a, hs, aas = predict(
    history,
    int(fixture["home_id"]),
    int(fixture["away_id"]),
    fixture["date"],
)

st.divider()
st.subheader(f"{fixture['home']}  vs  {fixture['away']}")

cols = st.columns(3)
labels = [("1", "Chủ nhà"), ("X", "Hòa"), ("2", "Đội khách")]
for col, (k, name), p in zip(cols, labels, probs):
    with col:
        st.metric(f"{k} — {name}", f"{p*100:.1f}%")

winner = ["1 — Chủ nhà", "X — Hòa", "2 — Đội khách"][int(np.argmax(probs))]
st.success(f"Xác suất cao nhất: **{winner}**")

x1, x2 = st.columns(2)
with x1:
    st.metric("xG chủ nhà", f"{xg_h:.2f}")
with x2:
    st.metric("xG đội khách", f"{xg_a:.2f}")

st.write(
    f"Form gần đây: **{fixture['home']}** {hs['matches']} trận, "
    f"{hs['points']:.2f} điểm/trận • **{fixture['away']}** {aas['matches']} trận, "
    f"{aas['points']:.2f} điểm/trận."
)

st.caption(
    "Lưu ý: đây là xác suất mô hình, không phải kết quả chắc chắn. "
    "API-Football Free hiện giới hạn 100 request/ngày; app có cache để giảm số lần gọi."
)

with st.expander("ℹ️ Cách hoạt động"):
    st.write(
        "App lấy danh sách giải đang hoạt động từ API-Football, sau đó tải lịch sử "
        "trận đã kết thúc và các trận sắp tới của giải được chọn. "
        "Dự đoán kết hợp Logistic Regression (nếu đủ dữ liệu) với Poisson xG."
    )
