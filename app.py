import streamlit as st
import pytesseract
from PIL import Image, ImageEnhance
from pdf2image import convert_from_bytes, pdfinfo_from_bytes
import io
from docx import Document

# --- 1. SETTINGS (No Local Paths for Deployment) ---
# ஆன்லைனில் Deploy செய்யும்போது TESS_PATH மற்றும் POPPLER_PATH தேவையில்லை.
# சிஸ்டம் தானாகவே packages.txt மூலம் எடுத்துக்கொள்ளும்.

# --- PAGE CONFIG ---
st.set_page_config(page_title="TamizhVizhi Pro OCR", layout="wide", page_icon="🎯")

st.title("🎯 TamizhVizhi OCR Pro")
st.write("Image / PDF / Camera மூலம் தமிழை உரையாக (Text) மாற்றுங்கள்!")

# --- SIDEBAR ---
st.sidebar.header("⚙️ Settings")
language = st.sidebar.selectbox("மொழியைத் தேர்ந்தெடுக்கவும்", ["Tamil", "English", "Tamil + English"])
lang_code = 'tam' if language == "Tamil" else 'eng' if language == "English" else 'tam+eng'
pdf_dpi = st.sidebar.slider("PDF OCR தரம் (DPI)", 150, 300, 200, step=25)

# --- HELPERS ---
def create_word_doc(text):
    doc = Document()
    doc.add_paragraph(text)
    bio = io.BytesIO()
    doc.save(bio)
    return bio.getvalue()

def improve_image(image):
    # Grayscale conversion and contrast enhancement for better OCR
    image = image.convert('L')
    enhancer = ImageEnhance.Contrast(image)
    image = enhancer.enhance(2.0)
    return image

def reset_ocr_state():
    st.session_state["ocr_text"] = ""
    st.session_state["ocr_error"] = ""
    st.session_state["ocr_done"] = False
    st.session_state["file_signature"] = None

if "ocr_text" not in st.session_state:
    reset_ocr_state()

# --- INPUT CHOICE ---
input_mode = st.radio("உள்ளீட்டு முறையைத் தேர்வு செய்யவும்:", ["கோப்பை பதிவேற்ற (Upload)", "படம் எடுக்க (Camera)"])

uploaded_file = None
if input_mode == "கோப்பை பதிவேற்ற (Upload)":
    uploaded_file = st.file_uploader("படம் அல்லது PDF-ஐப் பதிவேற்றவும்", type=["png", "jpg", "jpeg", "pdf"])
else:
    uploaded_file = st.camera_input("நேரடியாகப் படம் எடுக்கவும்")

if uploaded_file is not None:
    is_pdf = uploaded_file.type == "application/pdf"
    file_bytes = uploaded_file.getvalue()
    file_signature = (uploaded_file.name, len(file_bytes), uploaded_file.type, language, pdf_dpi)

    if st.session_state.get("file_signature") != file_signature:
        st.session_state["ocr_text"] = ""
        st.session_state["ocr_error"] = ""
        st.session_state["ocr_done"] = False
        st.session_state["file_signature"] = file_signature
    
    col1, col2 = st.columns(2)

    with col1:
        st.subheader("Preview & Edit")
        if not is_pdf:
            img = Image.open(io.BytesIO(file_bytes))
            rotation = st.slider("படத்தைத் திருப்ப (Rotate)", 0, 360, 0, step=90)
            if rotation != 0:
                img = img.rotate(-rotation, expand=True)
            st.image(img, use_container_width=True)
        else:
            try:
                pdf_info = pdfinfo_from_bytes(file_bytes)
                total_pages = int(pdf_info.get("Pages", 0))
                file_size_mb = len(file_bytes) / (1024 * 1024)
                st.info(f"PDF தயார் நிலையில் உள்ளது. Pages: {total_pages} | Size: {file_size_mb:.2f} MB")
            except Exception:
                total_pages = None
                st.info("PDF கோப்பு தயார் நிலையில் உள்ளது.")

    if st.button("🚀 Start OCR"):
        with st.spinner("எழுத்துக்களைப் பிரித்தெடுக்கிறது..."):
            try:
                extracted_text = ""
                # High accuracy config
                custom_config = r'--oem 3 --psm 3'
                
                if is_pdf:
                    pdf_info = pdfinfo_from_bytes(file_bytes)
                    total_pages = int(pdf_info.get("Pages", 0))
                    if total_pages <= 0:
                        raise ValueError("PDF-ல் எந்த page-யும் கண்டுபிடிக்க முடியவில்லை.")
                    progress_bar = st.progress(0, text="PDF பக்கங்கள் தயார் செய்யப்படுகிறது...")

                    for i in range(total_pages):
                        images = convert_from_bytes(
                            file_bytes,
                            dpi=pdf_dpi,
                            first_page=i + 1,
                            last_page=i + 1,
                            fmt="png",
                            thread_count=1
                        )
                        image = images[0]
                        image = improve_image(image)
                        text = pytesseract.image_to_string(image, lang=lang_code, config=custom_config)
                        extracted_text += f"\n--- Page {i+1} ---\n{text}\n"
                        progress_bar.progress((i + 1) / total_pages, text=f"OCR நடைபெறுகிறது... Page {i + 1} / {total_pages}")

                    progress_bar.empty()
                else:
                    processed_img = improve_image(img)
                    extracted_text = pytesseract.image_to_string(processed_img, lang=lang_code, config=custom_config)

                st.session_state["ocr_text"] = extracted_text
                st.session_state["ocr_error"] = ""
                st.session_state["ocr_done"] = True
            except Exception as e:
                st.session_state["ocr_text"] = ""
                st.session_state["ocr_done"] = False
                st.session_state["ocr_error"] = f"பிழை ஏற்பட்டுள்ளது: {e}"

    with col2:
        st.subheader("Extracted Text")
        if st.session_state.get("ocr_error"):
            st.error(st.session_state["ocr_error"])
        elif st.session_state.get("ocr_done"):
            if st.session_state["ocr_text"].strip():
                st.text_area("கண்டறியப்பட்ட உரை:", st.session_state["ocr_text"], height=400)

                c1, c2 = st.columns(2)
                with c1:
                    st.download_button(
                        "📥 Download Word (.docx)",
                        data=create_word_doc(st.session_state["ocr_text"]),
                        file_name="TamizhVizhi_Output.docx"
                    )
                with c2:
                    st.download_button(
                        "📄 Download Text (.txt)",
                        data=st.session_state["ocr_text"],
                        file_name="TamizhVizhi_Output.txt"
                    )

                st.success("வெற்றிகரமாக முடிக்கப்பட்டது!")
            else:
                st.error("மன்னிக்கவும், எழுத்துக்களைக் கண்டறிய முடியவில்லை.")

st.markdown("---")
st.caption("Developed by Nandhu | TamizhVizhi OCR Project")
