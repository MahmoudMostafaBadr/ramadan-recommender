import streamlit as st
import pandas as pd
import numpy as np
import gspread
import json
from google.oauth2.service_account import Credentials
from datetime import datetime

# ----------------------------
# CONFIG
# ----------------------------
APP_TITLE = "نظام توصية وجبات رمضان الصحية"
CSV_PATH = "epi_r.csv"

# Google Sheet
SHEET_ID = "19BsqWLeMByqhNoybXPowEgqiYdFumJY1-dqagv5atlU"
WORKSHEET_NAME = "logs"  # اسم التاب عندك

LOG_COLUMNS = [
    "timestamp",
    "username",
    "meal",
    "calories_max",
    "protein_min",
    "sodium_max",
    "top_n",
    "results_titles",
]

# Secrets key name (Streamlit Cloud -> Settings -> Secrets)
# لازم تحط فيها JSON كامل كسطر واحد داخل triple quotes
SECRETS_KEY = "gcp_service_account_json"


# ----------------------------
# UI SETTINGS
# ----------------------------
st.set_page_config(page_title=APP_TITLE, layout="wide")

CUSTOM_CSS = """
<style>
    .app-wrap {max-width: 1120px; margin: 0 auto;}
    .header{
        padding: 18px 18px;
        border-radius: 16px;
        background: linear-gradient(135deg, rgba(18,18,18,0.95), rgba(60,45,12,0.88));
        border: 1px solid rgba(255,215,120,0.22);
    }
    .title{
        font-size: 34px;
        font-weight: 850;
        margin: 0;
        color: #f7f1e3;
        letter-spacing: 0.2px;
    }
    .sub{
        margin-top: 6px;
        color: rgba(247,241,227,0.75);
        font-size: 14px;
        line-height: 1.7;
    }
    .card{
        padding: 14px 14px;
        border-radius: 14px;
        border: 1px solid rgba(255,215,120,0.18);
        background: rgba(18,18,18,0.70);
    }
    .muted{
        color: rgba(247,241,227,0.62);
        font-size: 13px;
        line-height: 1.65;
    }
</style>
"""
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)


