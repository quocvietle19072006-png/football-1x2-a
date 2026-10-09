import math
import statistics
from datetime import datetime, timezone

import requests
import streamlit as st

st.set_page_config(page_title="Kèo sáng | The Odds API", page_icon="🎯", layout="wide")

BASE = "https://api.the-odds-api.com/v4"
API_KEY = st.secrets.get("THE_ODDS_API_KEY", "").strip()

st.title("🎯 Kèo sáng — theo dõi odds")
st.caption("Một nguồn dữ liệu: The Odds API • Chọn môn → chọn trận → xem các market có sẵn")

if not API_KEY:
    st.error("Chưa có API key trong Streamlit Secrets.")
    st.code('THE_ODDS_API_KEY = "API_KEY_CUA_BAN"', language="toml")
    st.stop()

session = requests.Session()
session.headers.update({"User-Agent": "OddsValueScanner/2.0"})


def api_get(path, params=None):
    query = dict(params or {})
    query["apiKey"] = API_KEY
    try:
        response = session.get(BASE + path, params=query, timeout=25)
        if response.status_code == 401:
            return None, "API key không hợp lệ hoặc chưa được kích hoạt.", {}
        if response.status_code == 429:
            return None, "Đã hết hạn mức API. Hãy kiểm tra gói và quota.", {}
        if response.status_code == 422:
            try:
                detail = response.json().get("message", "")
            except Exception:
                detail = ""
            return None, "Môn hoặc market này không được gói API hỗ trợ. " + detail, {}
        if not response.ok:
            return None, f"API trả lỗi HTTP {response.status_code}: {response.text[:250]}", {}
        meta = {
            "remaining": response.headers.get("x-requests-remaining", "không rõ"),
            "used": response.headers.get("x-requests-used", "không rõ"),
        }
        return response.json(), None, meta
    except requests.RequestException as exc:
        return None, f"Không kết nối được API: {exc}", {}


def fmt_time(value):
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt.astimezone().strftime("%d/%m/%Y %H:%M")
    except (ValueError, TypeError):
        return "Giờ chưa rõ"


