import streamlit as st
import asyncio
import time
import io
import json
from datetime import datetime
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from llm_benchmark import run_benchmark

# Set page configuration
st.set_page_config(
    page_title="LLM Benchmark Visual Interface",
    page_icon="🚀",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom CSS for better aesthetics
st.markdown("""
<style>
    .reportview-container {
        margin-top: -2em;
    }
    .stButton>button {
        width: 100%;
        border-radius: 8px;
        height: 3em;
        font-weight: bold;
        background-color: #4CAF50;
        color: white;
    }
    .stButton>button:hover {
        background-color: #45a049;
    }
    .metric-card {
        background-color: #f0f2f6;
        padding: 1rem;
        border-radius: 8px;
        box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
    }
    [data-testid="stMetricValue"] {
        font-size: 24px;
        font-weight: bold;
    }
</style>
""", unsafe_allow_html=True)

st.title("🚀 LLM 性能与高并发压力测试看板")
st.markdown("一个用于测试、评估与分析大语言模型服务吞吐量、并发性能及响应延迟的可视化操作平台。")

def main():
    # Sidebar Configuration
    st.sidebar.header("⚙️ 参数配置")
    
    with st.sidebar.expander("🔑 API 接口设置", expanded=True):
        llm_url = st.text_input(
            "API 接口地址 (Base URL)", 
            value="http://localhost:8000/v1",
            help="兼容 OpenAI 格式的大模型服务 API 地址，例如：http://localhost:8000/v1"
        )
        api_key = st.text_input(
            "API 密钥 (API Key)", 
            value="default", 
            type="password",
            help="访问 API 所需的认证密钥。如果没有设置，则保持默认的 'default' 即可"
        )
        model = st.text_input(
            "模型名称 (Model)", 
            value="deepseek-r1",
            help="要调用的具体模型名称，必须与推理服务端注册的模型名称完全一致"
        )
        
    templates = {
        "自定义 (Custom)": {"req": 50, "conc": 10, "tokens": 100, "timeout": 60, "long": False},
        "🤖 自动化多级梯度测试 (Auto-Gradient)": None,
        "🟢 快速体验 (低负载/短文本)": {"req": 20, "conc": 2, "tokens": 50, "timeout": 30, "long": False},
        "🟡 中等负载 (常规并发)": {"req": 100, "conc": 30, "tokens": 150, "timeout": 60, "long": False},
        "🔴 极限压测 (高并发)": {"req": 500, "conc": 150, "tokens": 100, "timeout": 120, "long": False},
        "📚 长文本推理测试": {"req": 50, "conc": 10, "tokens": 250, "timeout": 120, "long": True},
    }
    
    preset_choice = st.sidebar.selectbox(
        "💡 快捷压测模板", 
        list(templates.keys()),
        help="选择预设的压测模板可以快速填充各项压测参数"
    )
    preset = templates[preset_choice]
    
    if preset is None:
        st.sidebar.info("💡 当前已选择【自动化多级梯度压测】。系统将自动运行多个并发档位 (1, 50, 100, 200, 300) 并生成压测性能曲线，您只需在下方选择是否使用长文本即可，无需填写单次压测参数。")
        use_long_context = st.sidebar.checkbox(
            "使用长文本上下文提示词", 
            value=False,
            help="勾选此项后，将使用长文章上下文进行梯度测试"
        )
        if st.sidebar.button("▶️ 开始多级梯度自动化压测", type="primary"):
            run_all_benchmarks_ui(llm_url, api_key, model, use_long_context)
    else:
        with st.sidebar.expander("📊 压测参数设置", expanded=True):
            num_requests = st.number_input(
                "总请求数 (Total Requests)", 
                min_value=1, 
                value=preset["req"], 
                step=10,
                help="本次压力测试总共要发送的请求个数"
            )
            concurrency = st.number_input(
                "并发数 (Concurrency)", 
                min_value=1, 
                max_value=1000, 
                value=preset["conc"], 
                step=1,
                help="最大并发连接数（控制同时处于活跃状态的协程/并发连接个数）"
            )
            output_tokens = st.number_input(
                "最大输出 Token 数", 
                min_value=1, 
                value=preset["tokens"], 
                step=50,
                help="限制大模型单次响应生成的最大 Token 数量（max_tokens）"
            )
            request_timeout = st.number_input(
                "请求超时时间 (秒)", 
                min_value=1, 
                value=preset["timeout"], 
                step=10,
                help="单个请求的最大超时等待时间。超时未完成的请求将计为失败"
            )
            use_long_context = st.checkbox(
                "使用长文本上下文提示词", 
                value=preset["long"],
                help="勾选此项后，系统将随机选用包含约 500~1000 字的长文章上下文作为提示词；如果不勾选，则默认使用简短提示词。"
            )
            
        if st.sidebar.button("▶️ 开始单次压力测试", type="primary"):
            run_benchmark_ui(num_requests, concurrency, request_timeout, output_tokens, llm_url, api_key, model, use_long_context)

def run_all_benchmarks_ui(llm_url, api_key, model, use_long_context):
    st.markdown("---")
    st.subheader("🤖 正在执行多阶段自动化梯度压测...")
    
    stages = [
        {"num_requests": 10, "concurrency": 1, "output_tokens": 100},
        {"num_requests": 100, "concurrency": 50, "output_tokens": 100},
        {"num_requests": 200, "concurrency": 100, "output_tokens": 100},
        {"num_requests": 400, "concurrency": 200, "output_tokens": 100},
        {"num_requests": 600, "concurrency": 300, "output_tokens": 100},
    ]
    
    overall_progress = st.progress(0)
    stage_text = st.empty()
    sub_progress = st.progress(0)
    sub_text = st.empty()
    
    results_list = []
    
    try:
        for idx, config in enumerate(stages):
            c_req = config["num_requests"]
            c_conc = config["concurrency"]
            c_tok = config["output_tokens"]
            
            stage_text.markdown(f"**🚀 第 {idx+1}/{len(stages)} 阶段**: 并发数 **{c_conc}**，请求总数 **{c_req}**")
            
            def progress_callback(completed, total):
                progress_val = min(completed / total, 1.0)
                sub_progress.progress(progress_val)
                sub_text.text(f"⏳ 当前并发档位进度: {completed} / {total} ({(progress_val * 100):.1f}%)")
            
            with st.spinner(f"正在全速测试并发档位 {c_conc} ..."):
                res = asyncio.run(run_benchmark(
                    num_requests=c_req,
                    concurrency=c_conc,
                    request_timeout=60,
                    output_tokens=c_tok,
                    llm_url=llm_url,
                    api_key=api_key,
                    model=model,
                    use_long_context=use_long_context,
                    progress_callback=progress_callback
                ))
            
            results_list.append(res)
            overall_progress.progress((idx + 1) / len(stages))
            
            if idx < len(stages) - 1:
                sub_text.text("☕ 正在等待服务端资源降温与连接释放 (5秒)...")
                time.sleep(5)
                
        sub_progress.empty()
        sub_text.empty()
        stage_text.success("✅ 所有并发梯队压力测试完毕！为您生成以下梯度分析报告：")
        
        display_aggregated_results(results_list)
        
    except Exception as e:
        st.error(f"❌ 自动化梯度压测执行过程中发生错误: {str(e)}")

def display_aggregated_results(results_list):
    st.markdown("---")
    st.subheader("📊 自动化梯度压测性能报告")
    
    data = []
    for r in results_list:
        data.append({
            "并发数 (Concurrency)": r['concurrency'],
            "成功率 (%)": (r['successful_requests'] / r['total_requests']) * 100 if r['total_requests'] > 0 else 0,
            "RPS (吞吐量)": r['requests_per_second'],
            "TPS (Token速率)": r['tokens_per_second']['average'],
            "平均延迟 (秒)": r['latency']['average'],
            "P99 延迟 (秒)": r['latency']['p99'],
            "首字延迟 (秒)": r['time_to_first_token']['average']
        })
        
    df = pd.DataFrame(data)
    
    st.markdown("### 📋 多并发梯度数据表")
    st.dataframe(df.style.format({
        "成功率 (%)": "{:.1f}%",
        "RPS (吞吐量)": "{:.2f}",
        "TPS (Token速率)": "{:.2f}",
        "平均延迟 (秒)": "{:.3f}",
        "P99 延迟 (秒)": "{:.3f}",
        "首字延迟 (秒)": "{:.3f}",
    }).background_gradient(subset=["RPS (吞吐量)"], cmap="Greens")
      .background_gradient(subset=["平均延迟 (秒)"], cmap="Reds"), use_container_width=True)
      
    st.markdown("### 📈 性能趋势曲线分析")
    col1, col2 = st.columns(2)
    with col1:
        fig_rps = px.line(df, x="并发数 (Concurrency)", y="RPS (吞吐量)", markers=True, 
                          title="吞吐量 (RPS) 随并发变化趋势（越高越好）")
        fig_rps.update_traces(line_color='#2ca02c')
        st.plotly_chart(fig_rps, use_container_width=True)
    with col2:
        fig_lat = px.line(df, x="并发数 (Concurrency)", y="平均延迟 (秒)", markers=True, 
                          title="平均响应延迟随并发变化趋势（越低越好）")
        fig_lat.update_traces(line_color='#d62728')
        st.plotly_chart(fig_lat, use_container_width=True)

    # Export section for gradient results
    st.markdown("---")
    render_export_section(df, prefix="gradient_benchmark")

def run_benchmark_ui(num_requests, concurrency, request_timeout, output_tokens, llm_url, api_key, model, use_long_context):
    st.markdown("---")
    st.subheader("🏃‍♂️ 压测进行中...")
    
    progress_bar = st.progress(0)
    status_text = st.empty()
    status_text.text("🔌 正在连接大模型服务器并初始化请求线程队列...")
    
    # Define a progress callback for real-time updates
    def progress_callback(completed, total):
        progress_val = min(completed / total, 1.0)
        progress_bar.progress(progress_val)
        status_text.text(f"⏳ 压测进度: 已完成 {completed} / {total} 个请求 ({(progress_val * 100):.1f}%)")

    try:
        # Run the async benchmark function
        with st.spinner("🚀 压力测试疯狂跑数据中，请稍候..."):
            results = asyncio.run(run_benchmark(
                num_requests=num_requests,
                concurrency=concurrency,
                request_timeout=request_timeout,
                output_tokens=output_tokens,
                llm_url=llm_url,
                api_key=api_key,
                model=model,
                use_long_context=use_long_context,
                progress_callback=progress_callback
            ))
            
        progress_bar.progress(1.0)
        status_text.text("✅ 压力测试全部完成！正在为您生成精美分析报告...")
        
        display_results(results)
        
    except Exception as e:
        st.error(f"❌ 压测执行过程中发生错误: {str(e)}")

def build_single_export_df(results):
    """把单次压测结果展平为 DataFrame"""
    rows = [
        {"指标分类": "基本信息", "指标名称": "模型", "值": results.get('model', '-')},
        {"指标分类": "基本信息", "指标名称": "并发数", "值": results['concurrency']},
        {"指标分类": "基本信息", "指标名称": "总请求数", "值": results['total_requests']},
        {"指标分类": "基本信息", "指标名称": "成功请求数", "值": results['successful_requests']},
        {"指标分类": "基本信息", "指标名称": "成功率 (%)", "值": round(results['successful_requests'] / results['total_requests'] * 100, 2)},
        {"指标分类": "基本信息", "指标名称": "总耗时 (秒)", "值": round(results['total_time'], 3)},
        {"指标分类": "基本信息", "指标名称": "累计输出 Token", "值": results['total_output_tokens']},
        {"指标分类": "吞吐性能", "指标名称": "RPS (每秒请求数)", "值": round(results['requests_per_second'], 4)},
        {"指标分类": "吞吐性能", "指标名称": "TPS 平均 (Token/秒)", "值": round(results['tokens_per_second']['average'], 4)},
        {"指标分类": "吞吐性能", "指标名称": "TPS P50", "值": round(results['tokens_per_second']['p50'], 4)},
        {"指标分类": "吞吐性能", "指标名称": "TPS P95", "值": round(results['tokens_per_second']['p95'], 4)},
        {"指标分类": "吞吐性能", "指标名称": "TPS P99", "值": round(results['tokens_per_second']['p99'], 4)},
        {"指标分类": "响应延迟", "指标名称": "平均延迟 (秒)", "值": round(results['latency']['average'], 4)},
        {"指标分类": "响应延迟", "指标名称": "延迟 P50 (秒)", "值": round(results['latency']['p50'], 4)},
        {"指标分类": "响应延迟", "指标名称": "延迟 P95 (秒)", "值": round(results['latency']['p95'], 4)},
        {"指标分类": "响应延迟", "指标名称": "延迟 P99 (秒)", "值": round(results['latency']['p99'], 4)},
        {"指标分类": "首字延迟 (TTFT)", "指标名称": "TTFT 平均 (秒)", "值": round(results['time_to_first_token']['average'], 4)},
        {"指标分类": "首字延迟 (TTFT)", "指标名称": "TTFT P50 (秒)", "值": round(results['time_to_first_token']['p50'], 4)},
        {"指标分类": "首字延迟 (TTFT)", "指标名称": "TTFT P95 (秒)", "值": round(results['time_to_first_token']['p95'], 4)},
        {"指标分类": "首字延迟 (TTFT)", "指标名称": "TTFT P99 (秒)", "值": round(results['time_to_first_token']['p99'], 4)},
    ]
    return pd.DataFrame(rows)

def build_export_bytes(df, fmt):
    """返回 (bytes, mime, suffix)"""
    if fmt == "CSV":
        buf = io.BytesIO()
        df.to_csv(buf, index=False, encoding="utf-8-sig")  # utf-8-sig 让 Excel 正确显示中文
        return buf.getvalue(), "text/csv", "csv"
    else:  # Excel
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="压测结果")
        return buf.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx"

