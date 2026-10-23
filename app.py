import os
import io
import re
import json
import time
import hashlib
import base64
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import streamlit as st
import gspread
from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import AuthorizedSession, Request
from google import genai
from google.genai import types
import extra_streamlit_components as stx

# ==========================================
# 1. 페이지 기본 설정 및 사용자 수정 가능 변수
# ==========================================
APP_TITLE = "개인 맞춤형 음성 변환 자동화"
APP_SUBTITLE = "AI 실시간 음성 요약 · 구글 시트 자동 누적 · 구글 캘린더 스마트 연동"
AUDIO_CHECKBOX_LABEL = "💾 음성 원본 녹음 여부 (체크를 하게 되면 음성원본이 저장됩니다)"

REDIRECT_URI = "https://public-voice-task.streamlit.app/"
SHEET_NAME = "Voice_Log_DB"

# [핵심 에러 해결] 404 NOT_FOUND 방지를 위한 최신 모델 자동 전환 리스트
GEMINI_MODEL_CANDIDATES = [
    "gemini-3.8-flash",
    "gemini-flash-latest",
    "gemini-3.0-flash",
    "gemini-2.0-flash",
    "gemini-2.5-flash"
]

COOKIE_EXPIRY_DAYS = 365
MAX_SINGLE_FREE_MINUTES = 120
DAILY_FREE_LIMIT_MINUTES = 600
DAILY_FREE_LIMIT_REQUESTS = 500
TOKENS_PER_SECOND = 32
INLINE_LIMIT_MB = 18

SHEET_HEADERS = [
    "등록일시", "카테고리", "담당자", "요약(큰타이틀)", 
    "상세내용(주제별정리/전체녹음)", "약속일시", "캘린더등록", "음성링크"
]

DEFAULT_EVENT_HOURS = 1
TIMEZONE = "Asia/Seoul"
KST = ZoneInfo(TIMEZONE)

st.set_page_config(
    page_title=APP_TITLE,
    page_icon="🎙️",
    layout="centered",
    initial_sidebar_state="collapsed"
)

# ==========================================
# 2. 모바일 웹뷰(어플) 호환 외부 사이트 이동 컴포넌트 (수정요청 4 반영)
# ==========================================
def render_app_friendly_link(url: str, label: str, icon: str = "🔗", primary: bool = True):
    """
    모바일 어플(WebView) 및 일반 브라우저 모두에서 막힘없이 열리는 하이브리드 링크 버튼입니다.
    """
    bg_color = "#2563EB" if primary else "#374151"
    safe_id = f"link_btn_{abs(hash(url + label)) % 100000}"

    html_code = f"""
    <div style="margin: 6px 0 12px 0;">
        <a id="{safe_id}"
           href="{url}"
           target="_top"
           rel="noopener noreferrer"
           onclick="try {{ window.top.location.href = '{url}'; }} catch(e) {{ window.location.href = '{url}'; }}"
           style="
               display: flex;
               align-items: center;
               justify-content: center;
               gap: 8px;
               width: 100%;
               box-sizing: border-box;
               background-color: {bg_color};
               color: #FFFFFF !important;
               text-decoration: none !important;
               font-weight: 700;
               font-size: 0.95rem;
               padding: 13px 16px;
               border-radius: 12px;
               box-shadow: 0 2px 6px rgba(0,0,0,0.12);
               text-align: center;
               line-height: 1.3;
           ">
            <span>{icon}</span>
            <span>{label}</span>
        </a>
        <div style="
            display: flex;
            align-items: center;
            justify-content: space-between;
            background: #F8FAFC;
            border: 1px solid #E2E8F0;
            border-radius: 8px;
            padding: 6px 10px;
            margin-top: 6px;
            font-size: 0.78rem;
            color: #475569;
        ">
            <span style="overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 72%;">
                🌐 이동 주소: <strong style="color:#1E293B;">{url}</strong>
            </span>
            <a href="{url}" target="_blank" rel="noopener noreferrer"
               style="color: #2563EB; font-weight: 700; text-decoration: underline; white-space: nowrap; margin-left: 8px;">
               새창 열기 ↗
            </a>
        </div>
    </div>
    """
    st.markdown(html_code, unsafe_allow_html=True)

