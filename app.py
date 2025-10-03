import io, re, urllib.parse, requests
import streamlit as st
import pdfplumber
from pdfminer.high_level import extract_text as pdfminer_extract_text
from pdfminer.layout import LAParams

st.set_page_config(page_title="PDF → Sections (verbatim)", layout="wide")

# ---------- Icons & headings ----------
SECTION_ORDER = [
    ("todays_must_know_news", "Today’s Must-Know News", "📌"),
    ("americas", "Americas", "🇺🇸"),
    ("greater_china", "Greater China", "🇨🇳"),
]

US_ALIASES = {"us", "u.s.", "usa", "u.s.a.", "united states"}
CN_ALIASES = {"cn", "prc", "china"}

def normalize(s: str) -> str:
    # Used only for heading detection (not for output)
    s = s.replace("’", "'").replace("–", "-").replace("—", "-")
    s = re.sub(r"\s+", " ", s.strip())
    return s.lower()

# ---------- Extraction engines ----------
def extract_verbatim_pdfminer(pdf_bytes: bytes) -> str:
    """Use pdfminer.six to extract text with layout parameters that keep line breaks and bullets."""
    laparams = LAParams(char_margin=2.0, line_margin=0.15, word_margin=0.1, boxes_flow=None, all_texts=True)
    try:
        text = pdfminer_extract_text(io.BytesIO(pdf_bytes), laparams=laparams)
        return text or ""
    except Exception:
        return ""

def extract_text_pdfplumber(pdf_bytes: bytes) -> str:
    """Fallback using pdfplumber page-by-page."""
    pages = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for p in pdf.pages:
            pages.append(p.extract_text(x_tolerance=1, y_tolerance=1) or "")
    return "".join(pages)

def extract_text_verbatim(pdf_bytes: bytes) -> str:
    """Primary + fallback."""
    t = extract_verbatim_pdfminer(pdf_bytes)
    if not t.strip():
        t = extract_text_pdfplumber(pdf_bytes)
    return t

# ---------- Section finding ----------
def find_section_spans(full_text: str):
    """
    Locate the three target sections by their headings and return absolute char spans
    from each heading to the next heading. Output text itself remains 100% verbatim.

    Also supports two-line headings like:
      US
      Americas
    and
      CN
      Greater China
    """
    lines = full_text.splitlines(keepends=True)
    L = [(i, raw, normalize(raw)) for i, raw in enumerate(lines)]

    # allow "1.", "1)", "1 .", etc., before headings
    enum = r"(?:\d+\s*[.)]\s*)?"

    H_TODAY = re.compile(rf"^{enum}today'?s\s+must-?know\s+news\s*$")
    H_AMER  = re.compile(rf"^{enum}americas\s*$")
    H_GC    = re.compile(rf"^{enum}greater\s+china\s*$")

    def looks_like_heading(raw: str) -> bool:
        s = raw.strip()
        return bool(s) and len(s) <= 80 and not s.endswith((".", "!", "?", ";", ","))

    # collect heading indices
    target_idx = {}
    all_heads = set()

    n = len(L)
    for i, raw, norm in L:
        # Single-line matches
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
        heading_line = lines[start_line]  # for reference
        return start_abs, end_abs, heading_line

    spans = {}
    for key in ["todays_must_know_news", "americas", "greater_china"]:
        if key in target_idx:
            spans[key] = span_from(target_idx[key])
    return spans

def prefix_icon_each_line(text: str, icon: str) -> str:
    """Add an icon before each non-empty line, without changing any words."""
    out = []
    for line in text.splitlines():
        if line.strip():
            out.append(f"{icon} {line}")
        else:
            out.append(line)
    return "\n".join(out)

def split_for_platform(s: str, limit: int = 1800):
    """Split long messages without altering words; prefer blank-line or line breaks."""
    if len(s) <= limit:
        return [s]
    parts = []
    remain = s
    while len(remain) > limit:
        window = remain[:limit]
        cut = window.rfind("\n\n")
        if cut < 0:
            cut = window.rfind("\n")
        if cut < 0:
            cut = limit
        parts.append(remain[:cut])
        remain = remain[cut:]
        if remain.startswith("\n"):
            remain = remain[1:]
    if remain:
        parts.append(remain)
    return parts

# ---------- UI ----------
st.title("PDF → Sections (verbatim)")

left, right = st.columns([2, 1], gap="large")
with right:
    st.markdown("**Options**")
    engine = st.selectbox("Extraction engine", ["PDFMiner (verbatim)", "pdfplumber (fallback)"], index=0,
                          help="PDFMiner often preserves line breaks and bullets more faithfully.")
    per_line_icons = st.checkbox("Add icon to every line (not just above heading)", value=True)
    monospace = st.checkbox("Show in monospace (preserve alignment)", value=True)
    webhook = st.text_input(
        "Discord Webhook URL (optional)",
        type="password",
        help="If set, the app can post the output to a Discord channel via webhook."
    )
    st.caption("WhatsApp: we use 'Click-to-Chat' links. Auto-send needs WhatsApp Business API (paid/approval).")

with left:
    file = st.file_uploader("Upload a DIGITAL-TEXT PDF (no scans)", type=["pdf"])
    if file:
        try:
            pdf_bytes = file.read()
            if engine.startswith("PDFMiner"):
                full = extract_text_verbatim(pdf_bytes)
            else:
                full = extract_text_pdfplumber(pdf_bytes)
            if not full.strip():
                st.error("Could not extract selectable text. This may be a scanned/image PDF.")
                st.stop()
        except Exception as e:
            st.error(f"Failed to read PDF: {e}")
            st.stop()

        spans = find_section_spans(full)
        if not spans:
            st.warning("Couldn’t find those sections in this PDF.")
            st.stop()

        for key, label, icon in SECTION_ORDER:
            if key not in spans:
                continue
            start_abs, end_abs, _ = spans[key]
            section_raw = full[start_abs:end_abs]  # EXACT original section text

            # Build message with icons
            if per_line_icons:
                message = prefix_icon_each_line(section_raw, icon).rstrip()
            else:
                message = f"{icon}\n{section_raw}".rstrip()

            st.subheader(f"{icon} {label}")
            if monospace:
                st.code(message)
            else:
                st.text_area(" ", value=message, height=260, label_visibility="collapsed")

            # Download
            st.download_button(
                label=f"Download {label}.txt",
                data=message.encode("utf-8"),
                file_name=f"{label.replace(' ', '_')}.txt",
                mime="text/plain",
                use_container_width=True
            )

            # WhatsApp "Click-to-Chat"
            wa_url = "https://wa.me/?text=" + urllib.parse.quote(message)
            st.markdown(f"[Share to WhatsApp (prefilled)]({wa_url})")

            # Optional: send to Discord via webhook
            if webhook:
                if st.button(f"Send {label} to Discord (webhook)", use_container_width=True):
                    try:
                        chunks = split_for_platform(message, limit=1800)
                        for ch in chunks:
                            resp = requests.post(webhook, json={"content": ch}, timeout=10)
                            if resp.status_code >= 300:
                                st.error(f"Webhook error {resp.status_code}: {resp.text[:200]}")
                                break
                        else:
                            st.success("Sent to Discord.")
                    except Exception as e:
                        st.error(f"Failed to send: {e}")
