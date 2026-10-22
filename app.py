import streamlit as st
import json
import io
import re
import hashlib
from datetime import datetime, timedelta
from audio_recorder_streamlit import audio_recorder
import gspread
from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import AuthorizedSession, Request
from google import genai
from google.genai import types

# =====================================================================
# [사용자 수정 가능 변수 구역 1] 기본 환경 및 무료 한도 설정
# =====================================================================
REDIRECT_URI = 'https://public-voice-task.streamlit.app/'
SHEET_NAME = 'Voice_Log_DB'
GEMINI_MODEL = 'gemini-2.5-flash'

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

# 구글 캘린더 기본 일정 길이(시간) 및 타임존
DEFAULT_EVENT_HOURS = 1
TIMEZONE = 'Asia/Seoul'

# =====================================================================
# [사용자 수정 가능 변수 구역 2] AI 프롬프트(지시문) 생성 함수
# =====================================================================
def build_gemini_prompt(current_time_str, weekday_korean, today_date_str):
    prompt = f"""
    현재 기준 시각은 {current_time_str} ({weekday_korean})이야.
    첨부된 음성(회의 또는 메모)을 끝까지 주의 깊게 듣고, 아래 규칙에 맞춰 반드시 JSON 형식으로만 출력해 줘.

    [작성 및 분류 상세 규칙]
    1. Category (카테고리 분류):
       - 반드시 '일정', '할일', '아이디어', '기록' 중 딱 하나로만 분류해.
       - '일정': 특정 날짜나 시간에 방문, 내방, 미팅, 회의 등 약속이 잡힌 경우
       - '할일': 기한 내에 처리하거나 준비해야 하는 업무 지시 및 확인 사항
       - '아이디어': 기획안, 신제품 아이디어, 새로운 제안, 브레인스토밍 내용
       - '기록': 위 3가지에 해당하지 않는 일반 참고 메모, 현장 상태 기록, 단순 대화 기록

    2. Assignee (담당자 추출):
       - 음성에서 언급된 담당자 이름, 직급, 업체명 또는 부서를 추출해.
       - 언급된 사람이 없으면 반드시 '미지정'이라고 적어.

    3. Summary (오늘 날짜 + 큰 회의 타이틀):
       - 전체 내용을 대표하는 핵심 제목을 한 줄로 작성하되, 맨 앞에 오늘 날짜([{today_date_str}])를 붙여줘.

    4. Details (상세 회의 주제별 정리 및 전체 녹음 원본 받아쓰기):
       - [파트 1: 상세 회의 주제별 정리] 논의된 안건들을 세부 주제별로 구분하여 핵심 내용을 깔끔하게 정리.
       - [파트 2: 전체 녹음 원본 받아쓰기] 음성에서 발화된 모든 내용을 처음부터 끝까지 받아쓰기(STT).

    5. DueDate (약속/기한 날짜) & EventTime (약속 시간):
       - '내일', '모레' 같은 상대적 날짜 표현이 나오면 현재 기준 시각({current_time_str})을 바탕으로 정확한 날짜(YYYY-MM-DD)를 계산해.
       - '오후 2시' 같은 시간 표현은 24시간제(HH:MM)로 변환해. 없으면 빈칸("")으로 비워둬.

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

st.set_page_config(page_title="Voice-to-Task 스마트 자동화", page_icon="🎙️", layout="wide")
st.title("🎙️ 개인 맞춤형 Voice-to-Task & 스마트 아카이브")
st.write("녹음 종료(OFF) 즉시 한글 카테고리 분류, 주제별 회의 정리, 캘린더 자동 등록 및 앱 내 원본 음성 재생을 지원합니다.")

# --- [보안 강화] 앱 접속 비밀번호 확인 ---
if "app_password" in st.secrets:
    if not st.session_state.get("password_verified", False):
        pwd_input = st.text_input("🔒 외부인 접근 차단을 위해 설정된 앱 비밀번호를 입력하세요:", type="password")
        if pwd_input == st.secrets["app_password"]:
            st.session_state["password_verified"] = True
            st.rerun()
        elif pwd_input:
            st.error("비밀번호가 일치하지 않습니다.")
        st.stop()

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

# --- [서버 캐시 설정] 보안 키(Code Verifier) 유지를 위한 저장소 생성 ---
@st.cache_resource
def get_flow_store():
    return {}

flow_store = get_flow_store()

# --- [핵심 업그레이드] Secrets에 저장된 refresh_token이 있으면 자동 연동! ---
if 'credentials' not in st.session_state and "refresh_token" in oauth_secrets:
    try:
        auto_creds = Credentials(
            token=None,
            refresh_token=oauth_secrets["refresh_token"],
            token_uri=oauth_secrets["token_uri"],
            client_id=oauth_secrets["client_id"],
            client_secret=oauth_secrets["client_secret"],
            scopes=SCOPES
        )
        auto_creds.refresh(Request())
        st.session_state['credentials'] = auto_creds
    except Exception as auto_err:
        st.warning(f"자동 로그인 토큰 갱신 중 확인이 필요합니다. 다시 로그인해 주세요: {auto_err}")

# --- [2단계] 최초 1회 로그인 처리 ---
if 'credentials' not in st.session_state:
    if "code" in st.query_params and "state" in st.query_params:
        auth_code = st.query_params["code"]
        state_key = st.query_params["state"]
        try:
            if state_key in flow_store:
                flow = flow_store.pop(state_key) 
                flow.fetch_token(code=auth_code)
                st.session_state['credentials'] = flow.credentials
                if flow.credentials.refresh_token:
                    st.session_state['new_refresh_token'] = flow.credentials.refresh_token
                st.query_params.clear()
                st.rerun()
            else:
                st.warning("로그인 세션이 만료되었습니다. 처음부터 다시 시도해 주세요.")
                st.query_params.clear()
                if st.button("🔄 다시 로그인"):
                    st.rerun()
                st.stop()
        except Exception as e:
            st.error(f"인증 처리 중 오류가 발생했습니다: {e}")
            if st.button("🔄 다시 로그인 시도"):
                st.query_params.clear()
                st.rerun()
            st.stop()
    else:
        flow = Flow.from_client_config(client_config, scopes=SCOPES, redirect_uri=REDIRECT_URI)
        auth_url, state_key = flow.authorization_url(
            prompt='consent',
            access_type='offline',
            include_granted_scopes='true'
        )
        flow_store[state_key] = flow 
        
        st.warning("🔒 최초 1회 구글 계정 연동이 필요합니다. 아래 버튼을 눌러 권한을 허용해 주세요.")
        
        # 핵심 해결: <button> 태그를 제거하고 <a> 태그 자체를 버튼처럼 디자인하여 클릭 증발 및 브라우저 차단 완벽 해결 (target="_blank" 사용)
        login_button_html = f'''
        <a href="{auth_url}" target="_blank" style="
            display: inline-block;
            background-color: #4285F4;
            color: white;
            padding: 12px 20px;
            text-decoration: none;
            border-radius: 6px;
            font-size: 16px;
            font-weight: bold;
            box-shadow: 0 2px 4px rgba(0,0,0,0.2);
            transition: background-color 0.3s;
            ">
            🚀 Google 계정으로 안전하게 로그인 (새 창)
        </a>
        '''
        st.markdown(login_button_html, unsafe_allow_html=True)
        st.info("💡 버튼을 누르면 새 탭에서 로그인이 진행됩니다. 로그인을 완료하면 자동으로 이 앱의 마이크 화면으로 돌아옵니다!")
        st.stop()

# 최초 로그인 직후, 평생 로그인 없이 쓰기 위한 refresh_token 설정 안내 박스 표시
if 'new_refresh_token' in st.session_state and "refresh_token" not in oauth_secrets:
    with st.expander("🎁 [중요] 앞으로 로그인 없이 앱을 켜자마자 바로 쓰려면 여기를 클릭하세요!", expanded=True):
        st.write("아래의 코드를 복사해서 Streamlit 관리자 화면의 **Secrets(비밀)** 맨 아랫줄에 추가로 붙여넣고 저장하세요.")
        st.code(f'refresh_token = "{st.session_state["new_refresh_token"]}"', language="toml")

# --- [3단계] 일일 무료 사용량 추적기 및 중복 방지 초기화 ---
today_str = datetime.now().strftime("%Y-%m-%d")
if st.session_state.get("usage_date") != today_str:
    st.session_state["usage_date"] = today_str
    st.session_state["daily_used_seconds"] = 0.0
    st.session_state["daily_request_count"] = 0

if "last_processed_hash" not in st.session_state:
    st.session_state["last_processed_hash"] = None

# --- [4단계] 장시간 토큰 자동 갱신 및 구글 연동 보조 함수들 ---
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
    metadata = {"name": f"Voice_Record_{datetime.now().strftime('%Y%m%d_%H%M%S')}.{ext}"}
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
    sheet.append_row(row_data)

# --- [5단계] 상단 무료 사용량 대시보드 및 기본 설정 UI ---
st.success("✅ 구글 계정 연동 완료! (장시간 회의 자동 연장 활성화됨)")

used_mins = st.session_state["daily_used_seconds"] / 60.0
remaining_mins = max(0.0, DAILY_FREE_LIMIT_MINUTES - used_mins)
req_count = st.session_state["daily_request_count"]

st.info(
    f"📊 **오늘의 무료 음성 사용 현황 ({today_str})**\n"
    f"- **1회 최대 무료 분석 한도:** 최대 **{MAX_SINGLE_FREE_MINUTES}분**\n"
    f"- **오늘 누적 사용 시간:** **{used_mins:.1f}분** / {DAILY_FREE_LIMIT_MINUTES}분 (남은 무료 여유: 약 **{remaining_mins:.1f}분**)\n"
    f"- **오늘 무료 분석 횟수:** **{req_count}회** / {DAILY_FREE_LIMIT_REQUESTS}회"
)
st.progress(min(1.0, used_mins / DAILY_FREE_LIMIT_MINUTES))

st.markdown("🔑 **[무료 Gemini API 키 발급받기 (Google AI Studio)](https://aistudio.google.com/app/apikey)**")
default_gemini_key = st.secrets.get("gemini_api_key", oauth_secrets.get("gemini_api_key", ""))
user_api_key = st.text_input("1️⃣ 본인의 무료 Gemini API 키를 입력하세요:", value=default_gemini_key, type="password")

save_audio_toggle = st.checkbox(
    "💾 [저장 여부 선택] 원본 음성 구글 드라이브 증거 백업 및 구글 시트 누적 저장하기",
    value=True
)

# --- [6단계] 탭 구성 ---
tab1, tab2 = st.tabs(["🎙️ 실시간 녹음 및 파일 변환", "📁 저장된 녹음 리스트 조회 및 바로 듣기"])

with tab1:
    input_method = st.radio("2️⃣ 음성 입력 방식을 선택하세요:", ["실시간 녹음", "파일 업로드"], horizontal=True)
    audio_bytes = None
    mime_type = "audio/wav"

    if input_method == "실시간 녹음":
        audio_bytes = audio_recorder(
            text="마이크 아이콘을 눌러 녹음을 시작하고, 다시 눌러 종료(OFF)하면 즉시 변환됩니다",
            icon_size="2x"
        )
        if audio_bytes:
            st.audio(audio_bytes, format="audio/wav")
    else:
        uploaded_file = st.file_uploader("음성 파일을 업로드하세요 (mp3, wav, m4a)", type=['mp3', 'wav', 'm4a'])
        if uploaded_file:
            audio_bytes = uploaded_file.read()
            ext = uploaded_file.name.split(".")[-1].lower()
            mime_map = {"mp3": "audio/mp3", "m4a": "audio/mp4", "wav": "audio/wav"}
            mime_type = mime_map.get(ext, "audio/wav")
            st.audio(audio_bytes, format=mime_type)

    current_audio_hash = hashlib.md5(audio_bytes).hexdigest() if audio_bytes else None
    is_new_audio = audio_bytes is not None and current_audio_hash != st.session_state["last_processed_hash"]
    manual_trigger = st.button("🔄 수동 변환 및 저장 재실행")

    if is_new_audio or manual_trigger:
        if not user_api_key:
            st.warning("⚠️ 상단에 무료 Gemini API 키를 먼저 입력해 주세요.")
        elif not audio_bytes:
            st.warning("⚠️ 분석할 음성 데이터가 없습니다.")
        elif used_mins >= DAILY_FREE_LIMIT_MINUTES or req_count >= DAILY_FREE_LIMIT_REQUESTS:
            st.error(f"🛑 오늘의 무료 사용 안전 한도({DAILY_FREE_LIMIT_MINUTES}분 또는 {DAILY_FREE_LIMIT_REQUESTS}회)를 모두 소진했습니다. 내일 다시 이용해 주세요.")
        else:
            file_size_mb = len(audio_bytes) / (1024 * 1024)
            try:
                now = datetime.now()
                current_time_str = now.strftime("%Y-%m-%d %H:%M:%S")
                weekday_korean = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"][now.weekday()]
                
                prompt_text = build_gemini_prompt(current_time_str, weekday_korean, today_str)
                client_gemini = genai.Client(api_key=user_api_key)
                
                with st.spinner("⚡ 녹음 종료 감지! 무료 Gemini AI가 회의 주제별 정리 및 전체 받아쓰기를 수행 중입니다..."):
                    if file_size_mb <= INLINE_LIMIT_MB:
                        response = client_gemini.models.generate_content(
                            model=GEMINI_MODEL,
                            contents=[types.Part.from_bytes(data=audio_bytes, mime_type=mime_type), prompt_text],
                            config=types.GenerateContentConfig(response_mime_type="application/json")
                        )
                    else:
                        uploaded_file_obj = None
                        try:
                            audio_stream = io.BytesIO(audio_bytes)
                            uploaded_file_obj = client_gemini.files.upload(
                                file=audio_stream,
                                config=types.UploadFileConfig(mime_type=mime_type)
                            )
                            response = client_gemini.models.generate_content(
                                model=GEMINI_MODEL,
                                contents=[uploaded_file_obj, prompt_text],
                                config=types.GenerateContentConfig(response_mime_type="application/json")
                            )
                        finally:
                            if uploaded_file_obj:
                                client_gemini.files.delete(name=uploaded_file_obj.name)
                                
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
                
                st.success("✨ AI 음성 분석 완료!")
                st.markdown(f"### 📌 {summary}")
                st.write(f"**🏷️ 카테고리:** `{category}` | **👤 담당자:** `{assignee}` | **⏰ 약속일시:** `{due_date} {event_time}`")
                st.text_area("📝 상세 회의 주제별 정리 및 전체 받아쓰기 내용", value=details, height=240)
                
                if save_audio_toggle:
                    with st.spinner("💾 인증 확인 후 구글 시트 기록, 캘린더 등록 및 원본 음성 드라이브 백업 중..."):
                        creds = get_valid_credentials()
                        calendar_status = add_to_google_calendar(creds, summary, assignee, details, due_date, event_time)
                        drive_link = upload_audio_to_drive(creds, audio_bytes, mime_type)
                        schedule_display = f"{due_date} {event_time}".strip()
                        
                        row_to_save = [
                            current_time_str, category, assignee, summary,
                            details, schedule_display, calendar_status, drive_link
                        ]
                        save_record_to_sheet(creds, row_to_save)
                        st.info(f"✅ 구글 시트 저장 및 백업 완료! (캘린더 상태: {calendar_status})")
                else:
                    st.warning("ℹ️ '저장 여부 선택' 스위치가 꺼져 있어 구글 시트 기록 및 음성 백업은 생략되었습니다.")
                    
            except Exception as e:
                st.error(f"처리 중 오류가 발생했습니다: {e}")

with tab2:
    st.subheader("📁 저장된 녹음 리스트 (날짜 · 큰 타이틀별 조회 및 즉시 듣기)")
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
                                with st.spinner("구글 드라이브에서 원본 음성을 불러오는 중입니다..."):
                                    fetched_audio = download_audio_from_drive(creds, audio_url)
                                    if fetched_audio:
                                        st.audio(fetched_audio, format="audio/wav")
                                    else:
                                        st.error("음성 파일을 불러오지 못했습니다. 우측 드라이브 링크를 확인해 주세요.")
                        with col2:
                            st.markdown(f"🔗 [구글 드라이브에서 원본 음성 파일 직접 열기]({audio_url})")
                    else:
                        st.caption("🎵 이 기록에는 백업된 원본 음성 파일이 없습니다.")
    except gspread.exceptions.SpreadsheetNotFound:
        st.info("아직 생성된 구글 스프레드시트(`Voice_Log_DB`)가 없습니다. 첫 녹음을 저장하면 자동으로 생성됩니다.")
    except Exception as list_err:
        st.warning(f"목록을 불러오는 중 일시적인 오류가 발생했습니다: {list_err}")
