import streamlit as st
import json
import io
import re
import time
import hashlib
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
# [사용자 수정 가능 변수 구역 1] 기본 환경 설정 (기존 코드 100% 보존)
# =====================================================================
APP_TITLE = "개인 맞춤형 음성 변환 자동화 시스템"
APP_SUBTITLE = "AI 실시간 음성 요약 · 구글 시트 자동 누적 · 구글 캘린더 스마트 연동"
AUDIO_CHECKBOX_LABEL = "💾 음성 원본 녹음 여부 (체크를 하게 되면 음성원본이 저장됩니다)"

REDIRECT_URI = "https://public-voice-task.streamlit.app/"
SHEET_NAME = "Voice_Log_DB"
DRIVE_FOLDER_NAME = "AI_음성녹음_보관함"

GEMINI_MODEL_CANDIDATES = [
    "gemini-flash-latest",
    "gemini-2.0-flash",
    "gemini-3.8-flash",
    "gemini-3.0-flash",
    "gemini-2.5-flash"
]

SHORT_AUDIO_TIMEOUT_MS = 25000
LONG_AUDIO_TIMEOUT_MS = 120000
RETRY_DELAY_SECONDS = 0.5

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

# =====================================================================
# [사용자 수정 가능 변수 구역 2] AI 프롬프트 (장시간 녹음 다중 요약 대응)
# =====================================================================
def build_gemini_prompt(current_time_str, weekday_korean, today_date_str):
    prompt = f"""
    현재 대한민국 기준 시각은 {current_time_str} ({weekday_korean})이야.
    첨부된 음성(30분 이상의 장시간 회의 또는 메모 포함)을 끝까지 주의 깊게 듣고, 아래 규칙에 맞춰 반드시 JSON 형식으로만 출력해 줘.

    [작성 및 분류 상세 규칙]
    1. Category (카테고리 분류):
       - 반드시 '일정', '할일', '아이디어', '기록' 중 딱 하나로만 분류해.
    2. Assignee (담당자 추출):
       - 음성에서 언급된 담당자 이름, 직급, 업체명 또는 부서를 추출해. 없으면 '미지정'.
    3. Summary (오늘 날짜 + 큰 회의 타이틀):
       - 전체 내용을 대표하는 핵심 제목을 한 줄로 작성하되, 맨 앞에 오늘 날짜([{today_date_str}])를 붙여줘.
    4. Details (장시간 녹음 다중 요약 및 전체 받아쓰기):
       - 녹음 파일이 길 경우(30분 이상), 내용을 시간대별이나 세부 주제별로 나누어 [파트 1], [파트 2] 형태로 체계적으로 다중 요약해.
       - [파트 1: 상세 회의 주제별 다중 요약] 논의된 안건들을 시간 흐름 및 주제별로 구분하여 핵심 정리.
       - [파트 2: 전체 녹음 원본 받아쓰기] 처음부터 끝까지 단 한 문장도 누락 없이 전체 받아쓰기(STT) 기재.
    5. DueDate & EventTime:
       - 약속 날짜(YYYY-MM-DD)와 시간(HH:MM, 24시간제) 추출. 없으면 빈칸("").

    [출력 JSON 규격]
    {{
        "Category": "일정, 할일, 아이디어, 기록 중 하나",
        "Assignee": "담당자 이름/업체명 (없으면 '미지정')",
        "Summary": "[{today_date_str}] 전체 회의를 관통하는 큰 타이틀 요약",
        "Details": "[파트 1: 상세 회의 주제별 다중 요약]\\n- 파트 1: ...\\n- 파트 2: ...\\n\\n[파트 2: 전체 녹음 원본 받아쓰기]\\n(전체 받아쓰기 내용 기재)",
        "DueDate": "YYYY-MM-DD (없으면 빈칸)",
        "EventTime": "HH:MM (없으면 빈칸)"
    }}
    """
    return prompt

# =====================================================================
# [UI 디자인: CSS 적용]
# =====================================================================
st.set_page_config(page_title=APP_TITLE, page_icon="🎙️", layout="wide")

