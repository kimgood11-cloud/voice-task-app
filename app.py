import streamlit as st
import json
import io
import re
import time
import base64
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import gspread
from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import AuthorizedSession, Request
from google import genai
from google.genai import types
import extra_streamlit_components as stx

# =====================================================================
# [1. 기본 환경 설정]
# =====================================================================
PAGE_TITLE = "AI음성변환"
APP_TITLE = "개인 맞춤형 AI음성변환 시스템"
APP_SUBTITLE = "AI 실시간 요약 · 구글 시트/캘린더 스마트 연동"
AUDIO_CHECKBOX_LABEL = "💾 원본 음성 구글 드라이브 백업"
ANALYZE_BUTTON_LABEL = "🚀 분석실행하기"

IMAGE_MODE_OPTIONS = ["안 함 (음성만)", "📁 사진 첨부하기"]
IMAGE_UPLOAD_TYPES = ["jpg", "jpeg", "png", "webp"]

AUTO_SYNC_USAGE_ON_START = False
LOGIN_LINK_TARGET = "_top"

REDIRECT_URI = "https://public-voice-task.streamlit.app/"
SHEET_NAME = "Voice_Log_DB"
CONFIG_SHEET_TAB = "_App_Config_"
DRIVE_FOLDER_NAME = "AI_음성녹음_보관함"
VAULT_FILE_NAME = "voice_api_key_vault.json"

GEMINI_MODEL_CANDIDATES = [
    "gemini-2.0-flash",
    "gemini-1.5-flash-latest",
    "gemini-1.5-flash"
]

SHORT_AUDIO_TIMEOUT_MS = 25000
LONG_AUDIO_TIMEOUT_MS = 120000

MAX_SINGLE_FREE_MINUTES = 120
DAILY_FREE_LIMIT_MINUTES = 600
DAILY_FREE_LIMIT_REQUESTS = 500
INLINE_LIMIT_MB = 18

SHEET_HEADERS = [
    "등록일시", "카테고리", "담당자", "요약(큰타이틀)", 
    "상세내용(주제별정리/전체녹음)", "약속일시", "캘린더등록", "음성링크"
]

DEFAULT_EVENT_HOURS = 1
TIMEZONE = "Asia/Seoul"
KST = ZoneInfo(TIMEZONE)

# =====================================================================
# [2. AI 프롬프트 생성 함수]
# =====================================================================
def build_gemini_prompt(current_time_str, weekday_korean, today_date_str, has_image=False):
    image_instruction = ""
    if has_image:
        image_instruction = """
       - [첨부 사진/이미지 판독 및 사양 기록]: 함께 첨부된 사진이나 이미지(제품 사진, 사양서, 화이트보드 필기, 서류 등)에 나타난 텍스트, 표, 수치, 제품 사양 및 시각적 특징을 정밀하게 판독하여 음성 내용과 함께 [파트 1]에 상세히 통합 기록해.
        """

    prompt = f"""
    현재 대한민국 기준 시각은 {current_time_str} ({weekday_korean})이야.
    첨부된 음성(30분 이상의 장시간 회의, 상담, 경찰 조사, 여론조사 또는 메모 포함){'과 첨부된 사진/이미지' if has_image else ''}를 끝까지 주의 깊게 확인하고, 아래 규칙에 맞춰 반드시 JSON 형식으로만 출력해 줘.

    [작성 및 분류 상세 규칙]
    1. Category (카테고리 분류): 반드시 '일정', '할일', '아이디어', '기록' 중 하나로만 분류해.
    2. Assignee (담당자/대상자): 언급된 이름, 직급, 업체명을 추출해. 없으면 '미지정'.
    3. Summary: 전체 내용을 대표하는 제목 한 줄. 맨 앞에 [{today_date_str}]를 붙여줘.
    4. Details:
       - [파트 1: 주제별 다중 요약 및 문답(Q&A) 정리] 주제별/시간별 핵심 정리. 2인 이상 질의응답 시 '질문(Q)과 답변(A)' 구조로 분리. 진술 속 과거 사건 일시는 이곳에 기록.{image_instruction}
       - [파트 2: 전체 녹음 원본 받아쓰기] 처음부터 끝까지 전체 STT 기재.
    5. DueDate & EventTime:
       - [중요] 과거 사건 일시는 캘린더 등록 날짜에서 제외할 것!
       - 현재 시각({current_time_str}) 이후로 향후 예정 출석, 미팅, 마감 등 실제 미래 스케줄만 추출(YYYY-MM-DD, HH:MM). 없으면 빈칸("").

    [출력 JSON 규격]
    {{
        "Category": "일정, 할일, 아이디어, 기록 중 하나",
        "Assignee": "담당자/업체명 (없으면 '미지정')",
        "Summary": "[{today_date_str}] 핵심 타이틀",
        "Details": "[파트 1: 다중 요약 및 문답 / 사진 사양]\\n...\\n\\n[파트 2: 전체 받아쓰기]\\n...",
        "DueDate": "YYYY-MM-DD",
        "EventTime": "HH:MM"
    }}
    """
    return prompt

# =====================================================================
# [3. CSS 설정 (모바일 50:50 비율 강제 고정 적용)]
# =====================================================================
st.set_page_config(page_title=PAGE_TITLE, page_icon="🎙️", layout="wide")

