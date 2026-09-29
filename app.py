import math
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import requests
import streamlit as st

BASE_URL = "https://sportapi.ai/api"

st.set_page_config(page_title="Football 1X2 AI", page_icon="⚽", layout="centered")

st.title("⚽ Football AI — Multi Market")
st.caption("Dữ liệu lịch sử + sân nhà/sân khách + phong độ + standings + H2H → 1X2, O/U, BTTS, tỷ số")

if "SPORTAPI_KEY" not in st.secrets or not st.secrets["SPORTAPI_KEY"]:
    st.error("Chưa có SPORTAPI_KEY trong Secrets.")
    st.info('Thêm: SPORTAPI_KEY = "API_KEY_CỦA_BẠN"')
    st.stop()

API_KEY = st.secrets["SPORTAPI_KEY"]
HEADERS = {
    "Authorization": f"Bearer {API_KEY}",
    "X-Api-Key": API_KEY,
    "Accept": "application/json",
}


def api_get(path, params=None):
    r = requests.get(BASE_URL + path, headers=HEADERS, params=params, timeout=20)
    if r.status_code >= 400:
        try:
            detail = r.json()
        except Exception:
            detail = r.text
        raise RuntimeError(f"HTTP {r.status_code}: {detail}")
    try:
        data = r.json()
    except ValueError:
        # SportAPI may prepend a PHP warning/HTML before the JSON body.
        # Extract the JSON object beginning with the actual API payload.
        import json
        raw = r.text.lstrip("\ufeff \r\n\t")
        start = raw.find('{"success"')
        if start < 0:
            start = raw.find('{')
        if start >= 0:
            candidate = raw[start:]
            try:
                data = json.loads(candidate)
            except ValueError:
                # Try the last closing brace in case there is trailing HTML.
                end = candidate.rfind('}')
                if end >= 0:
                    try:
                        data = json.loads(candidate[:end + 1])
                    except ValueError:
                        data = None
                else:
                    data = None
        else:
            data = None
        if data is None:
            ct = r.headers.get("content-type", "")
            preview = r.text[:700].replace("\n", " ").strip()
            raise RuntimeError(
                f"API không trả JSON hợp lệ (HTTP {r.status_code}, Content-Type: {ct}). "
                f"Phản hồi: {preview}"
            )
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


@st.cache_data(ttl=1800, show_spinner=False)
def h2h_detail(team1, team2):
    return api_get(f"/fixtures/h2h/{team1}/{team2}")


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


def norm_name(x):
    return " ".join(str(x or "").lower().replace("-", " ").split())


def result_for_team(m, team_id=None, team_name_value=""):
    hs = m.get("home_score")
    aws = m.get("away_score")
    if hs is None or aws is None:
        # Some responses may nest scores.
        scores = m.get("score") if isinstance(m.get("score"), dict) else {}
        hs = scores.get("home", hs)
        aws = scores.get("away", aws)
    try:
        hs, aws = float(hs), float(aws)
    except Exception:
        return None

    home_id = m.get("home_team_id") or m.get("home_id")
    away_id = m.get("away_team_id") or m.get("away_id")
    home_obj = m.get("home_team")
    away_obj = m.get("away_team")
    home_name = home_obj.get("name", "") if isinstance(home_obj, dict) else str(home_obj or "")
    away_name = away_obj.get("name", "") if isinstance(away_obj, dict) else str(away_obj or "")
    if isinstance(home_obj, dict):
        home_id = home_id or home_obj.get("id") or home_obj.get("team_id")
    if isinstance(away_obj, dict):
        away_id = away_id or away_obj.get("id") or away_obj.get("team_id")

    is_home = False
    if team_id is not None and (home_id == team_id or str(home_id) == str(team_id)):
        is_home = True
        belongs = True
    elif team_id is not None and (away_id == team_id or str(away_id) == str(team_id)):
        is_home = False
        belongs = True
    else:
        q = norm_name(team_name_value)
        belongs_home = q and q == norm_name(home_name)
        belongs_away = q and q == norm_name(away_name)
        if not (belongs_home or belongs_away):
            return None
        is_home = belongs_home
        belongs = True

    if not belongs:
        return None
    gf, ga = (hs, aws) if is_home else (aws, hs)
    pts = 3 if gf > ga else 1 if gf == ga else 0
    return {"gf": gf, "ga": ga, "pts": pts, "home": is_home,
            "date": m.get("date") or m.get("datetime") or m.get("kickoff") or ""}