premium_css = f"""
<style>
    @import url('https://cdn.jsdelivr.net/gh/orioncactus/pretendard/dist/web/static/pretendard.css');
    html, body, [class*="css"] {{ font-family: 'Pretendard', -apple-system, BlinkMacSystemFont, system-ui, sans-serif; }}
    #MainMenu, header, footer, [data-testid="stHeader"], [data-testid="stToolbar"], [data-testid="stDecoration"], [data-testid="stStatusWidget"] {{ visibility: hidden !important; display: none !important; }}
    .block-container {{ padding-top: 1.2rem !important; padding-bottom: 2.5rem !important; padding-left: 1rem !important; padding-right: 1rem !important; max-width: 820px !important; }}
    .premium-header {{ background: linear-gradient(135deg, #1e1b4b 0%, #312e81 50%, #4338ca 100%); color: #ffffff; padding: 20px 16px; border-radius: 18px; box-shadow: 0 10px 25px -5px rgba(49, 46, 129, 0.35); margin-bottom: 18px; border: 1px solid rgba(255, 255, 255, 0.12); }}
    .premium-badge {{ display: inline-block; background: rgba(255, 255, 255, 0.18); color: #e0e7ff; font-size: 11px; font-weight: 700; padding: 4px 10px; border-radius: 20px; margin-bottom: 8px; letter-spacing: 0.5px; }}
    .premium-title {{ font-size: clamp(14px, 4.2vw, 22px) !important; font-weight: 800 !important; margin: 0 0 6px 0 !important; line-height: 1.3 !important; white-space: nowrap !important; overflow: hidden !important; text-overflow: ellipsis !important; letter-spacing: -0.5px !important; }}
    .premium-subtitle {{ font-size: clamp(11px, 3.1vw, 13.5px); color: #c7d2fe; margin: 0; font-weight: 400; line-height: 1.45; word-break: keep-all; }}
    .usage-card {{ background: #ffffff; border: 1px solid #e2e8f0; border-radius: 16px; padding: 16px; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.04); margin-bottom: 12px; }}
    .usage-title {{ font-size: 15px; font-weight: 800; color: #1e293b; margin-bottom: 12px; }}
    .usage-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }}
    .usage-item {{ background: #f8fafc; border-radius: 12px; padding: 12px; border: 1px solid #f1f5f9; }}
    .usage-label {{ font-size: 12px; color: #64748b; font-weight: 600; margin-bottom: 4px; }}
    .usage-value {{ font-size: 16px; font-weight: 800; color: #2563eb; }}
    .mic-guide-card {{ background: #f8fafc; border: 1.5px solid #cbd5e1; border-left: 6px solid #000000; border-radius: 14px; padding: 15px 16px; margin-top: 8px; margin-bottom: 14px; word-break: keep-all; }}
    .inline-mic-icon {{ display: inline-flex; align-items: center; justify-content: center; width: 24px; height: 24px; border-radius: 50%; background-color: #000000; color: #ffffff; vertical-align: middle; margin: 0 2px; }}
    .inline-stop-icon {{ display: inline-flex; align-items: center; justify-content: center; width: 24px; height: 24px; border-radius: 50%; background-color: #ef4444; color: #ffffff; vertical-align: middle; margin: 0 2px; }}
    .login-card {{ background: #ffffff; border: 1.5px solid #cbd5e1; border-radius: 16px; padding: 26px 20px; text-align: center; box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.05); margin-top: 15px; margin-bottom: 14px; word-break: keep-all; }}
    .step-guide-box {{ background: #f8fafc; border: 1px solid #e2e8f0; border-left: 4px solid #3b82f6; border-radius: 10px; padding: 12px 15px; margin-bottom: 10px; font-size: 14px; color: #334155; line-height: 1.5; word-break: keep-all; }}
    [data-testid="stAudioInput"] {{ border: 2px solid #000000 !important; border-radius: 14px !important; padding: 12px !important; background-color: #ffffff !important; }}
    [data-testid="stAudioInput"] button:first-of-type {{ transform: scale(1.35) !important; margin-left: 6px !important; margin-right: 10px !important; }}
    .stButton > button {{ border-radius: 10px !important; font-weight: 700 !important; padding: 0.55rem 1rem !important; }}
</style>

<div class="premium-header">
    <div class="premium-badge">✨ AI SMART VOICE WORKSPACE</div>
    <div class="premium-title">🎙️ {APP_TITLE}</div>
    <div class="premium-subtitle">{APP_SUBTITLE}</div>
</div>
"""
st.markdown(premium_css, unsafe_allow_html=True)

# =====================================================================
# [핵심 디테일 추가] 쿠키 로딩 중 구글 로그인 화면 깜빡임 원천 차단
# =====================================================================
cookie_manager = stx.CookieManager(key="voice_app_cookie_mgr")

if "init_cookie_check" not in st.session_state:
    st.session_state["init_cookie_check"] = True
    st.markdown(
        """
        <div style="text-align: center; padding-top: 60px;">
            <div style="font-size: 40px; margin-bottom: 15px;">⏳</div>
            <div style="font-size: 18px; font-weight: 800; color: #1e293b;">안전한 통신을 위해 보안 인증 상태를 확인하고 있습니다...</div>
            <div style="font-size: 14px; color: #64748b; margin-top: 5px;">잠시만 기다려주세요 (약 1초 소요)</div>
        </div>
        """, 
        unsafe_allow_html=True
    )
    time.sleep(0.7) # 프론트엔드 쿠키가 백엔드로 완벽히 넘어올 시간을 부여하여 로그인 창 번쩍임 방지
    st.rerun()

