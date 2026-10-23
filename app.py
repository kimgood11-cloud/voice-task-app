import streamlit as st
import json
import io
import re
import time
import hashlib
import base64
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from audio_recorder_streamlit import audio_recorder
import gspread
from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import AuthorizedSession, Request
from google import genai
from google.genai import types
import extra_streamlit_components as stx

# =====================================================================
# [사용자 수정 가능 변수 구역 1] 기본 환경, 타이틀 및 영구 유지 기간 설정
# =====================================================================
APP_TITLE = "개인 맞춤형 음성 변환 자동화 시스템"
APP_SUBTITLE = "AI 실시간 음성 요약 · 구글 시트 자동 누적 · 구글 캘린더 스마트 연동"
AUDIO_CHECKBOX_LABEL = "💾 음성 원본 녹음 여부 (체크를 하게 되면 음성원본이 저장됩니다)"

REDIRECT_URI = 'https://public-voice-task.streamlit.app/'
SHEET_NAME = 'Voice_Log_DB'
GEMINI_MODEL = 'gemini-2.5-flash'

# 스마트폰 앱을 닫아도 로그인과 API 키가 유지되는 기간 (기본 365일)
COOKIE_EXPIRY_DAYS = 365

# 무료 조건 한도 설정 (1회 최대 120분, 하루 누적 600분 / 500회)
MAX_SINGLE_FREE_MINUTES = 120
DAILY_FREE_LIMIT_MINUTES = 600
DAILY_FREE_LIMIT_REQUESTS = 500
TOKENS_PER_SECOND = 32

# 인라인 전송 기준 용량 (18MB 이하 시 직접 전송, 초과 시 Files API 자동 전환)
INLINE_LIMIT_MB = 18

# 구글 스프레드시트 첫 행(헤더) 컬럼 구성 (8개 열)
SHEET_HEADERS = [
    "등록일시", "카테고리", "담당자", "요약(큰타이틀)", 
    "상세내용(주제별정리/전체녹음)", "약속일시", "캘린더등록", "음성링크"
]

# 구글 캘린더 기본 일정 길이(시간) 및 대한민국 표준 시간대(KST) 고정
DEFAULT_EVENT_HOURS = 1
TIMEZONE = 'Asia/Seoul'
KST = ZoneInfo(TIMEZONE)

# =====================================================================
# [사용자 수정 가능 변수 구역 2] AI 프롬프트(지시문) 생성 함수
# 나중에 분류 기준이나 회의록 양식을 바꾸고 싶을 때 이 함수 내부만 수정하세요!
# =====================================================================
def build_gemini_prompt(current_time_str, weekday_korean, today_date_str):
    prompt = f"""
    현재 대한민국 기준 시각은 {current_time_str} ({weekday_korean})이야.
    첨부된 음성(회의 또는 메모)을 끝까지 주의 깊게 듣고, 아래 규칙에 맞춰 반드시 JSON 형식으로만 출력해 줘.

    [작성 및 분류 상세 규칙]
    1. Category (카테고리 분류):
       - 반드시 '일정', '할일', '아이디어', '기록' 중 딱 하나로만 분류해.
       - '일정': 특정 날짜나 시간에 방문, 내방, 미팅, 회의 등 약속이 잡힌 경우 (예: 내일 오후 2시 내방)
       - '할일': 기한 내에 처리하거나 준비해야 하는 업무 지시 및 확인 사항
       - '아이디어': 기획안, 신제품 아이디어, 새로운 제안, 브레인스토밍 내용
       - '기록': 위 3가지에 해당하지 않는 일반 참고 메모, 현장 상태 기록, 단순 대화 기록

    2. Assignee (담당자 추출):
       - 음성에서 언급된 담당자 이름, 직급, 업체명 또는 부서를 추출해. (예: "홍길동 과장 (도루코)")
       - 언급된 사람이 없으면 반드시 '미지정'이라고 적어.

    3. Summary (오늘 날짜 + 큰 회의 타이틀):
       - 전체 내용을 대표하는 핵심 제목을 한 줄로 작성하되, 맨 앞에 오늘 날짜([{today_date_str}])를 붙여줘.
       - 예시: "[{today_date_str}] 도루코 홍길동 과장 신제품 소개 및 제안 건 내방"

    4. Details (상세 회의 주제별 정리 및 전체 녹음 원본 받아쓰기):
       - 두 시간짜리 긴 회의나 여러 주제가 섞여 있어도 행을 쪼개지 말고 이 항목 안에 아래 두 파트로 나누어 모두 기재해.
       - [파트 1: 상세 회의 주제별 정리] 논의된 안건들을 세부 주제별(주제 1, 주제 2...)로 구분하여 핵심 내용을 깔끔하게 정리.
       - [파트 2: 전체 녹음 원본 받아쓰기] 음성에서 발화된 모든 내용을 처음부터 끝까지 단 한 문장도 누락 없이 그대로 받아쓰기(STT).

    5. DueDate (약속/기한 날짜) & EventTime (약속 시간):
       - '내일', '모레', '다음 주 월요일' 같은 상대적 날짜 표현이 나오면 현재 기준 시각({current_time_str})을 바탕으로 정확한 날짜(YYYY-MM-DD)를 계산해.
       - '오후 2시' 같은 시간 표현은 24시간제(HH:MM, 예: 14:00)로 변환해. 날짜나 시간 언급이 전혀 없는 단순 '기록' 등은 빈칸("")으로 비워둬.

    [출력 JSON 규격]
    {{
        "Category": "일정, 할일, 아이디어, 기록 중 하나",
        "Assignee": "담당자 이름/업체명 (없으면 '미지정')",
        "Summary": "[{today_date_str}] 전체 회의를 관통하는 큰 타이틀 요약",
        "Details": "[상세 회의 주제별 정리]\\n- 주제 1: ...\\n- 주제 2: ...\\n\\n[전체 녹음 원본 받아쓰기]\\n(음성 전체 받아쓰기 내용 기재)",
        "DueDate": "YYYY-MM-DD (없으면 빈칸)",
        "EventTime": "HH:MM (없으면 빈칸)"
    }}
    """
    return prompt