# ==========================================
# 3. 맞춤형 CSS 스타일링 (수정요청 1, 2, 5 완벽 반영)
# ==========================================
st.markdown("""
<style>
    @import url('https://cdn.jsdelivr.net/gh/orioncactus/pretendard/dist/web/static/pretendard.css');
    
    html, body, [class*="css"] {
        font-family: 'Pretendard', -apple-system, BlinkMacSystemFont, system-ui, sans-serif;
    }

    #MainMenu {visibility: hidden !important; display: none !important;}
    header {visibility: hidden !important; display: none !important;}
    footer {visibility: hidden !important; display: none !important;}
    [data-testid="stHeader"] {display: none !important;}
    [data-testid="stToolbar"] {display: none !important;}
    [data-testid="stDecoration"] {display: none !important;}
    [data-testid="stStatusWidget"] {display: none !important;}

    .block-container {
        padding-top: 1.2rem !important;
        padding-bottom: 3rem !important;
        padding-left: 1rem !important;
        padding-right: 1rem !important;
        max-width: 680px !important;
    }

    /* [수정요청 5] 상단 배너 글씨 잘림 방지 */
    .hero-card {
        background: linear-gradient(135deg, #1E1B4B 0%, #312E81 50%, #3730A3 100%);
        border-radius: 20px;
        padding: 20px 18px;
        color: #FFFFFF;
        box-shadow: 0 8px 20px rgba(49, 46, 129, 0.25);
        margin-bottom: 16px;
        box-sizing: border-box;
        width: 100%;
    }
    .hero-badge {
        display: inline-block;
        background: rgba(255, 255, 255, 0.16);
        border: 1px solid rgba(255, 255, 255, 0.25);
        color: #E0E7FF;
        font-size: 0.72rem;
        font-weight: 700;
        letter-spacing: 0.04em;
        padding: 4px 12px;
        border-radius: 999px;
        margin-bottom: 10px;
    }
    .hero-title {
        font-size: clamp(1.12rem, 4.6vw, 1.45rem);
        font-weight: 800;
        color: #FFFFFF;
        line-height: 1.38;
        margin: 0 0 8px 0;
        white-space: normal !important;
        overflow: visible !important;
        text-overflow: unset !important;
        word-break: keep-all;
        overflow-wrap: break-word;
    }
    .hero-subtitle {
        font-size: clamp(0.82rem, 3.3vw, 0.92rem);
        color: #C7D2FE;
        line-height: 1.45;
        margin: 0;
        word-break: keep-all;
    }

    /* 사용량 대시보드 카드 */
    .usage-card-wrapper {
        background: #FFFFFF;
        border: 1px solid #E5E7EB;
        border-radius: 18px;
        padding: 16px;
        margin: 14px 0;
        box-shadow: 0 2px 8px rgba(0, 0, 0, 0.04);
    }
    .usage-header {
        font-size: 1rem;
        font-weight: 800;
        color: #111827;
        margin-bottom: 12px;
        line-height: 1.4;
    }
    .usage-grid {
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 10px;
    }
    .usage-item {
        background: #F8FAFC;
        border: 1px solid #F1F5F9;
        border-radius: 12px;
        padding: 12px;
    }
    .usage-label {
        font-size: 0.8rem;
        color: #64748B;
        font-weight: 600;
        margin-bottom: 6px;
    }
    .usage-value-blue {
        font-size: 1.12rem;
        font-weight: 800;
        color: #2563EB;
    }
    .usage-value-green {
        font-size: 1.12rem;
        font-weight: 800;
        color: #059669;
    }
    .usage-sub {
        font-size: 0.82rem;
        font-weight: 500;
        color: #64748B;
    }

    /* 녹음 상태 확인 안내 박스 */
    .guide-box {
        background: #F9FAFB;
        border: 1.5px solid #D1D5DB;
        border-left: 6px solid #374151;
        border-radius: 16px;
        padding: 16px;
        margin: 12px 0 16px 0;
    }
    .guide-title {
        font-size: 1.02rem;
        font-weight: 800;
        color: #1F2937;
        margin-bottom: 10px;
        display: flex;
        align-items: center;
        gap: 6px;
    }
    .guide-list {
        font-size: 0.92rem;
        color: #374151;
        line-height: 1.65;
        margin: 0;
        padding-left: 4px;
        list-style: none;
    }
    .guide-list li {
        margin-bottom: 6px;
        word-break: keep-all;
    }

    .recorder-prompt-box {
        background: #F3F4F6;
        border: 2px solid #4B5563;
        border-radius: 16px 16px 0 0;
        border-bottom: none;
        padding: 12px 14px 8px 14px;
        margin-top: 8px;
        font-size: 0.93rem;
        font-weight: 700;
        color: #1F2937;
        line-height: 1.45;
    }

    /* [수정요청 1 & 2] 진한 그레이 큰 마이크 & 우측 상단 불필요 아이콘 완전 제거 */
    div[data-testid="stAudioInput"] {
        background: #F3F4F6 !important;
        border: 2px solid #4B5563 !important;
        border-top: none !important;
        border-radius: 0 0 16px 16px !important;
        padding: 10px 12px 16px 12px !important;
        margin-top: -1rem !important;
        margin-bottom: 14px !important;
        position: relative !important;
        overflow: hidden !important;
    }

    div[data-testid="stAudioInput"] [data-testid="stAudioInputActionButton"],
    div[data-testid="stAudioInput"] > div > div:first-child button:not(:first-child),
    div[data-testid="stAudioInput"] [aria-label="Clear"],
    div[data-testid="stAudioInput"] [aria-label="Download"],
    div[data-testid="stAudioInput"] [title="Clear"],
    div[data-testid="stAudioInput"] [title="Download"],
    div[data-testid="stAudioInput"] a[download] {
        display: none !important;
        visibility: hidden !important;
        width: 0 !important;
        height: 0 !important;
        opacity: 0 !important;
        pointer-events: none !important;
    }

    div[data-testid="stAudioInput"] button:first-of-type {
        width: 58px !important;
        height: 58px !important;
        min-width: 58px !important;
        min-height: 58px !important;
        border-radius: 50% !important;
        background-color: #374151 !important;
        border: 3px solid #1F2937 !important;
        color: #FFFFFF !important;
        box-shadow: 0 4px 10px rgba(31, 41, 55, 0.35) !important;
        display: inline-flex !important;
        align-items: center !important;
        justify-content: center !important;
        transition: all 0.2s ease-in-out !important;
        margin-right: 8px !important;
    }

    div[data-testid="stAudioInput"] button:first-of-type svg {
        width: 28px !important;
        height: 28px !important;
        fill: #FFFFFF !important;
        color: #FFFFFF !important;
    }

    div[data-testid="stAudioInput"] button[aria-label*="Stop"],
    div[data-testid="stAudioInput"] button[aria-label*="stop"],
    div[data-testid="stAudioInput"] button[aria-label*="중지"],
    div[data-testid="stAudioInput"] button[aria-label*="정지"] {
        background-color: #111827 !important;
        border: 3px solid #DC2626 !important;
        box-shadow: 0 0 0 4px rgba(220, 38, 38, 0.25) !important;
        animation: recPulse 1.4s infinite !important;
    }

    div[data-testid="stAudioInput"] button[aria-label*="Stop"] svg,
    div[data-testid="stAudioInput"] button[aria-label*="stop"] svg,
    div[data-testid="stAudioInput"] button[aria-label*="중지"] svg,
    div[data-testid="stAudioInput"] button[aria-label*="정지"] svg {
        fill: #EF4444 !important;
        color: #EF4444 !important;
    }

    @keyframes recPulse {
        0% { box-shadow: 0 0 0 0 rgba(220, 38, 38, 0.45); }
        70% { box-shadow: 0 0 0 10px rgba(220, 38, 38, 0); }
        100% { box-shadow: 0 0 0 0 rgba(220, 38, 38, 0); }
    }

    div[data-testid="stAudioInput"] button[aria-label*="Play"],
    div[data-testid="stAudioInput"] button[aria-label*="play"],
    div[data-testid="stAudioInput"] button[aria-label*="재생"] {
        width: 40px !important;
        height: 40px !important;
        min-width: 40px !important;
        min-height: 40px !important;
        border-radius: 50% !important;
        background-color: #E5E7EB !important;
        border: 1.5px solid #4B5563 !important;
        color: #1F2937 !important;
        box-shadow: none !important;
    }
    div[data-testid="stAudioInput"] button[aria-label*="Play"] svg,
    div[data-testid="stAudioInput"] button[aria-label*="play"] svg {
        width: 18px !important;
        height: 18px !important;
        fill: #1F2937 !important;
    }

    button[data-baseweb="tab"] {
        font-size: 0.95rem !important;
        font-weight: 700 !important;
    }
</style>
""", unsafe_allow_html=True)

