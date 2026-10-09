import re
import statistics
from datetime import datetime, timezone
import requests
import streamlit as st

st.set_page_config(page_title="AI Kèo Sáng", page_icon="🎯", layout="wide")
BASE = "https://api.the-odds-api.com/v4"
API_KEY = str(st.secrets.get("THE_ODDS_API_KEY", "")).strip()
MARKET_NAMES = {"h2h":"Thắng trận / 1X2", "spreads":"Handicap", "totals":"Tài / Xỉu", "h2h_3_way":"1X2 (3 cửa)", "draw_no_bet":"Hòa hoàn tiền", "btts":"Hai đội cùng ghi bàn", "alternate_spreads":"Handicap phụ", "alternate_totals":"Tài/Xỉu phụ", "team_totals":"Tổng điểm/bàn đội", "h2h_h1":"Thắng hiệp 1", "spreads_h1":"Handicap hiệp 1", "totals_h1":"Tài/Xỉu hiệp 1", "h2h_q1":"Thắng quarter 1", "spreads_q1":"Handicap quarter 1", "totals_q1":"Tài/Xỉu quarter 1"}
MARKETS = list(MARKET_NAMES)

def api_get(path, params=None):
    p = dict(params or {}); p["apiKey"] = API_KEY
    try:
        r = requests.get(BASE + path, params=p, timeout=25)
        if r.status_code == 401: return None, "API key không hợp lệ hoặc chưa được kích hoạt.", {}
        if r.status_code == 429: return None, "Đã hết hạn mức API. Kiểm tra quota/gói của bạn.", {}
        if not r.ok:
            try: detail = r.json().get("message", "")
            except Exception: detail = r.text[:160]
            return None, f"API lỗi HTTP {r.status_code}. {detail}", {}
        return r.json(), None, {"remaining":r.headers.get("x-requests-remaining", "?"), "used":r.headers.get("x-requests-used", "?")}
    except requests.RequestException as e: return None, f"Không kết nối được API: {e}", {}

def norm(s): return re.sub(r"\s+", " ", str(s or "").strip().casefold())
def parse_dt(s):
    try: return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception: return None
def time_label(s):
    d = parse_dt(s)
    return d.astimezone().strftime("%d/%m/%Y %H:%M") if d else "Thời gian chưa rõ"

@st.cache_data(ttl=300, show_spinner=False)
def sports_data(): return api_get("/sports", {"all":"true"})
@st.cache_data(ttl=90, show_spinner=False)
def event_data(sport_key): return api_get(f"/sports/{sport_key}/events", {"dateFormat":"iso"})
@st.cache_data(ttl=60, show_spinner=False)
def odds_data(sport_key, event_id, region, markets):
    return api_get(f"/sports/{sport_key}/events/{event_id}/odds", {"regions":region,"markets":markets,"oddsFormat":"decimal","dateFormat":"iso"})

st.title("🎯 AI Kèo Sáng")
st.caption("Chọn môn → nhập tên đội/người chơi → chọn trận → phân tích odds có sẵn từ The Odds API")
if not API_KEY:
    st.error("Chưa có API key trong Streamlit Secrets.")
    st.code('THE_ODDS_API_KEY = "API_KEY_CUA_BAN"', language="toml")
    st.stop()

with st.spinner("Đang tải danh sách môn thể thao/esports..."):
    sports, err, _ = sports_data()
if err:
    st.error(err); st.stop()
if not isinstance(sports, list) or not sports:
    st.warning("API hiện không trả về danh sách môn nào."); st.stop()
