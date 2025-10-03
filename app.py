# Build v3 of the Streamlit app with bullet-level auto-emojis and stricter verbatim extraction
import os, zipfile

root = "/mnt/data/streamlit_pdf_sections_v3"
os.makedirs(root, exist_ok=True)

app_py = """import io, re, urllib.parse, requests
import streamlit as st
import pdfplumber
from pdfminer.high_level import extract_text as pdfminer_extract_text
from pdfminer.layout import LAParams

st.set_page_config(page_title="PDF → Sections (verbatim)", layout="wide")

# ---------- Target sections & defaults ----------
SECTION_ORDER = [
    ("todays_must_know_news", "Today’s Must-Know News", "📌"),
    ("americas", "Americas", "🇺🇸"),
    ("greater_china", "Greater China", "🇨🇳"),
]

US_ALIASES = {"us", "u.s.", "usa", "u.s.a.", "united states"}
CN_ALIASES = {"cn", "prc", "china"}

# Heuristic rules to choose an emoji for each BULLET item (no word changes).
# The first matching pattern wins. Tweak these patterns to your liking.
EMOJI_RULES = [
    (r"flight|airline|airport|non-?stop|route", "✈️"),
    (r"warn|ban|sanction|probe|investigat|violate|must-?nots|interfer", "🚫"),
    (r"semiconductor|chip|foundry|fab|license|production|manufactur|tech|taiwan|tsmc", "🔧"),
    (r"deal|agreement|talk|negotiat|summit|meeting|breakthrough|trade", "🤝"),
    (r"stock|market|index|shares|rall|sell-?off|volume|hang seng|hsi|overbought|resistance|support", "📈"),
]

def normalize(s: str) -> str:
    # Only for heading detection (NOT used for output)
    s = s.replace("’", "'").replace("–", "-").replace("—", "-")
    s = re.sub(r"\\s+", " ", s.strip())
    return s.lower()

# ---------- Extraction engines ----------
def extract_verbatim_pdfminer(pdf_bytes: bytes) -> str:
    \"\"\"Use pdfminer.six with parameters that try to keep line breaks and bullets.\"\"\"
    laparams = LAParams(char_margin=2.0, line_margin=0.15, word_margin=0.1, boxes_flow=None, all_texts=True)
    try:
        return pdfminer_extract_text(io.BytesIO(pdf_bytes), laparams=laparams) or ""
    except Exception:
        return ""

def extract_text_pdfplumber(pdf_bytes: bytes) -> str:
    pages = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for p in pdf.pages:
            pages.append(p.extract_text(x_tolerance=1, y_tolerance=1) or "")
    return "".join(pages)

def extract_text_verbatim(pdf_bytes: bytes) -> str:
    t = extract_verbatim_pdfminer(pdf_bytes)
    if not t.strip():
        t = extract_text_pdfplumber(pdf_bytes)
    return t

# ---------- Section finding ----------
def find_section_spans(full_text: str):
    \"\"\"Return dict: key -> (start_abs, end_abs, heading_line_index). Supports two-line headings.\"\"\"
    lines = full_text.splitlines(keepends=True)
    L = [(i, raw, normalize(raw)) for i, raw in enumerate(lines)]
    enum = r"(?:\\d+\\s*[.)]\\s*)?"

    H_TODAY = re.compile(rf"^{enum}today'?s\\s+must-?know\\s+news\\s*$")
    H_AMER  = re.compile(rf"^{enum}americas\\s*$")
    H_GC    = re.compile(rf"^{enum}greater\\s+china\\s*$")

    def looks_like_heading(raw: str) -> bool:
        s = raw.strip()
        return bool(s) and len(s) <= 80 and not s.endswith((".", "!", "?", ";", ","))

    target_idx = {}
    all_heads = set()
    n = len(L)

    for i, raw, norm in L:
        if "todays_must_know_news" not in target_idx and H_TODAY.match(norm):
            target_idx["todays_must_know_news"] = i
        if "americas" not in target_idx and H_AMER.match(norm):
            target_idx["americas"] = i
        if "greater_china" not in target_idx and H_GC.match(norm):
            target_idx["greater_china"] = i

        # Two-line patterns: US + Americas, CN + Greater China
        j = i + 1
        while j < n and not L[j][1].strip():
            j += 1
        if j < n:
            norm_i = norm
            norm_j = L[j][2]
            if ("americas" not in target_idx) and (norm_i in US_ALIASES) and H_AMER.match(norm_j):
                target_idx["americas"] = i
            if ("greater_china" not in target_idx) and (norm_i in CN_ALIASES) and H_GC.match(norm_j):
                target_idx["greater_china"] = i

        if looks_like_heading(raw):
            all_heads.add(i)

    all_heads_sorted = sorted(all_heads)

    def span_from(start_line: int):
        end_line = len(lines)
        for h in all_heads_sorted:
            if h > start_line:
                end_line = h
                break
        start_abs = sum(len(lines[k]) for k in range(start_line))
        end_abs   = sum(len(lines[k]) for k in range(end_line))
        return start_abs, end_abs, start_line

    spans = {}
    for key in ["todays_must_know_news", "americas", "greater_china"]:
        if key in target_idx:
            spans[key] = span_from(target_idx[key])
    return spans

# ---------- Emoji helpers (bullet-level) ----------
BULLET_PREFIX_RE = re.compile(r\"^([ \\t]*[•\\-‣▪◦]\\s+)(.*)$\")  # capture bullet marker + payload

def pick_emoji_for_text(payload: str, default_icon: str) -> str:
    t = normalize(payload)
    for pattern, emoji in EMOJI_RULES:
        if re.search(pattern, t):
            return emoji
    return default_icon

def add_emoji_to_bullets(section_text: str, default_icon: str) -> str:
    \"\"\"Prefix an emoji ONLY on the first line of each bullet item. Words remain untouched.\"\"\"
    out_lines = []
    in_bullet = False
    for line in section_text.splitlines():
        m = BULLET_PREFIX_RE.match(line)
        if m:
            # New bullet start
            marker, payload = m.group(1), m.group(2)
            emoji = pick_emoji_for_text(payload, default_icon)
            out_lines.append(f\"{emoji} {marker}{payload}\")
            in_bullet = True
        else:
            # Continuation or normal line
            out_lines.append(line)
            if not line.strip():
                in_bullet = False
    return \"\\n\".join(out_lines)

def add_icon_each_line(section_text: str, icon: str) -> str:
    \"\"\"Simple mode: prefix icon to every non-empty line (no word changes).\"\"\"
    lines = []
    for line in section_text.splitlines():
        if line.strip():
            lines.append(f\"{icon} {line}\")
        else:
            lines.append(line)
    return \"\\n\".join(lines)

def split_for_platform(s: str, limit: int = 1800):
    if len(s) <= limit:
        return [s]
    parts, remain = [], s
    while len(remain) > limit:
        window = remain[:limit]
        cut = window.rfind(\"\\n\\n\")
        if cut < 0: cut = window.rfind(\"\\n\")
        if cut < 0: cut = limit
        parts.append(remain[:cut])
        remain = remain[cut:]
        if remain.startswith(\"\\n\"): remain = remain[1:]
    if remain: parts.append(remain)
    return parts

# ---------- UI ----------
st.title(\"PDF → Sections (verbatim)\")

left, right = st.columns([2, 1], gap=\"large\")
with right:
    st.markdown(\"**Options**\")
    engine = st.selectbox(\"Extraction engine\", [\"PDFMiner (verbatim)\", \"pdfplumber (fallback)\"], index=0)
    mode = st.radio(\"Icon mode\", [\"Auto emoji on bullets\", \"Same icon on every line\"], index=0)
    monospace = st.checkbox(\"Show in monospace (preserve alignment)\", value=True)
    webhook = st.text_input(\"Discord Webhook URL (optional)\", type=\"password\")
    st.caption(\"WhatsApp uses 'Click-to-Chat'. Auto-send needs WhatsApp Business API (paid/approval).\")

with left:
    file = st.file_uploader(\"Upload a DIGITAL-TEXT PDF (no scans)\", type=[\"pdf\"])
    if file:
        try:
            pdf_bytes = file.read()
            full = extract_text_verbatim(pdf_bytes) if engine.startswith(\"PDFMiner\") else extract_text_pdfplumber(pdf_bytes)
            if not full.strip():
                st.error(\"Could not extract selectable text. This may be a scanned/image PDF.\")
                st.stop()
        except Exception as e:
            st.error(f\"Failed to read PDF: {e}\"); st.stop()

        spans = find_section_spans(full)
        if not spans:
            st.warning(\"Couldn’t find those sections in this PDF.\"); st.stop()

        for key, label, default_icon in SECTION_ORDER:
            if key not in spans:
                continue
            start_abs, end_abs, _ = spans[key]
            section_raw = full[start_abs:end_abs]  # EXACT original text from heading to next heading

            if mode == \"Auto emoji on bullets\":
                message = add_emoji_to_bullets(section_raw, default_icon).rstrip()
            else:
                message = add_icon_each_line(section_raw, default_icon).rstrip()

            st.subheader(f\"{default_icon} {label}\")
            if monospace:
                st.code(message)
            else:
                st.text_area(\" \", value=message, height=280, label_visibility=\"collapsed\")

            st.download_button(
                label=f\"Download {label}.txt\",
                data=message.encode(\"utf-8\"),
                file_name=f\"{label.replace(' ', '_')}.txt\",
                mime=\"text/plain\",
                use_container_width=True
            )

            wa_url = \"https://wa.me/?text=\" + urllib.parse.quote(message)
            st.markdown(f\"[Share to WhatsApp (prefilled)]({wa_url})\") 

            if webhook:
                if st.button(f\"Send {label} to Discord (webhook)\", use_container_width=True):
                    try:
                        for ch in split_for_platform(message):
                            resp = requests.post(webhook, json={\"content\": ch}, timeout=10)
                            if resp.status_code >= 300:
                                st.error(f\"Webhook error {resp.status_code}: {resp.text[:200]}\"); break
                        else:
                            st.success(\"Sent to Discord.\")
                    except Exception as e:
                        st.error(f\"Failed to send: {e}\")
"""

requirements = """streamlit>=1.33
pdfplumber>=0.11
pdfminer.six>=20221105
requests>=2.31
"""

with open(os.path.join(root, "app.py"), "w", encoding="utf-8") as f:
    f.write(app_py)
with open(os.path.join(root, "requirements.txt"), "w", encoding="utf-8") as f:
    f.write(requirements)

zip_path = "/mnt/data/streamlit_pdf_sections_v3.zip"
with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
    z.write(os.path.join(root, "app.py"), "app.py")
    z.write(os.path.join(root, "requirements.txt"), "requirements.txt")

zip_path