all_cookies = cookie_manager.get_all()

# --- [1단계] 구글 OAuth 2.0 권한 및 시크릿 설정 로드 ---
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
                cookie_manager.set("voice_rt", encoded_rt, expires_at=datetime.now() + timedelta(days=COOKIE_EXPIRY_DAYS), key="set_rt_cookie_init")
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
            auto_creds = Credentials(token=None, refresh_token=decoded_rt, token_uri=oauth_secrets["token_uri"], client_id=oauth_secrets["client_id"], client_secret=oauth_secrets["client_secret"], scopes=SCOPES)
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

if "credentials" not in st.session_state:
    if len(flow_store) > 100:
        flow_store.clear()
    flow = Flow.from_client_config(client_config, scopes=SCOPES, redirect_uri=REDIRECT_URI)
    auth_url, state_key = flow.authorization_url(prompt="consent", access_type="offline", include_granted_scopes="true")
    flow_store[state_key] = flow
    st.markdown(f"""
        <div class="login-card">
            <div style="font-size: 42px; margin-bottom: 10px;">🔐</div>
            <h3 style="margin: 0 0 12px 0; color: #1e293b; font-weight: 800;">구글 워크스페이스 안전 연동</h3>
            <p style="color: #475569; font-size: 14px; margin-bottom: 20px; line-height: 1.7;">
                서비스를 시작하려면 아래 버튼을 눌러 본인의 구글 계정으로 로그인해 주세요.<br>
                한 번 로그인하면 <b>앱을 닫아도 로그아웃 전까지 계정이 영구 유지</b>되며,<br>
                모든 데이터는 <b>본인의 구글 시트와 캘린더에만 독립적으로 저장</b>됩니다.
            </p>
            <a href="{auth_url}" target="_blank" rel="noopener noreferrer" style="display: block; background-color: #ffffff; border: 2.5px solid #2563eb; color: #2563eb !important; padding: 15px 20px; text-decoration: none !important; border-radius: 12px; font-size: 16px; font-weight: 800; box-shadow: 0 4px 12px rgba(37, 99, 235, 0.15);">
                🚀 내 Google 계정으로 로그인 시작하기
            </a>
        </div>
    """, unsafe_allow_html=True)
    st.stop()

if "credentials" in st.session_state and st.session_state.get("saved_rt_encoded"):
    existing_cookies = all_cookies or {}
    if not existing_cookies.get("voice_rt"):
        cookie_manager.set("voice_rt", st.session_state["saved_rt_encoded"], expires_at=datetime.now() + timedelta(days=COOKIE_EXPIRY_DAYS), key="reinforce_rt_cookie")

def get_valid_credentials():
    creds = st.session_state["credentials"]
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            st.session_state["credentials"] = creds
        except Exception:
            pass
    return creds

def get_or_create_drive_folder(creds, folder_name=DRIVE_FOLDER_NAME):
    if "drive_audio_folder_id" in st.session_state and st.session_state["drive_audio_folder_id"]:
        return st.session_state["drive_audio_folder_id"]
    authed_session = AuthorizedSession(creds)
    query = f"name = '{folder_name}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
    try:
        res = authed_session.get("https://www.googleapis.com/drive/v3/files", params={"q": query, "fields": "files(id, name)", "pageSize": 1})
        if res.status_code == 200:
            files = res.json().get("files", [])
            if files:
                folder_id = files[0]["id"]
                st.session_state["drive_audio_folder_id"] = folder_id
                return folder_id
        folder_metadata = {"name": folder_name, "mimeType": "application/vnd.google-apps.folder"}
        create_res = authed_session.post("https://www.googleapis.com/drive/v3/files", json=folder_metadata)
        if create_res.status_code in [200, 201]:
            folder_id = create_res.json().get("id")
            st.session_state["drive_audio_folder_id"] = folder_id
            return folder_id
    except Exception:
        pass
    return None

def upload_audio_to_drive(creds, audio_bytes, mime_type, file_title="Voice_Record"):
    authed_session = AuthorizedSession(creds)
    folder_id = get_or_create_drive_folder(creds, DRIVE_FOLDER_NAME)
    ext = "mp3" if "mp3" in mime_type else ("m4a" if "mp4" in mime_type else "wav")
    metadata = {"name": f"{file_title}_{datetime.now(KST).strftime('%Y%m%d_%H%M%S')}.{ext}"}
    if folder_id:
        metadata["parents"] = [folder_id]
    files = {"data": ("metadata", json.dumps(metadata), "application/json"), "file": ("audio", audio_bytes, mime_type)}
    try:
        res = authed_session.post("https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart", files=files)
        if res.status_code in [200, 201]:
            file_id = res.json().get("id")
            return f"https://drive.google.com/file/d/{file_id}/view"
        return ""
    except Exception:
        return ""

