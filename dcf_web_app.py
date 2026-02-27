import streamlit as st
import tushare as ts
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import os

# ==========================================
# 1. 基础配置与安全凭证 (华尔街极简风格)
# ==========================================
st.set_page_config(page_title="Pro DCF Analyzer", layout="wide", initial_sidebar_state="expanded")

# 安全做法：从环境变量读取。本地测试可直接替换字符串
TUSHARE_TOKEN = os.environ.get('TUSHARE_TOKEN', '38164b161ab8e53a584a8d88e17bee4a41520ae068dc0b582c2fad60')

try:
    ts.set_token(TUSHARE_TOKEN)
    pro = ts.pro_api()
except Exception:
    pass

# ==========================================
# 2. 数据层核心逻辑 (专为 A股“个股”定制)
# ==========================================
@st.cache_data(ttl=3600)
def get_real_data(ts_code):
    try:
        # 使用个股基础行情接口，获取最精准的收盘价和总股本
        df_basic = pro.daily_basic(ts_code=ts_code, limit=1)
        df_bal = pro.balancesheet(ts_code=ts_code, limit=1, fields='money_cap,total_liab')
        df_inc = pro.income(ts_code=ts_code, limit=3, fields='total_revenue')
        
        # 保护逻辑：查无此股
        if df_basic.empty:
            st.error(f"❌ 未查询到 {ts_code} 的行情，请检查股票代码是否正确 (例如: 688183.SH)。")
            return None
            
        close_price = df_basic['close'].iloc[0]
        total_share = df_basic['total_share'].iloc[0] # 单位：万股
        
        # 统一单位转换为：亿元 (避免 ¥ 符号引发浏览器误翻译为日元)
        money_cap = df_bal['money_cap'].iloc[0] if not df_bal.empty and pd.notna(df_bal['money_cap'].iloc[0]) else 0
        total_liab = df_bal['total_liab'].iloc[0] if not df_bal.empty and pd.notna(df_bal['total_liab'].iloc[0]) else 0
        net_debt = (total_liab - money_cap) / 100000000 
        
        hist_rev = (df_inc['total_revenue'][::-1] / 100000000).tolist() if not df_inc.empty else [10, 20, 30]
        
        return {
            "price": close_price,
            "shares": total_share / 10000, # 转换为亿股
            "net_debt": net_debt,
            "hist_rev": hist_rev
        }
    except Exception as e:
        st.error(f"⚠️ 数据拉取异常: {e}")
        return None

def run_dcf(base_rev, growth_rates, margin, wacc, tg, net_debt, shares):
    current_rev = base_rev
    p_fcfs = []
    for g in growth_rates:
        current_rev *= (1 + g)
        p_fcfs.append(current_rev * margin)
    
    # 年中折现计算
    pv_fcfs = sum([f / (1 + wacc)**(i + 0.5) for i, f in enumerate(p_fcfs)])
    
    # 财务数学保护：折现率必须大于永续增长率，否则公式失效
    if wacc <= tg:
        return 0.0
        
    tv = (p_fcfs[-1] * (1 + tg)) / (wacc - tg)
    pv_tv = tv / (1 + wacc)**5
    
    ev = pv_fcfs + pv_tv
    equity_value = ev - net_debt
    implied_price = equity_value / shares
    return max(implied_price, 0.0)

# ==========================================
# 3. 前端交互界面
# ==========================================
st.sidebar.markdown("### ⚙️ 机构风控参数台")
target_code = st.sidebar.text_input("输入个股代码 (例: 688183.SH)", value="688183.SH")

if st.sidebar.button("🔄 同步 Tushare 真实数据"):
    st.session_state.data = get_real_data(target_code)

if 'data' in st.session_state and st.session_state.data is not None:
    data = st.session_state.data
    
    st.sidebar.markdown("---")
    st.sidebar.markdown("#### 📈 增长与利润预期")
    g1 = st.sidebar.slider("第一年增速", 0.0, 2.0, 0.50, 0.05)
    g2 = st.sidebar.slider("第二年增速", 0.0, 2.0, 0.40, 0.05)
    g_rest = st.sidebar.slider("后续平均增速", 0.0, 1.0, 0.20, 0.05)
    margin = st.sidebar.slider("自由现金流利润率", 0.01, 0.50, 0.20, 0.01)
    
    st.sidebar.markdown("#### 💸 资本成本 (WACC)")
    wacc = st.sidebar.slider("折现率 (WACC)", 0.05, 0.15, 0.08, 0.005)
    tg = st.sidebar.slider("永续基地 (TG)", 0.01, 0.05, 0.03, 0.005)

    growth_list = [g1, g2, g_rest, g_rest, g_rest]
    
    # 动态计算核心目标价
    target_price = run_dcf(data['hist_rev'][-1], growth_list, margin, wacc, tg, data['net_debt'], data['shares'])
    upside = (target_price / data['price']) - 1 if data['price'] > 0 else 0

    # ---------------- 顶部看板 ----------------
    st.title(f"📊 {target_code} 深度 DCF 估值看板")
    st.markdown("---")
    
    col1, col2, col3, col4 = st.columns(4)
    # 彻底去除 ¥ 符号，改为“元”和“亿元”，根治“日元”翻译 Bug
    col1.metric("当前股价", f"{data['price']:.2f} 元")
    col2.metric("隐含目标价", f"{target_price:.2f} 元")
    
    upside_color = "🟢" if upside > 0 else "🔴"
    col3.markdown(f"**距现价空间**<br><span style='font-size:24px'>{upside_color} {upside:.2%}</span>", unsafe_allow_html=True)
    col4.metric("真实净负债", f"{data['net_debt']:.2f} 亿元")

    # ---------------- 敏感性矩阵 ----------------
    st.markdown("### 🛡️ 左侧博弈：WACC vs 永续成长矩阵")
    
    w_list = np.linspace(max(0.05, wacc-0.02), wacc+0.02, 5)
    t_list = np.linspace(max(0.01, tg-0.01), tg+0.01, 5)
    
    matrix = []
    text_matrix = []
    for w in w_list:
        row = []
        text_row = []
        for t in t_list:
            p = run_dcf(data['hist_rev'][-1], growth_list, margin, w, t, data['net_debt'], data['shares'])
            row.append(p)
            text_row.append(f"{p:.2f} 元")
        matrix.append(row)
        text_matrix.append(text_row)

    fig = go.Figure(data=go.Heatmap(
        z=matrix,
        x=[f"TG {t:.1%}" for t in t_list],
        y=[f"WACC {w:.1%}" for w in w_list],
        colorscale='RdYlGn',
        text=text_matrix,
        texttemplate="%{text}",
        showscale=False
    ))
    fig.update_layout(height=450, margin=dict(l=10, r=10, t=30, b=10), template="plotly_dark")
    st.plotly_chart(fig, use_container_width=True)
    
    st.info("💡 解读：绿色区域该参数组合下股价代表被低估，红色代表高估。对于左侧极值博弈，应重点关注深绿色价格区间作为终极防守底线。")

else:
    st.info("👈 请在左侧侧边栏点击【同步 Tushare 真实数据】启动投研引擎。")