premium_css = f"""
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="theme-color" content="#1e1b4b">
<style>
    /* 모바일 환경에서 컬럼이 세로로 떨어지지 않게 가로 50:50 배치 강제 고정 */
    @media (max-width: 768px) {{
        div[data-testid="stHorizontalBlock"] {{
            flex-direction: row !important;
            flex-wrap: nowrap !important;
            gap: 8px !important;
        }}
        div[data-testid="stHorizontalBlock"] > div[data-testid="column"] {{
            width: 50% !important;
            flex: 1 1 50% !important;
            min-width: 0 !important;
        }}
    }}

    @keyframes slideUpFade {{
        0% {{ opacity: 0; transform: translateY(20px); }}
        100% {{ opacity: 1; transform: translateY(0); }}
    }}
    @keyframes pulse {{
        0% {{ box-shadow: 0 0 0 0 rgba(0, 0, 0, 0.4); }}
        70% {{ box-shadow: 0 0 0 8px rgba(0, 0, 0, 0); }}
        100% {{ box-shadow: 0 0 0 0 rgba(0, 0, 0, 0); }}
    }}

    html, body, [class*="css"] {{ font-family: -apple-system, BlinkMacSystemFont, 'Malgun Gothic', system-ui, sans-serif; }}
    #MainMenu, header, footer, [data-testid="stHeader"], [data-testid="stToolbar"], [data-testid="stDecoration"], [data-testid="stStatusWidget"] {{ visibility: hidden !important; display: none !important; }}
    .block-container {{ padding-top: 0.8rem !important; padding-bottom: 2rem !important; padding-left: 0.8rem !important; padding-right: 0.8rem !important; max-width: 820px !important; }}
    
    .premium-header {{ 
        animation: slideUpFade 0.6s ease-out forwards;
        background: linear-gradient(135deg, #1e1b4b 0%, #312e81 50%, #4338ca 100%); color: #ffffff; padding: 18px 14px; border-radius: 16px; box-shadow: 0 8px 20px -5px rgba(49, 46, 129, 0.35); margin-bottom: 15px; border: 1px solid rgba(255, 255, 255, 0.12); 
    }}
    .premium-badge {{ display: inline-block; background: rgba(255, 255, 255, 0.18); color: #e0e7ff; font-size: 11px; font-weight: 700; padding: 3px 8px; border-radius: 20px; margin-bottom: 6px; letter-spacing: 0.5px; }}
    .premium-title {{ font-size: 18px !important; font-weight: 800 !important; margin: 0 0 4px 0 !important; line-height: 1.3 !important; letter-spacing: -0.5px !important; }}
    .premium-subtitle {{ font-size: 12px; color: #c7d2fe; margin: 0; font-weight: 400; line-height: 1.4; word-break: keep-all; }}
    
    .usage-card {{ 
        animation: slideUpFade 0.6s ease-out 0.1s forwards; opacity: 0;
        background: #ffffff; border: 1px solid #e2e8f0; border-radius: 14px; padding: 14px; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.04); margin-bottom: 12px; 
    }}
    .usage-title {{ font-size: 14px; font-weight: 800; color: #1e293b; margin-bottom: 10px; }}
    .usage-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }}
    .usage-item {{ background: #f8fafc; border-radius: 10px; padding: 10px; border: 1px solid #f1f5f9; transition: transform 0.2s; }}
    .usage-item:active {{ transform: scale(0.97); }}
    .usage-label {{ font-size: 11px; color: #64748b; font-weight: 600; margin-bottom: 3px; }}
    .usage-value {{ font-size: 14px; font-weight: 800; color: #2563eb; }}
    
    .login-card {{ 
        animation: slideUpFade 0.6s ease-out 0.1s forwards; opacity: 0;
        background: #ffffff; border: 2px solid #e2e8f0; border-radius: 16px; padding: 22px 18px; text-align: center; box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.05); margin-bottom: 12px; 
    }}
    .mic-guide-card {{ 
        animation: slideUpFade 0.6s ease-out 0.2s forwards; opacity: 0;
        background: #f8fafc; border: 1px solid #cbd5e1; border-left: 5px solid #000000; border-radius: 12px; padding: 12px 14px; margin-top: 6px; margin-bottom: 12px; word-break: keep-all; transition: all 0.3s;
    }}
    .mic-guide-card:hover {{ transform: translateY(-2px); box-shadow: 0 4px 12px rgba(0,0,0,0.05); }}
    
    .pulse-mic {{ animation: pulse 2s infinite; }}

    [data-testid="stAudioInput"] {{ border: 2px solid #000000 !important; border-radius: 14px !important; padding: 10px !important; background-color: #ffffff !important; transition: all 0.3s; }}
    
    div.stButton > button[kind="primary"], [data-testid="stBaseButton-primary"], div.stButton > button[kind="secondary"] {{
        background: linear-gradient(135deg, #4338ca 0%, #6366f1 50%, #818cf8 100%) !important;
        color: #ffffff !important;
        border: none !important;
        border-radius: 12px !important;
        box-shadow: 0 8px 16px -4px rgba(67, 56, 202, 0.5), 0 3px 5px -1px rgba(0, 0, 0, 0.05) !important;
        font-weight: 800 !important;
        padding: 0.6rem 1rem !important;
        transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1) !important;
    }}
    div.stButton > button:active {{
        transform: scale(0.96) !important;
        box-shadow: 0 4px 8px -2px rgba(67, 56, 202, 0.4) !important;
    }}
</style>
"""
st.markdown(premium_css, unsafe_allow_html=True)

# =====================================================================
# [4. 다중 사용자 쿠키 세션 처리]
# =====================================================================
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
    st.error("⚠️ `.streamlit/secrets.toml` 설정 파일 누락")
    st.stop()

cookie_manager = stx.CookieManager(key="cm_mgr")
all_cookies = cookie_manager.get_all() or {}

@st.cache_resource
def get_flow_store():
    return {}
flow_store = get_flow_store()

if "user_vault" not in st.session_state:
    st.session_state["user_vault"] = {
        "rt_encoded": "",
        "ak_encoded": "",
        "drive_folder_id": "",
        "logged_out": False
    }

if "api_popup_shown" not in st.session_state:
    st.session_state["api_popup_shown"] = False