def weighted_mean(values, decay=0.88):
    if not values:
        return None
    weights = np.array([decay ** i for i in range(len(values))], dtype=float)
    vals = np.array(values, dtype=float)
    return float(np.sum(vals * weights) / np.sum(weights))


def team_form(team_id, team_name_value=""):
    data = team_detail(team_id)
    team_obj = data.get("team", {}) if isinstance(data, dict) else {}
    real_name = team_obj.get("name", team_name_value) if isinstance(team_obj, dict) else team_name_value
    matches = extract_matches(data)
    vals = [result_for_team(m, team_id, real_name) for m in matches]
    vals = [v for v in vals if v]
    vals.sort(key=lambda x: str(x["date"]), reverse=True)
    vals = vals[:20]
    if not vals:
        return {"n": 0}

    gf = weighted_mean([v["gf"] for v in vals])
    ga = weighted_mean([v["ga"] for v in vals])
    pts = weighted_mean([v["pts"] for v in vals])
    home_vals = [v for v in vals if v["home"]][:10]
    away_vals = [v for v in vals if not v["home"]][:10]
    return {
        "n": len(vals),
        "ppg": pts,
        "gf": gf,
        "ga": ga,
        "home_n": len(home_vals),
        "away_n": len(away_vals),
        "home_ppg": weighted_mean([v["pts"] for v in home_vals]) if home_vals else pts,
        "away_ppg": weighted_mean([v["pts"] for v in away_vals]) if away_vals else pts,
        "home_gf": weighted_mean([v["gf"] for v in home_vals]) if home_vals else gf,
        "home_ga": weighted_mean([v["ga"] for v in home_vals]) if home_vals else ga,
        "away_gf": weighted_mean([v["gf"] for v in away_vals]) if away_vals else gf,
        "away_ga": weighted_mean([v["ga"] for v in away_vals]) if away_vals else ga,
        "source": "20_match_weighted",
    }


@st.cache_data(ttl=900, show_spinner=False)
def league_standings(league_id):
    return api_get(f"/standings/{league_id}")


def form_score(form):
    vals = []
    for x in form if isinstance(form, list) else []:
        x = str(x).upper().strip()
        if x == "W": vals.append(3)
        elif x == "D": vals.append(1)
        elif x == "L": vals.append(0)
    return sum(vals) / len(vals) if vals else None


def standings_form(league_id, team_id, team_name_value=""):
    data = league_standings(league_id)
    obj = data.get("data", {}) if isinstance(data, dict) else {}
    rows = obj.get("standings", []) if isinstance(obj, dict) else []
    q = norm_name(team_name_value)
    for row in rows if isinstance(rows, list) else []:
        rteam = row.get("team", {}) if isinstance(row, dict) else {}
        rid = row.get("team_id") or row.get("id") or (rteam.get("id") if isinstance(rteam, dict) else None)
        rname = row.get("team_name") or (rteam.get("name") if isinstance(rteam, dict) else "")
        if (team_id is not None and str(rid) == str(team_id)) or (q and q == norm_name(rname)):
            f = row.get("form")
            score = form_score(f)
            if score is not None:
                return {"n": len(f), "ppg": score, "gf": None, "ga": None, "source": "standings"}
    return {"n": 0}


def get_team_signal(league_id, team_id, team_name_value):
    tf = team_form(team_id, team_name_value)
    if tf.get("n", 0) >= 3:
        return tf
    sf = standings_form(league_id, team_id, team_name_value)
    if sf.get("n", 0) > 0:
        return sf
    return {"n": 0}

def poisson_matrix(lam_home, lam_away, max_goals=10):
    ph = np.array([math.exp(-lam_home) * lam_home**k / math.factorial(k) for k in range(max_goals + 1)])
    pa = np.array([math.exp(-lam_away) * lam_away**k / math.factorial(k) for k in range(max_goals + 1)])
    m = np.outer(ph, pa)
    return m / m.sum()