# =====================================================================
# [프리미엄 UI 디자인 및 불필요한 상하단 링크 완벽 숨김 CSS 적용]
# =====================================================================
st.set_page_config(page_title=APP_TITLE, page_icon="🎙️", layout="wide")

premium_css = f"""
<style>
    @import url('https://cdn.jsdelivr.net/gh/orioncactus/pretendard/dist/web/static/pretendard.css');
    
    html, body, [class*="css"] {{
        font-family: 'Pretendard', -apple-system, BlinkMacSystemFont, system-ui, sans-serif;
    }}

    /* 1. 상단 우측 GitHub/Fork 및 하단 Streamlit 링크 숨김 처리 */
    #MainMenu {{visibility: hidden !important; display: none !important;}}
    header {{visibility: hidden !important; display: none !important;}}
    footer {{visibility: hidden !important; display: none !important;}}
    [data-testid="stHeader"] {{display: none !important;}}
    [data-testid="stToolbar"] {{display: none !important;}}
    [data-testid="stDecoration"] {{display: none !important;}}
    [data-testid="stStatusWidget"] {{display: none !important;}}
    .viewerBadge_container__1QSob {{display: none !important;}}
    .styles_viewerBadge__1yB5_ {{display: none !important;}}

    /* 2. 전체 레이아웃 여백 최적화 */
    .block-container {{
        padding-top: 1.2rem !important;
        padding-bottom: 2.5rem !important;
        max-width: 880px !important;
    }}

    /* 3. 상단 프리미엄 히어로 배너 (모바일 한 줄 표시 최적화) */
    .premium-header {{
        background: linear-gradient(135deg, #1e1b4b 0%, #312e81 50%, #4338ca 100%);
        color: #ffffff;
        padding: 24px 20px;
        border-radius: 18px;
        box-shadow: 0 10px 25px -5px rgba(49, 46, 129, 0.35);
        margin-bottom: 20px;
        border: 1px solid rgba(255, 255, 255, 0.12);
    }}
    .premium-badge {{
        display: inline-block;
        background: rgba(255, 255, 255, 0.18);
        color: #e0e7ff;
        font-size: 11px;
        font-weight: 700;
        padding: 4px 10px;
        border-radius: 20px;
        margin-bottom: 10px;
        letter-spacing: 0.5px;
    }}
    .premium-title {{
        font-size: clamp(16px, 4.8vw, 24px);
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
        font-weight: 800;
        margin: 0 0 8px 0;
        letter-spacing: -0.5px;
        line-height: 1.3;
    }}
    .premium-subtitle {{
        font-size: clamp(11.5px, 3.2vw, 14px);
        color: #c7d2fe;
        margin: 0;
        font-weight: 400;
        line-height: 1.5;
        word-break: keep-all;
    }}

    /* 4. 대시보드 및 안내 카드 스타일 */
    .usage-card {{
        background: #ffffff;
        border: 1px solid #e2e8f0;
        border-radius: 14px;
        padding: 16px 18px;
        box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.04);
        margin-bottom: 12px;
    }}
    .usage-title {{
        font-size: 14px;
        font-weight: 700;
        color: #1e293b;
        margin-bottom: 10px;
    }}
    .usage-grid {{
        display: flex;
        justify-content: space-between;
        gap: 8px;
        flex-wrap: wrap;
    }}
    .usage-item {{
        background: #f8fafc;
        border-radius: 10px;
        padding: 10px 12px;
        flex: 1;
        min-width: 110px;
        border: 1px solid #f1f5f9;
    }}
    .usage-label {{
        font-size: 12px;
        color: #64748b;
        margin-bottom: 4px;
    }}
    .usage-value {{
        font-size: 15px;
        font-weight: 800;
        color: #3b82f6;
    }}

    /* 5. 마이크 녹음 상태 안내 카드 */
    .mic-guide-card {{
        background: linear-gradient(to right, #fff5f5, #ffffff);
        border: 1px solid #fecaca;
        border-left: 5px solid #ef4444;
        border-radius: 12px;
        padding: 15px 16px;
        margin-top: 8px;
        margin-bottom: 14px;
        box-shadow: 0 2px 8px rgba(239, 68, 68, 0.08);
        word-break: keep-all;
    }}

    /* 6. 로그인 전 웰컴 카드 */
    .login-card {{
        background: #ffffff;
        border: 1px solid #e2e8f0;
        border-radius: 16px;
        padding: 30px 20px;
        text-align: center;
        box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.05);
        margin-top: 15px;
        word-break: keep-all;
    }}

    /* 7. 팝업 내부 단계별 박스 스타일 */
    .step-guide-box {{
        background: #f8fafc;
        border: 1px solid #e2e8f0;
        border-left: 4px solid #3b82f6;
        border-radius: 10px;
        padding: 12px 15px;
        margin-bottom: 10px;
        font-size: 14px;
        color: #334155;
        line-height: 1.5;
        word-break: keep-all;
    }}

    /* 8. 버튼 및 탭 세련되게 다듬기 */
    .stButton > button {{
        border-radius: 10px !important;
        font-weight: 700 !important;
        padding: 0.5rem 1rem !important;
        transition: all 0.2s ease !important;
    }}
    .stTabs [data-baseweb="tab-list"] {{
        gap: 8px;
    }}
    .stTabs [data-baseweb="tab"] {{
        border-radius: 10px 10px 0 0 !important;
        padding: 10px 16px !important;
        font-weight: 700 !important;
    }}
</style>

<div class="premium-header">
    <div class="premium-badge">✨ AI SMART VOICE WORKSPACE</div>
    <div class="premium-title">🎙️ {APP_TITLE}</div>
    <div class="premium-subtitle">{APP_SUBTITLE}</div>
</div>
"""
st.markdown(premium_css, unsafe_allow_html=True)

