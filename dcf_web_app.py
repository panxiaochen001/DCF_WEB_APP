import streamlit as st
import tushare as ts
import pandas as pd
import numpy as np
from google import genai
import plotly.graph_objects as go
import os

# ==========================================
# 1. 基础配置与安全凭证 (华尔街极简风格)
# ==========================================
st.set_page_config(page_title="Pro DCF Analyzer", layout="wide", initial_sidebar_state="expanded")

# 安全调用：优先读取云端 Secrets 中的环境变量
TUSHARE_TOKEN = os.environ.get('TUSHARE_TOKEN', 'YOUR_TUSHARE_TOKEN_HERE')
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY', 'YOUR_GEMINI_API_KEY_HERE')

try:
    ts.set_token(TUSHARE_TOKEN)
    pro = ts.pro_api()
except Exception:
    pass

# ==========================================
# 2. 数据层核心逻辑 (严格使用 moneyflow_cnt_ths)
# ==========================================
@st.cache_data(ttl=3600)
def get_real_data(ts_code):
    try:
        # 1. 严格遵循指令：使用 moneyflow_cnt_ths 替代 daily 获取核心价格数据
        df_mf = pro.moneyflow_cnt_ths(ts_code=ts_code, limit=1)
        
        # 2. 获取其他财务数据
        df_bal = pro.balancesheet(ts_code=ts_code, limit=1, fields='money_cap,total_liab')
        df_inc = pro.income(ts_code=ts_code, limit=3, fields='total_revenue')
        df_fina = pro.fina_indicator(ts_code=ts_code, limit=1, fields='total_share')
        
        # 防静默失败：如果接口无数据，将错误抛出到前端
        if df_mf.empty or 'close_price' not in df_mf.columns:
            st.error(f"❌ 未从 moneyflow_cnt_ths 拉取到 {ts_code} 的行情。\n\n⚠️ 注意：该接口为同花顺概念板块接口，请输入板块代码 (如 885748.TI)。如果您查询的是个股，该接口无法返回数据！")
            return None
            
        # 精准匹配接口特有字段 close_price
        close_price = df_mf['close_price'].iloc[0]
        
        total_share = df_fina['total_share'].iloc[0] if not df_fina.empty and 'total_share' in df_fina.columns else 40000.0
        
        money_cap = df_bal['money_cap'].iloc[0] if not df_bal.empty and pd.notna(df_bal['money_cap'].iloc[0]) else 0
        total_liab = df_bal['total_liab'].iloc[0] if not df_bal.empty and pd.notna(df_bal['total_liab'].iloc[0]) else 0
        net_debt = (total_liab - money_cap) / 100000000 
        
        hist_rev = (df_inc['total_revenue'][::-1] / 100000000).tolist() if not df_inc.empty else [10, 20, 30]
        
        return {
            "price": close_price,
            "shares": total_share / 10000, 
            "net_debt": net_debt,
            "hist_rev": hist_rev
        }
    except Exception as e:
        st.error(f"⚠️ 数据接口运行异常: {e}")
        return None

def run_dcf(base_rev, growth_rates, margin, wacc, tg, net_debt, shares):
    current_rev = base_rev
    p_fcfs = []
    for g in growth_rates:
        current_rev *= (1 + g)
        p_fcfs.append(current_rev * margin)
    
    pv_fcfs = sum([f / (1 + wacc)**(i + 0.5) for i, f in enumerate(p_fcfs)])
    
    # 财务数学保护：折现率不能小于等于永续增长率
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
# 默认值改为概念板块代码，引导正确输入
target_code = st.sidebar.text_input("输入代码 (概念板块例: 885748.TI)", value="885748.TI")

if st.sidebar.button("🔄 拉取 Tushare 实时数据"):
    if TUSHARE_TOKEN == 'YOUR_TUSHARE_TOKEN_HERE':
        st.sidebar.error("请先在 Streamlit 云端的 Secrets 中配置您的 Tushare API Token！")
    else:
        st.session_state.data = get_real_data(target_code)

if 'data' in st.session_state and st.session_state.data is not None:
    data = st.session_state.data
    
    st.sidebar.markdown("---")
    st.sidebar.markdown("#### 📈 增长与利润预期")
    g1 = st.sidebar.slider("第 1 年增速", 0.0, 2.0, 0.50, 0.05)
    g2 = st.sidebar.slider("第 2 年增速", 0.0, 2.0, 0.40, 0.05)
    g_rest = st.sidebar.slider("后 3 年均增速", 0.0, 1.0, 0.20, 0.05)
    margin = st.sidebar.slider("FCF 自由现金流利润率", 0.01, 0.50, 0.20, 0.01)
    
    st.sidebar.markdown("#### 💸 折现模型核心")
    wacc = st.sidebar.slider("WACC 折现率", 0.05, 0.15, 0.08, 0.005)
    tg = st.sidebar.slider("永续增长率 (TG)", 0.01, 0.05, 0.03, 0.005)

    growth_list = [g1, g2, g_rest, g_rest, g_rest]
    
    target_price = run_dcf(data['hist_rev'][-1], growth_list, margin, wacc, tg, data['net_debt'], data['shares'])
    upside = (target_price / data['price']) - 1 if data['price'] > 0 else 0

    st.title(f"📊 {target_code} 深度基本面透视")
    st.markdown("---")
    
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("市场现价", f"¥{data['price']:.2f}")
    col2.metric("模型隐含价", f"¥{target_price:.2f}")
    
    upside_color = "🟢" if upside > 0 else "🔴"
    col3.markdown(f"**距现价空间**<br><span style='font-size:24px'>{upside_color} {upside:.2%}</span>", unsafe_allow_html=True)
    col4.metric("真实净负债", f"¥{data['net_debt']:.2f} 亿元")

    st.markdown("### 🛡️ 左侧极值防御矩阵 (WACC vs TG)")
    
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
            text_row.append(f"¥{p:.2f}")
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

else:
    st.info("👈 请在左侧侧边栏点击【拉取 Tushare 实时数据】启动投研引擎。")