# ==========================================
# 4. 상단 헤더 영역 (수정요청 5: 잘림 없이 전체 표시)
# ==========================================
st.markdown(f"""
<div class="hero-card">
    <div class="hero-badge">✨ AI SMART VOICE WORKSPACE</div>
    <div class="hero-title">🎙️ {APP_TITLE}</div>
    <p class="hero-subtitle">{APP_SUBTITLE}</p>
</div>
""", unsafe_allow_html=True)

# ==========================================
# 5. 구글 OAuth 2.0 인증 및 영구 쿠키 복구
# ==========================================
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/calendar.events"
]

try:
    oauth_secrets = st.secrets["google_oauth"]
    client_config = {
        "web": {
            "client_id": oauth_secrets["client_id"],
            "project_id": oauth_secrets["project_id"],
            "auth_uri": oauth_secrets["auth_uri"],
            "token_uri": oauth_secrets["token_uri"],
            "auth_provider_x509_cert_url": oauth_secrets["auth_provider_x509_cert_url"],
            "client_secret": oauth_secrets["client_secret"],
            "redirect_uris": [REDIRECT_URI]
        }
    }
except Exception:
    st.error("⚠️ `.streamlit/secrets.toml` 설정 파일을 찾을 수 없거나 형식이 올바르지 않습니다.")
    st.stop()

cookie_manager = stx.CookieManager(key="voice_app_cookie_mgr")
all_cookies = cookie_manager.get_all()

@st.cache_resource
def get_flow_store():
    return {}

flow_store = get_flow_store()

if "credentials" in st.session_state:
    saved_scopes = st.session_state["credentials"].scopes or []
    if "https://www.googleapis.com/auth/calendar.events" not in saved_scopes:
        del st.session_state["credentials"]

if "credentials" not in st.session_state and "code" in st.query_params and "state" in st.query_params:
    auth_code = st.query_params["code"]
    state_key = st.query_params["state"]
    try:
        if state_key in flow_store:
            flow = flow_store.pop(state_key)
            flow.fetch_token(code=auth_code)
            creds = flow.credentials
            st.session_state["credentials"] = creds
            st.session_state["logged_out_flag"] = False
            
            existing_cookies = all_cookies or {}
            prev_rt_encoded = existing_cookies.get("voice_rt", "") or st.query_params.get("rt", "")
            
            final_rt = creds.refresh_token
            if not final_rt and prev_rt_encoded:
                try:
                    final_rt = base64.b64decode(prev_rt_encoded.encode()).decode()
                except Exception:
                    final_rt = prev_rt_encoded

            existing_ak = st.query_params.get("ak", "") or existing_cookies.get("voice_ak", "")
            st.query_params.clear()
            
            if final_rt:
                encoded_rt = base64.b64encode(final_rt.encode()).decode()
                st.session_state["saved_rt_encoded"] = encoded_rt
                st.query_params["rt"] = encoded_rt
                cookie_manager.set(
                    "voice_rt", 
                    encoded_rt, 
                    expires_at=datetime.now() + timedelta(days=COOKIE_EXPIRY_DAYS),
                    key="set_rt_cookie_init"
                )
            if existing_ak:
                st.query_params["ak"] = existing_ak
        else:
            st.query_params.clear()
            st.rerun()
    except Exception as e:
        st.error(f"인증 처리 중 오류가 발생했습니다: {e}")
        st.query_params.clear()
        st.stop()