# 1. 구글 로그인 직후 콜백 처리
if "credentials" not in st.session_state and "code" in st.query_params:
    auth_code = st.query_params["code"]
    state_key = st.query_params.get("state", "")
    try:
        flow = flow_store.pop(state_key, None)
        if flow is None:
            flow = Flow.from_client_config(client_config, scopes=SCOPES, redirect_uri=REDIRECT_URI)
        flow.fetch_token(code=auth_code)
        creds = flow.credentials
        final_rt = creds.refresh_token
        
        st.session_state["credentials"] = creds
        st.session_state["logged_out_flag"] = False
        st.session_state["user_vault"]["logged_out"] = False
        st.session_state["just_logged_in"] = True
        
        if final_rt:
            encoded_rt = base64.b64encode(final_rt.encode()).decode() if not final_rt.startswith("1//") else final_rt
            st.session_state["saved_rt_encoded"] = encoded_rt
            st.session_state["user_vault"]["rt_encoded"] = encoded_rt
            cookie_manager.set("v_rt", encoded_rt, expires_at=datetime.now() + timedelta(days=365), key="set_rt_c")
            
        existing_ak = st.query_params.get("ak", "")
        if existing_ak:
            st.session_state["user_vault"]["ak_encoded"] = existing_ak
            cookie_manager.set("v_ak", existing_ak, expires_at=datetime.now() + timedelta(days=365), key="set_ak_c")
            
        st.query_params.clear()
    except Exception:
        st.query_params.clear()

# 2. 쿠키 기반 인증 복구
if "credentials" not in st.session_state and not st.session_state.get("logged_out_flag") and not st.session_state["user_vault"]["logged_out"]:
    encoded_rt = all_cookies.get("v_rt", "") or st.query_params.get("rt", "") or st.session_state["user_vault"]["rt_encoded"]
    if encoded_rt:
        try:
            try: decoded_rt = base64.b64decode(encoded_rt.encode()).decode()
            except Exception: decoded_rt = encoded_rt
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
            st.session_state["user_vault"]["rt_encoded"] = encoded_rt
            if not all_cookies.get("v_rt", ""):
                cookie_manager.set("v_rt", encoded_rt, expires_at=datetime.now() + timedelta(days=365), key="set_rt_c_r")
        except Exception:
            pass

is_logged_in = "credentials" in st.session_state

# =====================================================================
# [화면 상단 UI 렌더링]
# =====================================================================
st.markdown(f"""
<div class="premium-header">
    <div class="premium-badge">✨ AI VOICE WORKSPACE</div>
    <div class="premium-title">🎙️ {APP_TITLE}</div>
    <div class="premium-subtitle">{APP_SUBTITLE}</div>
</div>
""", unsafe_allow_html=True)

if st.session_state.get("just_logged_in"):
    st.info("✅ **로그인이 성공적으로 완료되었습니다!**\n\n현재 화면은 로그인을 위한 임시 브라우저 창입니다. 화면 상단 왼쪽의 **`X` 버튼**을 눌러 이 창을 완전히 닫은 후, 바탕화면의 앱 아이콘을 다시 실행해 주시면 꽉 찬 화면으로 쾌적하게 이용하실 수 있습니다.", icon="💡")
    st.stop()

if not is_logged_in:
    if len(flow_store) > 100: flow_store.clear()
    flow = Flow.from_client_config(client_config, scopes=SCOPES, redirect_uri=REDIRECT_URI)
    auth_url, state_key = flow.authorization_url(prompt="consent", access_type="offline", include_granted_scopes="true")
    flow_store[state_key] = flow
    st.markdown("""
        <div class="login-card">
            <h4 style="margin:0 0 8px 0;">🔐 구글 워크스페이스 연동</h4>
            <p style="font-size:13px; color:#475569; margin:0;">각자의 구글 계정으로 로그인하여 사용하세요.</p>
        </div>
    """, unsafe_allow_html=True)
    st.link_button("🚀 구글 로그인 시작 (클릭)", auth_url, type="primary", use_container_width=True)
    st.stop()

def get_valid_credentials():
    creds = st.session_state.get("credentials")
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            st.session_state["credentials"] = creds
        except Exception:
            pass
    return creds

# =====================================================================
# [5. 개인별 구글 드라이브/시트 조회 로직]
# =====================================================================
def get_or_create_drive_folder(creds, folder_name=DRIVE_FOLDER_NAME):
    if st.session_state["user_vault"].get("drive_folder_id"):
        return st.session_state["user_vault"]["drive_folder_id"]
    authed_session = AuthorizedSession(creds)
    query = f"name = '{folder_name}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
    try:
        res = authed_session.get("https://www.googleapis.com/drive/v3/files", params={"q": query, "fields": "files(id, name)", "pageSize": 1}, timeout=5)
        if res.status_code == 200:
            files = res.json().get("files", [])
            if files:
                folder_id = files[0]["id"]
                st.session_state["user_vault"]["drive_folder_id"] = folder_id
                return folder_id
        folder_metadata = {"name": folder_name, "mimeType": "application/vnd.google-apps.folder"}
        create_res = authed_session.post("https://www.googleapis.com/drive/v3/files", json=folder_metadata, timeout=5)
        if create_res.status_code in [200, 201]:
            folder_id = create_res.json().get("id")
            st.session_state["user_vault"]["drive_folder_id"] = folder_id
            return folder_id
    except Exception:
        pass
    return None