# --- [1단계] 구글 OAuth 2.0 권한 및 시크릿 설정 로드 ---
SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive.file',
    'https://www.googleapis.com/auth/calendar.events'
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

# --- [스마트폰 영구 저장소(CookieManager) 및 서버 캐시 초기화] ---
cookie_manager = stx.CookieManager(key="voice_app_cookie_mgr")
all_cookies = cookie_manager.get_all()

@st.cache_resource
def get_flow_store():
    return {}

flow_store = get_flow_store()

# 권한 스코프 누락 시 재로그인 유도
if 'credentials' in st.session_state:
    saved_scopes = st.session_state['credentials'].scopes or []
    if 'https://www.googleapis.com/auth/calendar.events' not in saved_scopes:
        del st.session_state['credentials']

# --- [핵심 해결책 1] OAuth 로그인 직후 들어온 코드(code)를 최우선으로 처리하여 쿠키에 영구 각인 ---
if 'credentials' not in st.session_state and "code" in st.query_params and "state" in st.query_params:
    auth_code = st.query_params["code"]
    state_key = st.query_params["state"]
    try:
        if state_key in flow_store:
            flow = flow_store.pop(state_key)
            flow.fetch_token(code=auth_code)
            creds = flow.credentials
            st.session_state['credentials'] = creds
            st.session_state["logged_out_flag"] = False
            
            # 기존에 저장된 토큰이 있고 새로 발급된 토큰이 없다면 기존 토큰 보존
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
            
            # 발급된 refresh_token을 Base64로 인코딩하여 브라우저 쿠키(365일)와 URL에 동시 저장!
            # 주의: 여기서 즉시 st.rerun()을 부르면 쿠키 저장이 취소되므로 자연스럽게 아래로 흘려보냅니다.
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
            st.warning("로그인 대기 시간이 지났습니다. 아래 버튼을 눌러 다시 로그인해 주세요.")
            st.query_params.clear()
            if st.button("🔄 다시 로그인 화면으로 이동"):
                st.rerun()
            st.stop()
    except Exception as e:
        st.error(f"인증 처리 중 오류가 발생했습니다: {e}")
        if st.button("🔄 다시 로그인 시도"):
            st.query_params.clear()
            st.rerun()
        st.stop()

