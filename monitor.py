import os
import requests
import yfinance as yf
import pandas as pd
from datetime import datetime

DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")

def get_macro_indicators():
    """心法 1：獲取美元指數 (DX-Y.NYB) 與 10 年美債殖利率 (^TNX)"""
    tickers = ["DX-Y.NYB", "^TNX"]
    result = {
        "dxy": {"val": 0, "score": 0},
        "tnx": {"val": 0, "score": 0}
    }
    
    try:
        data = yf.download(tickers, period="1mo", interval="1d", progress=False)['Close']
        
        dxy_series = data['DX-Y.NYB'].dropna()
        if len(dxy_series) >= 2:
            dxy_current = dxy_series.iloc[-1]
            dxy_ma20 = dxy_series.tail(20).mean()
            dxy_diff = dxy_current - dxy_series.iloc[-2]
            result["dxy"]["val"] = round(dxy_current, 2)
            result["dxy"]["score"] = 1 if (dxy_current < dxy_ma20 and dxy_diff <= 0) else (-1 if dxy_current > dxy_ma20 and dxy_diff > 0 else 0)
            
        tnx_series = data['^TNX'].dropna()
        if len(tnx_series) >= 2:
            tnx_current = tnx_series.iloc[-1]
            tnx_ma20 = tnx_series.tail(20).mean()
            tnx_diff = tnx_current - tnx_series.iloc[-2]
            result["tnx"]["val"] = round(tnx_current, 3)
            result["tnx"]["score"] = 1 if (tnx_current < tnx_ma20 and tnx_diff <= 0) else (-1 if tnx_current > tnx_ma20 and tnx_diff > 0 else 0)
            
    except Exception as e:
        print(f"⚠️ 取得總經資料失敗: {e}")
        
    return result

def get_institutional_data():
    """心法 2：獲取外資現貨買賣超與台指期淨未平倉口數 (導入 MA5、MA10 與防假跌破門檻)"""
    finmind_url = "https://api.finmindtrade.com/api/v4/data"
    
    # 1. 證交所外資現貨買賣超
    params_spot = {
        "dataset": "TaiwanStockTotalInstitutionalInvestors",
        "start_date": (datetime.now() - pd.Timedelta(days=7)).strftime("%Y-%m-%d")
    }
    foreign_spot = 0
    spot_error = ""
    try:
        res = requests.get(finmind_url, params_spot, timeout=15)
        res.raise_for_status()
        data = res.json()
        if data.get("msg") == "success" and len(data.get("data", [])) > 0:
            df = pd.DataFrame(data["data"])
            latest_date = df["date"].max()
            foreign_data = df[(df["date"] == latest_date) & (df["name"] == "Foreign_Investor")]
            if not foreign_data.empty:
                foreign_spot = int(foreign_data.iloc[0]["buy"] - foreign_data.iloc[0]["sell"]) / 1e8
        else:
            spot_error = "FinMind 現貨無資料回傳"
    except Exception as e:
        spot_error = f"FinMind 現貨請求失敗: {str(e)[:40]}"
        
    # 2. 期交所三大法人台指期未平倉
    params_futures = {
        "dataset": "TaiwanFuturesInstitutionalInvestors",
        "data_id": "TX",
        "start_date": (datetime.now() - pd.Timedelta(days=30)).strftime("%Y-%m-%d")
    }
    foreign_futures_oi = 0
    futures_oi_diff = 0
    futures_ma5 = 0
    futures_ma10 = 0
    futures_error = ""
    try:
        res_f = requests.get(finmind_url, params_futures, timeout=15)
        res_f.raise_for_status()
        data_f = res_f.json()
        
        if data_f.get("msg") == "success" and len(data_f.get("data", [])) > 0:
            df_f = pd.DataFrame(data_f["data"])
            
            if "institutional_investors" in df_f.columns:
                tx_df = df_f[df_f["institutional_investors"].astype(str).str.contains("外資及陸資|外資|Foreign", regex=True, na=False)]
                
                if not tx_df.empty:
                    dates = sorted(tx_df["date"].unique())
                    
                    # 計算每日淨未平倉序列
                    daily_oi_list = []
                    for d in dates:
                        d_data = tx_df[tx_df["date"] == d].iloc[0]
                        long_oi = int(d_data.get("long_open_interest_balance_volume", 0))
                        short_oi = int(d_data.get("short_open_interest_balance_volume", 0))
                        daily_oi_list.append(long_oi - short_oi)
                    
                    foreign_futures_oi = daily_oi_list[-1]
                    if len(daily_oi_list) >= 2:
                        futures_oi_diff = foreign_futures_oi - daily_oi_list[-2]
                        
                    # 計算 5 日均線
                    if len(daily_oi_list) >= 5:
                        futures_ma5 = int(sum(daily_oi_list[-5:]) / 5)
                    else:
                        futures_ma5 = int(sum(daily_oi_list) / len(daily_oi_list))
                        
                    # 計算 10 日均線
                    if len(daily_oi_list) >= 10:
                        futures_ma10 = int(sum(daily_oi_list[-10:]) / 10)
                    else:
                        futures_ma10 = int(sum(daily_oi_list) / len(daily_oi_list))
                else:
                    futures_error = "FinMind 篩選後無外資期貨資料"
            else:
                futures_error = f"找不到 institutional_investors 欄位"
        else:
            futures_error = "FinMind 期貨 API 回傳空陣列"
    except Exception as e:
        futures_error = f"期貨請求失敗: {str(e).splitlines()[0][:40]}"

    # 綜合評分：結合短線動能(乖離)與中線趨勢(實質死亡交叉)
    spot_score = 1 if foreign_spot > 50 else (-1 if foreign_spot < -50 else 0)
    
    oi_deviation = foreign_futures_oi - futures_ma5
    if oi_deviation > 5000:
        futures_score = 1    # 短線急補空單/佈多單
    elif oi_deviation < -5000:
        futures_score = -1   # 短線急殺建空單
    elif (futures_ma10 - futures_ma5) > 2000:
        futures_score = -1   # 緩跌：5MA實質跌破10MA超過2000口
    else:
        futures_score = 0    # 維持常態水位

    warning_flag = (foreign_spot > 20 and futures_oi_diff < -3000)

    return {
        "spot": round(foreign_spot, 2),
        "spot_score": spot_score,
        "spot_error": spot_error,
        "futures_oi": foreign_futures_oi,
        "futures_diff": futures_oi_diff,
        "futures_ma5": futures_ma5,
        "futures_ma10": futures_ma10,
        "futures_score": futures_score,
        "futures_error": futures_error,
        "warning": warning_flag
    }

