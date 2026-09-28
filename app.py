import os
import requests
import pandas as pd
import numpy as np
import streamlit as st

st.set_page_config(page_title="Football 1X2 AI", page_icon="⚽", layout="centered")

st.markdown("""
<style>
.block-container {max-width: 760px; padding-top: 1rem; padding-bottom: 2rem;}
h1 {font-size: 2rem !important;}
</style>
""", unsafe_allow_html=True)

BASE_URL = "https://v3.football.api-football.io"
# Correct API-Football host:
BASE_URL = "https://v3.football.api-sports.io"
API_KEY = st.secrets.get("API_FOOTBALL_KEY", os.getenv("API_FOOTBALL_KEY", ""))

if not API_KEY:
    st.title("⚽ Football 1X2 AI")
    st.error("Chưa có API_FOOTBALL_KEY trong Secrets.")
    st.info('Thêm: API_FOOTBALL_KEY = "API_KEY_CỦA_BẠN"')
    st.stop()


def api_get(endpoint, params=None):
    r = requests.get(
        f"{BASE_URL}/{endpoint}",
        headers={"x-apisports-key": API_KEY},
        params=params or {},
        timeout=30,
    )
    try:
        data = r.json()
    except Exception:
        data = {}
    remaining = r.headers.get("x-ratelimit-requests-remaining", "?")
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}: {data.get('errors', r.text[:200])}")
    if data.get("errors"):
        raise RuntimeError(str(data["errors"]))
    return data, remaining


@st.cache_data(ttl=86400, show_spinner=False)
def load_leagues():
    data, remaining = api_get("leagues", {"current": "true"})
    rows = []
    for item in data.get("response", []):
        league = item.get("league", {})
        country = item.get("country", {})
        if league.get("type") != "League":
            continue
        rows.append({
            "id": int(league["id"]),
            "name": league.get("name", ""),
            "country": country.get("name", "Other"),
            "flag": country.get("flag", ""),
        })
    df = pd.DataFrame(rows).drop_duplicates("id")
    if not df.empty:
        df = df.sort_values(["country", "name"]).reset_index(drop=True)
    return df, remaining


@st.cache_data(ttl=300, show_spinner=False)
def load_future(league_id, number=30):
    # Deliberately do NOT send season.
    # This avoids the Free-plan current-season restriction that affected
    # England National League (league 43) in the previous version.
    data, remaining = api_get(
        "fixtures",
        {"league": int(league_id), "next": int(number)}
    )
    rows = []
    for x in data.get("response", []):
        fx = x.get("fixture", {})
        status = fx.get("status", {}).get("short")
        if status not in {"NS", "TBD"}:
            continue
        home = x.get("teams", {}).get("home", {})
        away = x.get("teams", {}).get("away", {})
        league = x.get("league", {})
        rows.append({
            "fixture_id": int(fx.get("id")),
            "date": fx.get("date", ""),
            "home": home.get("name", "Home"),
            "away": away.get("name", "Away"),
            "league": league.get("name", ""),
            "country": league.get("country", ""),
            "round": league.get("round", ""),
        })
    df = pd.DataFrame(rows)
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"], utc=True, errors="coerce")
        df = df.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)
    return df, remaining


def pct_num(value):
    try:
        return float(str(value).replace("%", "").strip())
    except Exception:
        return None


def get_prediction(fixture_id):
    data, remaining = api_get("predictions", {"fixture": int(fixture_id)})
    response = data.get("response") or []
    if not response:
        return None, remaining

    pred = response[0].get("predictions", {})
    percent = pred.get("percent", {})

    home = pct_num(percent.get("home"))
    draw = pct_num(percent.get("draw"))
    away = pct_num(percent.get("away"))

    if None in (home, draw, away):
        return None, remaining

    probs = np.array([home, draw, away], dtype=float)
    probs = probs / probs.sum()

    winner = pred.get("winner") or {}
    advice = pred.get("advice")
    score = pred.get("score") or {}
    return {
        "probs": probs,
        "winner": winner.get("name"),
        "advice": advice,
        "home_score": score.get("halftime", {}).get("home"),
        "away_score": score.get("halftime", {}).get("away"),
    }, remaining