# --- [핵심 해결책 2] 앱을 닫았다 열었을 때 쿠키 컴포넌트가 준비될 때까지 0.5초 대기 후 100% 복구 ---
if 'credentials' not in st.session_state and not st.session_state.get("logged_out_flag"):
    existing_cookies = all_cookies or {}
    raw_rt_cookie = existing_cookies.get("voice_rt", "")
    raw_rt_param = st.query_params.get("rt", "")
    encoded_rt = raw_rt_cookie or raw_rt_param or st.session_state.get("saved_rt_encoded", "")

    # 앱을 방금 켜서 쿠키가 아직 도착하지 않은 첫 순간(0.5초)에는 성급히 로그인 창을 띄우지 않고 기다림!
    if not encoded_rt and not st.session_state.get("cookie_sync_checked"):
        st.session_state["cookie_sync_checked"] = True
        st.info("🔄 스마트폰에 저장된 자동 로그인 정보를 불러오는 중입니다...")
        time.sleep(0.6)
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
            st.session_state['credentials'] = auto_creds
            st.session_state["saved_rt_encoded"] = encoded_rt
            if "rt" not in st.query_params:
                st.query_params["rt"] = encoded_rt
        except Exception:
            if "rt" in st.query_params:
                del st.query_params["rt"]

# --- [API 키(ak) 영구 복구 (쿠키 우선 확인 -> URL 파라미터 확인)] ---
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

# --- [2단계] 쿠키와 URL 어디에도 로그인 정보가 없을 때만 로그인 화면 표시 ---
if 'credentials' not in st.session_state:
    if len(flow_store) > 100:
        flow_store.clear()
        
    flow = Flow.from_client_config(client_config, scopes=SCOPES, redirect_uri=REDIRECT_URI)
    auth_url, state_key = flow.authorization_url(
        prompt='consent',
        access_type='offline',
        include_granted_scopes='true'
    )
    flow_store[state_key] = flow
    
    st.markdown(
        f"""
        <div class="login-card">
            <div style="font-size: 42px; margin-bottom: 10px;">🔐</div>
            <h3 style="margin: 0 0 12px 0; color: #1e293b; font-weight: 800;">구글 워크스페이스 안전 연동</h3>
            <p style="color: #64748b; font-size: 14px; margin-bottom: 24px; line-height: 1.7;">
                서비스를 시작하려면 본인의 구글 계정으로 로그인해 주세요.<br>
                한 번 로그인하면 <b>앱을 닫아도 로그아웃 전까지 계정이 영구 유지</b>되며,<br>
                모든 데이터는 <b>본인의 구글 시트와 캘린더에만 독립적으로 저장</b>됩니다.
            </p>
            <a href="{auth_url}" target="_blank" rel="noopener noreferrer" style="
                display: inline-block;
                background: linear-gradient(135deg, #2563eb 0%, #1d4ed8 100%);
                color: white;
                padding: 14px 24px;
                text-decoration: none;
                border-radius: 12px;
                font-size: 15px;
                font-weight: 700;
                box-shadow: 0 4px 14px rgba(37, 99, 235, 0.35);
                ">
                🚀 내 Google 계정으로 시작하기 (새 창)
            </a>
        </div>
        """,
        unsafe_allow_html=True
    )
    st.stop()

# --- [로그인 상태 유지 보강] 메인 화면 진입 시 쿠키가 비어있다면 다시 한번 구워주기 ---
if 'credentials' in st.session_state and st.session_state.get("saved_rt_encoded"):
    existing_cookies = all_cookies or {}
    if not existing_cookies.get("voice_rt"):
        cookie_manager.set(
            "voice_rt",
            st.session_state["saved_rt_encoded"],
            expires_at=datetime.now() + timedelta(days=COOKIE_EXPIRY_DAYS),
            key="reinforce_rt_cookie"
        )

# --- [3단계] 대한민국 표준시(KST) 기준 일일 무료 사용량 추적기 초기화 ---
now_kst = datetime.now(KST)
today_str = now_kst.strftime("%Y-%m-%d")

if st.session_state.get("usage_date") != today_str:
    st.session_state["usage_date"] = today_str
    st.session_state["daily_used_seconds"] = 0.0
    st.session_state["daily_request_count"] = 0

if "last_processed_hash" not in st.session_state:
    st.session_state["last_processed_hash"] = None

# --- [4단계] API 키 영구 저장 함수, 팝업창(Dialog) 및 구글 연동 보조 함수들 ---
def save_api_key_permanently(raw_key):
    """입력된 API 키를 브라우저 쿠키(365일), 세션, URL에 동시에 저장합니다."""
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