if "credentials" not in st.session_state and not st.session_state.get("logged_out_flag"):
    existing_cookies = all_cookies or {}
    raw_rt_cookie = existing_cookies.get("voice_rt", "")
    raw_rt_param = st.query_params.get("rt", "")
    encoded_rt = raw_rt_cookie or raw_rt_param or st.session_state.get("saved_rt_encoded", "")

    if not encoded_rt and not st.session_state.get("cookie_sync_checked"):
        st.session_state["cookie_sync_checked"] = True
        time.sleep(0.5)
        st.rerun()

    if encoded_rt:
        try:
            try:
                decoded_rt = base64.b64decode(encoded_rt.encode()).decode()
            except Exception:
                decoded_rt = encoded_rt

            auto_creds = Credentials(
                token=None,
                refresh_token=decoded_rt,
                token_uri=oauth_secrets["token_uri"],
                client_id=oauth_secrets["client_id"],
                client_secret=oauth_secrets["client_secret"],
                scopes=SCOPES
            )
            auto_creds.refresh(Request())
            st.session_state["credentials"] = auto_creds
            st.session_state["saved_rt_encoded"] = encoded_rt
            if "rt" not in st.query_params:
                st.query_params["rt"] = encoded_rt
        except Exception:
            if "rt" in st.query_params:
                del st.query_params["rt"]

if "saved_api_key" not in st.session_state:
    st.session_state["saved_api_key"] = ""

if not st.session_state["saved_api_key"] and not st.session_state.get("key_cleared_flag"):
    existing_cookies = all_cookies or {}
    encoded_ak = existing_cookies.get("voice_ak", "") or st.query_params.get("ak", "")
    if encoded_ak:
        try:
            decoded_key = base64.b64decode(encoded_ak.encode()).decode()
            st.session_state["saved_api_key"] = decoded_key
            if "ak" not in st.query_params:
                st.query_params["ak"] = encoded_ak
        except Exception:
            pass

# 로그인 전 화면 (어플 호환 링크 버튼 적용)
if "credentials" not in st.session_state:
    if len(flow_store) > 100:
        flow_store.clear()
        
    flow = Flow.from_client_config(client_config, scopes=SCOPES, redirect_uri=REDIRECT_URI)
    auth_url, state_key = flow.authorization_url(
        prompt="consent",
        access_type="offline",
        include_granted_scopes="true"
    )
    flow_store[state_key] = flow
    
    st.warning("🔗 구글 계정이 로그아웃 상태입니다. 아래 버튼을 눌러 안전하게 연동해 주세요.")
    render_app_friendly_link(
        url=auth_url,
        label="내 Google 계정으로 시작하기 (구글 시트·캘린더 자동 연동)",
        icon="🚀",
        primary=True
    )
    st.stop()

if "credentials" in st.session_state and st.session_state.get("saved_rt_encoded"):
    existing_cookies = all_cookies or {}
    if not existing_cookies.get("voice_rt"):
        cookie_manager.set(
            "voice_rt",
            st.session_state["saved_rt_encoded"],
            expires_at=datetime.now() + timedelta(days=COOKIE_EXPIRY_DAYS),
            key="reinforce_rt_cookie"
        )

# 상단 로그인 상태 표시 및 로그아웃 버튼
col_status, col_logout = st.columns([3.8, 1.2])
with col_status:
    if st.session_state["saved_api_key"]:
        st.success("✅ 내 구글 계정 및 AI 키 영구 자동 유지 활성화됨")
    else:
        st.success("✅ 내 구글 계정 연동 완료! (아래에서 AI 키를 1회 등록해 주세요)")
with col_logout:
    if st.button("🚪 로그아웃", use_container_width=True):
        if "credentials" in st.session_state:
            del st.session_state["credentials"]
        st.session_state["saved_api_key"] = ""
        st.session_state["saved_rt_encoded"] = ""
        st.session_state["logged_out_flag"] = True
        st.session_state["key_cleared_flag"] = True
        st.query_params.clear()
        try:
            cookie_manager.delete("voice_rt", key="del_rt_cookie")
            cookie_manager.delete("voice_ak", key="del_ak_cookie_logout")
        except Exception:
            pass
        st.rerun()

# ==========================================
# 6. 오늘의 무료 AI 음성 사용 현황 대시보드
# ==========================================
now_kst = datetime.now(KST)
today_str = now_kst.strftime("%Y-%m-%d")

if st.session_state.get("usage_date") != today_str:
    st.session_state["usage_date"] = today_str
    st.session_state["daily_used_seconds"] = 0.0
    st.session_state["daily_request_count"] = 0