sports = sorted(sports, key=lambda x: (not x.get("active", True), str(x.get("group", "")), str(x.get("title", x.get("key", ""))).casefold()))
sport_i = st.selectbox("1. Chọn môn thể thao / esports", range(len(sports)), format_func=lambda i: str(sports[i].get("title", sports[i].get("key", "Môn chưa rõ"))) + ("" if sports[i].get("active", True) else " (tạm không hoạt động)"))
sport = sports[sport_i]; sport_key = sport.get("key")
c1, c2 = st.columns([3,1])
with c1: query = st.text_input("2. Nhập tên đội, người chơi hoặc giải đấu", placeholder="Ví dụ: Arsenal, Lakers, T1, Team Spirit...").strip()
with c2: region = st.selectbox("Khu vực odds", ["uk", "eu", "us", "au"], index=0)
include_started = st.checkbox("Bao gồm sự kiện đã bắt đầu (nếu API trả về)", value=True)
search = st.button("🔎 Tìm trận", type="primary", use_container_width=True)
if search:
    if not query:
        st.warning("Hãy nhập tên đội/người chơi trước."); st.stop()
    with st.spinner("Đang tìm sự kiện trong môn đã chọn..."):
        events, err, event_meta = event_data(sport_key)
    if err: st.error(err); st.stop()
    if not isinstance(events, list): st.warning("API không trả về danh sách sự kiện hợp lệ."); st.stop()
    q = norm(query); found=[]; now=datetime.now(timezone.utc)
    for e in events:
        hay = norm(" ".join([str(e.get("home_team", "")), str(e.get("away_team", "")), str(e.get("name", "")), str(e.get("sport_title", ""))]))
        if q in hay:
            start=parse_dt(e.get("commence_time"))
            if not include_started and start and start <= now: continue
            found.append(e)
    if not found:
        st.warning("Không tìm thấy trận khớp tên trong danh sách API. Thử tên ngắn hơn hoặc kiểm tra môn đã chọn."); st.stop()
    found.sort(key=lambda e: parse_dt(e.get("commence_time")) or datetime.max.replace(tzinfo=timezone.utc))
    st.success(f"Tìm thấy {len(found)} sự kiện phù hợp.")
    labels=[]
    for e in found:
        title=e.get("name") or f"{e.get('home_team','?')} vs {e.get('away_team','?')}"
        labels.append(f"{title} — {time_label(e.get('commence_time'))}")
    idx=st.selectbox("3. Chọn đúng trận", range(len(found)), format_func=lambda i: labels[i], key="event_choice")
    event=found[idx]
    st.markdown(f"### {event.get('home_team','')} vs {event.get('away_team','')}")
    st.caption(f"{time_label(event.get('commence_time'))} · Nguồn: The Odds API")
    with st.spinner("Đang lấy market và odds của trận..."):
        data, err, meta=odds_data(sport_key, event.get("id"), region, ",".join(MARKETS))
    if err and "HTTP 422" in err:
        data, err, meta=odds_data(sport_key, event.get("id"), region, "h2h,spreads,totals")
    if err: st.error(err); st.info("Gói/khu vực có thể không hỗ trợ tất cả market. Thử khu vực khác."); st.stop()
    if isinstance(data, dict): event_odds=data
    elif isinstance(data, list) and data: event_odds=data[0]
    else: event_odds={}
    books=event_odds.get("bookmakers", []) or []
    if not books: st.warning("Đã tìm thấy sự kiện nhưng chưa có odds ở khu vực này."); st.stop()
    grouped={}
    for book in books:
        bname=book.get("title") or book.get("key") or "Nhà cái"
        for market in book.get("markets", []) or []:
            mk=market.get("key", "unknown")
            for out in market.get("outcomes", []) or []:
                price=out.get("price")
                if not isinstance(price, (int,float)) or price <= 1: continue
                name=str(out.get("name", "Lựa chọn")); point=out.get("point")
                point=round(float(point),3) if point is not None else None
                grouped.setdefault((mk,point),{}).setdefault(name,[]).append({"book":bname,"price":float(price)})
    rows=[]
    for (mk,point), outcomes in grouped.items():
        per_book={}
        for name, entries in outcomes.items():
            for entry in entries: per_book.setdefault(entry["book"],{})[name]=entry["price"]
        samples={name:[] for name in outcomes}
        for _, prices in per_book.items():
            if len(prices)<2: continue
            inv={name:1/price for name,price in prices.items() if price>1}; total=sum(inv.values())
            if total:
                for name,value in inv.items():
                    if name in samples: samples[name].append(value/total)
        for name, entries in outcomes.items():
            best=max(entries,key=lambda x:x["price"]); probs=samples.get(name,[])
            p=statistics.median(probs) if probs else None
            edge=p*best["price"]-1 if p is not None else None
            if edge is None: status="Chưa đủ dữ liệu"
            elif edge>=.05 and len(probs)>=3: status="🔥 Đáng xem xét"
            elif edge>=.02: status="🟡 Theo dõi thêm"
            else: status="⛔ Bỏ qua"
            rows.append({"Market":MARKET_NAMES.get(mk,mk),"Mã market":mk,"Mốc":point,"Lựa chọn":name,"Odds tốt nhất":best["price"],"Nhà cái":best["book"],"Số nhà cái":len(entries),"Số nhà cái đủ kết quả":len(probs),"Xác suất thị trường":p,"Fair odds":1/p if p else None,"Edge":edge,"Trạng thái":status})
    if not rows: st.warning("Sự kiện này chưa có market/odds hợp lệ."); st.stop()
    picks=[r for r in rows if r["Edge"] is not None and r["Edge"]>=.03 and r["Số nhà cái đủ kết quả"]>=2]
    picks.sort(key=lambda r:(r["Edge"],r["Số nhà cái đủ kết quả"]),reverse=True)
    st.markdown("## 🔥 Kèo sáng được sàng lọc")
    if picks:
        for r in picks[:5]:
            with st.container(border=True):
                mt=r["Market"]+(f" · mốc {r['Mốc']}" if r["Mốc"] is not None else "")
                st.markdown(f"### {mt}: **{r['Lựa chọn']}**")
                a,b,c=st.columns(3); a.metric("Odds tốt nhất",f"{r['Odds tốt nhất']:.2f}"); b.metric("Xác suất thị trường",f"{r['Xác suất thị trường']*100:.1f}%"); c.metric("Edge ước tính",f"{r['Edge']*100:+.1f}%")
                st.caption(f"Nhà cái: {r['Nhà cái']} · So sánh đủ kết quả: {r['Số nhà cái đủ kết quả']} nhà cái")
    else: st.info("Không có tín hiệu đủ ngưỡng từ odds hiện tại. Khuyến nghị: BỎ QUA, không ép chọn kèo.")
    st.markdown("## 📋 Các market API trả về")
    rows.sort(key=lambda r:(r["Edge"] is not None,r["Edge"] or -999),reverse=True)
    display=[]
    for r in rows:
        display.append({"Market":r["Market"],"Mốc":r["Mốc"] if r["Mốc"] is not None else "—","Lựa chọn":r["Lựa chọn"],"Odds tốt nhất":round(r["Odds tốt nhất"],3),"Xác suất thị trường":f"{r['Xác suất thị trường']*100:.1f}%" if r["Xác suất thị trường"] is not None else "Chưa đủ","Fair odds":round(r["Fair odds"],3) if r["Fair odds"] else "—","Edge ước tính":f"{r['Edge']*100:+.1f}%" if r["Edge"] is not None else "—","Nhà cái":r["Nhà cái"],"Trạng thái":r["Trạng thái"]})
    st.dataframe(display,use_container_width=True,hide_index=True)
    st.caption(f"Requests còn lại: {meta.get('remaining','?')}. Chỉ có thể hiển thị môn, trận, market và odds mà API trả về cho gói/khu vực của bạn. Edge là tín hiệu từ odds thị trường, không phải dự đoán độc lập đã được kiểm chứng hay cam kết lợi nhuận.")
else:
    st.markdown("### Cách sử dụng")
    st.write("1. Chọn môn thể thao/esports.")
    st.write("2. Nhập tên đội/người chơi hoặc giải đấu.")
    st.write("3. Bấm **Tìm trận**, rồi chọn trận đúng.")
    st.write("4. App phân tích các market/odds có sẵn cho trận đó.")