@st.dialog("🔑 무료 Gemini API 키 1분 발급 절차 안내", width="large")
def show_api_guide_modal():
    st.markdown(
        """
        <div style="margin-bottom: 14px; font-size: 14px; color: #475569; line-height: 1.6; word-break: keep-all;">
            AI 음성 변환 기능을 무료로 이용하기 위해 본인의 <b>구글 AI 열쇠(API 키)</b>를 발급받는 절차입니다.<br>
            <b>(최초 1회만 등록하면 앱을 닫아도 영구 유지됩니다 / 평생 무료)</b>
        </div>
        <div style="text-align: center; margin-bottom: 18px;">
            <a href="https://aistudio.google.com/app/apikey" target="_blank" rel="noopener noreferrer" style="
                display: inline-block;
                background: linear-gradient(135deg, #2563eb 0%, #1d4ed8 100%);
                color: white;
                padding: 12px 20px;
                text-decoration: none;
                border-radius: 10px;
                font-size: 14px;
                font-weight: 700;
                box-shadow: 0 4px 12px rgba(37, 99, 235, 0.25);
            ">
                👉 1. 여기를 눌러 Google AI Studio 열기 (새 창)
            </a>
        </div>
        <div class="step-guide-box">
            <b>[1단계] 구글 로그인 및 약관 동의</b><br>
            위 파란색 버튼을 눌러 열린 사이트에서 본인의 <b>구글 계정(Gmail)</b>으로 로그인합니다.<br>
            처음 접속 시 약관 동의 창이 뜨면 체크박스에 체크 후 파란색 <b>[Continue(계속)]</b> 버튼을 누릅니다.
        </div>
        <div class="step-guide-box">
            <b>[2단계] API 키 생성 버튼 클릭</b><br>
            화면 좌측 또는 우측 상단에 있는 파란색 <b>[Get API key]</b> 또는 <b>[Create API key (API 키 만들기)]</b> 버튼을 클릭합니다.
        </div>
        <div class="step-guide-box">
            <b>[3단계] 새 프로젝트에서 키 발급</b><br>
            팝업창이 나타나면 <b>[Create API key in new project (새 프로젝트에서 API 키 만들기)]</b>를 클릭합니다.
        </div>
        <div class="step-guide-box">
            <b>[4단계] 발급된 키 복사 및 아래 칸에 붙여넣기</b><br>
            잠시 후 <code>AIzaSy...</code>로 시작하는 긴 영어 암호가 나타나면 우측의 <b>[Copy(복사)]</b> 버튼을 누른 뒤,<br>
            <b>바로 아래 입력칸에 붙여넣고 저장</b>을 누르면 완료됩니다!
        </div>
        """,
        unsafe_allow_html=True
    )
    st.markdown("---")
    modal_key = st.text_input("👇 발급받아 복사한 API 키를 여기에 바로 붙여넣으세요:", type="password", key="modal_api_input")
    if st.button("✅ API 키 영구 저장 및 시작하기", type="primary", use_container_width=True):
        if modal_key.strip():
            save_api_key_permanently(modal_key)
        else:
            st.warning("⚠️ 먼저 복사한 API 키를 입력칸에 붙여넣어 주세요.")

def get_valid_credentials():
    creds = st.session_state['credentials']
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            st.session_state['credentials'] = creds
        except Exception as refresh_err:
            st.warning(f"인증 토큰 자동 갱신 중 확인 필요: {refresh_err}")
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
        'data': ('metadata', json.dumps(metadata), 'application/json'),
        'file': ('audio', audio_bytes, mime_type)
    }
    try:
        res = authed_session.post(
            "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart",
            files=files
        )
        if res.status_code in [200, 201]:
            file_id = res.json().get('id')
            return f"https://drive.google.com/file/d/{file_id}/view"
        return ""
    except Exception:
        return ""

def download_audio_from_drive(creds, drive_url):
    match = re.search(r'/d/([a-zA-Z0-9_-]+)', drive_url)
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
            sheet.update(range_name='A1:H1', values=[SHEET_HEADERS])
    except gspread.exceptions.SpreadsheetNotFound:
        new_spreadsheet = client_gspread.create(SHEET_NAME)
        sheet = new_spreadsheet.sheet1
        sheet.append_row(SHEET_HEADERS)
    sheet.append_row(row_data, value_input_option='RAW')

# --- [5단계] 상단 상태 바, 무료 사용량 대시보드 및 API 키 스마트 숨김 UI ---
if st.session_state["saved_api_key"]:
    col_status, col_key_btn, col_logout = st.columns([3.2, 1.1, 1.1])
    with col_status:
        st.success("✅ 구글 계정 및 AI 키 영구 자동 유지 활성화됨")
    with col_key_btn:
        if st.button("🔑 API 키 변경", use_container_width=True):
            st.session_state["saved_api_key"] = ""
            st.session_state["key_cleared_flag"] = True
            if "ak" in st.query_params:
                del st.query_params["ak"]
            try:
                cookie_manager.delete("voice_ak", key="del_ak_cookie")
            except Exception:
                pass
    with col_logout:
        if st.button("🚪 로그아웃", use_container_width=True):
            if 'credentials' in st.session_state:
                del st.session_state['credentials']
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
else:
    col_status, col_logout = st.columns([4, 1.2])
    with col_status:
        st.success("✅ 내 구글 계정 연동 완료! (아래에서 AI 키를 1회 등록해 주세요)")
    with col_logout:
        if st.button("🚪 로그아웃", use_container_width=True):
            if 'credentials' in st.session_state:
                del st.session_state['credentials']
            st.session_state["saved_rt_encoded"] = ""
            st.session_state["logged_out_flag"] = True
            st.query_params.clear()
            try:
                cookie_manager.delete("voice_rt", key="del_rt_only")
            except Exception:
                pass

