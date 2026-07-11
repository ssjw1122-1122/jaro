import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from sklearn.linear_model import LinearRegression
import database
import datetime
import os

# 페이지 설정
st.set_page_config(page_title="체중 관리 대시보드", page_icon="📉", layout="wide")

def check_password(key_suffix=""):
    # 윈도우 환경(로컬)에서는 비밀번호 확인을 생략합니다.
    if os.name == 'nt':
        return True

    if st.session_state.get("password_correct", False):
        return True

    st.markdown("### 🔒 보안 접근")
    
    def password_entered():
        if st.session_state.get(f"pwd_input_{key_suffix}", "") == "1122":
            st.session_state["password_correct"] = True
        else:
            st.session_state["password_correct"] = False

    st.text_input("비밀번호를 입력하세요 (엔터)", type="password", key=f"pwd_input_{key_suffix}", on_change=password_entered)
    
    if "password_correct" in st.session_state and not st.session_state["password_correct"]:
        st.error("비밀번호가 일치하지 않습니다.")
        
    return False


# 상단 툴바(Deploy 등) 숨기기 및 상단 빈 공간(padding) 제거
st.markdown("""
    <style>
        .stAppHeader {display:none;}
        header {visibility: hidden;}
        .block-container {padding-top: 1rem !important; padding-bottom: 5rem !important;}
        /* 라디오버튼과 그래프 사이 여백 축소 */
        .stPlotlyChart {margin-top: -6rem;}
        .stExpander {margin-top: -7rem;}
        .stExpander [data-testid="stExpanderDetails"] {padding-top: 0 !important; padding-bottom: 2rem !important;}
        .stExpander .stPlotlyChart {margin-top: 0; margin-bottom: -2rem; padding: 0 1rem;}
        div[data-testid="stVerticalBlock"] div[class*="st-key-pace_chart"] .stPlotlyChart {margin-top: 0;}
    </style>
""", unsafe_allow_html=True)

# DB 초기화 (없으면 생성)
database.init_db()

st.subheader("📉 체중 관리 대시보드")

# 탭 구성
tab1, tab2, tab3, tab4 = st.tabs(["📊 대시보드", "🎯 4주 목표 관리", "✍️ 데이터 입력", "📝 부작용 기록"])