if "last_processed_hash" not in st.session_state:
    st.session_state["last_processed_hash"] = None

used_mins = st.session_state["daily_used_seconds"] / 60.0
remaining_mins = max(0.0, DAILY_FREE_LIMIT_MINUTES - used_mins)
req_count = st.session_state["daily_request_count"]

st.markdown(f"""
<div class="usage-card-wrapper">
    <div class="usage-header">📊 오늘의 무료 AI 음성 사용 현황 ({today_str})</div>
    <div class="usage-grid">
        <div class="usage-item">
            <div class="usage-label">1회 최대 분석 한도</div>
            <div class="usage-value-blue">최대 {MAX_SINGLE_FREE_MINUTES}분</div>
        </div>
        <div class="usage-item">
            <div class="usage-label">오늘 누적 사용 시간</div>
            <div class="usage-value-blue">{used_mins:.1f}분 <span class="usage-sub">/ {DAILY_FREE_LIMIT_MINUTES}분</span></div>
        </div>
        <div class="usage-item">
            <div class="usage-label">남은 무료 여유 시간</div>
            <div class="usage-value-green">약 {remaining_mins:.1f}분</div>
        </div>
        <div class="usage-item">
            <div class="usage-label">오늘 무료 분석 횟수</div>
            <div class="usage-value-blue">{req_count}회 <span class="usage-sub">/ {DAILY_FREE_LIMIT_REQUESTS}회</span></div>
        </div>
    </div>
</div>
""", unsafe_allow_html=True)

# ==========================================
# 7. Gemini AI 분석 및 구글 시트/캘린더/드라이브 연동 함수
# ==========================================
def build_gemini_prompt(current_time_str, weekday_korean, today_date_str):
    return f"""
    현재 대한민국 기준 시각은 {current_time_str} ({weekday_korean})이야.
    첨부된 음성(회의 또는 메모)을 끝까지 주의 깊게 듣고, 아래 규칙에 맞춰 반드시 JSON 형식으로만 출력해 줘.

    1. Category: 반드시 '일정', '할일', '아이디어', '기록' 중 딱 하나로만 분류해.
    2. Assignee: 언급된 담당자 이름/직급/업체명 추출 (없으면 '미지정').
    3. Summary: 맨 앞에 오늘 날짜([{today_date_str}])를 붙인 핵심 한 줄 제목.
    4. Details: [상세 회의 주제별 정리]와 [전체 녹음 원본 받아쓰기] 두 파트로 나누어 누락 없이 작성.
    5. DueDate: 약속/기한 날짜 YYYY-MM-DD 계산 (없으면 빈칸 "").
    6. EventTime: 약속 시간 24시간제 HH:MM 변환 (없으면 빈칸 "").
    """

def generate_with_model_fallback(client_gemini, contents_payload, gen_config):
    last_error = None
    for model_name in GEMINI_MODEL_CANDIDATES:
        try:
            response = client_gemini.models.generate_content(
                model=model_name,
                contents=contents_payload,
                config=gen_config
            )
            return response, model_name
        except Exception as err:
            err_str = str(err)
            last_error = err
            if "404" in err_str or "NOT_FOUND" in err_str or "no longer available" in err_str or "not found" in err_str.lower():
                continue
            else:
                raise err
    raise last_error

def save_api_key_permanently(raw_key):
    clean_key = raw_key.strip()
    encoded_val = base64.b64encode(clean_key.encode()).decode()
    st.session_state["saved_api_key"] = clean_key
    st.session_state["key_cleared_flag"] = False
    st.query_params["ak"] = encoded_val
    cookie_manager.set(
        "voice_ak", 
        encoded_val, 
        expires_at=datetime.now() + timedelta(days=COOKIE_EXPIRY_DAYS),
        key="set_ak_cookie"
    )

def get_valid_credentials():
    creds = st.session_state["credentials"]
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            st.session_state["credentials"] = creds
        except Exception:
            pass
    return creds

def add_to_google_calendar(creds, summary, assignee, details, due_date, event_time):
    if not due_date:
        return "일정 없음 (시트만 저장)"
    authed_session = AuthorizedSession(creds)
    calendar_url = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
    title_prefix = f"[담당: {assignee}] " if assignee and assignee != "미지정" else ""
    event_title = f"{title_prefix}{summary}"
    try:
        if event_time:
            start_dt = datetime.strptime(f"{due_date} {event_time}", "%Y-%m-%d %H:%M")
            end_dt = start_dt + timedelta(hours=DEFAULT_EVENT_HOURS)
            event_body = {
                "summary": event_title,
                "description": details,
                "start": {"dateTime": start_dt.strftime("%Y-%m-%dT%H:%M:00"), "timeZone": TIMEZONE},
                "end": {"dateTime": end_dt.strftime("%Y-%m-%dT%H:%M:00"), "timeZone": TIMEZONE}
            }
        else:
            start_date_obj = datetime.strptime(due_date, "%Y-%m-%d")
            end_date_obj = start_date_obj + timedelta(days=1)
            event_body = {
                "summary": event_title,
                "description": details,
                "start": {"date": start_date_obj.strftime("%Y-%m-%d")},
                "end": {"date": end_date_obj.strftime("%Y-%m-%d")}
            }
        res = authed_session.post(calendar_url, json=event_body)
        return "✅ 캘린더 등록 완료" if res.status_code in [200, 201] else f"⚠️ 캘린더 오류 ({res.status_code})"
    except Exception as cal_err:
        return f"⚠️ 캘린더 형식 오류 ({cal_err})"