with st.expander("📲 휴대폰 바탕화면(홈 화면)에 추가하여 편리하게 쓰는 법"):
    st.markdown(
        """
        <div style="font-size: 14px; color: #334155; line-height: 1.7; word-break: keep-all;">
            • <b>영구 자동 로그인 및 키 유지 적용됨:</b><br>
            이제 한 번만 구글 로그인과 API 키 입력을 완료하면 브라우저 내부에 안전하게 기억되어, <b>앱을 완전히 닫았다가 다시 열어도 로그인이나 키 입력 창이 다시 나오지 않습니다!</b><br>
            • 휴대폰 크롬(또는 사파리) 메뉴에서 <b>[홈 화면에 추가]</b>를 해두시면 일반 앱처럼 1초 만에 바로 마이크 화면을 여실 수 있습니다.
        </div>
        """,
        unsafe_allow_html=True
    )

used_mins = st.session_state["daily_used_seconds"] / 60.0
remaining_mins = max(0.0, DAILY_FREE_LIMIT_MINUTES - used_mins)
req_count = st.session_state["daily_request_count"]

st.markdown(
    f"""
    <div class="usage-card">
        <div class="usage-title">📊 오늘의 무료 AI 음성 사용 현황 ({today_str})</div>
        <div class="usage-grid">
            <div class="usage-item">
                <div class="usage-label">1회 최대 분석 한도</div>
                <div class="usage-value">최대 {MAX_SINGLE_FREE_MINUTES}분</div>
            </div>
            <div class="usage-item">
                <div class="usage-label">오늘 누적 사용 시간</div>
                <div class="usage-value">{used_mins:.1f}분 <span style="font-size:12px; color:#64748b; font-weight:500;">/ {DAILY_FREE_LIMIT_MINUTES}분</span></div>
            </div>
            <div class="usage-item">
                <div class="usage-label">남은 무료 여유 시간</div>
                <div class="usage-value" style="color:#10b981;">약 {remaining_mins:.1f}분</div>
            </div>
            <div class="usage-item">
                <div class="usage-label">오늘 무료 분석 횟수</div>
                <div class="usage-value">{req_count}회 <span style="font-size:12px; color:#64748b; font-weight:500;">/ {DAILY_FREE_LIMIT_REQUESTS}회</span></div>
            </div>
        </div>
    </div>
    """,
    unsafe_allow_html=True
)
st.progress(min(1.0, used_mins / DAILY_FREE_LIMIT_MINUTES))

# API 키 미등록 시 안내 카드 + 팝업 버튼 + 저장 버튼 제공
if not st.session_state["saved_api_key"]:
    st.markdown(
        """
        <div style="background:#eff6ff; border:1px solid #bfdbfe; border-radius:12px; padding:16px; margin-bottom:10px; line-height:1.6; word-break:keep-all;">
            <b style="color:#1e40af; font-size:15px;">🔑 최초 1회 무료 Gemini API 키 등록이 필요합니다</b><br>
            <span style="color:#3b82f6; font-size:13.5px;">
                한 번만 입력하고 저장하면 이 입력창은 화면에서 영구적으로 숨겨지며,<br>
                앱을 닫았다가 다시 켜도 자동으로 유지됩니다.
            </span>
        </div>
        """,
        unsafe_allow_html=True
    )
    
    col_guide_btn, col_direct_link = st.columns([1.5, 1.5])
    with col_guide_btn:
        if st.button("📖 처음이신가요? 무료 API 키 발급 절차 보기 (팝업)", type="primary", use_container_width=True):
            show_api_guide_modal()
    with col_direct_link:
        st.markdown(
            '<a href="https://aistudio.google.com/app/apikey" target="_blank" rel="noopener noreferrer" style="display:block; text-align:center; background:#ffffff; border:1px solid #2563eb; color:#2563eb; padding:9px 12px; border-radius:10px; font-weight:700; text-decoration:none; font-size:14px;">🔗 Google AI Studio 바로 열기 (새 창)</a>',
            unsafe_allow_html=True
        )
        
    col_input, col_save_btn = st.columns([3.5, 1.2])
    with col_input:
        key_input = st.text_input("[1단계] 발급받은 무료 Gemini API 키(AIza...로 시작)를 붙여넣으세요:", type="password")
    with col_save_btn:
        st.write("")
        st.write("")
        save_key_clicked = st.button("💾 키 영구 저장", type="primary", use_container_width=True)
        
    if key_input.strip() or save_key_clicked:
        if key_input.strip():
            save_api_key_permanently(key_input)

user_api_key = st.session_state["saved_api_key"]

