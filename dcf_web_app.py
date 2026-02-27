import streamlit as st
import tushare as ts
import pandas as pd
import numpy as np
from google import genai
import json
import plotly.graph_objects as go

# ==========================================
# 1. 基础配置与 API 初始化
# ==========================================
st.set_page_config(page_title="Institutional DCF Analyzer", layout="wide")
TUSHARE_TOKEN = '38164b161ab8e53a584a8d88e17bee4a41520ae068dc0b582c2fad60'
GEMINI_API_KEY = 'AIzaSyCXiW5itDuouxvhOpBYg0oYeNNx3ApSM_Q'

ts.set_token(TUSHARE_TOKEN)
pro = ts.pro_api()
client = genai.Client(api_key=GEMINI_API_KEY)

# ==========================================
# 2. 核心计算逻辑 (封装)
# ==========================================
def get_real_data(ts_code):
    # 抓取行情
    df_basic = pro.daily_basic(ts_code=ts_code, limit=1)
    # 抓取资产负债
    df_bal = pro.balancesheet(ts_code=ts_code, limit=1, fields='money_cap,total_liab')
    # 抓取营收历史
    df_inc = pro.income(ts_code=ts_code, limit=3, fields='total_revenue')
    
    net_debt = (df_bal['total_liab'].iloc[0] - df_bal['money_cap'].iloc[0]) / 1e6
    hist_rev = (df_inc['total_revenue'][::-1] / 1e6).tolist()
    
    return {
        "price": df_basic['close'].iloc[0],
        "shares": df_basic['total_share'].iloc[0] / 100,
        "net_debt": net_debt,
        "hist_rev": hist_rev
    }

def run_dcf(base_rev, growth_rates, margin, wacc, tg, net_debt, shares):
    current_rev = base_rev
    p_fcfs = []
    for g in growth_rates:
        current_rev *= (1 + g)
        p_fcfs.append(current_rev * margin)
    
    pv_fcfs = sum([f / (1 + wacc)**(i + 0.5) for i, f in enumerate(p_fcfs)])
    tv = (p_fcfs[-1] * (1 + tg)) / (wacc - tg)
    pv_tv = tv / (1 + wacc)**5
    
    ev = pv_fcfs + pv_tv
    equity_value = ev - net_debt
    return equity_value / shares

# ==========================================
# 3. 网页侧边栏：参数交互
# ==========================================
st.sidebar.header("🎯 机构风控参数配置")
target_code = st.sidebar.text_input("股票代码", value="603501.SH")

if st.sidebar.button("同步 Tushare 真实数据"):
    st.session_state.data = get_real_data(target_code)

if 'data' in st.session_state:
    data = st.session_state.data
    
    st.sidebar.subheader("📈 增长与利润预测")
    g1 = st.sidebar.slider("第一年增长率", 0.0, 1.0, 0.30)
    g2 = st.sidebar.slider("第二年增长率", 0.0, 1.0, 0.20)
    g_rest = st.sidebar.slider("后续平均增长率", 0.0, 0.5, 0.10)
    margin = st.sidebar.slider("FCF 利润率", 0.05, 0.40, 0.13)
    
    st.sidebar.subheader("💸 资本成本 (WACC)")
    wacc = st.sidebar.slider("折现率 (WACC)", 0.05, 0.15, 0.10)
    tg = st.sidebar.slider("永续增长率 (TG)", 0.01, 0.05, 0.03)

    # ==========================================
    # 4. 主界面展示
    # ==========================================
    st.title(f"📊 {target_code} 深度 DCF 估值看板")
    
    col1, col2, col3 = st.columns(3)
    col1.metric("当前股价", f"¥{data['price']}")
    
    growth_list = [g1, g2, g_rest, g_rest, g_rest]
    target_price = run_dcf(data['hist_rev'][-1], growth_list, margin, wacc, tg, data['net_debt'], data['shares'])
    
    upside = (target_price / data['price']) - 1
    col2.metric("隐含目标价", f"¥{target_price:.2f}", f"{upside:.2%}", delta_color="inverse")
    col3.metric("真实净债务", f"¥{data['net_debt']:,.0f}M")

    # 敏感性分析矩阵计算
    st.subheader("🛡️ 左侧博弈：WACC vs 永续增长 敏感性矩阵")
    w_list = np.linspace(wacc-0.02, wacc+0.02, 5)
    t_list = np.linspace(tg-0.01, tg+0.01, 5)
    
    matrix = []
    for w in w_list:
        row = []
        for t in t_list:
            p = run_dcf(data['hist_rev'][-1], growth_list, margin, w, t, data['net_debt'], data['shares'])
            row.append(p)
        matrix.append(row)

    # 绘制热力图
    fig = go.Figure(data=go.Heatmap(
        z=matrix,
        x=[f"TG {t:.1%}" for t in t_list],
        y=[f"WACC {w:.1%}" for w in w_list],
        colorscale='RdYlGn',
        text=[[f"¥{val:.2f}" for val in row] for row in matrix],
        texttemplate="%{text}",
    ))
    fig.update_layout(height=400, margin=dict(l=20, r=20, t=20, b=20))
    st.plotly_chart(fig, use_container_width=True)

    st.info("💡 解读：绿色区域代表该参数组合下股价被低估，红色代表高估。对于左侧交易，应重点关注深绿色价格区间作为安全垫。")

else:
    st.warning("请在侧边栏点击『同步 Tushare 真实数据』开始分析。")
