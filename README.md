# Football 1X2 AI — Python 3

Ứng dụng dự đoán bóng đá 1X2 (Home / Draw / Away) theo hướng machine learning + statistical modeling.

## Mô hình

Pipeline mặc định kết hợp:

1. **Elo rating** theo thời gian, cập nhật sau từng trận.
2. **Rolling form**: điểm, bàn thắng/bàn thua, hiệu suất sân nhà/sân khách trong các trận trước đó.
3. **Poisson goal model**: ước lượng phân phối bàn thắng và xác suất 1X2.
4. **XGBoost multiclass** nếu cài được.
5. **Logistic regression** làm mô hình dự phòng.
6. **Ensemble + probability calibration** để đầu ra là xác suất.
7. **Time-series backtest**: chỉ dùng dữ liệu quá khứ, tránh nhìn trước tương lai.

> Không có mô hình nào đảm bảo kết quả bóng đá. Xác suất là ước lượng thống kê, không phải cam kết thắng cược.

## Cài đặt

Python 3.10+ khuyến nghị.

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

pip install -r requirements.txt
```

## Dữ liệu

Đặt file CSV lịch sử vào:

`data/matches.csv`

Schema tối thiểu:

```text
date,home_team,away_team,home_goals,away_goals
2025-08-15,Arsenal,Manchester United,2,1
```

Có thể thêm:

```text
home_xg,away_xg
```

Nếu không có xG, pipeline vẫn chạy.

Ngày nên ở dạng `YYYY-MM-DD` hoặc datetime chuẩn.

## Huấn luyện

```bash
python train.py --data data/matches.csv --model artifacts/model.joblib
```

Sau khi train, hệ thống tạo:

- `artifacts/model.joblib`
- `artifacts/metrics.json`
- `artifacts/backtest.csv`

## Dự đoán từ terminal

```bash
python predict.py --model artifacts/model.joblib --home Arsenal --away Chelsea
```

## Chạy app

```bash
streamlit run app.py
```

Mở địa chỉ Streamlit hiển thị trên terminal.

## CSV mẫu

```bash
python make_sample.py
```

Lệnh này tạo dữ liệu synthetic để kiểm tra app. **Không dùng dữ liệu synthetic để đánh giá khả năng dự đoán bóng đá thật.**

## Dùng dữ liệu thật

Một nguồn dữ liệu thực tế cần có ít nhất kết quả trận đấu. Nếu muốn hệ thống mạnh hơn, hãy bổ sung:

- xG/xGA
- shots / shots on target
- possession
- red cards
- rest days
- injuries/suspensions
- đội hình dự kiến
- odds thị trường (nếu dùng, phải cực kỳ cẩn thận với leakage)
- strength-of-schedule

Không đưa thông tin chỉ xuất hiện sau giờ kickoff vào feature pre-match.

## Cấu trúc

```text
football_1x2_ai/
├── app.py
├── train.py
├── predict.py
├── make_sample.py
├── requirements.txt
├── README.md
├── src/
│   ├── __init__.py
│   ├── features.py
│   ├── models.py
│   └── pipeline.py
├── data/
└── artifacts/
```

## Diễn giải kết quả

Ví dụ:

```text
Arsenal
Home: 55.2%
Draw: 24.7%
Away: 20.1%
```

Đây là phân phối xác suất của mô hình. Có thể hiển thị thêm:

- predicted class
- entropy/uncertainty
- Poisson probability
- ML probability
- Elo difference
- expected goals

### Quan trọng

Đừng tối ưu bằng accuracy đơn thuần. Với 1X2 nên theo dõi:

- Log Loss
- Brier Score
- calibration
- accuracy
- confusion matrix

Một mô hình 55% accuracy nhưng xác suất sai lệch có thể kém hơn một mô hình có calibration tốt.

## Nâng cấp production

Bản này là nền tảng nghiêm túc. Để lên production, nên thêm:

- database PostgreSQL/SQLite
- scheduler tự cập nhật dữ liệu
- API provider
- đội hình/injury feed
- league-specific models
- rolling retraining
- drift detection
- SHAP explanations
- Docker
- monitoring
- model registry
- prediction history