def render_dashboard():
    df = database.get_interpolated_data()
    
    if df.empty:
        st.info("데이터가 없습니다. '데이터 입력 & 기록' 탭에서 체중 데이터를 추가해주세요.")
        return
        
    df['date'] = pd.to_datetime(df['date'])
    df = df.sort_values('date').reset_index(drop=True)
    
    latest_record = df.iloc[-1]
    latest_date = latest_record['date']
    current_weight = latest_record['weight']
    
    start_record = df.iloc[0]
    start_weight = start_record['weight']
    
    # --- C. 감량 속도 분석 (Pace Analysis) ---
    st.markdown("#### 💡 감량 속도 분석")
    
    # 기준일 설정
    date_1w_ago = latest_date - pd.Timedelta(days=7)
    date_2w_ago = latest_date - pd.Timedelta(days=14)
    date_1m_ago = latest_date - pd.Timedelta(days=30)
    
    # 1주전, 1달전 데이터 찾기 (보간되었으므로 날짜 매칭 가능)
    weight_1w_ago = df[df['date'] <= date_1w_ago]['weight'].iloc[-1] if not df[df['date'] <= date_1w_ago].empty else start_weight
    weight_2w_ago = df[df['date'] <= date_2w_ago]['weight'].iloc[-1] if not df[df['date'] <= date_2w_ago].empty else start_weight
    weight_1m_ago = df[df['date'] <= date_1m_ago]['weight'].iloc[-1] if not df[df['date'] <= date_1m_ago].empty else start_weight
    
    loss_total = current_weight - start_weight
    loss_1w = current_weight - weight_1w_ago
    loss_1m = current_weight - weight_1m_ago
    loss_prev_1w = weight_1w_ago - weight_2w_ago
    
    col1, col2, col3 = st.columns(3)
    
    with col1:
        st.metric(label="시작 체중 / 현재 체중", 
                  value=f"{start_weight:.1f} / {current_weight:.1f} kg", 
                  delta=f"{loss_total:.1f} kg",
                  delta_color="inverse")
                  
    with col2:
        st.metric(label="최근 1개월", 
                  value=f"{weight_1m_ago:.1f} → {current_weight:.1f} kg", 
                  delta=f"{loss_1m:.1f} kg",
                  delta_color="inverse")
                  
    with col3:
        st.metric(label="최근 1주일", 
                  value=f"{weight_1w_ago:.1f} → {current_weight:.1f} kg", 
                  delta=f"{loss_1w:.1f} kg",
                  delta_color="inverse")

    # 페이스 피드백
    st.markdown("##### 👩‍⚕️ 목표 페이스 피드백")
    if loss_1w > 0:
        st.warning("최근 1주일 동안 체중이 증가했습니다. 식단이나 운동량을 점검해보세요.")
    elif loss_prev_1w < 0 and loss_1w < 0:
        ratio = loss_1w / loss_prev_1w
        if ratio < 0.5:
            st.info(f"이번 주는 지난 주보다 감량 속도가 {(1-ratio)*100:.0f}% 느립니다. 정체기일 수 있습니다. 포기하지 마세요!")
        elif ratio > 1.5:
            st.success("지난 주보다 감량 속도가 빠릅니다! 훌륭한 페이스입니다.")
        else:
            st.success("안정적인 감량 페이스를 유지하고 있습니다.")
    elif loss_1w < 0:
        st.success("순조롭게 감량 중입니다.")
    else:
        st.info("체중에 변화가 없습니다.")

    st.divider()

    # --- B. 대시보드 & 시각화 ---
    st.markdown("#### 📈 체중 변화 추이 및 예측")
    
    view_type = st.radio("그래프 보기 기준", ["전체 기간 (All)", "최근 30일 (Daily)", "최근 10주 (Weekly)"], horizontal=True, label_visibility="collapsed")
    
    plot_df = df.copy()
    plot_df = plot_df.set_index('date')
    
    if view_type == "최근 10주 (Weekly)":
        # W-MON means weekly starting on Monday
        plot_df = plot_df.resample('W-MON').mean(numeric_only=True).reset_index()
        plot_df = plot_df.tail(10)
        plot_df['date_label'] = plot_df['date'].dt.strftime('%Y-%m-%d') + " 주차"
    elif view_type == "최근 30일 (Daily)":
        plot_df = plot_df.reset_index()
        plot_df = plot_df.tail(30)
        plot_df['date_label'] = plot_df['date'].dt.strftime('%Y-%m-%d')
    else:
        plot_df = plot_df.reset_index()
        plot_df['date_label'] = plot_df['date'].dt.strftime('%Y-%m-%d')
        
    plot_df = plot_df.dropna(subset=['weight'])
    
    fig = go.Figure()
    
    # 실제 체중 데이터
    fig.add_trace(go.Scatter(
        x=plot_df['date'], y=plot_df['weight'],
        mode='lines+markers',
        name='실제 체중',
        line=dict(color='royalblue', width=2),
        marker=dict(size=6),
        text=plot_df['date_label'],
        hovertemplate='<b>%{text}</b><br>체중: %{y:.1f}kg<extra></extra>'
    ))
    
    # 미래 추세선 (전체 기간일 때만 표시, 혹은 최근 30일도 표시)
    if view_type != "최근 10주 (Weekly)":
        recent_df = df.tail(30).copy()
        if len(recent_df) > 5:
            recent_df['days_since'] = (recent_df['date'] - recent_df['date'].min()).dt.days
            
            X = recent_df['days_since'].values.reshape(-1, 1)
            y = recent_df['weight'].values
            
            model = LinearRegression()
            model.fit(X, y)
            
            future_days = 30
            if view_type == "전체 기간 (All)":
                slope = model.coef_[0]
                intercept = model.intercept_
                if slope < -0.001:
                    target_day = (80.0 - intercept) / slope
                    last_day_val = recent_df['days_since'].max()
                    days_to_target = int(target_day - last_day_val)
                    if 0 < days_to_target < 1000:
                        future_days = days_to_target
                        
            last_day_val = recent_df['days_since'].max()
            future_X = np.arange(last_day_val + 1, last_day_val + future_days + 1).reshape(-1, 1)
            future_y = model.predict(future_X)
            
            future_dates = [recent_df['date'].max() + pd.Timedelta(days=int(i)) for i in range(1, future_days + 1)]
            
            fig.add_trace(go.Scatter(
                x=future_dates, y=future_y,
                mode='lines',
                name='예상 추세 (최근 30일 회귀)',
                line=dict(color='firebrick', width=2, dash='dash'),
                hovertemplate='예상 체중: %{y:.1f}kg<extra></extra>'
            ))
            
    # 목표선 (가로선) 그리기
    fig.add_hline(y=95, line_dash="dash", line_color="#2ca02c", annotation_text="목표 95kg", annotation_position="bottom right")
    fig.add_hline(y=85, line_dash="dash", line_color="#ff7f0e", annotation_text="목표 85kg", annotation_position="bottom right")
    fig.add_hline(y=80, line_dash="dash", line_color="#d62728", annotation_text="최종 목표 80kg", annotation_position="bottom right")
            
    fig.update_layout(
        yaxis_title='체중 (kg)',
        hovermode='x unified',
        template='plotly_white',
        legend=dict(yanchor="top", y=0.99, xanchor="right", x=0.99),
        xaxis=dict(fixedrange=True)
    )
    
    if view_type == "최근 30일 (Daily)" or view_type == "최근 10주 (Weekly)":
        min_w = plot_df['weight'].min()
        max_w = plot_df['weight'].max()
        y_min = np.floor(min_w / 5.0) * 5
        y_max = np.ceil(max_w / 5.0) * 5
        if y_min == y_max:
            y_min -= 5
            y_max += 5
        fig.update_yaxes(range=[y_min, y_max], fixedrange=True)
    else:
        fig.update_yaxes(range=[80, 115], fixedrange=True)
        
    st.plotly_chart(fig, width='stretch', config={'displayModeBar': False})
    
    with st.expander("📊 기타 체성분 수치 보기"):
        metrics_cols = ['body_fat_percent', 'skeletal_muscle_percent', 'bmi']
        labels = ['체지방률 (%)', '골격근률 (%)', 'BMI']
        
        chart_idx = 0
        for col, label in zip(metrics_cols, labels):
            if df[col].notnull().any():
                if chart_idx > 0:
                    st.divider()
                chart_idx += 1
                fig_sub = go.Figure()
                
                # 표준 범위 녹색 영역 (hrect)
                if col == 'body_fat_percent':
                    fig_sub.add_hrect(y0=15, y1=20, line_width=0, fillcolor="green", opacity=0.1, annotation_text="표준 권장 (15~20%)", annotation_position="top left")
                elif col == 'skeletal_muscle_percent':
                    fig_sub.add_hrect(y0=33, y1=39, line_width=0, fillcolor="green", opacity=0.1, annotation_text="표준 권장 (33~39%)", annotation_position="top left")
                elif col == 'bmi':
                    fig_sub.add_hrect(y0=18.5, y1=22.9, line_width=0, fillcolor="green", opacity=0.1, annotation_text="정상 범위 (18.5~22.9)", annotation_position="top left")
                    
                fig_sub.add_trace(go.Scatter(
                    x=plot_df['date'], y=plot_df[col],
                    mode='lines+markers',
                    name=label,
                    line=dict(color='seagreen', width=2),
                    hovertemplate=f'{label}: %{{y:.1f}}<extra></extra>'
                ))
                fig_sub.update_layout(
                    yaxis_title=label,
                    hovermode='x unified',
                    template='plotly_white',
                    height=345,
                    margin=dict(t=25, b=10),
                    xaxis=dict(fixedrange=True),
                    yaxis=dict(fixedrange=True)
                )
                st.plotly_chart(fig_sub, width='stretch', config={'displayModeBar': False})