def upload_text_to_drive(creds, text_content, file_name):
    authed_session = AuthorizedSession(creds)
    folder_id = get_or_create_drive_folder(creds, DRIVE_FOLDER_NAME)
    metadata = {"name": file_name, "mimeType": "text/plain"}
    if folder_id:
        metadata["parents"] = [folder_id]
    files = {"data": ("metadata", json.dumps(metadata), "application/json"), "file": ("text", text_content, "text/plain")}
    try:
        res = authed_session.post("https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart", files=files)
        if res.status_code in [200, 201]:
            file_id = res.json().get("id")
            return f"https://drive.google.com/file/d/{file_id}/view"
        return ""
    except Exception:
        return ""

def sync_today_usage_from_sheet(today_date_str):
    try:
        creds = get_valid_credentials()
        client_gspread = gspread.authorize(creds)
        sheet = client_gspread.open(SHEET_NAME).sheet1
        all_rows = sheet.get_all_values()
        if len(all_rows) <= 1:
            return 0, 0.0
        today_count = 0
        total_chars = 0
        for row in all_rows[1:]:
            if row and row[0].startswith(today_date_str):
                today_count += 1
                if len(row) >= 5:
                    total_chars += len(row[4])
        est_mins = max(today_count * 0.5, round(total_chars / 350.0, 1)) if today_count > 0 else 0.0
        return today_count, est_mins
    except Exception:
        return 0, 0.0

now_kst = datetime.now(KST)
today_str = now_kst.strftime("%Y-%m-%d")

if "sheet_synced_flag" not in st.session_state or st.session_state.get("usage_date") != today_str:
    st.session_state["usage_date"] = today_str
    s_count, s_mins = sync_today_usage_from_sheet(today_str)
    st.session_state["daily_request_count"] = s_count
    st.session_state["daily_used_seconds"] = s_mins * 60.0
    st.session_state["sheet_synced_flag"] = True

if "last_processed_hash" not in st.session_state:
    st.session_state["last_processed_hash"] = None

def generate_with_model_fallback(client_gemini, contents_payload, gen_config):
    last_error = None
    for attempt in range(2):
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
                err_lower = err_str.lower()
                last_error = err
                if "404" in err_str or "not_found" in err_lower or "no longer available" in err_lower or "not found" in err_lower or "timeout" in err_lower or "timed out" in err_lower or "deadline" in err_lower:
                    continue
                if "503" in err_str or "unavailable" in err_lower or "high demand" in err_lower or "overloaded" in err_lower or "429" in err_str or "resource_exhausted" in err_lower:
                    time.sleep(RETRY_DELAY_SECONDS)
                    continue
                raise err
    raise last_error

def save_api_key_permanently(raw_key):
    clean_key = raw_key.strip()
    encoded_val = base64.b64encode(clean_key.encode()).decode()
    st.session_state["saved_api_key"] = clean_key
    st.session_state["key_cleared_flag"] = False
    st.query_params["ak"] = encoded_val
    cookie_manager.set("voice_ak", encoded_val, expires_at=datetime.now() + timedelta(days=COOKIE_EXPIRY_DAYS), key="set_ak_cookie")

@st.dialog("🔑 무료 Gemini API 키 1분 발급 절차 안내", width="large")
def show_api_guide_modal():
    st.markdown("""
        <div style="margin-bottom: 14px; font-size: 14px; color: #475569; line-height: 1.6; word-break: keep-all;">
            AI 음성 변환 기능을 무료로 이용하기 위해 본인의 <b>구글 AI 열쇠(API 키)</b>를 발급받는 절차입니다.<br>
            <b>(최초 1회만 등록하면 앱을 닫아도 영구 유지됩니다 / 평생 무료)</b>
        </div>
        <div style="margin-bottom: 14px;">
            <a href="https://aistudio.google.com/app/apikey" target="_blank" rel="noopener noreferrer" style="display: block; text-align: center; background: #ffffff; border: 2px solid #2563eb; color: #2563eb !important; padding: 12px 16px; border-radius: 10px; font-weight: 800; text-decoration: none !important; font-size: 14px;">
                👉 1. 여기를 눌러 Google AI Studio 열기 (새 창)
            </a>
        </div>
    """, unsafe_allow_html=True)
    modal_key = st.text_input("👇 발급받아 복사한 API 키를 여기에 바로 붙여넣으세요:", type="password", key="modal_api_input")
    if st.button("✅ API 키 영구 저장 및 시작하기", type="primary", use_container_width=True):
        if modal_key.strip():
            save_api_key_permanently(modal_key)
            st.rerun()

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
            event_body = {"summary": event_title, "description": details, "start": {"dateTime": start_dt.strftime("%Y-%m-%dT%H:%M:00"), "timeZone": TIMEZONE}, "end": {"dateTime": end_dt.strftime("%Y-%m-%dT%H:%M:00"), "timeZone": TIMEZONE}}
        else:
            start_date_obj = datetime.strptime(due_date, "%Y-%m-%d")
            end_date_obj = start_date_obj + timedelta(days=1)
            event_body = {"summary": event_title, "description": details, "start": {"date": start_date_obj.strftime("%Y-%m-%d")}, "end": {"date": end_date_obj.strftime("%Y-%m-%d")}}
        res = authed_session.post(calendar_url, json=event_body)
        return "✅ 캘린더 등록 완료" if res.status_code in [200, 201] else f"⚠️ 캘린더 오류 ({res.status_code})"
    except Exception as cal_err:
        return f"⚠️ 캘린더 형식 오류 ({cal_err})"

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
            st.rerun()
    with col_logout:
        if st.button("🚪 로그아웃", use_container_width=True):
            if "credentials" in st.session_state:
                del st.session_state["credentials"]
            st.session_state["saved_api_key"] = ""
            st.session_state["saved_rt_encoded"] = ""
            st.session_state["logged_out_flag"] = True
            st.session_state["key_cleared_flag"] = True
            st.query_params.clear()
            st.rerun()