def upload_audio_to_drive(creds, audio_bytes, mime_type):
    authed_session = AuthorizedSession(creds)
    ext = "mp3" if "mp3" in mime_type else ("m4a" if "mp4" in mime_type else "wav")
    metadata = {"name": f"Voice_Record_{datetime.now(KST).strftime('%Y%m%d_%H%M%S')}.{ext}"}
    files = {
        "data": ("metadata", json.dumps(metadata), "application/json"),
        "file": ("audio", audio_bytes, mime_type)
    }
    try:
        res = authed_session.post(
            "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart",
            files=files
        )
        if res.status_code in [200, 201]:
            file_id = res.json().get("id")
            return f"https://drive.google.com/file/d/{file_id}/view"
        return ""
    except Exception:
        return ""

def download_audio_from_drive(creds, drive_url):
    match = re.search(r"/d/([a-zA-Z0-9_-]+)", drive_url)
    if not match:
        return None
    file_id = match.group(1)
    authed_session = AuthorizedSession(creds)
    try:
        res = authed_session.get(f"https://www.googleapis.com/drive/v3/files/{file_id}?alt=media")
        if res.status_code == 200:
            return res.content
        return None
    except Exception:
        return None

def save_record_to_sheet(creds, row_data):
    client_gspread = gspread.authorize(creds)
    try:
        sheet = client_gspread.open(SHEET_NAME).sheet1
        if sheet.row_values(1) != SHEET_HEADERS:
            sheet.update(range_name="A1:H1", values=[SHEET_HEADERS])
    except gspread.exceptions.SpreadsheetNotFound:
        new_spreadsheet = client_gspread.create(SHEET_NAME)
        sheet = new_spreadsheet.sheet1
        sheet.append_row(SHEET_HEADERS)
    sheet.append_row(row_data, value_input_option="RAW")

# ==========================================
# 8. Gemini API 키 등록 및 외부 사이트 바로가기 (수정요청 4 반영)
# ==========================================
with st.expander(
    "🔑 최초 1회 무료 Gemini API 키 등록 및 외부 사이트 연결 설정"
    if not st.session_state["saved_api_key"]
    else "✅ 무료 Gemini API 키 등록 완료 (클릭하여 키 변경 또는 사이트 이동)",
    expanded=not bool(st.session_state["saved_api_key"])
):
    st.markdown("**1단계:** 아래 버튼을 누르면 무료 Gemini API 키 발급 사이트(Google AI Studio)로 즉시 연결됩니다.")
    render_app_friendly_link(
        url="https://aistudio.google.com/app/apikey",
        label="무료 Gemini API 키 발급 사이트 열기 (Google AI Studio)",
        icon="🔑",
        primary=True
    )

    api_key_input = st.text_input(
        "발급받은 Gemini API 키를 붙여넣으세요:",
        value=st.session_state["saved_api_key"],
        type="password",
        placeholder="AIzaSy..."
    )

    col_save, col_clear = st.columns([3, 1])
    with col_save:
        if st.button("💾 API 키 영구 저장하기", use_container_width=True, type="primary"):
            if api_key_input.strip():
                save_api_key_permanently(api_key_input)
                st.success("✅ Gemini API 키가 안전하게 영구 저장되었습니다!")
                st.rerun()
    with col_clear:
        if st.button("초기화", use_container_width=True):
            st.session_state["saved_api_key"] = ""
            st.session_state["key_cleared_flag"] = True
            if "ak" in st.query_params:
                del st.query_params["ak"]
            try:
                cookie_manager.delete("voice_ak", key="del_ak_cookie")
            except Exception:
                pass
            st.rerun()

    st.markdown("---")
    st.markdown("**🔗 연동 사이트 바로가기 (어플/모바일 호환)**")
    render_app_friendly_link(
        url="https://docs.google.com/spreadsheets/",
        label="내 구글 시트(Google Sheets) 바로 열기",
        icon="📊",
        primary=False
    )
    render_app_friendly_link(
        url="https://calendar.google.com/",
        label="내 구글 캘린더(Google Calendar) 바로 열기",
        icon="📅",
        primary=False
    )

user_api_key = st.session_state["saved_api_key"]
st.markdown("<hr style='margin: 12px 0; border-color: #E5E7EB;'>", unsafe_allow_html=True)

# ==========================================
# 9. 메인 탭 구성 (수정요청 1, 2, 3 완벽 반영)
# ==========================================
tab_record, tab_saved = st.tabs([
    "🎙️ 실시간 녹음 및 파일 변환",
    "📁 내 저장된 녹음 리스트"
])

