from __future__ import annotations

import hashlib
import json
import os
import re
import time
from datetime import datetime, timedelta
from typing import Any, List
from urllib.parse import parse_qs, urlparse

import pymysql
import streamlit as st
from dotenv import load_dotenv

from llm_client import DeepSeekClient
from parser import parse_docx, parse_ppt

load_dotenv()
st.set_page_config(page_title="工科教案思政分析平台", page_icon="🎓", layout="wide")

api_key = (os.environ.get("DEEPSEEK_API_KEY") or "").strip()
if not api_key:
    st.error("系统未检测到 API 密钥，请在根目录 .env 文件中配置")
    st.stop()

SYSTEM_PROMPT_KNOWLEDGE_EXPERT = """
你是资深工科教学内容解析专家。请从输入教学文本提取核心技术名词或专业概念。
仅输出 JSON: {"knowledge_list":["概念1","概念2"]}。
""".strip()

SYSTEM_PROMPT_LOGIC_EXPERT = """
你是工科教育逻辑建模专家。为知识点匹配思政/职业素养维度并给出理由。
仅输出 JSON: {"matches":[{"point":"知识点","dimensions":["维度"],"reason":"理由"}]}。
""".strip()

SYSTEM_PROMPT_SENIOR_TEACHER = """
你是工科实训基地技术总监，用自然口语化方式生成课堂导入。
仅输出 JSON:
{
 "hook":"引入情境",
 "bridge":"避坑指南",
 "insight":"价值升华",
 "golden_line":"课堂金句"
}
""".strip()


def inject_global_css() -> None:
    st.markdown(
        """
<style>
/* 整个页面透明，让固定背景层可见 */
.stApp {
  background: transparent !important;
}
/* 隐藏 Streamlit 默认 header/footer */
header[data-testid="stHeader"] {
  display: none !important;
}
footer {
  display: none !important;
}

/* 全屏固定背景图，放在最底层 */
.login-bg {
  position: fixed;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  background-image: linear-gradient(rgba(6,18,38,0.6), rgba(6,18,38,0.6)),
    url("https://images.unsplash.com/photo-1581092335397-9583eb92d232?auto=format&fit=crop&w=1800&q=80");
  background-size: cover;
  background-position: center;
  z-index: -1;
}

/* 登录卡片容器 */
.login-card {
  max-width: 520px;
  margin: 15vh auto 0 auto;
  background: #ffffff !important;           /* 强制纯白不透明 */
  border-radius: 18px;
  padding: 1.5rem 1.5rem 1rem 1.5rem;
  box-shadow: 0 18px 40px rgba(0,0,0,0.3);
  position: relative;
  z-index: 1;
  color: #000000 !important;                /* 卡片内所有文字默认纯黑 */
  font-weight: 400;
  -webkit-font-smoothing: antialiased;      /* 文字边缘更清晰 */
  -moz-osx-font-smoothing: grayscale;
}

/* 登录卡片标题，不再强制字号，允许内联调整 */
.login-card h3 {
  color: #111111 !important;
  font-weight: 700 !important;
  /* 移除 font-size: 1.6rem !important; */
  text-shadow: 0 1px 2px rgba(0,0,0,0.05);
}

/* 副标题 */
.login-card .stCaptionContainer p,
.login-card .stCaption {
  color: #333333 !important;
  font-weight: 500 !important;
}

/* 表单标签文字 */
.login-card label {
  color: #222222 !important;
  font-weight: 600 !important;
  font-size: 0.95rem !important;
  text-shadow: 0 1px 0 rgba(0,0,0,0.02);
}

/* 输入框文字：黑底白字？改为浅灰底黑字，确保看清 */
.login-card input,
.login-card textarea {
  color: #000000 !important;
  background-color: #f4f4f4 !important;
  border: 1px solid #b0b0b0 !important;
  font-weight: 500 !important;
}

/* 按钮样式，保持白色文字 */
.login-card .stButton button {
  color: #ffffff !important;
  background-color: #1e3a5f !important;
  border: none !important;
  font-weight: 600 !important;
}

/* 错误/信息提示框文字 */
.login-card .stAlert {
  color: #b22222 !important;
  background-color: #ffe6e6 !important;
}

/* 平台名称：放大显示 */
.platform-name {
  display: block;
  font-size: 1.4rem;
  font-weight: 700;
  color: #111111;
  margin-bottom: 0.2rem;
  text-align: center;
}

/* 分割线轻量化 */
.login-divider {
  border: none;
  border-top: 1px solid #d0d0d0;
  margin: 0.3rem 0;
}

/* ------- 其他页面样式保持不变 ------- */
.nav-wrap {
  display: flex;
  justify-content: space-between;
  align-items: center;
  background: #ffffff;
  border: 1px solid #e7edf8;
  border-radius: 12px;
  padding: 0.8rem 1rem;
  margin-bottom: 1rem;
}
.upload-card {
  border: 1px solid #e1e8f5;
  border-radius: 14px;
  background: #fff;
  padding: 1rem;
}
.workspace-col {
  background: #fff;
  border: 1px solid #e5ecf8;
  border-radius: 12px;
  padding: 0.75rem;
}
</style>
        """,
        unsafe_allow_html=True,
    )

