import os
import requests
import yfinance as yf
import pandas as pd
from datetime import datetime

DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")

def get_macro_indicators():
    """心法 1：獲取美元指數 (DX-Y.NYB) 與 10 年美債殖利率 (^TNX)"""
    tickers = ["DX-Y.NYB", "^TNX"]
    
    # 建立預設值，避免抓取失敗時程式崩潰
    result = {
        "dxy": {"val": 0, "score": 0},
        "tnx": {"val": 0, "score": 0}
    }
    
    try:
        data = yf.download(tickers, period="1mo", interval="1d", progress=False)['Close']
        
        # 處理美元指數，先確認資料筆數是否足夠
        dxy_series = data['DX-Y.NYB'].dropna()
        if len(dxy_series) >= 2:
            dxy_current = dxy_series.iloc[-1]
            dxy_ma20 = dxy_series.tail(20).mean()
            dxy_diff = dxy_current - dxy_series.iloc[-2]
            result["dxy"]["val"] = round(dxy_current, 2)
            result["dxy"]["score"] = 1 if (dxy_current < dxy_ma20 and dxy_diff <= 0) else (-1 if dxy_current > dxy_ma20 and dxy_diff > 0 else 0)
            
        # 處理美債殖利率，先確認資料筆數是否足夠
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
    """心法 2：改用 FinMind API 獲取外資現貨買賣超"""
    # ... 前略 ...
    
    # 1. 證交所外資買賣超 (改用 FinMind API 避免 GitHub Actions IP 被擋)
    finmind_url = "https://api.finmindtrade.com/api/v4/data"
    params = {
        "dataset": "TaiwanStockTotalInstitutionalInvestors",
        # 抓取近幾天的資料，避免遇到假日沒資料
        "start_date": (datetime.now() - pd.Timedelta(days=5)).strftime("%Y-%m-%d")
    }
    
    foreign_spot = 0
    spot_error = ""
    try:
        res = requests.get(finmind_url, params=params, timeout=15)
        res.raise_for_status()
        data = res.json()
        
        if data.get("msg") == "success" and len(data.get("data", [])) > 0:
            df = pd.DataFrame(data["data"])
            # 篩選最新一天的外資資料 (外資及陸資買賣超)
            latest_date = df["date"].max()
            foreign_data = df[(df["date"] == latest_date) & (df["name"] == "Foreign_Investor")]
            if not foreign_data.empty:
                # FinMind 單位為元，轉換為億元
                foreign_spot = int(foreign_data.iloc[0]["buy"] - foreign_data.iloc[0]["sell"]) / 1e8
        else:
            spot_error = "FinMind 無資料回傳"
    except Exception as e:
        spot_error = f"FinMind 請求失敗: {str(e)[:40]}"
        
    # ... 後續期貨抓取邏輯保留原樣（若期貨也被擋，同樣可尋找第三方替代） ...

def send_discord_notification(macro, chips, total_score, status, allocation, color):
    if not DISCORD_WEBHOOK_URL:
        print("未設定 DISCORD_WEBHOOK_URL")
        return

    today = datetime.now().strftime("%Y-%m-%d")
    
    # 處理現貨字串與錯誤訊息
    spot_text = f"• 外資現貨買賣超：`{chips['spot']} 億元`"
    if chips['spot_error']:
        spot_text += f"\n  ⚠️ **抓取失敗**: `{chips['spot_error']}`"
        
    # 處理期貨字串與錯誤訊息
    futures_text = f"• 外資台指期淨未平倉：`{chips['futures_oi']:,} 口` (日變化: `{chips['futures_diff']:,}` 口)"
    if chips['futures_error']:
        futures_text += f"\n  ⚠️ **抓取失敗**: `{chips['futures_error']}`"
    
    embed = {
        "title": f"📊 陳族元投資心法 - 大盤環境與籌碼日報 ({today})",
        "description": f"**總體評估：{status}**\n建議資金水位：`{allocation}` (評分: {total_score}/+4)",
        "color": color,
        "fields": [
            {
                "name": "🌐 心法 1：總經資金指標",
                "value": f"• 美元指數 (DXY)：`{macro['dxy']['val']}` (趨勢分: {macro['dxy']['score']})\n"
                         f"• 美 10 年債殖利率：`{macro['tnx']['val']}%` (趨勢分: {macro['tnx']['score']})",
                "inline": False
            },
            {
                "name": "🎯 心法 2：外資籌碼指標",
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
def evaluate_strategy(macro, chips):
    total_score = (macro["dxy"]["score"] + macro["tnx"]["score"] + 
                   chips["spot_score"] + chips["futures_score"])
    
    if chips["warning"]:
        status = "⚠️ 警訊發布（現貨買但期貨大減）"
        allocation = "防禦降檔 (≤ 30%)"
        color = 0xE74C3C  # 紅色
    elif total_score >= 3:
        status = "🟢 資金與籌碼全面偏多"
        allocation = "積極放大 (80% ~ 100%)"
        color = 0x2ECC71  # 綠色
    elif total_score <= -2:
        status = "🔴 資金抽離且外資偏空"
        allocation = "極低持股或避險 (0% ~ 30%)"
        color = 0xE74C3C
    else:
        status = "🟡 盤勢震盪中性"
        allocation = "中性控管 (40% ~ 60%)"
        color = 0xF1C40F  # 黃色
        
    return total_score, status, allocation, color

def send_discord_notification(macro, chips, total_score, status, allocation, color):
    if not DISCORD_WEBHOOK_URL:
        print("未設定 DISCORD_WEBHOOK_URL")
        return

    today = datetime.now().strftime("%Y-%m-%d")
    
    embed = {
        "title": f"📊 陳族元投資心法 - 大盤環境與籌碼日報 ({today})",
        "description": f"**總體評估：{status}**\n建議資金水位：`{allocation}` (評分: {total_score}/+4)",
        "color": color,
        "fields": [
            {
                "name": "🌐 心法 1：總經資金指標",
                "value": f"• 美元指數 (DXY)：`{macro['dxy']['val']}` (趨勢分: {macro['dxy']['score']})\n"
                         f"• 美 10 年債殖利率：`{macro['tnx']['val']}%` (趨勢分: {macro['tnx']['score']})",
                "inline": False
            },
            {
                "name": "🎯 心法 2：外資籌碼指標",
                "value": f"• 外資現貨買賣超：`{chips['spot']} 億元`\n"
                         f"• 外資台指期淨未平倉：`{chips['futures_oi']:,} 口` (日變化: `{chips['futures_diff']:,}` 口)",
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