def save_api_key_to_cloud_vault(creds, encoded_key):
    try:
        client_gspread = gspread.authorize(creds)
        try:
            spreadsheet = client_gspread.open(SHEET_NAME)
            sheet1 = spreadsheet.sheet1
        except gspread.exceptions.SpreadsheetNotFound:
            spreadsheet = client_gspread.create(SHEET_NAME)
            sheet1 = spreadsheet.sheet1
            
        try: sheet1.update(range_name="A1:I1", values=[SHEET_HEADERS + [encoded_key]])
        except Exception:
            try: sheet1.update(range_name="I1", values=[[encoded_key]])
            except Exception: pass
            
        try: ws = spreadsheet.worksheet(CONFIG_SHEET_TAB)
        except gspread.exceptions.WorksheetNotFound:
            ws = spreadsheet.add_worksheet(title=CONFIG_SHEET_TAB, rows=10, cols=2)
            try: ws.hide()
            except Exception: pass
        ws.update(range_name="A1:B1", values=[["GEMINI_API_KEY", encoded_key]])
    except Exception:
        pass

    try:
        authed_session = AuthorizedSession(creds)
        folder_id = get_or_create_drive_folder(creds, DRIVE_FOLDER_NAME)
        q_str = f"name = '{VAULT_FILE_NAME}' and trashed = false"
        if folder_id: q_str = f"'{folder_id}' in parents and name = '{VAULT_FILE_NAME}' and trashed = false"
        res = authed_session.get("https://www.googleapis.com/drive/v3/files", params={"q": q_str, "fields": "files(id)", "pageSize": 5}, timeout=5)
        if res.status_code == 200:
            for f in res.json().get("files", []):
                authed_session.delete(f"https://www.googleapis.com/drive/v3/files/{f['id']}", timeout=5)
        if encoded_key:
            metadata = {"name": VAULT_FILE_NAME, "mimeType": "application/json"}
            if folder_id: metadata["parents"] = [folder_id]
            vault_payload = json.dumps({"ak": encoded_key, "updated": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")})
            files = {"data": ("metadata", json.dumps(metadata), "application/json"), "file": ("vault", vault_payload.encode("utf-8"), "application/json")}
            authed_session.post("https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart", files=files, timeout=5)
    except Exception:
        pass

def load_api_key_from_cloud_vault(creds):
    try:
        client_gspread = gspread.authorize(creds)
        spreadsheet = client_gspread.open(SHEET_NAME)
        try:
            sheet1 = spreadsheet.sheet1
            row1 = sheet1.row_values(1)
            if len(row1) >= 9 and len(row1[8].strip()) > 10: return row1[8].strip()
        except Exception: pass
        try:
            ws = spreadsheet.worksheet(CONFIG_SHEET_TAB)
            val_b1 = ws.acell("B1").value
            if val_b1 and len(val_b1.strip()) > 10: return val_b1.strip()
        except Exception: pass
    except Exception:
        pass

    try:
        authed_session = AuthorizedSession(creds)
        folder_id = get_or_create_drive_folder(creds, DRIVE_FOLDER_NAME)
        for q_str in [f"'{folder_id}' in parents and name = '{VAULT_FILE_NAME}' and trashed = false" if folder_id else "", f"name = '{VAULT_FILE_NAME}' and trashed = false"]:
            if not q_str: continue
            res = authed_session.get("https://www.googleapis.com/drive/v3/files", params={"q": q_str, "fields": "files(id)", "pageSize": 1}, timeout=5)
            if res.status_code == 200:
                files = res.json().get("files", [])
                if files:
                    file_id = files[0]["id"]
                    content_res = authed_session.get(f"https://www.googleapis.com/drive/v3/files/{file_id}?alt=media", timeout=5)
                    if content_res.status_code == 200:
                        ak_val = content_res.json().get("ak", "").strip()
                        if ak_val: return ak_val
    except Exception:
        pass
    return ""

def sync_today_usage_from_sheet(today_date_str):
    try:
        creds = get_valid_credentials()
        client_gspread = gspread.authorize(creds)
        sheet = client_gspread.open(SHEET_NAME).sheet1
        all_rows = sheet.get_all_values()
        if not all_rows: return 0, 0.0, ""
            
        found_encoded_ak = ""
        if len(all_rows[0]) >= 9 and all_rows[0][8].strip():
            found_encoded_ak = all_rows[0][8].strip()

        if len(all_rows) <= 1: return 0, 0.0, found_encoded_ak
        today_count = 0
        total_chars = 0
        for row in all_rows[1:]:
            if row and row[0].startswith(today_date_str):
                today_count += 1
                if len(row) >= 5: total_chars += len(row[4])
        mins = max(today_count * 0.5, round(total_chars / 350.0, 1)) if today_count > 0 else 0.0
        return today_count, mins, found_encoded_ak
    except Exception:
        return 0, 0.0, ""

now_kst = datetime.now(KST)
today_str = now_kst.strftime("%Y-%m-%d")

if "saved_api_key" not in st.session_state:
    st.session_state["saved_api_key"] = ""

if "daily_request_count" not in st.session_state:
    st.session_state["daily_request_count"] = 0
    st.session_state["daily_used_seconds"] = 0.0

if AUTO_SYNC_USAGE_ON_START and not st.session_state.get("usage_synced_once"):
    st.session_state["usage_synced_once"] = True
    s_count, s_mins, raw_sheet_ak = sync_today_usage_from_sheet(today_str)
    st.session_state["daily_request_count"] = s_count
    st.session_state["daily_used_seconds"] = s_mins * 60.0
    if raw_sheet_ak and not st.session_state.get("key_cleared_flag") and not st.session_state.get("saved_api_key"):
        try:
            decoded_ak = base64.b64decode(raw_sheet_ak.encode()).decode()
            if decoded_ak.startswith("AIza"):
                st.session_state["saved_api_key"] = decoded_ak
                st.session_state["user_vault"]["ak_encoded"] = raw_sheet_ak
                if not all_cookies.get("v_ak", ""):
                    cookie_manager.set("v_ak", raw_sheet_ak, expires_at=datetime.now() + timedelta(days=365), key="set_ak_c_1")
        except Exception:
            pass

if not st.session_state["saved_api_key"] and not st.session_state.get("key_cleared_flag"):
    encoded_ak = all_cookies.get("v_ak", "") or st.query_params.get("ak", "") or st.session_state["user_vault"]["ak_encoded"]
    if not encoded_ak and "credentials" in st.session_state:
        encoded_ak = load_api_key_from_cloud_vault(get_valid_credentials())
    if encoded_ak:
        try:
            decoded_key = base64.b64decode(encoded_ak.encode()).decode()
            st.session_state["saved_api_key"] = decoded_key
            st.session_state["user_vault"]["ak_encoded"] = encoded_ak
            if not all_cookies.get("v_ak", ""):
                cookie_manager.set("v_ak", encoded_ak, expires_at=datetime.now() + timedelta(days=365), key="set_ak_c_2")
        except Exception:
            pass

def save_api_key_permanently(raw_key):
    clean_key = raw_key.strip()
    encoded_val = base64.b64encode(clean_key.encode()).decode()
    st.session_state["saved_api_key"] = clean_key
    st.session_state["key_cleared_flag"] = False
    st.session_state["user_vault"]["ak_encoded"] = encoded_val
    cookie_manager.set("v_ak", encoded_val, expires_at=datetime.now() + timedelta(days=365), key="set_ak_c_perm")
    if "credentials" in st.session_state:
        save_api_key_to_cloud_vault(get_valid_credentials(), encoded_val)

@st.dialog("🎉 환영합니다! 무료 Gemini API 키 등록", width="large")
def show_api_guide_modal():
    st.markdown("<p style='font-size:14px; color:#334155; margin-bottom:10px;'>원활한 AI 분석을 위해 최초 1회 <b>API 키 등록</b>이 필요합니다.<br>발급받은 키는 개인 드라이브 금고에 안전하게 보관됩니다.</p>", unsafe_allow_html=True)
    st.link_button("👉 무료 API 키 발급받기 (새 창 열기)", "https://aistudio.google.com/app/apikey", use_container_width=True)
    modal_key = st.text_input("복사한 키 붙여넣기:", type="password", placeholder="AIzaSy...")
    if st.button("✅ 저장하고 시작하기", type="primary", use_container_width=True):
        if modal_key.strip():
            with st.spinner("안전하게 저장 중..."): 
                save_api_key_permanently(modal_key)
            st.rerun()

if is_logged_in and not st.session_state["saved_api_key"]:
    if not st.session_state.get("api_popup_shown"):
        st.session_state["api_popup_shown"] = True
        show_api_guide_modal()

# =====================================================================
# [50:50 나란히 배치 및 컴포넌트 렌더링]
# =====================================================================
if st.session_state["saved_api_key"]:
    st.success("✅ 구글 & API키 자동유지 됨")
else:
    st.success("✅ 계정 연동됨! 아래 키 등록 요망")

btn_col1, btn_col2 = st.columns(2)

with btn_col1:
    if st.button("🚪 로그아웃", use_container_width=True, key="btn_logout_row"):
        del st.session_state["credentials"]
        st.session_state["saved_api_key"] = ""
        st.session_state["saved_rt_encoded"] = ""
        st.session_state["logged_out_flag"] = True
        st.session_state["key_cleared_flag"] = True
        st.session_state["user_vault"]["rt_encoded"] = ""
        st.session_state["user_vault"]["ak_encoded"] = ""
        st.session_state["user_vault"]["logged_out"] = True
        cookie_manager.delete("v_rt", key="del_rt_btn")
        cookie_manager.delete("v_ak", key="del_ak_btn")
        st.query_params.clear()
        st.rerun()

with btn_col2:
    if st.session_state["saved_api_key"]:
        if st.button("🔑 키 변경", use_container_width=True, key="btn_keychange_row"):
            if "credentials" in st.session_state: save_api_key_to_cloud_vault(get_valid_credentials(), "")
            st.session_state["saved_api_key"] = ""
            st.session_state["key_cleared_flag"] = True
            st.session_state["user_vault"]["ak_encoded"] = ""
            cookie_manager.delete("v_ak", key="del_ak")
            st.session_state["api_popup_shown"] = False
            if "ak" in st.query_params: del st.query_params["ak"]
            st.rerun()
    else:
        if st.button("🔑 키 등록", use_container_width=True, key="btn_keyreg_row"):
            show_api_guide_modal()

used_mins = st.session_state["daily_used_seconds"] / 60.0
st.markdown(f"""
    <div class="usage-card">
        <div class="usage-title">📊 오늘 사용량 데이터 ({today_str})</div>
        <div class="usage-grid">
            <div class="usage-item"><div class="usage-label">1회 최대 분석 한도</div><div class="usage-value">최대 {MAX_SINGLE_FREE_MINUTES}분</div></div>
            <div class="usage-item"><div class="usage-label">오늘 누적 사용 시간</div><div class="usage-value">{used_mins:.1f}분 <span style="font-size:10px; color:#64748b;">/ {DAILY_FREE_LIMIT_MINUTES}분</span></div></div>
            <div class="usage-item"><div class="usage-label">잔여 시간</div><div class="usage-value" style="color:#10b981;">{max(0.0, DAILY_FREE_LIMIT_MINUTES - used_mins):.1f}분</div></div>
            <div class="usage-item"><div class="usage-label">사용 횟수</div><div class="usage-value">{st.session_state["daily_request_count"]}회 <span style="font-size:10px; color:#64748b;">/500</span></div></div>
        </div>
    </div>
""", unsafe_allow_html=True)

# 💡 [버그 수정 완료] NameError 원천 차단을 위해 변수를 전역 공간에 명시적으로 선언합니다.
user_api_key = st.session_state.get("saved_api_key", "")

# =====================================================================
# [6. 메인 입력 구역]
# =====================================================================
BLACK_MIC_BADGE = '<span class="pulse-mic" style="display:inline-flex; align-items:center; justify-content:center; width:22px; height:22px; border-radius:50%; background-color:#000000; color:#ffffff; vertical-align:middle; margin:0 2px;"><svg width="12" height="12" viewBox="0 0 24 24" fill="white"><path d="M12 14c1.66 0 3-1.34 3-3V5c0-1.66-1.34-3-3-3S9 3.34 9 5v6c0 1.66 1.34 3 3 3zm5.91-3c-.49 0-.9.36-.98.85C16.52 14.2 14.47 16 12 16s-4.52-1.8-4.93-4.15c-.08-.49-.49-.85-.98-.85-.61 0-1.09.54-1 1.14.49 3 2.89 5.35 5.91 5.78V20c0 .55.45 1 1 1s1-.45 1-1v-2.08c3.02-.43 5.42-2.78 5.91-5.78.1-.6-.39-1.14-1-1.14z"/></svg></span>'
RED_STOP_BADGE = '<span style="display:inline-flex; align-items:center; justify-content:center; width:22px; height:22px; border-radius:50%; background-color:#ef4444; color:#ffffff; vertical-align:middle; margin:0 2px;"><svg width="10" height="10" viewBox="0 0 24 24" fill="white"><rect x="6" y="6" width="12" height="12" rx="2"/></svg></span>'

input_method = st.radio("입력 방식", ["실시간 녹음", "파일 업로드"], horizontal=True)
save_audio_toggle = st.checkbox(AUDIO_CHECKBOX_LABEL, value=True)

audio_bytes = None
mime_type = "audio/wav"

if input_method == "실시간 녹음":
    st.markdown(f'<div class="mic-guide-card"><span style="font-weight:bold;">🎙️ [1단계] 녹음 (대기 방식)</span><br><span style="font-size:13px; color:#475569;">왼쪽의 {BLACK_MIC_BADGE} <b>마이크</b>를 눌러 시작하고, {RED_STOP_BADGE} <b style="color:#ef4444;">빨간색 정지</b>를 누르면 자동 대기됩니다.</span><br><span style="font-size:12px; color:#ef4444; font-weight:600; display:inline-block; margin-top:4px;">⚠️ 스마트폰 통신 보안상 전화 통화 중에는 마이크 녹음이 차단됩니다.</span></div>', unsafe_allow_html=True)
    recorded_file = st.audio_input("마이크 누르기", key="main_voice_recorder")
    if recorded_file:
        audio_bytes = recorded_file.getvalue()
        st.info("✅ 녹음 대기 중! 사진을 추가하거나 분석을 누르세요.")
else:
    st.markdown('<div class="mic-guide-card"><span style="font-weight:bold;">📂 [1단계] 파일 업로드</span><br><span style="font-size:12px; color:#475569;">30분 이상의 대용량 파일(mp3, wav, m4a)을 지원합니다.</span></div>', unsafe_allow_html=True)
    uploaded_file = st.file_uploader("음성 선택", type=["mp3", "wav", "m4a"], key="main_audio_uploader")
    if uploaded_file:
        audio_bytes = uploaded_file.read()
        mime_type = {"mp3": "audio/mp3", "m4a": "audio/mp4", "wav": "audio/wav"}.get(uploaded_file.name.split(".")[-1].lower(), "audio/wav")
        st.audio(audio_bytes, format=mime_type)
        st.info("✅ 파일 대기 중!")

image_bytes = None
image_mime_type = "image/jpeg"

st.markdown('<div class="mic-guide-card" style="border-left-color:#10b981;"><span style="font-weight:bold; color:#065f46;">📸 [2단계] 사진 첨부 (선택)</span><br><span style="font-size:12px; color:#475569;">회의 자료나 사양서가 있으면 추가하세요. 없으면 건너뜁니다.</span></div>', unsafe_allow_html=True)
img_input_mode = st.radio("사진 첨부", IMAGE_MODE_OPTIONS, horizontal=True)

if img_input_mode == IMAGE_MODE_OPTIONS[1]:
    uploaded_img = st.file_uploader("사진 선택", type=IMAGE_UPLOAD_TYPES, key="main_photo_uploader")
    if uploaded_img:
        image_bytes = uploaded_img.getvalue()
        ext_lower = uploaded_img.name.split(".")[-1].lower() if "." in uploaded_img.name else "jpg"
        image_mime_type = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg"}.get(ext_lower, "image/jpeg")
        st.image(image_bytes, caption="✅ 이미지 대기 중", use_container_width=True)

st.write("")
analyze_clicked = st.button(ANALYZE_BUTTON_LABEL, type="primary", use_container_width=True)

# =====================================================================
# [7. 분석 실행 및 구글 시트 연동]
# =====================================================================
def upload_audio_to_drive(creds, abytes, mtype, ftitle="Voice_Record"):
    authed_session = AuthorizedSession(creds)
    folder_id = get_or_create_drive_folder(creds, DRIVE_FOLDER_NAME)
    ext = "mp3" if "mp3" in mtype else ("m4a" if "mp4" in mtype else "wav")
    metadata = {"name": f"{ftitle}_{datetime.now(KST).strftime('%Y%m%d_%H%M%S')}.{ext}"}
    if folder_id: metadata["parents"] = [folder_id]
    files = {"data": ("metadata", json.dumps(metadata), "application/json"), "file": ("audio", abytes, mtype)}
    try:
        res = authed_session.post("https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart", files=files)
        if res.status_code in [200, 201]: return f"https://drive.google.com/file/d/{res.json().get('id')}/view"
    except Exception: pass
    return ""

def add_to_google_calendar(creds, summary, assignee, details, due_date, event_time):
    if not due_date: return "일정 없음"
    authed_session = AuthorizedSession(creds)
    title = f"[{assignee}] {summary}" if assignee and assignee != "미지정" else summary
    try:
        if event_time:
            s_dt = datetime.strptime(f"{due_date} {event_time}", "%Y-%m-%d %H:%M")
            body = {"summary": title, "description": details, "start": {"dateTime": s_dt.strftime("%Y-%m-%dT%H:%M:00"), "timeZone": TIMEZONE}, "end": {"dateTime": (s_dt + timedelta(hours=DEFAULT_EVENT_HOURS)).strftime("%Y-%m-%dT%H:%M:00"), "timeZone": TIMEZONE}}
        else:
            s_dt = datetime.strptime(due_date, "%Y-%m-%d")
            body = {"summary": title, "description": details, "start": {"date": s_dt.strftime("%Y-%m-%d")}, "end": {"date": (s_dt + timedelta(days=1)).strftime("%Y-%m-%d")}}
        res = authed_session.post("https://www.googleapis.com/calendar/v3/calendars/primary/events", json=body)
        return "✅ 캘린더 등록" if res.status_code in [200, 201] else "⚠️ 오류"
    except Exception: return "⚠️ 형식 오류"

def save_record_to_sheet(creds, row_data):
    client_gspread = gspread.authorize(creds)
    enc_k = base64.b64encode(st.session_state["saved_api_key"].encode()).decode() if st.session_state.get("saved_api_key") else ""
    try:
        sheet = client_gspread.open(SHEET_NAME).sheet1
        if sheet.row_values(1)[:8] != SHEET_HEADERS: sheet.update(range_name="A1:I1", values=[SHEET_HEADERS + [enc_k]])
        elif enc_k: sheet.update(range_name="I1", values=[[enc_k]])
    except Exception:
        sheet = client_gspread.create(SHEET_NAME).sheet1
        sheet.update(range_name="A1:I1", values=[SHEET_HEADERS + [enc_k]])
    sheet.append_row(row_data, value_input_option="RAW")

if analyze_clicked:
    req_count = st.session_state.get("daily_request_count", 0)
    if not user_api_key: st.warning("⚠️ 무료 Gemini API 키를 먼저 등록해 주세요.")
    elif not audio_bytes: st.warning("⚠️ 분석할 음성 데이터가 없습니다.")
    elif used_mins >= DAILY_FREE_LIMIT_MINUTES or req_count >= DAILY_FREE_LIMIT_REQUESTS: st.error("🛑 오늘의 무료 한도를 초과했습니다.")
    else:
        file_size_mb = len(audio_bytes) / (1024 * 1024)
        has_img = image_bytes is not None
        try:
            now = datetime.now(KST)
            ctime = now.strftime("%Y-%m-%d %H:%M:%S")
            wkday = ["월", "화", "수", "목", "금", "토", "일"][now.weekday()] + "요일"
            
            p_text = build_gemini_prompt(ctime, wkday, today_str, has_image=has_img)
            client = genai.Client(api_key=user_api_key.strip(), http_options=types.HttpOptions(timeout=SHORT_AUDIO_TIMEOUT_MS if file_size_mb<=5 and not has_img else LONG_AUDIO_TIMEOUT_MS))
            schema = types.Schema(type=types.Type.OBJECT, properties={"Category": types.Schema(type=types.Type.STRING), "Assignee": types.Schema(type=types.Type.STRING), "Summary": types.Schema(type=types.Type.STRING), "Details": types.Schema(type=types.Type.STRING), "DueDate": types.Schema(type=types.Type.STRING), "EventTime": types.Schema(type=types.Type.STRING)}, required=["Category", "Assignee", "Summary", "Details", "DueDate", "EventTime"])
            config = types.GenerateContentConfig(response_mime_type="application/json", response_schema=schema, temperature=0.1)
            
            with st.spinner("⚡ AI가 정밀 분석 중입니다..."):
                if file_size_mb <= INLINE_LIMIT_MB:
                    plist = [types.Part.from_bytes(data=audio_bytes, mime_type=mime_type)]
                    if has_img: plist.append(types.Part.from_bytes(data=image_bytes, mime_type=image_mime_type))
                    plist.append(p_text)
                    last_err = None
                    for _ in range(2):
                        for m in GEMINI_MODEL_CANDIDATES:
                            try:
                                resp = client.models.generate_content(model=m, contents=plist, config=config)
                                result_data = json.loads(resp.text)
                                break
                            except Exception as e:
                                last_err = e; err_str = str(e).lower()
                                if "503" in err_str or "429" in err_str: time.sleep(0.5); continue
                        else: continue
                        break
                    else: raise last_err
                else:
                    up_file = client.files.upload(file=io.BytesIO(audio_bytes), config=types.UploadFileConfig(mime_type=mime_type))
                    for _ in range(60):
                        if client.files.get(name=up_file.name).state.name == "ACTIVE": break
                        time.sleep(1)
                    plist = [up_file]
                    if has_img: plist.append(types.Part.from_bytes(data=image_bytes, mime_type=image_mime_type))
                    plist.append(p_text)
                    last_err = None
                    for _ in range(2):
                        for m in GEMINI_MODEL_CANDIDATES:
                            try:
                                resp = client.models.generate_content(model=m, contents=plist, config=config)
                                result_data = json.loads(resp.text)
                                break
                            except Exception as e:
                                last_err = e; err_str = str(e).lower()
                                if "503" in err_str or "429" in err_str: time.sleep(0.5); continue
                        else: continue
                        break
                    else: raise last_err
                    try: client.files.delete(name=up_file.name)
                    except Exception: pass
            
            cat, assign, summ, det, due, evt = result_data.get("Category","기록"), result_data.get("Assignee","미지정"), result_data.get("Summary",""), result_data.get("Details",""), result_data.get("DueDate","").strip(), result_data.get("EventTime","").strip()
            
            with st.spinner("💾 클라우드 저장 및 연동 중..."):
                creds = get_valid_credentials()
                cal_stat = add_to_google_calendar(creds, summ, assign, det, due, evt)
                if save_audio_toggle:
                    dlink = upload_audio_to_drive(creds, audio_bytes, mime_type)
                else:
                    dlink = "텍스트만 기록"
                save_record_to_sheet(creds, [ctime, cat, assign, summ, det, f"{due} {evt}".strip(), cal_stat, dlink])
                
                s_count, s_mins, _ = sync_today_usage_from_sheet(today_str)
                st.session_state["daily_request_count"], st.session_state["daily_used_seconds"] = s_count, s_mins * 60.0
            
            st.success("✨ 분석 및 저장 완료!")
            st.write(f"**[{cat}] {summ}** (담당: {assign} / 일정: {due} {evt})")
            st.text_area("📝 상세 요약", value=det, height=250)
        except Exception as e: st.error(f"⚠️ 오류 발생: {e}")

# =====================================================================
# [8. 보관함 구역]
# =====================================================================
st.markdown("<div style='margin:40px 0 20px 0; border-top: 2px dashed #cbd5e1;'></div>", unsafe_allow_html=True)
st.markdown("<div style='font-size:16px; font-weight:800; color:#475569; margin-bottom:12px;'>📦 내 보관함 (과거 기록 및 보고서)</div>", unsafe_allow_html=True)

def download_audio_from_drive(creds, drive_url):
    match = re.search(r"/d/([a-zA-Z0-9_-]+)", drive_url)
    if not match: return None
    try:
        res = AuthorizedSession(creds).get(f"https://www.googleapis.com/drive/v3/files/{match.group(1)}?alt=media")
        if res.status_code == 200: return res.content
    except Exception: pass
    return None

def upload_text_to_drive(creds, text_content, file_name):
    authed_session = AuthorizedSession(creds)
    folder_id = get_or_create_drive_folder(creds, DRIVE_FOLDER_NAME)
    metadata = {"name": file_name, "mimeType": "text/plain"}
    if folder_id: metadata["parents"] = [folder_id]
    try:
        res = authed_session.post("https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart", files={"data": ("metadata", json.dumps(metadata), "application/json"), "file": ("text", text_content, "text/plain")})
        if res.status_code in [200, 201]: return f"https://drive.google.com/file/d/{res.json().get('id')}/view"
    except Exception: pass
    return ""

with st.expander("📁 내 저장된 녹음 리스트 조회"):
    if st.button("📂 저장된 녹음 목록 불러오기 / 새로고침", use_container_width=True):
        st.session_state["show_archive_list"] = True
        
    if st.session_state.get("show_archive_list"):
        try:
            creds = get_valid_credentials()
            all_values = gspread.authorize(creds).open(SHEET_NAME).sheet1.get_all_values()
            if len(all_values) <= 1: st.info("아직 기록이 없습니다.")
            else:
                for idx, row in enumerate(reversed(all_values[1:])):
                    while len(row) < 8: row.append("")
                    r_date, cat, assign, title, det, sched, c_stat, url = row[:8]
                    with st.expander(f"[{cat}] {title} (담당:{assign})"):
                        st.write(f"⏰ 일정: {sched if sched else '없음'} | 캘린더: {c_stat}")
                        st.text(det)
                        if url and "drive.google.com" in url:
                            if st.button("▶️ 바로 듣기", key=f"play_{idx}"):
                                fetched = download_audio_from_drive(creds, url)
                                if fetched: st.audio(fetched, format="audio/wav")
                                else: st.error("파일 로드 실패")
                            st.markdown(f'<a href="{url}" target="_blank">🔗 구글 드라이브 열기</a>', unsafe_allow_html=True)
        except Exception: st.warning("목록 로드 실패")

with st.expander("📊 주간 요약 보고서 자동 생성"):
    st.markdown("<p style='font-size:12px; color:#475569;'>최근 7일간 데이터를 모아 시트에 요약 페이지를 추가합니다.</p>", unsafe_allow_html=True)
    if st.button("📈 이번 주간 보고서 생성", type="primary"):
        try:
            creds = get_valid_credentials()
            spreadsheet = gspread.authorize(creds).open(SHEET_NAME)
            all_rows = spreadsheet.sheet1.get_all_values()
            if len(all_rows) <= 1: st.warning("데이터가 없습니다.")
            else:
                headers = all_rows[0][:8]
                recent_rows = []
                counts = {"일정": 0, "할일": 0, "아이디어": 0, "기록": 0}
                s_ago = datetime.now(KST) - timedelta(days=7)
                for r in [r[:8] for r in all_rows[1:]]:
                    if r:
                        try:
                            if datetime.strptime(r[0], "%Y-%m-%d %H:%M:%S").replace(tzinfo=KST) >= s_ago:
                                recent_rows.append(r)
                                c = r[1] if len(r)>1 and r[1] in counts else "기록"
                                counts[c] += 1
                        except Exception: pass
                st.success(f"✅ 총 {len(recent_rows)}건 집계 완료!")
                report_md = f"# 주간 보고서 ({s_ago.strftime('%Y-%m-%d')} ~)\n- 총 {len(recent_rows)}건\n"
                for r in recent_rows: report_md += f"- [{r[1]}] {r[3]}\n"
                st.text_area("📄 미리보기", value=report_md, height=150)
                
                with st.spinner("시트 추가 및 드라이브 저장 중..."):
                    try:
                        rs_name = f"주간_{datetime.now(KST).strftime('%m월%d일')}"
                        try: ws = spreadsheet.worksheet(rs_name)
                        except Exception:
                            ws = spreadsheet.add_worksheet(title=rs_name, rows=100, cols=10)
                            ws.append_row(["총 건수", str(len(recent_rows))])
                            ws.append_row(headers)
                            for r in recent_rows: ws.append_row(r)
                        durl = upload_text_to_drive(creds, report_md, f"Weekly_{datetime.now(KST).strftime('%Y-%m-%d')}.txt")
                        st.success(f"✨ 시트 전용 탭({rs_name}) 추가 완료!")
                    except Exception: st.warning("저장 중 일부 오류")
        except Exception as e: st.error(f"오류: {e}")
