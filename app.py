import math
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import requests
import streamlit as st

BASE_URL = "https://sportapi.ai/api"

st.set_page_config(page_title="Football 1X2 AI", page_icon="⚽", layout="centered")

st.title("⚽ Football 1X2 AI")
st.caption("Chọn giải → chọn trận TƯƠNG LAI → bấm dự đoán → xem xác suất 1/X/2")

if "SPORTAPI_KEY" not in st.secrets or not st.secrets["SPORTAPI_KEY"]:
    st.error("Chưa có SPORTAPI_KEY trong Secrets.")
    st.info('Thêm: SPORTAPI_KEY = "API_KEY_CỦA_BẠN"')
    st.stop()

API_KEY = st.secrets["SPORTAPI_KEY"]
HEADERS = {
    "Authorization": f"Bearer {API_KEY}",
    "X-Api-Key": API_KEY,
}


def api_get(path, params=None):
    r = requests.get(BASE_URL + path, headers=HEADERS, params=params, timeout=20)
    if r.status_code >= 400:
        try:
            detail = r.json()
        except Exception:
            detail = r.text
        raise RuntimeError(f"HTTP {r.status_code}: {detail}")
    data = r.json()
    if isinstance(data, dict) and data.get("success") is False:
        raise RuntimeError(str(data))
    return data


def as_list(value):
    return value if isinstance(value, list) else []


def get_leagues():
    data = api_get("/leagues")
    rows = []
    groups = data.get("data", data.get("leagues", [])) if isinstance(data, dict) else []
    if isinstance(groups, dict):
        groups = [groups]
    for group in groups:
        country = group.get("country", "") if isinstance(group, dict) else ""
        leagues = group.get("leagues", []) if isinstance(group, dict) else []
        for lg in leagues:
            if isinstance(lg, dict) and lg.get("id") is not None:
                rows.append({"id": lg["id"], "name": lg.get("name", ""), "country": country})
    return rows


@st.cache_data(ttl=86400, show_spinner=False)
def cached_leagues():
    return get_leagues()


@st.cache_data(ttl=300, show_spinner=False)
def league_detail(league_id):
    return api_get(f"/leagues/{league_id}")


@st.cache_data(ttl=300, show_spinner=False)
def fixture_detail(fixture_id):
    return api_get(f"/fixtures/{fixture_id}")


@st.cache_data(ttl=900, show_spinner=False)
def team_detail(team_id):
    return api_get(f"/teams/{team_id}")


def fixture_list_from_league(data):
    league = data.get("league", {}) if isinstance(data, dict) else {}
    fixtures = league.get("upcoming_fixtures", []) if isinstance(league, dict) else []
    if not fixtures and isinstance(data, dict):
        fixtures = data.get("upcoming_fixtures", data.get("fixtures", []))
    return as_list(fixtures)


def parse_dt(x):
    if not x:
        return None
    try:
        s = str(x).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def fixture_id(f):
    return f.get("id") or f.get("fixture_id")


def team_name(f, side):
    keys = [f"{side}_team", f"{side}_name"]
    for k in keys:
        if f.get(k):
            return str(f[k])
    obj = f.get(side)
    if isinstance(obj, dict):
        return str(obj.get("name", obj.get("team_name", "")))
    return ""


def team_id_from_fixture(f, side):
    for k in (f"{side}_team_id", f"{side}_id"):
        if f.get(k) is not None:
            return f.get(k)
    obj = f.get(side)
    if isinstance(obj, dict):
        return obj.get("id") or obj.get("team_id")
    return None


def future_fixtures(league_id):
    data = league_detail(league_id)
    fixtures = fixture_list_from_league(data)
    now = datetime.now(timezone.utc)
    out = []
    for f in fixtures:
        dt = parse_dt(f.get("datetime") or f.get("date_time") or f.get("kickoff"))
        if dt is None:
            # Some responses expose only date. Keep it if it is clearly future.
            try:
                d = datetime.fromisoformat(str(f.get("date")))
                dt = d.replace(tzinfo=timezone.utc)
            except Exception:
                continue
        status = str(f.get("status", "")).upper()
        if dt >= now and status not in {"FT", "AET", "PEN", "FINISHED"}:
            f = dict(f)
            f["_dt"] = dt
            out.append(f)
    out.sort(key=lambda x: x["_dt"])
    return out


def extract_matches(team_data):
    team = team_data.get("team", {}) if isinstance(team_data, dict) else {}
    matches = []
    for source in [team.get("matches"), team_data.get("matches") if isinstance(team_data, dict) else None]:
        if isinstance(source, list):
            matches.extend(source)
    # de-duplicate
    seen = set()
    out = []
    for m in matches:
        mid = m.get("id") or m.get("fixture_id")
        if mid in seen:
            continue
        seen.add(mid)
        out.append(m)
    return out


def result_for_team(m, team_id):
    hs = m.get("home_score")
    aws = m.get("away_score")
    if hs is None or aws is None:
        return None
    try:
        hs, aws = float(hs), float(aws)
    except Exception:
        return None
    home_id = m.get("home_team_id")
    away_id = m.get("away_team_id")
    home_name = team_name(m, "home")
    away_name = team_name(m, "away")
    is_home = home_id == team_id if team_id is not None else False
    if team_id is None:
        return None
    if home_id != team_id and away_id != team_id:
        return None
    gf, ga = (hs, aws) if is_home else (aws, hs)
    pts = 3 if gf > ga else 1 if gf == ga else 0
    return {"gf": gf, "ga": ga, "pts": pts, "home": is_home, "date": m.get("date") or m.get("datetime") or ""}