def evaluate_strategy(macro, chips):
    total_score = (macro["dxy"]["score"] + macro["tnx"]["score"] + 
                   chips["spot_score"] + chips["futures_score"])
    
    if chips["warning"]:
        status = "⚠️ 警訊發布（現貨買但期貨大減）"
        allocation = "防禦降檔 (≤ 30%)"
        color = 0xE74C3C
    elif total_score >= 3:
        status = "🟢 資金與籌碼全面偏多"
        allocation = "積極放大 (80% ~ 100%)"
        color = 0x2ECC71
    elif total_score <= -2:
        status = "🔴 資金抽離且外資偏空"
        allocation = "極低持股或避險 (0% ~ 30%)"
        color = 0xE74C3C
    else:
        status = "🟡 盤勢震盪中性"
        allocation = "中性控管 (40% ~ 60%)"
        color = 0xF1C40F
        
    return total_score, status, allocation, color

def send_discord_notification(macro, chips, total_score, status, allocation, color):
    if not DISCORD_WEBHOOK_URL:
        print("未設定 DISCORD_WEBHOOK_URL")
        return

    today = datetime.now().strftime("%Y-%m-%d")
    
    spot_text = f"• 外資現貨買賣超：`{chips['spot']} 億元`"
    if chips.get('spot_error'):
        spot_text += f"\n  ⚠️ **抓取錯誤**: `{chips['spot_error']}`"
        
    # 同步顯示 5MA 與 10MA
    futures_text = f"• 外資台指期淨未平倉：`{chips['futures_oi']:,} 口` (日變化: `{chips['futures_diff']:,}` 口)\n" \
                   f"• 均線基準：5MA `{chips['futures_ma5']:,}` | 10MA `{chips['futures_ma10']:,}`\n" \
                   f"• 期貨綜合評分：`{chips['futures_score']}`"
    if chips.get('futures_error'):
        futures_text += f"\n  ⚠️ **抓取錯誤**: `{chips['futures_error']}`"
    
    embed = {
        "title": f"📊 陳族元投資心法 - 大盤環境與籌碼日報 ({today})",
        "description": f"**總體評估：{status}**\n建議資金水位：`{allocation}` (總分: {total_score}/+4)",
        "color": color,
        "fields": [
            {
                "name": "🌐 心法 1：總經資金指標",
                "value": f"• 美元指數 (DXY)：`{macro['dxy']['val']}` (趨勢分: {macro['dxy']['score']})\n"
                         f"• 美 10 年債殖利率：`{macro['tnx']['val']}%` (趨勢分: {macro['tnx']['score']})",
                "inline": False
            },
            {
                "name": "🎯 心法 2：外資籌碼指標 (緩衝門檻修正版)",
                "value": f"{spot_text}\n{futures_text}",
                "inline": False
            }
        ],
        "footer": {"text": "覆巢之下無完卵｜自動監控通知"}
    }
    
    if chips["warning"]:
        embed["fields"].append({
            "name": "🚨 關鍵轉折警示",
            "value": "外資呈現「現貨買超、期貨顯著減碼」之背離結構，需嚴防大盤逢高變盤！",
            "inline": False
        })
        
    requests.post(DISCORD_WEBHOOK_URL, json={"embeds": [embed]}, timeout=10)

if __name__ == "__main__":
    macro_data = get_macro_indicators()
    chips_data = get_institutional_data()
    score, status, alloc, color = evaluate_strategy(macro_data, chips_data)
    send_discord_notification(macro_data, chips_data, score, status, alloc, color)