else:
    col_status, col_logout = st.columns([4, 1.2])
    with col_status:
        st.success("✅ 내 구글 계정 연동 완료! (아래에서 AI 키를 1회 등록해 주세요)")
    with col_logout:
        if st.button("🚪 로그아웃", use_container_width=True):
            if "credentials" in st.session_state:
                del st.session_state["credentials"]
            st.session_state["saved_rt_encoded"] = ""
            st.session_state["logged_out_flag"] = True
            st.query_params.clear()
            st.rerun()

used_mins = st.session_state["daily_used_seconds"] / 60.0
remaining_mins = max(0.0, DAILY_FREE_LIMIT_MINUTES - used_mins)
req_count = st.session_state["daily_request_count"]

st.markdown(f"""
    <div class="usage-card">
        <div class="usage-title">📊 오늘의 무료 AI 음성 사용 현황 ({today_str})</div>
        <div class="usage-grid">
            <div class="usage-item"><div class="usage-label">1회 최대 분석 한도</div><div class="usage-value">최대 {MAX_SINGLE_FREE_MINUTES}분</div></div>
            <div class="usage-item"><div class="usage-label">오늘 누적 사용 시간</div><div class="usage-value">{used_mins:.1f}분 <span style="font-size:12px; color:#64748b; font-weight:500;">/ {DAILY_FREE_LIMIT_MINUTES}분</span></div></div>
            <div class="usage-item"><div class="usage-label">남은 무료 여유 시간</div><div class="usage-value" style="color:#10b981;">약 {remaining_mins:.1f}분</div></div>
            <div class="usage-item"><div class="usage-label">오늘 무료 분석 횟수</div><div class="usage-value">{req_count}회 <span style="font-size:12px; color:#64748b; font-weight:500;">/ {DAILY_FREE_LIMIT_REQUESTS}회</span></div></div>
        </div>
    </div>
""", unsafe_allow_html=True)

if not st.session_state["saved_api_key"]:
    col_guide_btn, col_direct_link = st.columns([1.5, 1.5])
    with col_guide_btn:
        if st.button("📖 처음이신가요? 무료 API 키 발급 절차 보기 (팝업)", type="primary", use_container_width=True):
            show_api_guide_modal()
    with col_direct_link:
        st.markdown('<a href="https://aistudio.google.com/app/apikey" target="_blank" rel="noopener noreferrer" style="display:flex; align-items:center; justify-content:center; background:#ffffff; border:2px solid #2563eb; color:#2563eb !important; padding:9px 14px; border-radius:10px; font-weight:800; font-size:14px; text-decoration:none !important;">🔗 Google AI Studio 바로 열기 (새 창)</a>', unsafe_allow_html=True)
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
            st.rerun()

user_api_key = st.session_state["saved_api_key"]

# --- [6단계] 탭 구성: 탭1(녹음), 탭2(리스트), 탭3(주간 보고서 전용 페이지 자동 추가) ---
tab1, tab2, tab3 = st.tabs([
    "🎙️ 실시간 녹음 및 파일 변환", 
    "📁 내 저장된 녹음 리스트 조회 및 바로 듣기", 
    "📊 주간 요약 보고서 및 전용 페이지 추가"
])

BLACK_MIC_BADGE = '<span class="inline-mic-icon"><svg width="14" height="14" viewBox="0 0 24 24" fill="white"><path d="M12 14c1.66 0 3-1.34 3-3V5c0-1.66-1.34-3-3-3S9 3.34 9 5v6c0 1.66 1.34 3 3 3zm5.91-3c-.49 0-.9.36-.98.85C16.52 14.2 14.47 16 12 16s-4.52-1.8-4.93-4.15c-.08-.49-.49-.85-.98-.85-.61 0-1.09.54-1 1.14.49 3 2.89 5.35 5.91 5.78V20c0 .55.45 1 1 1s1-.45 1-1v-2.08c3.02-.43 5.42-2.78 5.91-5.78.1-.6-.39-1.14-1-1.14z"/></svg></span>'
RED_STOP_BADGE = '<span class="inline-stop-icon"><svg width="12" height="12" viewBox="0 0 24 24" fill="white"><rect x="6" y="6" width="12" height="12" rx="2"/></svg></span>'