def render_target_management():
    df = database.get_interpolated_data()
    if df.empty:
        st.info("데이터가 없습니다.")
        return
        
    df['date'] = pd.to_datetime(df['date'])
    df = df.sort_values('date').reset_index(drop=True)
    
    st.subheader("🎯 4주 감량 목표 관리")
    
    # 목표는 고정 2kg
    target_loss = 2.0
        
    # 투여 시작 기준일 (2025-12-20 토요일: 2026-07-04와 4주 단위로 정확히 맞아떨어짐)
    base_date = pd.to_datetime('2025-12-20')
    
    # 28일 간격으로 기수(Phase/Box) 나누기
    df['days_from_base'] = (df['date'] - base_date).dt.days
    df = df[df['days_from_base'] >= 0].copy()
    
    if df.empty:
        st.info("2025-12-20 이후의 데이터가 필요합니다.")
        return
        
    df['phase'] = (df['days_from_base'] // 28) + 1
    
    current_phase = int(df['phase'].iloc[-1])
    current_phase_df = df[df['phase'] == current_phase]
    
    if current_phase_df.empty:
        return
        
    # 각 기수의 시작일은 그 기수의 0일차
    phase_start_date_exact = base_date + pd.Timedelta(days=28*(current_phase-1))
    
    phase_start_weight = current_phase_df['weight'].iloc[0]
    current_weight = current_phase_df['weight'].iloc[-1]
    phase_target_weight = phase_start_weight - target_loss
    
    days_in_phase = current_phase_df['days_from_base'].iloc[-1] % 28
    current_week = (days_in_phase // 7) + 1
    days_left = 28 - days_in_phase - 1 # 오늘 포함
    
    current_loss = phase_start_weight - current_weight
    remaining_loss = current_weight - phase_target_weight
    
    # 최근 4주(28일) 롤링 감량폭 계산
    latest_date = df['date'].iloc[-1]
    date_28d_ago = latest_date - pd.Timedelta(days=28)
    if not df[df['date'] <= date_28d_ago].empty:
        weight_28d_ago = df[df['date'] <= date_28d_ago]['weight'].iloc[-1]
    else:
        weight_28d_ago = df['weight'].iloc[0]
    rolling_28d_loss = weight_28d_ago - current_weight
    
    st.markdown(f"#### ✨ 현재 진행 중: {current_phase}기")
    st.markdown(f"**{current_phase}기 시작일**: {phase_start_date_exact.strftime('%Y-%m-%d')} (토요일)  &nbsp;|&nbsp;&nbsp; **현재 주차**: {current_week}주차 (남은 기간: {days_left}일)  &nbsp;|&nbsp;&nbsp; **최근 4주 롤링 감량폭**: {rolling_28d_loss:.1f}kg", unsafe_allow_html=True)
    
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("기수 시작 체중", f"{phase_start_weight:.1f} kg")
    with col2:
        st.metric("현재 체중", f"{current_weight:.1f} kg")
    with col3:
        st.metric("목표 체중", f"{phase_target_weight:.1f} kg")
    with col4:
        st.metric(f"기수 누적 감량폭", f"{-current_loss:.1f} kg" if current_loss > 0 else f"+{-current_loss:.1f} kg")
        
    # 프로그레스 바 (실제 달성률 + 목표 페이스 수직선)
    progress = max(0.0, min(1.0, current_loss / target_loss))
    target_progress = max(0.0, min(1.0, (days_in_phase + 1) / 28))
    
    fig_pace = go.Figure()
    
    fig_pace.add_trace(go.Bar(
        y=[''], x=[progress * 100],
        orientation='h',
        marker_color='royalblue',
        text=f'{progress*100:.1f}%', textposition='inside',
        textfont=dict(color='white', size=13, family='Arial Black'),
        hovertemplate='실제 달성: %{x:.1f}%<extra></extra>',
        showlegend=False,
        width=0.5
    ))
    
    # 목표 페이스 수직 점선
    fig_pace.add_vline(
        x=target_progress * 100,
        line_dash="dot", line_color="#2ca02c", line_width=2.5,
        annotation_text=f"목표 {target_progress*100:.0f}%",
        annotation_position="top",
        annotation_font=dict(color="#2ca02c", size=11)
    )
    
    fig_pace.update_layout(
        xaxis=dict(range=[0, 100], showticklabels=False, showgrid=False, zeroline=False, fixedrange=True),
        yaxis=dict(showticklabels=False, fixedrange=True),
        height=70, margin=dict(l=0, r=0, t=20, b=0),
        template='plotly_white',
        plot_bgcolor='rgba(0,0,0,0)'
    )
    
    with st.container(key="pace_chart"):
        st.plotly_chart(fig_pace, width='stretch', config={'displayModeBar': False})
    
    st.markdown("#### 🏃‍♂️ 액션 플랜")
    if remaining_loss <= 0:
        st.success("🎉 이번 기수의 목표 감량을 이미 달성했습니다! 현재 페이스를 유지하세요.")
    else:
        if days_left > 0:
            required_daily_loss = remaining_loss / days_left
            weekly_target_weight = phase_start_weight - (target_loss * current_week / 4)
            st.warning(f"목표 달성을 위해 남은 **{days_left}일** 동안 **{remaining_loss:.1f}kg**을 더 감량해야 합니다.")
            st.info(f"💡 하루 페이스로 환산하면, 매일 약 **{required_daily_loss:.2f}kg** 감량이 필요합니다.")
            st.info(f"💡 이번 주({current_week}주차)에는 **{weekly_target_weight:.1f}kg**까지 빼야 합니다.")
        else:
            st.error(f"이번 기수가 오늘로 종료됩니다. 최종 목표까지 {remaining_loss:.1f}kg 남았습니다.")
            
    st.divider()
    
    # 과거 성적표
    st.markdown("#### 📚 과거 기수별 달성 성적표")
    history = []
    for p in range(1, current_phase):
        pdf = df[df['phase'] == p]
        if not pdf.empty:
            p_start = pdf['weight'].iloc[0]
            p_end = pdf['weight'].iloc[-1]
            p_loss = p_start - p_end
            success = "✅ 성공" if p_loss >= target_loss else "❌ 미달성"
            history.append({
                "기수": f"{p}기",
                "시작 체중 (kg)": round(p_start, 1),
                "종료 체중 (kg)": round(p_end, 1),
                "감량폭 (kg)": round(p_loss, 1),
                "달성 여부": success
            })
            
    if history:
        total_start = history[0]["시작 체중 (kg)"]
        total_end = history[-1]["종료 체중 (kg)"]
        total_loss = total_start - total_end
        avg_loss = total_loss / len(history)
        
        history.append({
            "기수": "합계 (전체 기수)",
            "시작 체중 (kg)": round(total_start, 1),
            "종료 체중 (kg)": round(total_end, 1),
            "감량폭 (kg)": round(total_loss, 1),
            "달성 여부": f"(평균 4주당 {avg_loss:.1f}kg 감량)"
        })
        st.dataframe(pd.DataFrame(history), width='stretch')
    else:
        st.write("아직 종료된 과거 기수가 없습니다.")

def render_data_input():
    if not check_password("tab3"):
        return
        
    st.subheader("데이터 입력 및 관리")
    
    st.markdown("#### 1. CSV 파일 병합")
    
    uploaded_file = st.file_uploader("CSV 파일 업로드", type=['csv'])
    
    if uploaded_file is not None:
        if st.button("CSV 데이터 병합하기"):
            with st.spinner("데이터 처리 중..."):
                try:
                    database.process_and_upload_csv(uploaded_file)
                    st.success("CSV 데이터가 성공적으로 병합되었습니다!")
                    st.rerun()
                except Exception as e:
                    st.error(f"오류가 발생했습니다: {e}")
                    
    st.divider()
    
    st.markdown("#### 2. 누락 데이터 입력")
    
    missing_dates = database.get_missing_dates()
    
    if not missing_dates:
        st.success("✅ 누락된 날짜가 없습니다! 모든 데이터가 입력되어 있습니다.")
    else:
        st.info(f"📋 총 **{len(missing_dates)}개** 날짜의 체중 데이터가 누락되어 있습니다.")
        
        # 최신 날짜를 위에 보여주기 위해 역순 정렬
        missing_dates_desc = list(reversed(missing_dates))
        
        for date_str in missing_dates_desc:
            col_date, col_input, col_btn, col_skip = st.columns([3, 4, 2, 2], vertical_alignment="bottom")
            with col_date:
                st.markdown(f"**📅 {date_str}**")
            with col_input:
                st.number_input(
                    "체중(kg)", min_value=30.0, max_value=200.0, value=None,
                    step=0.1, key=f"w_{date_str}", label_visibility="collapsed",
                    placeholder="체중(kg)"
                )
            with col_btn:
                if st.button("저장", key=f"save_{date_str}", width='stretch'):
                    val = st.session_state.get(f"w_{date_str}")
                    if val is not None:
                        success, msg = database.upsert_manual_entry(date_str, val)
                        if success:
                            st.toast(f"{date_str} 저장 완료!")
                            st.rerun()
                        else:
                            st.warning(msg)
                    else:
                        st.toast("⚠️ 체중을 입력하세요.")
            with col_skip:
                if st.button("측정누락", key=f"skip_{date_str}", width='stretch'):
                    database.skip_date(date_str)
                    st.toast(f"{date_str} 측정누락 처리!")
                    st.rerun()
        
        st.divider()
        
        if st.button("💾 입력된 항목 일괄 저장", type="primary", width='stretch'):
            entries = []
            for date_str in missing_dates:
                val = st.session_state.get(f"w_{date_str}")
                if val is not None:
                    entries.append((date_str, val))
            
            if entries:
                saved, skipped = database.upsert_manual_entries(entries)
                if saved > 0:
                    st.success(f"✅ {saved}건 저장 완료!")
                if skipped > 0:
                    st.warning(f"⚠️ {skipped}건은 CSV 데이터가 존재하여 건너뛰었습니다.")
                if saved > 0:
                    st.rerun()
            else:
                st.warning("입력된 체중 데이터가 없습니다.")

def render_side_effect_record():
    if not check_password("tab4"):
        return
        
    col1, col2, col3 = st.columns([3, 7, 2], vertical_alignment="bottom")
    with col1:
        note_date = st.date_input("날짜", value=datetime.date.today(), label_visibility="collapsed")
    with col2:
        notes = st.text_input("내용", placeholder="", label_visibility="collapsed")
    with col3:
        if st.button("등록", type="primary", width='stretch'):
            if notes.strip():
                database.save_side_effect(note_date.strftime('%Y-%m-%d'), notes)
                st.rerun()
            else:
                st.warning("내용을 입력하세요.")
                
    st.markdown("<hr style='margin-top: 15px; margin-bottom: 15px;'/>", unsafe_allow_html=True)
    
    se_df = database.get_all_side_effects()
    if not se_df.empty:
        st.markdown("""
        <style>
        .record-line { 
            font-size: 15px; 
            margin: 0 !important; 
            padding: 0 !important; 
            line-height: 28px !important;
        }
        /* 각 X 버튼은 st-key-del_숫자 클래스를 가짐. 이것들만 타겟팅 */
        div[class*="st-key-del_"] button { 
            padding: 0 !important; 
            height: 22px !important; 
            min-height: 22px !important; 
            font-size: 9px !important;
            margin: 0 !important;
            line-height: 1 !important;
        }
        </style>
        """, unsafe_allow_html=True)
        
        for idx, row in se_df.iterrows():
            c1, c2 = st.columns([1, 15], vertical_alignment="center")
            if c1.button("❌", key=f"del_{row['id']}", width='stretch'):
                database.delete_side_effect(row['id'])
                st.rerun()
            c2.markdown(f"<span class='record-line'>🗓️ <b>{row['date']}</b> &nbsp;|&nbsp; {row['notes']}</span>", unsafe_allow_html=True)
    else:
        st.info("아직 기록된 내용이 없습니다.")
        
with tab1:
    render_dashboard()
    
with tab2:
    render_target_management()
    
with tab3:
    render_data_input()

with tab4:
    render_side_effect_record()

