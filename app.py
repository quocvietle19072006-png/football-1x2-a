import os, math, statistics, time
from typing import Any, Dict, List, Tuple
import requests
import streamlit as st

st.set_page_config(page_title="AI Sports Value Scanner", page_icon="🎯", layout="wide")

API_BASE = "https://api.fieldfunded.com"
API_KEY = st.secrets.get("FIELDFUNDED_API_KEY", os.getenv("FIELDFUNDED_API_KEY", ""))

st.title("🎯 AI Sports Value Scanner V2")
st.caption("Multi-sport + esports • live/pre-match odds • market consensus • value/edge ranking")

if not API_KEY:
    st.warning("Chưa có FIELDFUNDED_API_KEY. Thêm key vào Streamlit Secrets để kết nối dữ liệu.")
    st.info("Secrets: FIELDFUNDED_API_KEY = \"YOUR_KEY\"")
    st.stop()

session = requests.Session()
session.headers.update({"X-API-Key": API_KEY, "Accept": "application/json"})

def api(path, params=None, method="GET", timeout=15):
    url = API_BASE.rstrip("/") + path
    try:
        r = session.request(method, url, params=params, timeout=timeout)
        if r.status_code == 429:
            return {"_error": "RATE_LIMIT", "_retry": r.headers.get("Retry-After", "2")}
        r.raise_for_status()
        return r.json()
    except Exception as e:
        return {"_error": str(e)}

def unwrap(x):
    if isinstance(x, dict):
        if isinstance(x.get("data"), (dict, list)):
            return x["data"]
        if isinstance(x.get("events"), list):
            return x["events"]
        if isinstance(x.get("markets"), list):
            return x["markets"]
    return x

def first_list(x):
    x=unwrap(x)
    if isinstance(x,list): return x
    if isinstance(x,dict):
        for k,v in x.items():
            if isinstance(v,list): return v
    return []

def event_name(e):
    home=e.get("homeTeam") or e.get("home") or e.get("home_team") or e.get("participant1") or "Home"
    away=e.get("awayTeam") or e.get("away") or e.get("away_team") or e.get("participant2") or "Away"
    def nm(x):
        return x.get("name", str(x)) if isinstance(x,dict) else str(x)
    return f"{nm(home)} vs {nm(away)}"

def event_live(e):
    s=str(e.get("status") or e.get("state") or e.get("gameStatus") or "").lower()
    return bool(e.get("isLive") or e.get("live") or s in {"live","inplay","in-play","ongoing","started"})

def odds_value(o):
    if isinstance(o,(int,float)): return float(o)
    if isinstance(o,dict):
        for k in ("decimal","price","odds","value","odd"):
            try:
                if o.get(k) is not None: return float(o[k])
            except: pass
    return None

def outcome_name(o):
    if isinstance(o,str): return o
    if not isinstance(o,dict): return str(o)
    for k in ("name","label","selection","outcome","title","value"):
        if o.get(k) is not None: return str(o[k])
    return "Unknown"

def flatten_market(m):
    # Returns list of (outcome, decimal odds)
    out=[]
    raw = m.get("outcomes") or m.get("selections") or m.get("odds") or []
    if isinstance(raw,dict):
        raw=[dict({"name":k}, **(v if isinstance(v,dict) else {"price":v})) for k,v in raw.items()]
    if isinstance(raw,list):
        for o in raw:
            if isinstance(o,dict):
                price=odds_value(o)
                if price and price>1.0:
                    out.append((outcome_name(o),price))
    return out

def normalize_probs(rows):
    inv=[1/o for _,o in rows if o>1]
    s=sum(inv)
    if s<=0:return []
    return [(name,(1/odd)/s,odd) for (name,odd) in rows]

def canonical_market_name(m):
    for k in ("name","marketName","market","label","type","key"):
        if m.get(k): return str(m[k])
    return "Unknown Market"

def collect_markets(e):
    raw=e.get("markets") or e.get("odds") or []
    if isinstance(raw,dict):
        tmp=[]
        for k,v in raw.items():
            if isinstance(v,list):
                tmp += [{"name":k,"outcomes":v}]
            elif isinstance(v,dict):
                z=dict(v); z.setdefault("name",k); tmp.append(z)
        raw=tmp
    return raw if isinstance(raw,list) else []

def consensus(markets):
    # Aggregate normalized probabilities across bookmaker/market copies.
    buckets={}
    for m in markets:
        rows=flatten_market(m)
        if len(rows)<2: continue
        probs=normalize_probs(rows)
        name=canonical_market_name(m)
        key=name.lower().strip()
        for out,p,odd in probs:
            buckets.setdefault((key,out.lower().strip()), {"name":name,"outcome":out,"ps":[],"odds":[]})
            buckets[(key,out.lower().strip())]["ps"].append(p)
            buckets[(key,out.lower().strip())]["odds"].append(odd)
    result=[]
    for b in buckets.values():
        if not b["ps"]: continue
        p=statistics.median(b["ps"])
        best=max(b["odds"])
        # A simple dispersion penalty: disagreement across books reduces confidence.
        disp=statistics.pstdev(b["ps"]) if len(b["ps"])>1 else 0
        result.append({"market":b["name"],"outcome":b["outcome"],"p":p,"best_odds":best,"disp":disp,"books":len(b["ps"])})
    return result