with tab1:
    input_method = st.radio("음성 입력 방식을 선택하세요:", ["실시간 녹음", "파일 업로드"], horizontal=True)
    save_audio_toggle = st.checkbox(AUDIO_CHECKBOX_LABEL, value=True)
    
    audio_bytes = None
    mime_type = "audio/wav"
    should_process = False

    if input_method == "실시간 녹음":
        st.markdown(
            f"""
            <div class="mic-guide-card">
                <div style="font-size:15px; font-weight:800; color:#000000; margin-bottom:8px;">🎙️ 실시간 녹음 상태 확인 안내</div>
                <div style="font-size:14px; color:#374151; line-height:1.85;">
                    • 아래 녹음 박스 왼쪽의 {BLACK_MIC_BADGE} <b>마이크 아이콘</b>을 클릭하면 ➡️ 즉시 {RED_STOP_BADGE} <b style="color:#ef4444;">빨간색 정지 아이콘(녹음중)</b> 상태로 변합니다.<br>
                    • 말씀을 마친 후 {RED_STOP_BADGE} <b style="color:#ef4444;">정지 아이콘</b>을 다시 클릭하면 ➡️ <b>녹음이 종료되며 즉시 자동 변환</b>됩니다!
                </div>
            </div>
            """,
            unsafe_allow_html=True
        )
        
        recorded_file = st.audio_input("👇 아래 왼쪽의 마이크 아이콘(🎙️)을 눌러 녹음을 시작하세요 (정지 클릭 시 자동 변환)")
        if recorded_file is not None:
            audio_bytes = recorded_file.getvalue()
            
        current_audio_hash = hashlib.md5(audio_bytes).hexdigest() if audio_bytes else None
        if audio_bytes is not None and current_audio_hash != st.session_state["last_processed_hash"]:
            should_process = True

    else:
        st.markdown(
            """
            <div style="background:#f8fafc; border:1px solid #cbd5e1; border-left:5px solid #3b82f6; border-radius:12px; padding:15px 18px; margin-top:8px; margin-bottom:14px; word-break:keep-all;">
                <div style="font-size:15px; font-weight:800; color:#1e3a8a; margin-bottom:6px;">📂 녹음 파일 업로드 및 변환 방법 (30분 이상 장시간 녹음 지원)</div>
                <div style="font-size:14px; color:#334155; line-height:1.7;">
                    1. 아래 <b>[Browse files]</b> 버튼을 눌러 휴대폰이나 PC에 저장된 대용량 음성 파일(mp3, wav, m4a)을 선택하세요.<br>
                    2. 파일이 첨부되면 아래에 나타나는 <b>[🚀 업로드한 파일 변환 및 저장 실행]</b> 버튼을 클릭하세요.
                </div>
            </div>
            """,
            unsafe_allow_html=True
        )
        uploaded_file = st.file_uploader("변환할 음성 파일을 선택하세요 (mp3, wav, m4a)", type=["mp3", "wav", "m4a"])
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
            st.error(f"🛑 오늘의 무료 사용 안전 한도({DAILY_FREE_LIMIT_MINUTES}분 또는 {DAILY_FREE_LIMIT_REQUESTS}회)를 모두 소진했습니다.")
        else:
            file_size_mb = len(audio_bytes) / (1024 * 1024)
            try:
                now = datetime.now(KST)
                current_time_str = now.strftime("%Y-%m-%d %H:%M:%S")
                weekday_korean = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"][now.weekday()]
                
                prompt_text = build_gemini_prompt(current_time_str, weekday_korean, today_str)
                active_timeout_ms = SHORT_AUDIO_TIMEOUT_MS if file_size_mb <= 5.0 else LONG_AUDIO_TIMEOUT_MS
                client_gemini = genai.Client(
                    api_key=user_api_key.strip(),
                    http_options=types.HttpOptions(timeout=active_timeout_ms)
                )
                
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
                
                spinner_msg = "⚡ 초고속 Gemini AI가 장시간 녹음을 정밀 다중 요약 중입니다..." if file_size_mb > 5.0 else "⚡ 최신 Gemini AI가 음성을 정밀 분석 중입니다..."
                with st.spinner(spinner_msg):
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
                            for _ in range(60):
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
                    st.session_state["last_processed_hash"] = current_audio_hash
                
                category = result_data.get("Category", "기록")
                assignee = result_data.get("Assignee", "미지정")
                summary = result_data.get("Summary", "")
                details = result_data.get("Details", "")
                due_date = result_data.get("DueDate", "").strip()
                event_time = result_data.get("EventTime", "").strip()
                
                with st.spinner(f"💾 내 구글 시트 기록, 캘린더 연동 및 드라이브 자동 저장 중..."):
                    creds = get_valid_credentials()
                    calendar_status = add_to_google_calendar(creds, summary, assignee, details, due_date, event_time)
                    
                    if save_audio_toggle:
                        drive_link = upload_audio_to_drive(creds, audio_bytes, mime_type, file_title="Long_Voice_Record" if file_size_mb > 5.0 else "Voice_Record")
                        backup_msg = f"구글 드라이브 `{DRIVE_FOLDER_NAME}` 폴더 내 원본 저장 완료"
                    else:
                        drive_link = "원본 음성 저장 안 함 (텍스트만 기록)"
                        backup_msg = "원본 음성 백업 제외 (텍스트 및 캘린더만 연동)"
                        
                    schedule_display = f"{due_date} {event_time}".strip()
                    row_to_save = [current_time_str, category, assignee, summary, details, schedule_display, calendar_status, drive_link]
                    save_record_to_sheet(creds, row_to_save)
                    
                    s_count, s_mins = sync_today_usage_from_sheet(today_str)
                    st.session_state["daily_request_count"] = s_count
                    st.session_state["daily_used_seconds"] = s_mins * 60.0
                
                st.success(f"✨ AI 음성 분석 및 구글 시트 저장 완료! (적용 모델: `{used_model_name}` | 오늘 누적: {s_count}회)")
                st.markdown(f"### 📌 {summary}")
                st.write(f"**🏷️ 카테고리:** `{category}` | **👤 담당자:** `{assignee}` | **⏰ 약속일시:** `{due_date} {event_time}`")
                st.text_area("📝 상세 회의 주제별 다중 요약 및 전체 받아쓰기", value=details, height=260)
                st.info(f"✅ 저장 상태: {backup_msg} | 캘린더 연동: {calendar_status}")
                    
            except Exception as e:
                st.error(f"⚠️ 분석 중 오류가 발생했습니다: {e}")

