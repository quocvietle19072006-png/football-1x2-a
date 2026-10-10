import math
import statistics
from datetime import datetime, timezone
from typing import Any

import requests
import streamlit as st

st.set_page_config(page_title="Sports Value Scanner", page_icon="🎯", layout="wide")
BASE = "https://api.the-odds-api.com/v4"
API_KEY = st.secrets.get("THE_ODDS_API_KEY", "")

st.title("🎯 Sports Value Scanner — The Odds API")
st.caption("Pre-match/live where supported • market odds • de-vig probabilities • value screening")

if not API_KEY:
    st.error('Chưa tìm thấy THE_ODDS_API_KEY trong Streamlit Secrets.')
    st.code('THE_ODDS_API_KEY = "YOUR_KEY_HERE"', language="toml")
    st.stop()

session = requests.Session()

def api(path: str, params: dict | None = None):
    p = dict(params or {})
    p["apiKey"] = API_KEY
    try:
        resp = session.get(BASE + path, params=p, timeout=20)
        if resp.status_code == 401:
            return {"_error": "API key không hợp lệ hoặc chưa được kích hoạt."}
        if resp.status_code == 429:
            return {"_error": "Đã hết hạn mức requests của gói API."}
        if resp.status_code == 422:
            return {"_error": "Thể thao/thị trường này không được hỗ trợ trong gói hiện tại."}
        resp.raise_for_status()
        return resp.json(), {"remaining": resp.headers.get("x-requests-remaining"), "used": resp.headers.get("x-requests-used")}
    except Exception as exc:
        return {"_error": str(exc)}

def unpack(result):
    if isinstance(result, tuple):
        return result[0], result[1]
    return result, {}

def fmt_time(value):
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.astimezone().strftime("%d/%m %H:%M %Z")
    except Exception:
        return str(value or "Time unknown")

def market_probabilities(bookmakers, selected_market):
    # Build one de-vig probability vector per bookmaker, then take the median per outcome.
    by_outcome: dict[str, list[dict]] = {}
    for book in bookmakers or []:
        for market in book.get("markets", []) or []:
            if market.get("key") != selected_market:
                continue
            outcomes = market.get("outcomes", []) or []
            valid = [o for o in outcomes if isinstance(o.get("price"), (int, float)) and o["price"] > 1]
            if len(valid) < 2:
                continue
            total = sum(1 / o["price"] for o in valid)
            for o in valid:
                name = str(o.get("name", "Unknown"))
                by_outcome.setdefault(name, []).append({"p": (1 / o["price"]) / total, "odds": float(o["price"]), "book": book.get("title", book.get("key", "Bookmaker"))})
    rows = []
    for name, vals in by_outcome.items():
        probs = [x["p"] for x in vals]
        odds = [x["odds"] for x in vals]
        p = statistics.median(probs)
        dispersion = statistics.pstdev(probs) if len(probs) > 1 else 0.0
        best_odds = max(odds)
        edge = p * best_odds - 1
        fair_odds = 1 / p if p else math.inf
        confidence = max(0, min(100, p * 100 - dispersion * 400))
        if edge >= 0.08 and confidence >= 52:
            status = "🔥 NÊN XEM XÉT"
        elif edge >= 0.03:
            status = "🟡 CÂN NHẮC"
        else:
            status = "⛔ KHÔNG ĐỀ XUẤT"
        rows.append({"Market": selected_market, "Lựa chọn": name, "Xác suất thị trường": p, "Odds tốt nhất": best_odds, "Edge ước tính": edge, "Fair odds": fair_odds, "Độ phân tán": dispersion, "Số nhà cái": len(vals), "Trạng thái": status, "Nhà cái tốt nhất": max(vals, key=lambda x: x["odds"])["book"]})
    return rows

sports_payload, meta = unpack(api("/sports", {"all": "true"}))
if isinstance(sports_payload, dict) and sports_payload.get("_error"):
    st.error(sports_payload["_error"])
    st.stop()
if not isinstance(sports_payload, list) or not sports_payload:
    st.warning("API không trả về danh sách môn thể thao khả dụng.")
    st.stop()

active_sports = [s for s in sports_payload if s.get("active", True)]
if not active_sports:
    active_sports = sports_payload

sport_labels = [f"{s.get('title', s.get('key','Sport'))} ({s.get('key','')})" for s in active_sports]
sport_idx = st.selectbox("Chọn môn / giải đấu", range(len(active_sports)), format_func=lambda i: sport_labels[i])
sport = active_sports[sport_idx]
sport_key = sport.get("key")

c1, c2, c3 = st.columns(3)
with c1:
    regions = st.selectbox("Khu vực nhà cái", ["us", "uk", "eu", "au"], index=0, help="Chỉ hiển thị odds từ khu vực được chọn và gói API hỗ trợ.")
with c2:
    mode = st.selectbox("Loại sự kiện", ["Sắp diễn ra", "Live (nếu API có cung cấp)"])