def parse_dt(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


MARKET_NAMES = {
    "h2h": "Thắng trận / 1X2",
    "spreads": "Handicap",
    "totals": "Tài / Xỉu",
    "outrights": "Vô địch / outright",
    "h2h_q1": "Thắng hiệp 1 / quarter 1",
    "h2h_h1": "Thắng hiệp 1",
    "totals_h1": "Tài / Xỉu hiệp 1",
    "spreads_h1": "Handicap hiệp 1",
}


def market_groups(event):
    """Group prices by market + line point to avoid mixing different totals/spreads."""
    groups = {}
    for bookmaker in event.get("bookmakers", []) or []:
        book_name = bookmaker.get("title") or bookmaker.get("key") or "Nhà cái"
        for market in bookmaker.get("markets", []) or []:
            mkey = market.get("key", "unknown")
            for outcome in market.get("outcomes", []) or []:
                price = outcome.get("price")
                if not isinstance(price, (int, float)) or price <= 1:
                    continue
                name = str(outcome.get("name", "Lựa chọn"))
                point = outcome.get("point")
                point_key = None if point is None else round(float(point), 4)
                group_key = (mkey, point_key)
                groups.setdefault(group_key, {}).setdefault(name, []).append({
                    "price": float(price),
                    "book": book_name,
                    "point": point_key,
                })
    return groups


def analyse_market(market_key, point, outcome_prices):
    # De-vig within each bookmaker/line where all outcomes are available.
    per_book = {}
    for outcome_name, entries in outcome_prices.items():
        for entry in entries:
            per_book.setdefault(entry["book"], {}).setdefault(outcome_name, entry["price"])

    probability_samples = {name: [] for name in outcome_prices}
    for book, prices in per_book.items():
        # Only normalize complete outcome sets returned by this book for this exact market/line.
        if len(prices) < 2:
            continue
        implied = {name: 1 / price for name, price in prices.items() if price > 1}
        total = sum(implied.values())
        if total <= 0:
            continue
        for name, value in implied.items():
            if name in probability_samples:
                probability_samples[name].append(value / total)

    rows = []
    for name, entries in outcome_prices.items():
        odds = [e["price"] for e in entries]
        best = max(entries, key=lambda e: e["price"])
        probs = probability_samples.get(name, [])
        p_market = statistics.median(probs) if probs else None
        edge = (p_market * best["price"] - 1) if p_market is not None else None
        spread = statistics.pstdev(probs) if len(probs) > 1 else 0.0
        if edge is None:
            status = "⚪ Chưa đủ dữ liệu"
        elif edge >= 0.05 and len(probs) >= 3:
            status = "🔥 Đáng xem xét"
        elif edge >= 0.02:
            status = "🟡 Theo dõi thêm"
        else:
            status = "⛔ Bỏ qua"
        rows.append({
            "Market": MARKET_NAMES.get(market_key, market_key),
            "Mã market": market_key,
            "Mốc": point,
            "Lựa chọn": name,
            "Odds tốt nhất": best["price"],
            "Nhà cái tốt nhất": best["book"],
            "Số giá ghi nhận": len(entries),
            "Số nhà cái có đủ kết quả": len(probs),
            "Xác suất thị trường": p_market,
            "Fair odds": (1 / p_market) if p_market and p_market > 0 else None,
            "Edge ước tính": edge,
            "Độ phân tán": spread,
            "Trạng thái": status,
        })
    return rows


@st.cache_data(ttl=90, show_spinner=False)
def get_sports():
    return api_get("/sports", {"all": "true"})


@st.cache_data(ttl=60, show_spinner=False)
def get_odds(sport_key, regions, markets):
    return api_get(
        f"/sports/{sport_key}/odds",
        {
            "regions": regions,
            "markets": ",".join(markets),
            "oddsFormat": "decimal",
            "dateFormat": "iso",
        },
    )


with st.spinner("Đang tải danh sách môn thể thao được API hỗ trợ..."):
    sports, error, sports_meta = get_sports()

if error:
    st.error(error)
    st.stop()
if not isinstance(sports, list) or not sports:
    st.warning("Nguồn API hiện không trả về môn thể thao nào.")
    st.stop()

# Show all sports returned by provider, with active sports first.
sports = sorted(sports, key=lambda item: (not item.get("active", True), str(item.get("group", "")), str(item.get("title", item.get("key", "")))))
sport_labels = [
    f"{item.get('title', item.get('key', 'Môn chưa rõ'))}"
    + ("" if item.get("active", True) else " — tạm không hoạt động")
    for item in sports
]
sport_idx = st.selectbox("1. Chọn môn thể thao / giải đấu", range(len(sports)), format_func=lambda i: sport_labels[i])
sport = sports[sport_idx]
sport_key = sport.get("key")

c1, c2, c3 = st.columns([1, 1, 1])
with c1:
    regions = st.selectbox("Khu vực nhà cái", ["uk", "eu", "us", "au"], index=0)
with c2:
    event_filter = st.selectbox("Danh sách trận", ["Tất cả trận API trả về", "Chưa bắt đầu", "Đã đến giờ bắt đầu trở đi"])
with c3:
    refresh = st.button("🔄 Làm mới odds", use_container_width=True)

available_markets = ["h2h", "spreads", "totals"]
selected_markets = st.multiselect(
    "Market muốn xem",
    available_markets,
    default=available_markets,
    format_func=lambda key: MARKET_NAMES.get(key, key),
)
if not selected_markets:
    st.info("Hãy chọn ít nhất một market.")
    st.stop()

if refresh:
    get_odds.clear()

with st.spinner("Đang lấy các trận và odds mới nhất..."):
    events, odds_error, odds_meta = get_odds(sport_key, regions, tuple(selected_markets))

if odds_error:
    st.error(odds_error)
    st.info("Thử chọn khu vực khác hoặc chỉ chọn một market. Gói miễn phí không hỗ trợ mọi môn, giải đấu hay market.")
    st.stop()
if not isinstance(events, list):
    st.warning("API không trả về danh sách trận hợp lệ.")
    st.stop()

now = datetime.now(timezone.utc)
filtered = []
for event in events:
    start = parse_dt(event.get("commence_time"))
    if event_filter == "Chưa bắt đầu" and start and start <= now:
        continue
    if event_filter == "Đã đến giờ bắt đầu trở đi" and start and start > now:
        continue
    filtered.append(event)

def event_label(event):
    home = event.get("home_team", "Đội / người chơi 1")
    away = event.get("away_team", "Đội / người chơi 2")
    return f"{home} vs {away} — {fmt_time(event.get('commence_time'))}"

st.caption(
    f"Môn/giải: {sport.get('title', sport_key)} • Trận tìm thấy: {len(filtered)} • "
    f"API requests còn lại: {odds_meta.get('remaining', 'không rõ')}"
)
if not filtered:
    st.warning("Không có trận phù hợp với bộ lọc. Thử ‘Tất cả trận API trả về’ hoặc môn/giải khác.")
    st.stop()

selected_event_idx = st.selectbox(
    "2. Chọn trận/sự kiện muốn phân tích",
    range(len(filtered)),
    format_func=lambda i: event_label(filtered[i]),
)
event = filtered[selected_event_idx]
st.subheader(event_label(event))

if not event.get("bookmakers"):
    st.warning("Trận này hiện chưa có odds từ khu vực nhà cái đã chọn.")
    st.stop()

groups = market_groups(event)
all_rows = []
for (market_key, point), outcomes in groups.items():
    all_rows.extend(analyse_market(market_key, point, outcomes))

if not all_rows:
    st.warning("Trận này chưa có market/odds hợp lệ.")
    st.stop()

# A cautious shortlist. These are market-derived signals, not independent AI predictions.
shortlist = [
    row for row in all_rows
    if row["Edge ước tính"] is not None
    and row["Edge ước tính"] >= 0.03
    and row["Số nhà cái có đủ kết quả"] >= 2
]
shortlist.sort(key=lambda row: (row["Edge ước tính"], row["Số nhà cái có đủ kết quả"]), reverse=True)

st.markdown("## 🔥 Kèo sáng để xem xét")
if shortlist:
    for row in shortlist[:3]:
        with st.container(border=True):
            title = f"{row['Market']}"
            if row["Mốc"] is not None:
                title += f" ({row['Mốc']:+g})" if row["Mã market"] == "spreads" else f" ({row['Mốc']:g})"
            st.markdown(f"### {title} — {row['Lựa chọn']}")
            a, b, c = st.columns(3)
            a.metric("Odds tốt nhất", f"{row['Odds tốt nhất']:.2f}")
            b.metric("Xác suất thị trường", f"{row['Xác suất thị trường']*100:.1f}%" if row["Xác suất thị trường"] is not None else "Chưa đủ")
            c.metric("Edge ước tính", f"{row['Edge ước tính']*100:+.1f}%" if row["Edge ước tính"] is not None else "Chưa đủ")
            st.write(f"Nhà cái có odds tốt nhất: **{row['Nhà cái tốt nhất']}** · Giá đối chiếu: **{row['Số nhà cái có đủ kết quả']} nhà cái**")
else:
    st.info("Không có tín hiệu edge đủ điều kiện từ dữ liệu hiện tại. Khuyến nghị: BỎ QUA, không ép chọn kèo.")

st.markdown("## 📊 Toàn bộ market của trận đã chọn")
display = []
for row in sorted(all_rows, key=lambda item: (item["Edge ước tính"] is not None, item["Edge ước tính"] or -999), reverse=True):
    display.append({
        "Market": row["Market"],
        "Mốc": row["Mốc"] if row["Mốc"] is not None else "—",
        "Lựa chọn": row["Lựa chọn"],
        "Odds tốt nhất": round(row["Odds tốt nhất"], 3),
        "Xác suất thị trường": f"{row['Xác suất thị trường']*100:.1f}%" if row["Xác suất thị trường"] is not None else "Chưa đủ dữ liệu",
        "Fair odds": round(row["Fair odds"], 3) if row["Fair odds"] else "—",
        "Edge ước tính": f"{row['Edge ước tính']*100:+.1f}%" if row["Edge ước tính"] is not None else "—",
        "Nhà cái tốt nhất": row["Nhà cái tốt nhất"],
        "Số nhà cái": row["Số giá ghi nhận"],
        "Trạng thái": row["Trạng thái"],
    })
st.dataframe(display, use_container_width=True, hide_index=True)

st.caption(
    "Giới hạn quan trọng: app chỉ có thể hiển thị môn, trận, market và odds mà The Odds API trả về cho key/gói/khu vực của bạn. "
    "The Odds API không đảm bảo bao phủ toàn bộ esports hoặc mọi market live. Mốc thời gian đã qua không tự chứng minh trận đang live. "
    "Edge ở đây được ước tính từ odds thị trường đã loại margin, không phải dự đoán AI độc lập và không đảm bảo lợi nhuận."
)