def team_form(team_id):
    data = team_detail(team_id)
    matches = extract_matches(data)
    vals = [result_for_team(m, team_id) for m in matches]
    vals = [v for v in vals if v]
    vals.sort(key=lambda x: str(x["date"]), reverse=True)
    vals = vals[:10]
    if not vals:
        return {"n": 0, "ppg": 1.0, "gf": 1.2, "ga": 1.2, "home_ppg": 1.0, "away_ppg": 1.0}
    n = len(vals)
    return {
        "n": n,
        "ppg": sum(v["pts"] for v in vals) / n,
        "gf": sum(v["gf"] for v in vals) / n,
        "ga": sum(v["ga"] for v in vals) / n,
        "home_ppg": (sum(v["pts"] for v in vals if v["home"]) / max(1, sum(v["home"] for v in vals))),
        "away_ppg": (sum(v["pts"] for v in vals if not v["home"]) / max(1, sum(not v["home"] for v in vals))),
    }


def poisson_probs(lam_home, lam_away, max_goals=8):
    probs = {}
    ph = [math.exp(-lam_home) * lam_home**k / math.factorial(k) for k in range(max_goals + 1)]
    pa = [math.exp(-lam_away) * lam_away**k / math.factorial(k) for k in range(max_goals + 1)]
    home = draw = away = 0.0
    for i, p1 in enumerate(ph):
        for j, p2 in enumerate(pa):
            p = p1 * p2
            if i > j:
                home += p
            elif i == j:
                draw += p
            else:
                away += p
    s = home + draw + away
    return {"1": home / s, "X": draw / s, "2": away / s}


try:
    leagues = cached_leagues()
except Exception as e:
    st.error(f"Không tải được danh sách giải: {e}")
    st.stop()

country_options = sorted({x["country"] for x in leagues if x["country"]})
st.subheader("🌍 Quốc gia")
country = st.selectbox("", ["Tất cả"] + country_options, label_visibility="collapsed")

filtered = [x for x in leagues if country == "Tất cả" or x["country"] == country]
search = st.text_input("🔎 Tìm giải", placeholder="Ví dụ: National League")
if search.strip():
    q = search.strip().lower()
    filtered = [x for x in filtered if q in x["name"].lower() or q in x["country"].lower()]

if not filtered:
    st.warning("Không tìm thấy giải phù hợp.")
    st.stop()

labels = [f"{x['country']} — {x['name']}" for x in filtered]
choice = st.selectbox("🏆 Chọn giải đấu", labels)
selected_league = filtered[labels.index(choice)]

try:
    fixtures = future_fixtures(selected_league["id"])
except Exception as e:
    st.error(f"Không tải được các trận tương lai: {e}")
    st.stop()

if not fixtures:
    st.info("Giải này hiện chưa có trận tương lai trong dữ liệu SportAPI.")
    st.stop()

fixture_labels = []
for f in fixtures:
    dt = f["_dt"].astimezone()
    h = team_name(f, "home") or "Đội nhà"
    a = team_name(f, "away") or "Đội khách"
    fixture_labels.append(f"{dt:%d/%m %H:%M} — {h} vs {a}")

fidx = st.selectbox("⚽ Chọn trận TƯƠNG LAI", range(len(fixtures)), format_func=lambda i: fixture_labels[i])
selected = fixtures[fidx]

st.write(f"**{team_name(selected, 'home')}**  vs  **{team_name(selected, 'away')}**")

if st.button("📊 Dự đoán 1X2", type="primary", use_container_width=True):
    with st.spinner("Đang phân tích phong độ 2 đội..."):
        try:
            fid = fixture_id(selected)
            if fid:
                detail = fixture_detail(fid)
                fx = detail.get("fixture", detail) if isinstance(detail, dict) else {}
            else:
                fx = selected

            home_id = team_id_from_fixture(fx, "home") or team_id_from_fixture(selected, "home")
            away_id = team_id_from_fixture(fx, "away") or team_id_from_fixture(selected, "away")

            if home_id is None or away_id is None:
                st.error("API chưa trả về ID của hai đội trong trận này, nên chưa thể tính 1X2.")
                st.stop()

            hf = team_form(home_id)
            af = team_form(away_id)

            # Simple transparent model from recent form + scoring/conceding.
            home_attack = max(0.25, hf["gf"])
            away_attack = max(0.25, af["gf"])
            home_def = max(0.25, hf["ga"])
            away_def = max(0.25, af["ga"])

            lam_home = 0.35 + 0.48 * home_attack + 0.22 * away_def + 0.12 * max(0, hf["ppg"] - af["ppg"])
            lam_away = 0.22 + 0.48 * away_attack + 0.22 * home_def + 0.10 * max(0, af["ppg"] - hf["ppg"])
            lam_home = float(np.clip(lam_home, 0.35, 3.5))
            lam_away = float(np.clip(lam_away, 0.25, 3.2))

            probs = poisson_probs(lam_home, lam_away)
            best = max(probs, key=probs.get)
            names = {"1": "Chủ nhà thắng", "X": "Hòa", "2": "Đội khách thắng"}

            st.subheader("📈 Xác suất 1X2")
            c1, c2, c3 = st.columns(3)
            c1.metric("1 — Chủ nhà", f"{probs['1']*100:.1f}%")
            c2.metric("X — Hòa", f"{probs['X']*100:.1f}%")
            c3.metric("2 — Đội khách", f"{probs['2']*100:.1f}%")
            st.success(f"Xác suất cao nhất theo mô hình: **{best} — {names[best]}**")

            st.caption(
                f"Mô hình tham khảo từ tối đa 10 trận gần nhất của mỗi đội. "
                f"Dữ liệu dùng: {hf['n']} trận gần nhất của chủ nhà và {af['n']} trận của đội khách."
            )
        except Exception as e:
            st.error(f"Không thể dự đoán trận này: {e}")