with tab_record:
    input_mode = st.radio(
        "음성 입력 방식을 선택하세요:",
        options=["실시간 녹음", "파일 업로드"],
        horizontal=True
    )

    # [수정요청 3] 원본 저장 체크박스를 '실시간 녹음' 바로 아래에 배치
    save_original_audio = st.checkbox(AUDIO_CHECKBOX_LABEL, value=True)

    audio_bytes = None
    mime_type = "audio/wav"
    should_process = False

    if input_mode == "실시간 녹음":
        st.markdown("""
        <div class="guide-box">
            <div class="guide-title">🎙️ 실시간 녹음 상태 확인 안내</div>
            <ul class="guide-list">
                <li>• 아래 녹음 박스 왼쪽의 큼직한 <strong>🎙️ 진한 그레이 마이크 버튼</strong>을 누르면 ➡️ 즉시 <strong>🔴 정지 버튼 및 파형(녹음중)</strong>으로 전환됩니다.</li>
                <li>• 말씀을 마친 후 <strong>⏹️ 정지(완료) 버튼</strong>을 다시 누르면 ➡️ <strong>🎙️ 진한 그레이 마이크</strong>가 다시 나오며 녹음이 종료되고 <strong>즉시 자동 변환</strong>됩니다!</li>
            </ul>
        </div>
        <div class="recorder-prompt-box">
            👇 아래 왼쪽의 큰 진한 그레이 마이크 버튼(🎙️)을 눌러 녹음을 시작하세요 (정지 클릭 시 자동 변환)
        </div>
        """, unsafe_allow_html=True)

        recorded_audio = st.audio_input("실시간 음성 녹음", label_visibility="collapsed")

        if recorded_audio is not None:
            audio_bytes = recorded_audio.getvalue()
            st.markdown("""
            <div style="
                background: #F3F4F6;
                border: 1.5px solid #374151;
                color: #111827;
                padding: 10px 14px;
                border-radius: 12px;
                font-size: 0.9rem;
                font-weight: 700;
                margin-bottom: 12px;
                display: flex;
                align-items: center;
                gap: 8px;
            ">
                <span style="
                    display:inline-flex;
                    align-items:center;
                    justify-content:center;
                    width:28px;
                    height:28px;
                    border-radius:50%;
                    background:#374151;
                    color:#FFFFFF;
                    font-size:0.85rem;
                ">🎙️</span>
                <span>녹음 정지 완료! (진한 그레이 마이크 대기 상태로 복귀됨)</span>
            </div>
            """, unsafe_allow_html=True)

        current_audio_hash = hashlib.md5(audio_bytes).hexdigest() if audio_bytes else None
        if audio_bytes is not None and current_audio_hash != st.session_state["last_processed_hash"]:
            should_process = True

    else:
        uploaded_file = st.file_uploader(
            "음성 파일을 선택하세요 (mp3, wav, m4a, ogg 등)",
            type=["mp3", "wav", "m4a", "ogg", "webm"]
        )
        if uploaded_file is not None:
            audio_bytes = uploaded_file.read()
            ext = uploaded_file.name.split(".")[-1].lower()
            mime_map = {"mp3": "audio/mp3", "m4a": "audio/mp4", "wav": "audio/wav"}
            mime_type = mime_map.get(ext, "audio/wav")
            st.audio(audio_bytes, format=mime_type)

        current_audio_hash = hashlib.md5(audio_bytes).hexdigest() if audio_bytes else None
        if st.button("🚀 업로드한 음성 파일 AI 변환 시작", use_container_width=True, type="primary"):
            should_process = True

    if should_process:
        if not user_api_key:
            st.warning("⚠️ 상단에 본인의 무료 Gemini API 키를 먼저 등록해 주세요.")
        elif not audio_bytes or len(audio_bytes) < 100:
            st.warning("⚠️ 분석할 음성 데이터가 없거나 너무 짧습니다.")
        elif used_mins >= DAILY_FREE_LIMIT_MINUTES or req_count >= DAILY_FREE_LIMIT_REQUESTS:
            st.error("🛑 오늘의 무료 사용 안전 한도를 모두 소진했습니다. 내일 다시 이용해 주세요.")
        else:
            file_size_mb = len(audio_bytes) / (1024 * 1024)
            try:
                now = datetime.now(KST)
                current_time_str = now.strftime("%Y-%m-%d %H:%M:%S")
                weekday_korean = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"][now.weekday()]
                
                prompt_text = build_gemini_prompt(current_time_str, weekday_korean, today_str)
                client_gemini = genai.Client(api_key=user_api_key.strip())
                
                json_schema = types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "Category": types.Schema(type=types.Type.STRING),
                        "Assignee": types.Schema(type=types.Type.STRING),
                        "Summary": types.Schema(type=types.Type.STRING),
                        "Details": types.Schema(type=types.Type.STRING),
                        "DueDate": types.Schema(type=types.Type.STRING),
                        "EventTime": types.Schema(type=types.Type.STRING),
                    },
                    required=["Category", "Assignee", "Summary", "Details", "DueDate", "EventTime"]
                )
                gen_config = types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=json_schema,
                    temperature=0.1
                )
                
                with st.spinner("🤖 최신 AI가 음성을 정밀 분석하여 요약 및 받아쓰기를 수행 중입니다..."):
                    if file_size_mb <= INLINE_LIMIT_MB:
                        response, used_model_name = generate_with_model_fallback(
                            client_gemini,
                            [types.Part.from_bytes(data=audio_bytes, mime_type=mime_type), prompt_text],
                            gen_config
                        )
                    else:
                        uploaded_file_obj = None
                        try:
                            audio_stream = io.BytesIO(audio_bytes)
                            uploaded_file_obj = client_gemini.files.upload(
                                file=audio_stream,
                                config=types.UploadFileConfig(mime_type=mime_type)
                            )
                            for _ in range(30):
                                file_info = client_gemini.files.get(name=uploaded_file_obj.name)
                                if file_info.state.name == "ACTIVE":
                                    break
                                time.sleep(1)
                            response, used_model_name = generate_with_model_fallback(
                                client_gemini,
                                [uploaded_file_obj, prompt_text],
                                gen_config
                            )
                        finally:
                            if uploaded_file_obj:
                                try:
                                    client_gemini.files.delete(name=uploaded_file_obj.name)
                                except Exception:
                                    pass
                                
                    result_data = json.loads(response.text)
                    prompt_tokens = response.usage_metadata.prompt_token_count if response.usage_metadata else 100
                    estimated_seconds = max(1.0, prompt_tokens / TOKENS_PER_SECOND)
                    st.session_state["daily_used_seconds"] += estimated_seconds
                    st.session_state["daily_request_count"] += 1
                    st.session_state["last_processed_hash"] = current_audio_hash
                
                category = result_data.get("Category", "기록")
                assignee = result_data.get("Assignee", "미지정")
                summary = result_data.get("Summary", "")
                details = result_data.get("Details", "")
                due_date = result_data.get("DueDate", "").strip()
                event_time = result_data.get("EventTime", "").strip()
                
                st.success(f"🎉 실시간 녹음 자동 변환 완료! (적용 모델: `{used_model_name}`)")
                with st.container(border=True):
                    st.subheader(f"📌 {summary}")
                    st.write(f"**🏷️ 카테고리:** `{category}` | **👤 담당자:** `{assignee}` | **⏰ 약속일시:** `{due_date} {event_time}`")
                    st.text_area("📝 상세 회의 주제별 정리 및 전체 받아쓰기 원문", value=details, height=220)
                
                with st.spinner("💾 내 구글 시트 요약 기록 및 캘린더 일정 자동 연동 중..."):
                    creds = get_valid_credentials()
                    calendar_status = add_to_google_calendar(creds, summary, assignee, details, due_date, event_time)
                    
                    if save_original_audio:
                        drive_link = upload_audio_to_drive(creds, audio_bytes, mime_type)
                        backup_msg = "원본 음성 드라이브 백업 포함"
                    else:
                        drive_link = "원본 음성 저장 안 함 (텍스트만 기록)"
                        backup_msg = "원본 음성 백업 제외"
                        
                    schedule_display = f"{due_date} {event_time}".strip()
                    row_to_save = [
                        current_time_str, category, assignee, summary,
                        details, schedule_display, calendar_status, drive_link
                    ]
                    save_record_to_sheet(creds, row_to_save)
                    st.info(f"✅ 내 구글 시트(`{SHEET_NAME}`) 저장 완료! ({backup_msg} | 캘린더: {calendar_status})")
                    
            except Exception as e:
                st.error(f"⚠️ 분석 중 확인이 필요합니다: {e}")