with tab2:
    st.subheader("📁 내 구글 시트에 저장된 녹음 리스트 (조회 및 즉시 듣기)")
    if st.button("🔄 저장 목록 새로고침"):
        s_count, s_mins = sync_today_usage_from_sheet(today_str)
        st.session_state["daily_request_count"] = s_count
        st.session_state["daily_used_seconds"] = s_mins * 60.0
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
                    st.markdown("**📝 상세 회의 주제별 요약 및 전체 녹음 내용:**")
                    st.text(details)
                    
                    if audio_url and "drive.google.com" in audio_url:
                        st.markdown("---")
                        col1, col2 = st.columns([1, 2])
                        with col1:
                            if st.button("▶️ 앱에서 원본 녹음 바로 듣기", key=f"play_btn_{idx}"):
                                with st.spinner("구글 드라이브에서 음성을 불러오는 중..."):
                                    fetched_audio = download_audio_from_drive(creds, audio_url)
                                    if fetched_audio:
                                        st.audio(fetched_audio, format="audio/wav")
                                    else:
                                        st.error("음성 파일을 불러오지 못했습니다.")
                        with col2:
                            st.markdown(
                                f'<a href="{audio_url}" target="_blank" rel="noopener noreferrer" style="display:inline-block; background:#ffffff; border:2px solid #2563eb; color:#2563eb !important; padding:8px 14px; border-radius:8px; font-weight:800; text-decoration:none;">🔗 구글 드라이브에서 열기</a>',
                                unsafe_allow_html=True
                            )
                    else:
                        st.caption("🎵 이 기록은 텍스트 요약만 보관되어 있습니다.")
    except gspread.exceptions.SpreadsheetNotFound:
        st.info(f"아직 구글 드라이브에 스프레드시트(`{SHEET_NAME}`)가 없습니다.")
    except Exception as list_err:
        st.warning(f"목록을 불러오는 중 오류 발생: {list_err}")