def probs_1x2(matrix):
    home = float(np.tril(matrix, -1).sum())
    draw = float(np.trace(matrix))
    away = float(np.triu(matrix, 1).sum())
    return {"1": home, "X": draw, "2": away}


def over_under(matrix, line):
    under = 0.0
    for i in range(matrix.shape[0]):
        for j in range(matrix.shape[1]):
            if i + j < line:
                under += float(matrix[i, j])
    return {"Over": 1.0 - under, "Under": under}


def btts_probs(matrix):
    no = float(matrix[0, :].sum() + matrix[:, 0].sum() - matrix[0, 0])
    return {"Có": 1.0 - no, "Không": no}


def top_scores(matrix, n=5):
    rows=[]
    for i in range(matrix.shape[0]):
        for j in range(matrix.shape[1]):
            rows.append((float(matrix[i,j]), i, j))
    rows.sort(reverse=True)
    return rows[:n]


def h2h_signal(data, home_id, away_id):
    if not isinstance(data, dict):
        return {"n": 0}
    fixtures = data.get("fixtures", [])
    if not isinstance(fixtures, list):
        return {"n": 0}
    vals=[]
    for m in fixtures[:10]:
        r=result_for_team(m, home_id, "")
        if r:
            vals.append(r["pts"])
    if not vals:
        return {"n": 0}
    return {"n": len(vals), "ppg": float(np.mean(vals))}


def safe_lambdas(hf, af, h2h, league_avg=2.55):
    # Ensemble of recent weighted form, venue splits and a small H2H prior.
    # League average is used only as shrinkage, not as a fabricated team result.
    home_attack = max(0.20, 0.65*hf.get("home_gf", hf["gf"]) + 0.35*hf["gf"])
    away_attack = max(0.20, 0.65*af.get("away_gf", af["gf"]) + 0.35*af["gf"])
    home_def = max(0.20, 0.65*hf.get("home_ga", hf["ga"]) + 0.35*hf["ga"])
    away_def = max(0.20, 0.65*af.get("away_ga", af["ga"]) + 0.35*af["ga"])

    home_base = 0.48*home_attack + 0.32*away_def + 0.20*(league_avg/2)
    away_base = 0.48*away_attack + 0.32*home_def + 0.20*(league_avg/2)
    venue_diff = hf.get("home_ppg", hf["ppg"]) - af.get("away_ppg", af["ppg"])
    form_diff = hf["ppg"] - af["ppg"]
    home = home_base + 0.16*venue_diff + 0.10*form_diff + 0.12
    away = away_base - 0.10*venue_diff - 0.06*form_diff

    if h2h.get("n", 0) >= 3:
        hdiff = h2h["ppg"] - 1.5
        home += 0.04*hdiff
        away -= 0.02*hdiff

    return float(np.clip(home, 0.25, 3.8)), float(np.clip(away, 0.20, 3.5))


def league_goal_average(data):
    if not isinstance(data, dict):
        return 2.55
    league = data.get("league", {}) if isinstance(data.get("league"), dict) else {}
    results = league.get("recent_results", []) if isinstance(league, dict) else []
    if not results and isinstance(data, dict):
        results = data.get("recent_results", [])
    goals=[]
    for m in results if isinstance(results,list) else []:
        hs=m.get("home_score"); aws=m.get("away_score")
        try:
            if hs is not None and aws is not None:
                goals.append(float(hs)+float(aws))
        except Exception:
            pass
    return float(np.mean(goals)) if goals else 2.55


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