with tab_saved:
    st.markdown("#### 📁 내 구글 시트에 저장된 녹음 리스트 (조회 및 바로 듣기)")
    if st.button("🔄 저장 목록 새로고침"):
        st.rerun()
        
    try:
        creds = get_valid_credentials()
        client_gspread = gspread.authorize(creds)
        sheet = client_gspread.open(SHEET_NAME).sheet1
        all_values = sheet.get_all_values()
        
        if len(all_values) <= 1:
            st.info("아직 저장된 녹음 내역이 없습니다. 첫 번째 탭에서 녹음을 진행해 보세요!")
        else:
            data_rows = all_values[1:]
            for idx, row in enumerate(reversed(data_rows)):
                while len(row) < 8:
                    row.append("")
                reg_date, cat, assignee, title, details, sched, cal_status, audio_url = row[:8]
                
                with st.expander(f"🎙️ [{cat}] {title} (등록: {reg_date} | 담당: {assignee})"):
                    st.write(f"**⏰ 약속 일시:** {sched if sched else '없음'} | **📅 캘린더 상태:** {cal_status}")
                    st.text_area("원문 및 상세 내용", value=details, height=150, key=f"det_{idx}")
                    
                    if audio_url and "drive.google.com" in audio_url:
                        if st.button("▶️ 앱에서 원본 녹음 바로 듣기", key=f"play_btn_{idx}"):
                            with st.spinner("원본 음성을 불러오는 중입니다..."):
                                fetched_audio = download_audio_from_drive(creds, audio_url)
                                if fetched_audio:
                                    st.audio(fetched_audio, format="audio/wav")
                        render_app_friendly_link(
                            url=audio_url,
                            label="내 구글 드라이브에서 원본 음성 파일 직접 열기",
                            icon="🔗",
                            primary=False
                        )
                    else:
                        st.caption("ℹ️ 원본 음성 미저장 (텍스트 요약본만 보관됨)")
    except gspread.exceptions.SpreadsheetNotFound:
        st.info(f"아직 생성된 스프레드시트(`{SHEET_NAME}`)가 없습니다. 첫 녹음을 저장하면 자동 생성됩니다!")
    except Exception as list_err:
        st.warning(f"목록을 불러오는 중 확인 필요: {list_err}")
