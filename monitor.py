import os
import requests
import yfinance as yf
import pandas as pd
from datetime import datetime

DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")

def get_macro_indicators():
    """心法 1：獲取美元指數 (DX-Y.NYB) 與 10 年美債殖利率 (^TNX)"""
    tickers = ["DX-Y.NYB", "^TNX"]
    data = yf.download(tickers, period="1mo", interval="1d", progress=False)['Close']
    
    dxy_current = data['DX-Y.NYB'].dropna().iloc[-1]
    dxy_ma20 = data['DX-Y.NYB'].dropna().tail(20).mean()
    dxy_diff = dxy_current - data['DX-Y.NYB'].dropna().iloc[-2]
    
    tnx_current = data['^TNX'].dropna().iloc[-1]
    tnx_ma20 = data['^TNX'].dropna().tail(20).mean()
    tnx_diff = tnx_current - data['^TNX'].dropna().iloc[-2]
    
    # 判斷多空 (DXY/TNX 走弱有利資金回流台股)
    dxy_score = 1 if (dxy_current < dxy_ma20 and dxy_diff <= 0) else (-1 if dxy_current > dxy_ma20 and dxy_diff > 0 else 0)
    tnx_score = 1 if (tnx_current < tnx_ma20 and tnx_diff <= 0) else (-1 if tnx_current > tnx_ma20 and tnx_diff > 0 else 0)
    
    return {
        "dxy": {"val": round(dxy_current, 2), "score": dxy_score},
        "tnx": {"val": round(tnx_current, 3), "score": tnx_score}
    }

def get_institutional_data():
    """心法 2：獲取外資現貨買賣超與台指期淨未平倉口數"""
    # 加入 User-Agent 偽裝成一般瀏覽器，避免被證交所防護機制阻擋
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    # 1. 證交所外資買賣超 (三大法人買賣超日報)
    twse_url = "https://openapi.twse.com.tw/v1/fund/BFI82U"
    foreign_spot = 0
    try:
        res_twse = requests.get(twse_url, headers=headers, timeout=15)
        res_twse.raise_for_status()  # 若 HTTP 狀態碼非 200，會拋出錯誤
        
        # 確保回傳內容存在且能解析為 JSON
        if res_twse.text.strip():
            twse_data = res_twse.json()
            for row in twse_data:
                if "外資及陸資" in row.get("單位名稱", ""):
                    foreign_spot = int(row.get("買賣差額", 0).replace(",", "")) / 1e8
                    break
    except Exception as e:
        print(f"⚠️ 取得證交所現貨資料失敗: {e}")
        # 若失敗則 foreign_spot 維持 0，讓程式不崩潰繼續執行
            
    # 2. 期交所三大法人台指期未平倉
    taifex_url = "https://openapi.taifex.com.tw/v1/DailyForeignFutures"
    foreign_futures_oi = 0
    futures_oi_diff = 0
    try:
        res_taifex = requests.get(taifex_url, headers=headers, timeout=15)
        res_taifex.raise_for_status()
        
        if res_taifex.text.strip():
            taifex_data = res_taifex.json()
            # 篩選台指期 (TX) 的外資數據
            for row in taifex_data:
                if row.get("ContractId") == "TX" and "外資" in row.get("Identity", ""):
                    foreign_futures_oi = int(row.get("NetOpenInterest", 0))
                    futures_oi_diff = int(row.get("NetOpenInterestChange", 0))
                    break
    except Exception as e:
        print(f"⚠️ 取得期交所期貨資料失敗: {e}")

    # 籌碼評分
    spot_score = 1 if foreign_spot > 50 else (-1 if foreign_spot < -50 else 0)
    futures_score = 1 if foreign_futures_oi > 0 else (-1 if foreign_futures_oi < -20000 else 0)
    
    # 陳族元核心警訊：現貨買超但期貨大幅減碼避險
    warning_flag = (foreign_spot > 20 and futures_oi_diff < -3000)

    return {
        "spot": round(foreign_spot, 2),
        "spot_score": spot_score,
        "futures_oi": foreign_futures_oi,
        "futures_diff": futures_oi_diff,
        "futures_score": futures_score,
        "warning": warning_flag
    }

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