save_audio_toggle = st.checkbox(AUDIO_CHECKBOX_LABEL, value=True)

# --- [6단계] 탭 구성: 탭1(음성 입력/변환) & 탭2(저장된 녹음 리스트 조회/듣기) ---
tab1, tab2 = st.tabs(["🎙️ 실시간 녹음 및 파일 변환", "📁 내 저장된 녹음 리스트 조회 및 바로 듣기"])

with tab1:
    input_method = st.radio("음성 입력 방식을 선택하세요:", ["실시간 녹음", "파일 업로드"], horizontal=True)
    audio_bytes = None
    mime_type = "audio/wav"
    should_process = False

    if input_method == "실시간 녹음":
        st.markdown(
            """
            <div class="mic-guide-card">
                <div style="font-size:15px; font-weight:800; color:#991b1b; margin-bottom:6px;">🎙️ 실시간 녹음 상태 확인 안내</div>
                <div style="font-size:14px; color:#374151; line-height:1.7;">
                    • 아래 <b>검정색 마이크</b>를 클릭하면 ➡️ 즉시 <b style="color:#ef4444;">🔴 빨간색 마이크 (녹음중)</b> 상태로 변합니다.<br>
                    • 말씀을 마친 후 <b style="color:#ef4444;">🔴 빨간색 마이크</b>를 다시 클릭하면 ➡️ <b>녹음중이 사라지며 즉시 자동 변환</b>됩니다!
                </div>
            </div>
            """,
            unsafe_allow_html=True
        )
        
        audio_bytes = audio_recorder(
            text="👉 마이크 클릭 시 시작 (🔴빨간색 = 녹음중 / 다시 클릭 시 녹음종료 및 자동변환)",
            recording_color="#ef4444",
            neutral_color="#1e293b",
            icon_size="2x"
        )
        
        if audio_bytes:
            st.success("✅ 녹음이 완료되었습니다! 아래에서 녹음된 음성을 확인하실 수 있습니다.")
            st.audio(audio_bytes, format="audio/wav")
            
        current_audio_hash = hashlib.md5(audio_bytes).hexdigest() if audio_bytes else None
        if audio_bytes is not None and current_audio_hash != st.session_state["last_processed_hash"]:
            should_process = True

    else:
        st.markdown(
            """
            <div style="background:#f8fafc; border:1px solid #cbd5e1; border-left:5px solid #3b82f6; border-radius:12px; padding:15px 18px; margin-top:8px; margin-bottom:14px; word-break:keep-all;">
                <div style="font-size:15px; font-weight:800; color:#1e3a8a; margin-bottom:6px;">📂 녹음 파일 업로드 및 변환 방법</div>
                <div style="font-size:14px; color:#334155; line-height:1.7;">
                    1. 아래 <b>[Browse files]</b> 버튼을 눌러 휴대폰이나 PC에 저장된 음성 파일(mp3, wav, m4a)을 선택하세요.<br>
                    2. 파일이 첨부되면 아래에 나타나는 <b>[🚀 업로드한 파일 변환 및 저장 실행]</b> 버튼을 클릭해야 변환이 시작됩니다.
                </div>
            </div>
            """,
            unsafe_allow_html=True
        )
        uploaded_file = st.file_uploader("변환할 음성 파일을 선택하세요 (mp3, wav, m4a)", type=['mp3', 'wav', 'm4a'])
        if uploaded_file:
            audio_bytes = uploaded_file.read()
            ext = uploaded_file.name.split(".")[-1].lower()
            mime_map = {"mp3": "audio/mp3", "m4a": "audio/mp4", "wav": "audio/wav"}
            mime_type = mime_map.get(ext, "audio/wav")
            st.audio(audio_bytes, format=mime_type)
            
        current_audio_hash = hashlib.md5(audio_bytes).hexdigest() if audio_bytes else None
        if st.button("🚀 업로드한 파일 변환 및 저장 실행", type="primary", use_container_width=True):
            should_process = True

    if should_process:
        if not user_api_key:
            st.warning("⚠️ 상단에 본인의 무료 Gemini API 키를 먼저 등록해 주세요.")
        elif not audio_bytes or len(audio_bytes) < 100:
            st.warning("⚠️ 분석할 음성 데이터가 없거나 너무 짧습니다. 다시 녹음하거나 파일을 선택해 주세요.")
        elif used_mins >= DAILY_FREE_LIMIT_MINUTES or req_count >= DAILY_FREE_LIMIT_REQUESTS:
            st.error(f"🛑 오늘의 무료 사용 안전 한도({DAILY_FREE_LIMIT_MINUTES}분 또는 {DAILY_FREE_LIMIT_REQUESTS}회)를 모두 소진했습니다. 내일 다시 이용해 주세요.")
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
                
                with st.spinner("⚡ AI가 음성을 정밀 분석하여 주제별 요약 및 전체 받아쓰기를 수행 중입니다..."):
                    if file_size_mb <= INLINE_LIMIT_MB:
                        response = client_gemini.models.generate_content(
                            model=GEMINI_MODEL,
                            contents=[types.Part.from_bytes(data=audio_bytes, mime_type=mime_type), prompt_text],
                            config=gen_config
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
                                
                            response = client_gemini.models.generate_content(
                                model=GEMINI_MODEL,
                                contents=[uploaded_file_obj, prompt_text],
                                config=gen_config
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
                
                st.success("✨ AI 음성 분석 및 요약 정리 완료!")
                st.markdown(f"### 📌 {summary}")
                st.write(f"**🏷️ 카테고리:** `{category}` | **👤 담당자:** `{assignee}` | **⏰ 약속일시:** `{due_date} {event_time}`")
                st.text_area("📝 상세 회의 주제별 정리 및 전체 받아쓰기 내용", value=details, height=240)
                
                with st.spinner("💾 내 구글 시트 요약 기록 및 캘린더 일정 자동 연동 중..."):
                    creds = get_valid_credentials()
                    calendar_status = add_to_google_calendar(creds, summary, assignee, details, due_date, event_time)
                    
                    if save_audio_toggle:
                        drive_link = upload_audio_to_drive(creds, audio_bytes, mime_type)
                        backup_msg = "원본 음성 드라이브 백업 포함"
                    else:
                        drive_link = "원본 음성 저장 안 함 (텍스트만 기록)"
                        backup_msg = "원본 음성 백업 제외 (텍스트 및 캘린더만 연동)"
                        
                    schedule_display = f"{due_date} {event_time}".strip()
                    
                    row_to_save = [
                        current_time_str, category, assignee, summary,
                        details, schedule_display, calendar_status, drive_link
                    ]
                    save_record_to_sheet(creds, row_to_save)
                    st.info(f"✅ 내 구글 시트(`{SHEET_NAME}`) 저장 완료! ({backup_msg} | 캘린더 상태: {calendar_status})")
                    
            except Exception as e:
                st.error(f"⚠️ 분석 중 확인이 필요합니다 (입력하신 API 키가 정확한지, 혹은 무료 한도를 초과하지 않았는지 확인해 주세요): {e}")

with tab2:
    st.subheader("📁 내 구글 시트에 저장된 녹음 리스트 (조회 및 즉시 듣기)")
    if st.button("🔄 저장 목록 새로고침"):
        st.rerun()
        
    try:
        creds = get_valid_credentials()
        client_gspread = gspread.authorize(creds)
        sheet = client_gspread.open(SHEET_NAME).sheet1
        all_values = sheet.get_all_values()
        
        if len(all_values) <= 1:
            st.info("아직 저장된 녹음 기록이 없습니다. 첫 번째 탭에서 녹음을 진행해 보세요!")
        else:
            data_rows = all_values[1:]
            for idx, row in enumerate(reversed(data_rows)):
                while len(row) < 8:
                    row.append("")
                reg_date, cat, assignee, title, details, sched, cal_status, audio_url = row[:8]
                
                expander_label = f"🗂️ [{cat}] {title} (등록: {reg_date} | 담당: {assignee})"
                with st.expander(expander_label):
                    st.write(f"**⏰ 약속 일시:** {sched if sched else '없음'} | **📅 캘린더 등록 상태:** {cal_status}")
                    st.markdown("**📝 상세 회의 주제별 정리 및 전체 녹음 내용:**")
                    st.text(details)
                    
                    if audio_url and "drive.google.com" in audio_url:
                        st.markdown("---")
                        col1, col2 = st.columns([1, 2])
                        with col1:
                            if st.button("▶️ 앱에서 원본 녹음 바로 듣기", key=f"play_btn_{idx}"):
                                with st.spinner("내 구글 드라이브에서 원본 음성을 불러오는 중입니다..."):
                                    fetched_audio = download_audio_from_drive(creds, audio_url)
                                    if fetched_audio:
                                        st.audio(fetched_audio, format="audio/wav")
                                    else:
                                        st.error("음성 파일을 불러오지 못했습니다. 우측 드라이브 링크를 확인해 주세요.")
                        with col2:
                            st.markdown(f"🔗 [내 구글 드라이브에서 원본 음성 파일 직접 열기]({audio_url})")
                    else:
                        st.caption("🎵 이 기록은 '음성 원본 녹음 여부' 체크 해제 상태로 저장되어 텍스트 요약만 보관되어 있습니다.")
    except gspread.exceptions.SpreadsheetNotFound:
        st.info(f"아직 내 구글 드라이브에 생성된 스프레드시트(`{SHEET_NAME}`)가 없습니다. 첫 녹음을 저장하면 자동으로 생성됩니다!")
    except Exception as list_err:
        st.warning(f"목록을 불러오는 중 일시적인 오류가 발생했습니다: {list_err}")