MYSQL_URL = (os.environ.get("MYSQL_URL") or "").strip()
if not MYSQL_URL:
    st.error("系统未检测到 MySQL 连接串，请在 .env 中配置 MYSQL_URL")
    st.stop()

PHONE_REGEX = re.compile(r"^1[3-9]\d{9}$")
PASSWORD_REGEX = re.compile(
    r"^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[^\w\s]).{8,}$"
)
MAX_LOGIN_ATTEMPTS = 5
LOCK_MINUTES = 15


def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def parse_mysql_url(mysql_url: str) -> dict[str, Any]:
    parsed = urlparse(mysql_url)
    if parsed.scheme not in {"mysql", "mysql+pymysql"}:
        raise ValueError("MYSQL_URL 协议必须是 mysql:// 或 mysql+pymysql://")
    query = parse_qs(parsed.query)
    return {
        "host": parsed.hostname or "127.0.0.1",
        "port": parsed.port or 3306,
        "user": parsed.username or "",
        "password": parsed.password or "",
        "database": (parsed.path or "").lstrip("/"),
        "charset": query.get("charset", ["utf8mb4"])[0],
        "autocommit": False,
    }


def get_db_conn() -> pymysql.connections.Connection:
    config = parse_mysql_url(MYSQL_URL)
    return pymysql.connect(**config, cursorclass=pymysql.cursors.DictCursor)


def init_auth_db() -> None:
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id BIGINT PRIMARY KEY AUTO_INCREMENT,
                username VARCHAR(64) UNIQUE NOT NULL,
                password_hash VARCHAR(128) NOT NULL,
                display_name VARCHAR(64) NOT NULL,
                failed_attempts INT NOT NULL DEFAULT 0,
                locked_until DATETIME NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
            )
            # 初始化默认管理员：admin / 123456Aa!
            cur.execute("SELECT 1 FROM users WHERE username = %s", ("admin",))
            if cur.fetchone() is None:
                cur.execute(
                    "INSERT INTO users (username, password_hash, display_name) VALUES (%s, %s, %s)",
                    ("admin", hash_password("123456Aa!"), "管理员老师"),
                )
        conn.commit()
    finally:
        conn.close()


def is_valid_phone(username: str) -> bool:
    return bool(PHONE_REGEX.fullmatch(username))


def is_strong_password(password: str) -> bool:
    return bool(PASSWORD_REGEX.fullmatch(password))


def safe_json_extract(text: str) -> dict[str, Any]:
    text = text.strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    fenced = re.findall(r"```json\s*(.*?)```", text, flags=re.S | re.I)
    if fenced:
        try:
            return json.loads(fenced[0].strip())
        except Exception:
            pass
    obj = re.search(r"\{.*\}", text, flags=re.S)
    if obj:
        try:
            return json.loads(obj.group(0))
        except Exception:
            pass
    return {}


def stream_to_text(generator) -> str:
    chunks: List[str] = []
    for token in generator:
        chunks.append(token)
    return "".join(chunks).strip()


def normalize_text_for_cache(text: str) -> str:
    normalized = text.strip().lower()
    normalized = re.sub(r"\s+", " ", normalized)
    normalized = re.sub(r"[^\w\u4e00-\u9fff ]+", "", normalized)
    return normalized


def build_cache_key(text: str) -> str:
    return hashlib.sha256(normalize_text_for_cache(text).encode("utf-8")).hexdigest()


