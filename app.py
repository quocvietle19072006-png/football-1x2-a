import json
from pathlib import Path
import pandas as pd
import streamlit as st

from src.pipeline import predict_fixture

st.set_page_config(
    page_title="Football 1X2 AI",
    page_icon="⚽",
    layout="wide"
)

st.title("⚽ Football 1X2 AI")
st.caption("XGBoost/Logistic + Elo + Poisson + leakage-safe historical features")

model_path = Path("artifacts/model.joblib")

if not model_path.exists():
    st.warning("Chưa có model. Hãy chạy: python train.py --data data/matches.csv")
    st.stop()

import joblib
artifact = joblib.load(model_path)
teams = artifact["teams"]

st.sidebar.header("Trận đấu")
home = st.sidebar.selectbox("Đội nhà", teams, index=0)
away_options = [x for x in teams if x != home]
away = st.sidebar.selectbox("Đội khách", away_options, index=0)

if st.button("🔮 Dự đoán", type="primary"):
    result = predict_fixture(model_path, home, away)

    cols = st.columns(3)
    labels = [("1", result["H"]), ("X", result["D"]), ("2", result["A"])]
    for col, (label, p) in zip(cols, labels):
        col.metric(label, f"{p*100:.2f}%")

    st.subheader(f"{home}  vs  {away}")
    st.write(f"**Kết quả có xác suất cao nhất theo mô hình:** `{result['predicted']}`")

    chart = pd.DataFrame({
        "Outcome": ["1 - Home", "X - Draw", "2 - Away"],
        "Probability": [result["H"], result["D"], result["A"]]
    }).set_index("Outcome")
    st.bar_chart(chart)

    c1, c2 = st.columns(2)
    with c1:
        st.subheader("Mô hình ML")
        st.write({
            "1": f"{result['ml_H']*100:.2f}%",
            "X": f"{result['ml_D']*100:.2f}%",
            "2": f"{result['ml_A']*100:.2f}%"
        })
    with c2:
        st.subheader("Poisson")
        st.write({
            "1": f"{result['poisson_H']*100:.2f}%",
            "X": f"{result['poisson_D']*100:.2f}%",
            "2": f"{result['poisson_A']*100:.2f}%"
        })

    st.info(
        f"Expected goals: {result['expected_home_goals']:.2f} - "
        f"{result['expected_away_goals']:.2f}"
    )

st.divider()
st.subheader("📊 Chất lượng model")

m = artifact.get("metrics", {})
if m:
    a, b, c, d = st.columns(4)
    a.metric("Accuracy", f"{m.get('accuracy', 0)*100:.2f}%")
    b.metric("Log Loss", f"{m.get('log_loss', 0):.4f}")
    c.metric("Brier", f"{m.get('brier', 0):.4f}")
    d.metric("Test matches", str(m.get("n", 0)))

st.caption(
    "Backtest là dữ liệu quá khứ và không đảm bảo kết quả tương lai. "
    "Không dùng xác suất mô hình như một cam kết thắng cược."
)