if st.button("🤖 Phân tích AI đa kèo", type="primary", use_container_width=True):
    with st.spinner("Đang thu thập dữ liệu và chạy ensemble model..."):
        try:
            fid = fixture_id(selected)
            detail = fixture_detail(fid) if fid else selected
            fx = detail.get("fixture", detail) if isinstance(detail, dict) else {}
            home_id = team_id_from_fixture(fx, "home") or team_id_from_fixture(selected, "home")
            away_id = team_id_from_fixture(fx, "away") or team_id_from_fixture(selected, "away")
            if home_id is None or away_id is None:
                st.error("API chưa trả về ID của hai đội trong trận này.")
                st.stop()

            home_name = team_name(fx, "home") or team_name(selected, "home")
            away_name = team_name(fx, "away") or team_name(selected, "away")
            hf = team_form(home_id, home_name)
            af = team_form(away_id, away_name)
            sfh = standings_form(selected_league["id"], home_id, home_name)
            sfa = standings_form(selected_league["id"], away_id, away_name)
            h2h = h2h_signal(h2h_detail(home_id, away_id), home_id, away_id)

            if hf.get("n",0) < 5 or af.get("n",0) < 5:
                st.warning("Chưa đủ tối thiểu 5 trận lịch sử cho một đội. App không tự bịa dữ liệu để ép ra dự đoán.")
                st.stop()

            lg = league_detail(selected_league["id"])
            league_avg = league_goal_average(lg)
            lam_home, lam_away = safe_lambdas(hf, af, h2h, league_avg)
            matrix = poisson_matrix(lam_home, lam_away)
            p1x2 = probs_1x2(matrix)

            # Small standings blend when available; keeps probabilities calibrated rather than replacing match data.
            if sfh.get("n",0) and sfa.get("n",0):
                sd = sfh["ppg"] - sfa["ppg"]
                adj = float(np.clip(sd * 0.025, -0.06, 0.06))
                p1x2["1"] += adj
                p1x2["2"] -= adj
                p1x2["X"] = max(0.001, 1.0-p1x2["1"]-p1x2["2"])
                total=sum(p1x2.values()); p1x2={k:v/total for k,v in p1x2.items()}

            best=max(p1x2,key=p1x2.get)
            names={"1":"Chủ nhà thắng","X":"Hòa","2":"Đội khách thắng"}

            st.subheader("🎯 1X2")
            c1,c2,c3=st.columns(3)
            c1.metric("1 — Chủ nhà",f"{p1x2['1']*100:.1f}%")
            c2.metric("X — Hòa",f"{p1x2['X']*100:.1f}%")
            c3.metric("2 — Đội khách",f"{p1x2['2']*100:.1f}%")
            st.success(f"Kết quả có xác suất cao nhất: **{best} — {names[best]}**")

            st.subheader("⚽ Over / Under")
            cols=st.columns(4)
            for col,line in zip(cols,[1.5,2.5,3.5,4.5]):
                pu=over_under(matrix,line)
                col.metric(f"O {line}",f"{pu['Over']*100:.1f}%")
                col.caption(f"U {line}: {pu['Under']*100:.1f}%")

            st.subheader("🥅 BTTS")
            b=btts_probs(matrix)
            c1,c2=st.columns(2)
            c1.metric("BTTS — Có",f"{b['Có']*100:.1f}%")
            c2.metric("BTTS — Không",f"{b['Không']*100:.1f}%")

            st.subheader("🔢 Tỷ số có xác suất cao")
            score_rows=top_scores(matrix,5)
            df=pd.DataFrame([{
                "Tỷ số":f"{i}-{j}",
                "Xác suất":f"{p*100:.1f}%"
            } for p,i,j in score_rows])
            st.dataframe(df,use_container_width=True,hide_index=True)

            st.subheader("🔁 Double Chance")
            dc={
                "1X":p1x2["1"]+p1x2["X"],
                "X2":p1x2["X"]+p1x2["2"],
                "12":p1x2["1"]+p1x2["2"],
            }
            cols=st.columns(3)
            for col,k in zip(cols,["1X","X2","12"]): col.metric(k,f"{dc[k]*100:.1f}%")

            st.divider()
            st.caption(
                f"Data Engine: tối đa {hf['n']} trận gần nhất chủ nhà + {af['n']} trận gần nhất đội khách, "
                f"trọng số trận mới cao hơn; standings={sfh.get('n',0)}/{sfa.get('n',0)}; H2H={h2h.get('n',0)}; "
                f"league goal avg={league_avg:.2f}. λ={lam_home:.2f}/{lam_away:.2f}."
            )
            st.info("Đây là xác suất mô hình, không phải tỷ lệ cược nhà cái và không đảm bảo kết quả. Các thị trường như góc/thẻ chỉ nên thêm khi có đủ dữ liệu lịch sử tương ứng.")
        except Exception as e:
            st.error(f"Không thể phân tích trận này: {e}")