def extract_text_from_upload(uploaded_file) -> str:
    raw = uploaded_file.read()
    name = uploaded_file.name.lower()
    if name.endswith(".pptx"):
        return parse_ppt(raw)
    if name.endswith(".docx"):
        return parse_docx(raw)
    return ""


def normalize_knowledge_points(data: dict[str, Any]) -> list[str]:
    points = data.get("knowledge_list", [])
    return list(dict.fromkeys([p.strip() for p in points if isinstance(p, str) and p.strip()]))


def normalize_dimension_matches(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for item in data.get("matches", []):
        if not isinstance(item, dict):
            continue
        point = item.get("point")
        dims = item.get("dimensions", [])
        if not isinstance(point, str) or not point.strip():
            continue
        clean_dims = list(dict.fromkeys([d.strip() for d in dims if isinstance(d, str) and d.strip()]))
        if not clean_dims:
            continue
        out[point.strip()] = {"dimensions": clean_dims, "reason": str(item.get("reason", "")).strip()}
    return out


def to_dimension_candidate_dict(detailed_map: dict[str, dict[str, Any]]) -> dict[str, list[str]]:
    return {p: info.get("dimensions", []) for p, info in detailed_map.items() if info.get("dimensions")}


@st.cache_data(ttl=60 * 60 * 24, show_spinner=False)
def cached_knowledge_extraction(
    *, cache_key: str, model_name: str, normalized_teacher_text: str
) -> list[str]:
    del cache_key
    client = DeepSeekClient(api_key=api_key, model=model_name.strip())
    raw = stream_to_text(
        client.stream_chat(
            system_prompt=SYSTEM_PROMPT_KNOWLEDGE_EXPERT,
            user_prompt=f"请提取知识点：\n{normalized_teacher_text}",
            temperature=0.2,
        )
    )
    return normalize_knowledge_points(safe_json_extract(raw))


@st.cache_data(ttl=60 * 60 * 24, show_spinner=False)
def cached_dimension_matching(
    *, cache_key: str, model_name: str, selected_points: list[str]
) -> dict[str, dict[str, Any]]:
    del cache_key
    client = DeepSeekClient(api_key=api_key, model=model_name.strip())
    raw = stream_to_text(
        client.stream_chat(
            system_prompt=SYSTEM_PROMPT_LOGIC_EXPERT,
            user_prompt=f"知识点列表：\n{json.dumps(selected_points, ensure_ascii=False)}",
            temperature=0.2,
        )
    )
    return normalize_dimension_matches(safe_json_extract(raw))


@st.cache_data(ttl=60 * 60 * 24, show_spinner=False)
def cached_single_teaching_card(
    *, cache_key: str, model_name: str, point: str, dimensions: list[str], reason: str, style_hint: str
) -> dict[str, str]:
    del cache_key
    client = DeepSeekClient(api_key=api_key, model=model_name.strip())
    raw = stream_to_text(
        client.stream_chat(
            system_prompt=SYSTEM_PROMPT_SENIOR_TEACHER,
            user_prompt=(
                f"知识点: {point}\n维度: {','.join(dimensions)}\n理由:{reason}\n风格:{style_hint}\n"
                "按 JSON 输出 hook/bridge/insight/golden_line。"
            ),
            temperature=0.6,
        )
    )
    data = safe_json_extract(raw)
    return {
        "hook": str(data.get("hook", "")).strip(),
        "bridge": str(data.get("bridge", "")).strip(),
        "insight": str(data.get("insight", "")).strip(),
        "golden_line": str(data.get("golden_line", "")).strip(),
        "raw": raw,
    }


def init_state() -> None:
    st.session_state.setdefault("page", "login")
    st.session_state.setdefault("user", None)
    st.session_state.setdefault(
        "processed_data",
        {
            "parsed_text": "",
            "file_name": "",
            "knowledge_points": [],
            "dimension_scan_map": {},
            "dimension_candidates_map": {},
        },
    )
    st.session_state.setdefault("selected_points", [])
    st.session_state.setdefault("last_selected_points", [])
    st.session_state.setdefault("dimension_map", {})
    st.session_state.setdefault("teaching_cards", {})
    st.session_state.setdefault("selected_card_point", None)
    st.session_state.setdefault("style_rounds", {})


def switch_page(target: str, message: str) -> None:
    ph = st.empty()
    with ph.container():
        with st.spinner(message):
            time.sleep(0.6)
    st.session_state.page = target
    st.rerun()


def check_login(username: str, password: str) -> bool:
    if not username or not password:
        return False
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT username, display_name, password_hash, failed_attempts, locked_until FROM users WHERE username = %s",
                (username,),
            )
            row = cur.fetchone()
            if not row:
                return False

            now = datetime.now()
            locked_until = row.get("locked_until")
            if locked_until and now < locked_until:
                remain = int((locked_until - now).total_seconds() // 60) + 1
                st.error(f"账号已被临时锁定，请约 {remain} 分钟后重试。")
                return False

            if row["password_hash"] != hash_password(password):
                attempts = int(row.get("failed_attempts", 0)) + 1
                if attempts >= MAX_LOGIN_ATTEMPTS:
                    lock_until = now + timedelta(minutes=LOCK_MINUTES)
                    cur.execute(
                        "UPDATE users SET failed_attempts = %s, locked_until = %s WHERE username = %s",
                        (attempts, lock_until, username),
                    )
                    conn.commit()
                    st.error(f"连续登录失败过多，账号已锁定 {LOCK_MINUTES} 分钟。")
                else:
                    cur.execute(
                        "UPDATE users SET failed_attempts = %s WHERE username = %s",
                        (attempts, username),
                    )
                    conn.commit()
                    st.error(f"账号或密码错误，已失败 {attempts}/{MAX_LOGIN_ATTEMPTS} 次。")
                return False

            cur.execute(
                "UPDATE users SET failed_attempts = 0, locked_until = NULL WHERE username = %s",
                (username,),
            )
            conn.commit()
    finally:
        conn.close()

    st.session_state.user = {
        "username": row["username"],
        "display_name": row["display_name"] or "老师",
    }
    return True


def register_user_placeholder(username: str, password: str) -> tuple[bool, str]:
    if not username or not password:
        return False, "请输入完整注册信息。"
    if username != "admin" and not is_valid_phone(username):
        return False, "账号需为有效手机号（11 位中国大陆手机号）。"
    if not is_strong_password(password):
        return False, "密码需至少 8 位，且包含大小写字母、数字和特殊字符。"
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM users WHERE username = %s", (username,))
            if cur.fetchone() is not None:
                return False, "该账号已存在，请直接登录。"
            cur.execute(
                "INSERT INTO users (username, password_hash, display_name) VALUES (%s, %s, %s)",
                (username, hash_password(password), "老师"),
            )
        conn.commit()
    finally:
        conn.close()
    return True, "注册成功，请使用新账号登录。"


def reset_for_reupload() -> None:
    st.session_state.processed_data = {
        "parsed_text": "",
        "file_name": "",
        "knowledge_points": [],
        "dimension_scan_map": {},
        "dimension_candidates_map": {},
    }
    st.session_state.selected_points = []
    st.session_state.last_selected_points = []
    st.session_state.dimension_map = {}
    st.session_state.teaching_cards = {}
    st.session_state.selected_card_point = None
    st.session_state.style_rounds = {}


def logout() -> None:
    st.session_state.user = None
    reset_for_reupload()
    switch_page("login", "正在安全退出...")


def render_login_page() -> None:
    # 全屏固定背景
    st.markdown('<div class="login-bg"></div>', unsafe_allow_html=True)

    # 登录卡片容器：平台名称 → 分割线 → 教师登录（缩小字体）
    st.markdown(
    '<div class="login-card">'
    '<span class="platform-name">🎓 工科实训课程思政智能推荐平台</span>'
    '<hr class="login-divider">'
    '<h3 style="font-size: 1.15rem; margin-top: 0;">教师登录</h3>',
    unsafe_allow_html=True
)

    with st.form("login_form"):
        username = st.text_input("教师工号 / 手机号", placeholder="请输入工号或手机号")
        password = st.text_input("密码", placeholder="请输入密码", type="password")
        c1, c2 = st.columns(2)
        login_submit = c1.form_submit_button("登录", use_container_width=True)
        register_submit = c2.form_submit_button("注册", use_container_width=True)

    if login_submit:
        if check_login(username.strip(), password):
            switch_page("upload", "登录成功，进入资源上传页...")
        else:
            st.error("账号或密码错误。默认管理员账号：admin / 123456Aa!")
    if register_submit:
        _, msg = register_user_placeholder(username.strip(), password)
        st.info(msg)

    st.markdown("</div>", unsafe_allow_html=True)


def render_upload_page(model_name: str) -> None:
    user = st.session_state.user or {"display_name": "老师"}
    st.markdown(
        f"""
<div class="nav-wrap">
  <div><strong>DeepSeek-V3 多智能体引擎</strong></div>
  <div>👩‍🏫 欢迎您，{user.get("display_name", "老师")}</div>
</div>
        """,
        unsafe_allow_html=True,
    )
    b1, b2, _ = st.columns([1, 1, 1])
    if b1.button("注销", use_container_width=True):
        logout()
    if b2.button("手动清缓存", use_container_width=True):
        st.cache_data.clear()
        st.success("缓存已清空。")

    c1, c2 = st.columns(2)
    with c1:
        st.markdown('<div class="upload-card">📊 上传 PPT 课件</div>', unsafe_allow_html=True)
        ppt_file = st.file_uploader("选择 PPTX 文件", type=["pptx"], key="upload_ppt")
    with c2:
        st.markdown('<div class="upload-card">📄 上传 教案文档</div>', unsafe_allow_html=True)
        doc_file = st.file_uploader("选择 DOCX 文件", type=["docx"], key="upload_doc")

    parse_trigger = st.button("开始解析文档", type="primary", use_container_width=True)
    if parse_trigger:
        target_file = ppt_file or doc_file
        if target_file is None:
            st.error("请先上传 PPT 或 DOCX 文件。")
            return
        try:
            with st.spinner("多智能体正在深度解析工程图纸与文档..."):
                parsed_text = extract_text_from_upload(target_file)
            if not parsed_text.strip():
                st.error("解析结果为空，请检查文档内容。")
                return
            norm_text = normalize_text_for_cache(parsed_text)
            points = cached_knowledge_extraction(
                cache_key=build_cache_key(f"{model_name}|k|{norm_text}"),
                model_name=model_name,
                normalized_teacher_text=norm_text,
            )
            if not points:
                st.error("未提取到有效知识点。")
                return
            with st.spinner("正在执行维度有效性映射..."):
                scan = cached_dimension_matching(
                    cache_key=build_cache_key(f"{model_name}|d|{json.dumps(points, ensure_ascii=False)}"),
                    model_name=model_name,
                    selected_points=points,
                )
            candidate_map = to_dimension_candidate_dict(scan)
            filtered_points = [p for p in points if candidate_map.get(p)]
            if not filtered_points:
                st.error("未在本文档中发现典型思政切入点，建议补充工程应用案例。")
                return

            st.session_state.processed_data = {
                "parsed_text": parsed_text,
                "file_name": target_file.name,
                "knowledge_points": filtered_points,
                "dimension_scan_map": scan,
                "dimension_candidates_map": candidate_map,
            }
            st.session_state.selected_points = filtered_points[: min(5, len(filtered_points))]
            st.session_state.last_selected_points = list(st.session_state.selected_points)
            st.session_state.dimension_map = {
                p: {"dimensions": list(candidate_map.get(p, [])), "reason": scan.get(p, {}).get("reason", "")}
                for p in st.session_state.selected_points
            }
            st.session_state.teaching_cards = {}
            st.success("解析成功，即将进入正式操作工作台。")
            switch_page("operation", "正在加载操作工作台...")
        except Exception as e:
            st.error(f"解析失败：{e}")


def render_operation_page(model_name: str) -> None:
    pdata = st.session_state.processed_data
    if not pdata.get("parsed_text"):
        st.warning("暂无可操作数据，请先上传并解析文档。")
        if st.button("返回上传页"):
            switch_page("upload", "返回上传页...")
        return

    left, mid, right = st.columns([1, 2, 1], gap="small")

    with left:
        st.markdown('<div class="workspace-col">', unsafe_allow_html=True)
        st.markdown("#### 控制列")
        st.caption(f"文件：`{pdata.get('file_name', '未命名')}`")
        st.caption(f"核心知识点：{len(pdata.get('knowledge_points', []))} 个")
        if st.button("重新上传", use_container_width=True):
            reset_for_reupload()
            switch_page("upload", "正在返回上传页...")
        if st.button("注销", use_container_width=True):
            logout()
        st.markdown("</div>", unsafe_allow_html=True)

    with mid:
        st.markdown('<div class="workspace-col">', unsafe_allow_html=True)
        st.markdown("#### 动态级联选择")
        points = pdata.get("knowledge_points", [])
        selected = st.multiselect(
            "知识点勾选",
            options=points,
            default=st.session_state.selected_points,
            key="selected_points_widget",
        )
        st.session_state.selected_points = selected

        candidate_map = pdata.get("dimension_candidates_map", {})
        scan_map = pdata.get("dimension_scan_map", {})
        if st.session_state.selected_points != st.session_state.last_selected_points:
            for p in list(st.session_state.dimension_map.keys()):
                if p not in st.session_state.selected_points:
                    st.session_state.dimension_map.pop(p, None)
            for p in st.session_state.selected_points:
                st.session_state.dimension_map.setdefault(
                    p,
                    {"dimensions": list(candidate_map.get(p, [])), "reason": scan_map.get(p, {}).get("reason", "")},
                )
            st.session_state.last_selected_points = list(st.session_state.selected_points)

        for point in st.session_state.selected_points:
            choices = candidate_map.get(point, [])
            if not choices:
                continue
            current = st.session_state.dimension_map.get(point, {}).get("dimensions", [])
            st.session_state.dimension_map[point]["dimensions"] = st.multiselect(
                f"{point} 的维度",
                options=choices,
                default=[d for d in current if d in choices] or choices,
                key=f"dims_{point}",
            )

        active_points = [
            p for p in st.session_state.selected_points if st.session_state.dimension_map.get(p, {}).get("dimensions")
        ]
        if st.button("生成话术卡片流", type="primary", use_container_width=True):
            if not active_points:
                st.error("请至少保留一个知识点维度。")
            else:
                with st.spinner("正在生成课堂无感导入方案..."):
                    for p in active_points:
                        info = st.session_state.dimension_map.get(p, {})
                        dims = info.get("dimensions", [])
                        reason = info.get("reason", "")
                        style = "默认工程案例切入"
                        card = cached_single_teaching_card(
                            cache_key=build_cache_key(f"{model_name}|s|{p}|{json.dumps(dims, ensure_ascii=False)}|{style}"),
                            model_name=model_name,
                            point=p,
                            dimensions=dims,
                            reason=reason,
                            style_hint=style,
                        )
                        st.session_state.teaching_cards[p] = card
                st.success("话术卡片已生成。")

        st.markdown("#### 话术卡片流")
        c1, c2 = st.columns(2)
        card_points = [p for p in st.session_state.selected_points if p in st.session_state.teaching_cards]
        for i, p in enumerate(card_points):
            target = c1 if i % 2 == 0 else c2
            card = st.session_state.teaching_cards[p]
            with target.container(border=True):
                st.markdown(f"**{p}**")
                st.caption("引入情境")
                st.write(card.get("hook", ""))
                st.caption("避坑指南")
                st.write(card.get("bridge", ""))
                st.caption("金句参考")
                st.write(card.get("golden_line", ""))
                if st.button("查看详情", key=f"detail_{p}", use_container_width=True):
                    st.session_state.selected_card_point = p
        st.markdown("</div>", unsafe_allow_html=True)

    with right:
        st.markdown('<div class="workspace-col">', unsafe_allow_html=True)
        st.markdown("#### 详情预览")
        current = st.session_state.selected_card_point
        if current and current in st.session_state.teaching_cards:
            info = st.session_state.dimension_map.get(current, {})
            card = st.session_state.teaching_cards[current]
            st.markdown(f"**知识点**: {current}")
            st.markdown(f"**维度**: {'、'.join(info.get('dimensions', []))}")
            st.markdown(f"**背景素材建议**: {info.get('reason', '可结合实验事故、工程标准或行业案例展开。')}")
            st.markdown(f"**可讲金句**: {card.get('golden_line', '')}")
            st.markdown(f"**价值升华**: {card.get('insight', '')}")
        else:
            st.caption("点击中间任意话术卡片的“查看详情”，此处显示完整背景素材。")
        st.markdown("</div>", unsafe_allow_html=True)


init_state()
inject_global_css()
init_auth_db()

if st.session_state.page == "login":
    render_login_page()
elif st.session_state.page == "upload":
    render_upload_page(model_name="deepseek-chat")
elif st.session_state.page == "operation":
    render_operation_page(model_name="deepseek-chat")
else:
    st.session_state.page = "login"
    st.rerun()