with tab3:
    st.subheader("📊 최근 7일간 주간 요약 보고서 및 구글 시트 전용 페이지 자동 추가")
    st.markdown("지인 공유 및 실무 활용을 위해 최근 7일 동안 시트에 누적된 회의/일정 데이터를 자동 집계하고, 파일 다운로드 및 구글 시트에 요약 전용 페이지를 자동 추가하여 보관합니다.")
    
    if st.button("📈 이번 주간 보고서 데이터 집계 및 생성하기", type="primary"):
        try:
            creds = get_valid_credentials()
            client_gspread = gspread.authorize(creds)
            spreadsheet = client_gspread.open(SHEET_NAME)
            sheet = spreadsheet.sheet1
            all_rows = sheet.get_all_values()
            
            if len(all_rows) <= 1:
                st.warning("⚠️ 시트에 누적된 데이터가 없습니다.")
            else:
                headers = all_rows[0]
                rows = all_rows[1:]
                
                # 최근 7일 필터링
                seven_days_ago = datetime.now(KST) - timedelta(days=7)
                recent_rows = []
                cat_counts = {"일정": 0, "할일": 0, "아이디어": 0, "기록": 0}
                
                for r in rows:
                    if r and len(r) > 0:
                        try:
                            row_dt = datetime.strptime(r[0], "%Y-%m-%d %H:%M:%S").replace(tzinfo=KST)
                            if row_dt >= seven_days_ago:
                                recent_rows.append(r)
                                c = r[1] if len(r) > 1 and r[1] in cat_counts else "기록"
                                cat_counts[c] = cat_counts.get(c, 0) + 1
                        except Exception:
                            pass
                
                st.success(f"✅ 최근 7일간 총 {len(recent_rows)}건의 기록이 집계되었습니다!")
                
                col1, col2, col3, col4 = st.columns(4)
                col1.metric("📅 총 회의/기록 건수", f"{len(recent_rows)}건")
                col2.metric("📌 일정", f"{cat_counts['일정']}건")
                col3.metric("✅ 할일", f"{cat_counts['할일']}건")
                col4.metric("💡 아이디어/기록", f"{cat_counts['아이디어'] + cat_counts['기록']}건")
                
                # 요약 텍스트 생성
                report_md = f"# [주간 요약 보고서] ({seven_days_ago.strftime('%Y-%m-%d')} ~ {datetime.now(KST).strftime('%Y-%m-%d')})\n\n"
                report_md += f"- 총 기록 건수: {len(recent_rows)}건\n"
                report_md += f"- 카테고리별 통계: 일정({cat_counts['일정']}), 할일({cat_counts['할일']}), 아이디어({cat_counts['아이디어']}), 기록({cat_counts['기록']})\n\n"
                report_md += "## [세부 항목 리스트]\n"
                for r in recent_rows:
                    report_md += f"- [{r[1]}] {r[3]} (담당자: {r[2]} | 일시: {r[0]})\n"
                
                st.text_area("📄 생성된 주간 요약 보고서 미리보기", value=report_md, height=200)
                
                # 방법 1: 파일 다운로드 버튼 제공
                csv_buffer = io.StringIO()
                import csv
                writer = csv.writer(csv_buffer)
                writer.writerow(headers)
                writer.writerows(recent_rows)
                
                st.download_button(
                    label="📥 주간 보고서 엑셀(CSV) 파일로 다운로드",
                    data=csv_buffer.getvalue().encode('utf-8-sig'),
                    file_name=f"Weekly_Report_{datetime.now(KST).strftime('%Y%m%d')}.csv",
                    mime="text/csv"
                )
                
                # 방법 2: 구글 시트 새 페이지(탭) 자동 추가 및 구글 드라이브 백업
                with st.spinner("💾 구글 시트 내 주간 요약 전용 페이지 자동 추가 및 드라이브 저장 중..."):
                    try:
                        # 시트 새 페이지(탭) 생성
                        report_sheet_name = f"주간보고서_{datetime.now(KST).strftime('%m월%d일')}"
                        try:
                            ws = spreadsheet.worksheet(report_sheet_name)
                        except gspread.exceptions.WorksheetNotFound:
                            ws = spreadsheet.add_worksheet(title=report_sheet_name, rows=100, cols=10)
                            ws.append_row(["구분", "내용"])
                            ws.append_row(["보고서 기간", f"{seven_days_ago.strftime('%Y-%m-%d')} ~ {datetime.now(KST).strftime('%Y-%m-%d')}"])
                            ws.append_row(["총 건수", str(len(recent_rows))])
                            ws.append_row(["카테고리 통계", str(cat_counts)])
                            ws.append_row([])
                            ws.append_row(headers)
                            for r in recent_rows:
                                ws.append_row(r)
                        
                        # 드라이브 폴더에 텍스트 백업
                        drive_file_url = upload_text_to_drive(creds, report_md, f"Weekly_Report_{datetime.now(KST).strftime('%Y%m%d')}.txt")
                        
                        st.success(f"✨ 구글 시트 내 주간 요약 전용 페이지({report_sheet_name}) 자동 추가 및 구글 드라이브 저장 완료!")
                        if drive_link:
                            st.markdown(f"🔗 [구글 드라이브 보관함에서 주간 보고서 파일 확인하기]({drive_file_url})")
                    except Exception as archive_err:
                        st.warning(f"⚠️ 저장 중 일부 오류 발생: {archive_err}")
                        
        except Exception as e:
            st.error(f"⚠️ 주간 보고서 집계 중 오류가 발생했습니다: {e}")