with c3:
    markets = st.multiselect("Thị trường", ["h2h", "spreads", "totals"], default=["h2h", "spreads", "totals"], format_func=lambda x: {"h2h":"Moneyline / 1X2", "spreads":"Handicap / Spread", "totals":"Over / Under"}.get(x, x))

if not markets:
    st.info("Chọn ít nhất một thị trường.")
    st.stop()

if st.button("🔄 Tải odds và phân tích", type="primary", use_container_width=True):
    st.cache_data.clear()

# The Odds API v4 odds endpoint returns upcoming events for a sport; in-play events may appear while active.
with st.spinner("Đang tải sự kiện và odds..."):
    events_payload, odds_meta = unpack(api(f"/sports/{sport_key}/odds", {"regions": regions, "markets": ",".join(markets), "oddsFormat": "decimal", "dateFormat": "iso"}))

if isinstance(events_payload, dict) and events_payload.get("_error"):
    st.error(events_payload["_error"])
    st.info("Gói miễn phí có thể giới hạn môn, market, khu vực nhà cái hoặc live odds. Hãy thử môn khác hoặc ít market hơn.")
    st.stop()
if not isinstance(events_payload, list):
    st.warning("API không trả về danh sách sự kiện hợp lệ.")
    st.stop()

now = datetime.now(timezone.utc)
def is_live(e):
    try:
        start = datetime.fromisoformat(e.get("commence_time", "").replace("Z", "+00:00"))
        return start <= now
    except Exception:
        return False

if mode.startswith("Live"):
    events = [e for e in events_payload if is_live(e)]
else:
    events = [e for e in events_payload if not is_live(e)]

st.caption(f"Sự kiện tìm thấy: {len(events)} • Requests còn lại: {odds_meta.get('remaining', 'API không trả header')}")
if not events:
    st.warning("Không có sự kiện trong nhóm đã chọn. Live odds chỉ có khi sự kiện đang diễn ra và nhà cung cấp hỗ trợ; hãy thử ‘Sắp diễn ra’.")
    st.stop()

def event_label(e):
    return f"{e.get('home_team','Home')} vs {e.get('away_team','Away')} — {fmt_time(e.get('commence_time'))}"
idx = st.selectbox("Chọn sự kiện", range(len(events)), format_func=lambda i: event_label(events[i]))
event = events[idx]
st.subheader(event_label(event))

all_rows = []
for market_key in markets:
    all_rows.extend(market_probabilities(event.get("bookmakers", []), market_key))

if not all_rows:
    st.warning("Sự kiện này chưa có odds hợp lệ cho các market đã chọn.")
    st.stop()

# Note: this is a market-odds value screen, not an independent outcome prediction model.
# Best price is compared against median de-vig probability; it may not be a true positive EV signal if books differ by market/line.
positive = [r for r in all_rows if r["Edge ước tính"] >= 0.03]
positive.sort(key=lambda r: (r["Edge ước tính"], r["Độ phân tán"] * -1), reverse=True)

st.subheader("🔥 Kèo đáng xem xét nhất")
if positive:
    cols = st.columns(min(3, len(positive)))
    for col, row in zip(cols, positive[:3]):
        with col:
            st.metric(row["Trạng thái"], row["Lựa chọn"], f"{row['Edge ước tính']*100:+.1f}% edge ước tính")
            st.write(f"**Market:** {row['Market']}")
            st.write(f"**Xác suất thị trường:** {row['Xác suất thị trường']*100:.1f}%")
            st.write(f"**Odds tốt nhất:** {row['Odds tốt nhất']:.2f}")
            st.write(f"**Fair odds:** {row['Fair odds']:.2f}")
            st.write(f"**Nhà cái:** {row['Nhà cái tốt nhất']}")
else:
    st.info("⛔ Không tìm thấy edge dương từ odds hiện có. Không đề xuất cược.")

st.subheader("📊 Tất cả market trả về từ API")
display = []
for r in sorted(all_rows, key=lambda x: x["Edge ước tính"], reverse=True):
    display.append({"Market": r["Market"], "Lựa chọn": r["Lựa chọn"], "Xác suất thị trường": f"{r['Xác suất thị trường']*100:.1f}%", "Odds tốt nhất": round(r["Odds tốt nhất"], 3), "Edge ước tính": f"{r['Edge ước tính']*100:+.1f}%", "Fair odds": round(r["Fair odds"], 3), "Nhà cái tốt nhất": r["Nhà cái tốt nhất"], "Số nhà cái": r["Số nhà cái"], "Trạng thái": r["Trạng thái"]})
st.dataframe(display, use_container_width=True, hide_index=True)
st.caption("Lưu ý: The Odds API không bao phủ mọi môn/esports hay mọi market trong gói miễn phí. Đây là bộ lọc giá trị dựa trên odds hiện có, không phải mô hình AI độc lập và không đảm bảo thắng. Kiểm tra hạn mức, thị trường và trạng thái live trong tài khoản API.")