st.title("⚽ Football 1X2 AI")
st.caption("Chọn giải → chọn trận TƯƠNG LAI → xem xác suất 1/X/2")

try:
    leagues, remaining = load_leagues()
except Exception as e:
    st.error(f"Không tải được danh sách giải: {e}")
    st.stop()

if leagues.empty:
    st.warning("Không có danh sách giải đấu.")
    st.stop()

c1, c2 = st.columns(2)
with c1:
    countries = ["Tất cả"] + sorted(leagues["country"].unique().tolist())
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
    st.warning("Không tìm thấy giải.")
    st.stop()

filtered["label"] = filtered.apply(
    lambda r: f"{r['flag']} {r['country']} — {r['name']}".strip(), axis=1
)
league_choice = st.selectbox("🏆 Chọn giải đấu", filtered["label"].tolist())
league_row = filtered[filtered["label"] == league_choice].iloc[0]

try:
    with st.spinner("Đang tìm các trận sắp tới..."):
        future, rem = load_future(int(league_row["id"]), 30)
except Exception as e:
    st.error(f"Không tải được các trận tương lai: {e}")
    st.stop()

st.caption(f"League ID: {int(league_row['id'])} • API còn khoảng: {rem} request/ngày")

if future.empty:
    st.warning("Hiện API chưa trả về trận sắp tới cho giải này.")
    st.stop()

# Convert to Vietnam time for display.
future["date_vn"] = future["date"].dt.tz_convert("Asia/Ho_Chi_Minh")
future["label"] = future.apply(
    lambda r: (
        f"{r['date_vn'].strftime('%d/%m %H:%M')} — "
        f"{r['home']} vs {r['away']}"
    ),
    axis=1,
)

fixture_choice = st.selectbox("⚽ Chọn trận tương lai", future["label"].tolist())
fixture = future[future["label"] == fixture_choice].iloc[0]

st.divider()
st.subheader(f"{fixture['home']}  vs  {fixture['away']}")
st.write(
    f"📅 **{fixture['date_vn'].strftime('%d/%m/%Y %H:%M')} (giờ Việt Nam)**"
)
if fixture["round"]:
    st.caption(f"{fixture['league']} • {fixture['round']}")

if st.button("📊 Dự đoán 1X2 trận này", type="primary", use_container_width=True):
    with st.spinner("Đang tính xác suất..."):
        try:
            result, pred_remaining = get_prediction(int(fixture["fixture_id"]))
        except Exception as e:
            result = None
            pred_remaining = "?"
            st.error(f"Không lấy được dự đoán: {e}")

    if result is not None:
        p1, px, p2 = result["probs"]

        cols = st.columns(3)
        for col, title, value in zip(
            cols,
            ["1 — Chủ nhà", "X — Hòa", "2 — Đội khách"],
            [p1, px, p2]
        ):
            with col:
                st.metric(title, f"{value * 100:.1f}%")

        best = ["1 — Chủ nhà", "X — Hòa", "2 — Đội khách"][int(np.argmax(result["probs"]))]
        st.success(f"Xác suất cao nhất: **{best}**")

        if result["winner"]:
            st.write(f"🏆 API-Football dự đoán đội thắng: **{result['winner']}**")
        if result["advice"]:
            st.write(f"💡 Nhận định API: {result['advice']}")

        st.caption(f"API còn khoảng: {pred_remaining} request/ngày")
    else:
        st.warning("API chưa có dữ liệu dự đoán cho trận này.")

st.info(
    "Chỉ các trận chưa bắt đầu mới được hiển thị. "
    "Bạn tự chọn trận rồi mới bấm Dự đoán, giúp tiết kiệm quota 100 request/ngày của gói Free."
)