def model_score(p, odds, disp, live=False):
    # Market-first baseline: fair probability is consensus p.
    # The app only recommends when price is meaningfully above fair price.
    fair=1/p if p>0 else 999
    edge=p*odds-1
    confidence=max(0,min(100, 100*p - disp*400))
    if live: confidence*=0.97
    # Require both positive EV and a minimum edge.
    if edge >= 0.08 and confidence >= 52: status="🔥 NÊN CHƠI"
    elif edge >= 0.03: status="🟡 CÓ THỂ CÂN NHẮC"
    else: status="⛔ KHÔNG NÊN CHƠI"
    return edge,fair,confidence,status

def extract_events(payload):
    arr=first_list(payload)
    return [x for x in arr if isinstance(x,dict)]

# Try documented/common endpoints. If provider changes route, UI reports it instead of fabricating data.
EVENT_PATHS = ["/v1/live", "/v1/events"]
UPCOMING_PATHS = ["/v1/events"]

tab1,tab2=st.tabs(["🔥 Live","📅 Sắp diễn ra"])
with tab1:
    if st.button("🔄 Tải live mới nhất", use_container_width=True):
        st.cache_data.clear()
    live_payload=api("/v1/live")
    if live_payload.get("_error"):
        st.error(f"Không tải được live: {live_payload['_error']}")
        st.stop()
    live_events=extract_events(live_payload)
with tab2:
    days=st.slider("Phạm vi",1,7,2)
    upcoming_payload=api("/v1/events", {"days":days})
    upcoming_events=extract_events(upcoming_payload)

events=live_events if 'live_events' in locals() else upcoming_events
if not events:
    st.info("Nguồn hiện không trả về sự kiện. Kiểm tra API key hoặc cấu trúc endpoint của tài khoản.")
    st.stop()

# Deduplicate
seen=set(); clean=[]
for e in events:
    eid=str(e.get("id") or e.get("eventId") or event_name(e))
    if eid not in seen:
        seen.add(eid); clean.append(e)
events=clean

labels=[("🔴 LIVE " if event_live(e) else "🟢 ") + event_name(e) for e in events]
idx=st.selectbox("Chọn sự kiện", range(len(events)), format_func=lambda i: labels[i])
ev=events[idx]
eid=ev.get("id") or ev.get("eventId")

detail=ev
if eid:
    d=api(f"/v1/events/{eid}")
    if not d.get("_error"):
        cand=unwrap(d)
        if isinstance(cand,dict): detail=cand

markets=collect_markets(detail)
rows=consensus(markets)

st.subheader("📌 Phân tích kèo")
if not rows:
    st.warning("Sự kiện này chưa có market/odds đọc được từ API.")
    st.stop()

recs=[]
for r in rows:
    edge,fair,conf,status=model_score(r["p"],r["best_odds"],r["disp"],event_live(ev))
    rec=dict(r,edge=edge,fair=fair,confidence=conf,status=status)
    if edge>0: recs.append(rec)

recs.sort(key=lambda x:(x["edge"],x["confidence"]), reverse=True)

if recs:
    top=recs[:3]
    cols=st.columns(min(3,len(top)))
    for c,r in zip(cols,top):
        with c:
            st.metric(r["status"], r["outcome"], f"{r['edge']*100:+.1f}% edge")
            st.write(f"**Market:** {r['market']}")
            st.write(f"Xác suất thị trường: **{r['p']*100:.1f}%**")
            st.write(f"Odds tốt nhất: **{r['best_odds']:.2f}**")
            st.write(f"Fair odds: **{r['fair']:.2f}**")
            st.write(f"Độ tin cậy: **{r['confidence']:.0f}/100**")
else:
    st.info("⛔ Không tìm thấy kèo có edge dương đủ để đề xuất.")

st.divider()
st.subheader("📊 Toàn bộ market có thể phân tích")
display=[]
for r in sorted(rows,key=lambda x:x["p"],reverse=True):
    edge,fair,conf,status=model_score(r["p"],r["best_odds"],r["disp"],event_live(ev))
    display.append({
        "Market":r["market"],"Lựa chọn":r["outcome"],
        "Xác suất":f"{r['p']*100:.1f}%",
        "Odds tốt nhất":round(r["best_odds"],2),
        "Edge":f"{edge*100:+.1f}%",
        "Fair odds":round(fair,2),
        "Confidence":round(conf),
        "Trạng thái":status
    })
st.dataframe(display,use_container_width=True,hide_index=True)

st.caption("⚠️ Đây là market-based value scanner: xác suất lấy từ đồng thuận odds sau khi loại margin, không phải đảm bảo thắng. Chỉ đề xuất khi có edge đủ lớn.")