# ----------------------------
# DATA LOADING + PREP
# ----------------------------
@st.cache_data
def load_raw(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    df.columns = [c.strip() for c in df.columns]
    return df


@st.cache_data
def prepare_for_app(df: pd.DataFrame) -> pd.DataFrame:
    required = ["title", "calories", "protein", "fat", "sodium"]
    for c in required:
        if c not in df.columns:
            raise ValueError(f"ملف CSV ناقص عمود أساسي: {c}")

    for col in ["calories", "protein", "fat", "sodium"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna(subset=["title", "calories", "protein", "fat", "sodium"]).copy()

    # sanity filter (زي اللي انت عملته)
    df = df[
        (df["calories"].between(1, 1993)) &
        (df["protein"].between(0, 200)) &
        (df["fat"].between(0, 194)) &
        (df["sodium"].between(0, 5980))
    ].copy()

    # meal_slot
    if "meal_slot" not in df.columns:
        df["meal_slot"] = "either"
    else:
        df["meal_slot"] = df["meal_slot"].astype(str).str.strip().str.lower()
        df.loc[~df["meal_slot"].isin(["suhoor", "iftar", "either"]), "meal_slot"] = "either"

    # why
    if "why" not in df.columns:
        def build_why(r):
            sodium_note = "صوديوم أقل" if r["sodium"] <= 600 else "صوديوم أعلى"
            protein_note = "بروتين عالي" if r["protein"] >= 25 else "بروتين متوسط"
            return f"{sodium_note} + {protein_note}"
        df["why"] = df.apply(build_why, axis=1)

    # final_score (لو مش موجود)
    if "final_score" not in df.columns:
        df["final_score"] = (df["protein"] * 0.8) - (df["sodium"] / 1200) - (df["fat"] * 0.05)

    # kind optional
    if "kind" in df.columns:
        df["kind"] = df["kind"].astype(str).str.strip().str.lower()

    return df


# ----------------------------
# GOOGLE SHEETS (Secrets-based)
# ----------------------------
@st.cache_resource
def get_worksheet():
    if SECRETS_KEY not in st.secrets:
        raise ValueError(
            f"مش لاقي Secret اسمها {SECRETS_KEY}. "
            "على Streamlit Cloud: Settings -> Secrets وحط gcp_service_account_json."
        )

    sa_json_str = st.secrets[SECRETS_KEY]
    info = json.loads(sa_json_str)

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_info(info, scopes=scopes)
    gc = gspread.authorize(creds)

    sh = gc.open_by_key(SHEET_ID)
    ws = sh.worksheet(WORKSHEET_NAME)
    return ws


def ensure_sheet_header():
    ws = get_worksheet()
    first_row = ws.row_values(1)
    normalized = [c.strip().lower() for c in first_row]
    if normalized != LOG_COLUMNS:
        ws.update("A1:H1", [LOG_COLUMNS])


def log_request(username: str, meal: str, calories_max: int, protein_min: int, sodium_max: int, top_n: int, titles: str):
    ws = get_worksheet()
    row = [
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        username,
        meal,
        int(calories_max),
        int(protein_min),
        int(sodium_max),
        int(top_n),
        titles,
    ]
    ws.append_row(row)


# ----------------------------
# RECOMMENDER
# ----------------------------
def recommend(df: pd.DataFrame,
              meal: str,
              calories_max: int,
              protein_min: int,
              sodium_max: int,
              top_n: int) -> pd.DataFrame:
    meal = meal.strip().lower()
    pool = df.copy()

    if meal in ["suhoor", "iftar"]:
        pool = pool[pool["meal_slot"].isin([meal, "either"])]

    pool = pool[
        (pool["calories"] <= calories_max) &
        (pool["protein"] >= protein_min) &
        (pool["sodium"] <= sodium_max)
    ].copy()

    if pool.empty:
        return pool

    # قرب السعرات من الحد (عامل مساعد)
    pool["calorie_fit"] = 1.0 - (np.abs(pool["calories"] - calories_max) / max(calories_max, 1))
    pool["calorie_fit"] = pool["calorie_fit"].clip(lower=0, upper=1)

    pool["score"] = pool["final_score"] + (pool["calorie_fit"] * 0.6)
    pool = pool.sort_values("score", ascending=False)

    # تنويع بسيط حسب kind لو موجود
    if "kind" in pool.columns:
        out = []
        per_kind_cap = max(1, top_n // 4)
        for _, grp in pool.groupby("kind", dropna=False):
            out.append(grp.head(per_kind_cap))
        out = pd.concat(out, ignore_index=True).sort_values("score", ascending=False).head(top_n)
        return out

    return pool.head(top_n)


# ----------------------------
# SESSION / ROUTING
# ----------------------------
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
if "username" not in st.session_state:
    st.session_state.username = ""


def go_login():
    st.session_state.logged_in = False
    st.session_state.username = ""
    st.rerun()


def do_login(name: str):
    name = (name or "").strip()
    if not name:
        st.error("لازم تكتب اسمك.")
        return
    st.session_state.username = name
    st.session_state.logged_in = True
    st.rerun()


# ----------------------------
# PAGE: LOGIN
# ----------------------------
def page_login():
    st.markdown('<div class="app-wrap">', unsafe_allow_html=True)

    st.markdown(
        f"""
        <div class="header">
            <p class="title">{APP_TITLE}</p>
            <div class="sub">
                اكتب اسمك الأول. ده دخول بسيط للتجربة وتسجيل الاختيارات في الشيت.
            </div>
        </div>
        """,
        unsafe_allow_html=True
    )

    st.write("")
    c1, c2, c3 = st.columns([1, 2, 1])

    with c2:
        st.markdown('<div class="card">', unsafe_allow_html=True)
        st.markdown("**تسجيل دخول**")
        name = st.text_input("اسم المستخدم", placeholder="مثال: Mahmoud", value="")
        st.write("")
        if st.button("دخول", use_container_width=True):
            do_login(name)
        st.markdown('<div class="muted"></div>', unsafe_allow_html=True)
        st.markdown("</div>", unsafe_allow_html=True)

    st.markdown("</div>", unsafe_allow_html=True)


# ----------------------------
# PAGE: RECOMMENDATIONS
# ----------------------------
def page_recs():
    st.markdown('<div class="app-wrap">', unsafe_allow_html=True)

    st.markdown(
        f"""
        <div class="header">
            <p class="title">{APP_TITLE}</p>
            <div class="sub">
                أهلاً {st.session_state.username} — اختار الإعدادات وشوف التوصيات.
            </div>
        </div>
        """,
        unsafe_allow_html=True
    )

    top_bar = st.columns([3, 1])
    with top_bar[1]:
        if st.button("تسجيل خروج", use_container_width=True):
            go_login()

    st.write("")
    left, right = st.columns([1, 2], gap="large")

    with left:
        st.markdown('<div class="card">', unsafe_allow_html=True)

        st.markdown("**نوع الوجبة**")
        meal = st.selectbox("اختيار", ["suhoor", "iftar", "either"], index=0)

        st.markdown("**حدود التغذية**")

        calories_max = st.slider(
            "أقصى سعرات حرارية (kcal)",
            min_value=100,
            max_value=2000,
            value=700,
            step=25,
            help="السعرات الحرارية للوجبة الواحدة"
        )

        protein_min = st.slider(
            "أقل كمية بروتين (جرام)",
            min_value=0,
            max_value=200,
            value=20,
            step=1,
            help="البروتين أعلى = شبع أفضل"
        )

        sodium_max = st.slider(
            "أقصى صوديوم (مليجرام)",
            min_value=0,
            max_value=6000,
            value=1200,
            step=50,
            help="الصوديوم العالي يزيد الإحساس بالعطش"
        )

        top_n = st.slider(
            "عدد النتائج المعروضة",
            min_value=1,
            max_value=20,
            value=10,
            step=1
        )

        if meal == "suhoor":
            st.markdown('<div class="muted">اقتراح للسحور: بروتين عالي + صوديوم أقل لتقليل العطش.</div>', unsafe_allow_html=True)
        elif meal == "iftar":
            st.markdown('<div class="muted">اقتراح للإفطار: سعرات معتدلة + صوديوم متوسط + تجنب دهون عالية.</div>', unsafe_allow_html=True)
        else:
            st.markdown('<div class="muted">either: هيرشح من كل الأنواع.</div>', unsafe_allow_html=True)

        st.write("")
        run_btn = st.button("اعرض التوصيات", use_container_width=True)

        st.markdown("</div>", unsafe_allow_html=True)

    with right:
        st.markdown('<div class="card">', unsafe_allow_html=True)
        st.markdown("**النتائج**")
        st.markdown('<div class="muted">لو مفيش نتائج، وسّع القيود شوية.</div>', unsafe_allow_html=True)

        if run_btn:
            try:
                ensure_sheet_header()

                raw = load_raw(CSV_PATH)
                df = prepare_for_app(raw)
                recs = recommend(df, meal, calories_max, protein_min, sodium_max, top_n)

                if recs.empty:
                    st.warning("مفيش نتائج بالشروط دي. جرّب تغيّر الحدود.")
                else:
                    show_cols = ["title", "meal_slot", "calories", "protein", "fat", "sodium", "score", "why"]
                    if "kind" in recs.columns:
                        show_cols.insert(1, "kind")

                    view = recs[show_cols].copy()
                    view = view.rename(columns={"score": "final_score"})
                    st.dataframe(view.reset_index(drop=True), width="stretch")

                    titles = " | ".join(recs["title"].astype(str).head(top_n).tolist())
                    log_request(st.session_state.username, meal, calories_max, protein_min, sodium_max, top_n, titles)

                    st.success("تم تسجيل الطلب في Google Sheet.")
            except Exception as e:
                st.error(f"حصل خطأ: {e}")

        st.markdown("</div>", unsafe_allow_html=True)

    st.markdown("</div>", unsafe_allow_html=True)


# ----------------------------
# ROUTER
# ----------------------------
if st.session_state.logged_in:
    page_recs()
else:
    page_login()