def render_export_section(df, prefix="benchmark"):
    """渲染导出区域（格式选择 + 下载按钮），可复用于单次和梯度压测"""
    st.markdown("### 📥 导出测试结果")
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    col_fmt, col_btn = st.columns([1, 3])
    with col_fmt:
        fmt = st.selectbox("导出格式", ["Excel (.xlsx)", "CSV (.csv)"], key=f"export_fmt_{prefix}_{ts}")
    fmt_key = "Excel" if "Excel" in fmt else "CSV"
    file_bytes, mime, suffix = build_export_bytes(df, fmt_key)
    filename = f"{prefix}_{ts}.{suffix}"
    with col_btn:
        st.markdown("<div style='margin-top:28px'>", unsafe_allow_html=True)
        st.download_button(
            label=f"⬇️ 下载 {fmt}",
            data=file_bytes,
            file_name=filename,
            mime=mime,
            use_container_width=True,
            key=f"dl_{prefix}_{ts}"
        )
        st.markdown("</div>", unsafe_allow_html=True)

def display_results(results):
    st.markdown("---")
    st.subheader("📊 压力测试结果汇总")
    
    # 1. Overview Metrics
    st.markdown("### 🎯 核心数据概览")
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric(label="总请求发送数", value=results['total_requests'])
    with col2:
        st.metric(label="成功请求数", value=results['successful_requests'], 
                  delta=f"{(results['successful_requests']/results['total_requests']*100):.1f}% 成功率", delta_color="normal" if results['successful_requests'] == results['total_requests'] else "off")
    with col3:
        st.metric(label="总共耗时 (秒)", value=f"{results['total_time']:.2f}")
    with col4:
        st.metric(label="累计输出 Token 总数", value=f"{results['total_output_tokens']:,}")
        
    st.markdown("<br>", unsafe_allow_html=True)
    
    # 2. Performance Metrics
    st.markdown("### ⚡ 吞吐与并发性能")
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric(label="每秒请求数 (RPS / QPS)", value=f"{results['requests_per_second']:.2f}")
    with col2:
        st.metric(label="每秒平均生成 Token 数 (TPS)", value=f"{results['tokens_per_second']['average']:.2f}")
    with col3:
        st.metric(label="压测并发度 (Concurrency)", value=results['concurrency'])

    st.markdown("<br>", unsafe_allow_html=True)

    # 3. Percentiles Tables & Charts
    st.markdown("### 📈 细粒度分位数统计指标")
    
    # Prepare DataFrames
    latency_data = {
        "指标分位数": ["平均值 (Average)", "中位数 (P50)", "高位延迟 (P95)", "尾部延迟 (P99)"],
        "响应延迟 (秒)": [
            results['latency']['average'],
            results['latency']['p50'],
            results['latency']['p95'],
            results['latency']['p99']
        ]
    }
    
    ttft_data = {
        "指标分位数": ["平均值 (Average)", "中位数 (P50)", "高位延迟 (P95)", "尾部延迟 (P99)"],
        "首字延迟 (TTFT/秒)": [
            results['time_to_first_token']['average'],
            results['time_to_first_token']['p50'],
            results['time_to_first_token']['p95'],
            results['time_to_first_token']['p99']
        ]
    }
    
    tps_data = {
        "指标分位数": ["平均值 (Average)", "中位数 (P50)", "高位延迟 (P95)", "尾部延迟 (P99)"],
        "Token生成速率 (Tokens/秒)": [
            results['tokens_per_second']['average'],
            results['tokens_per_second']['p50'],
            results['tokens_per_second']['p95'],
            results['tokens_per_second']['p99']
        ]
    }

    df_latency = pd.DataFrame(latency_data).set_index("指标分位数")
    df_ttft = pd.DataFrame(ttft_data).set_index("指标分位数")
    df_tps = pd.DataFrame(tps_data).set_index("指标分位数")

    col1, col2, col3 = st.columns(3)
    
    with col1:
        st.markdown("**整体响应延迟 (秒)**")
        st.dataframe(df_latency.style.format("{:.3f}").background_gradient(cmap='Blues'), use_container_width=True)
    
    with col2:
        st.markdown("**首Token延迟 (TTFT / 秒)**")
        st.dataframe(df_ttft.style.format("{:.3f}").background_gradient(cmap='Greens'), use_container_width=True)
        
    with col3:
        st.markdown("**Token生成吞吐速度 (TPS / 秒)**")
        st.dataframe(df_tps.style.format("{:.2f}").background_gradient(cmap='Oranges'), use_container_width=True)

    # 4. Charts
    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown("### 📊 直观分布图表")
    
    col1, col2 = st.columns(2)
    
    with col1:
        # Latency bar chart
        fig_latency = px.bar(
            x=["平均值", "中位数 (P50)", "高位延迟 (P95)", "尾部延迟 (P99)"], 
            y=[results['latency']['average'], results['latency']['p50'], results['latency']['p95'], results['latency']['p99']],
            labels={'x': '分位数指标', 'y': '响应延迟 (秒)'},
            title="响应延迟分布情况 (秒)",
            color_discrete_sequence=['#1f77b4']
        )
        st.plotly_chart(fig_latency, use_container_width=True)

    with col2:
        # TPS bar chart
        fig_tps = px.bar(
            x=["平均值", "中位数 (P50)", "高位延迟 (P95)", "尾部延迟 (P99)"], 
            y=[results['tokens_per_second']['average'], results['tokens_per_second']['p50'], results['tokens_per_second']['p95'], results['tokens_per_second']['p99']],
            labels={'x': '分位数指标', 'y': 'Token 生成速度 (Tokens/秒)'},
            title="每秒 Token 生成速度分布 (TPS)",
            color_discrete_sequence=['#ff7f0e']
        )
        st.plotly_chart(fig_tps, use_container_width=True)

    # Raw JSON expander
    st.markdown("<br>", unsafe_allow_html=True)
    with st.expander("📂 显示原始压测结果 JSON 数据"):
        st.json(results)

    # 5. Export section
    st.markdown("---")
    export_df = build_single_export_df(results)
    render_export_section(export_df, prefix="single_benchmark")

if __name__ == "__main__":
    main()
