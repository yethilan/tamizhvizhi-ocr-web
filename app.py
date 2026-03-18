import io
import json
import re
import zipfile
from datetime import datetime

import pytesseract
import streamlit as st
from docx import Document
from pdf2image import convert_from_bytes, pdfinfo_from_bytes
from PIL import Image, ImageEnhance, ImageFilter
from pytesseract import Output


st.set_page_config(page_title="TamizhVizhi Pro OCR", layout="wide", page_icon="🎯")

st.title("🎯 TamizhVizhi OCR Pro")
st.write("Image / PDF / Camera மூலம் தமிழை உரையாக (Text) மாற்றுங்கள்!")


st.sidebar.header("⚙️ Settings")
language = st.sidebar.selectbox("மொழியைத் தேர்ந்தெடுக்கவும்", ["Tamil", "English", "Tamil + English"])
lang_code = "tam" if language == "Tamil" else "eng" if language == "English" else "tam+eng"

psm_options = {
    "Auto Layout": 3,
    "Uniform Text Block": 6,
    "Single Line": 7,
    "Sparse Text": 11,
    "Sparse Text with OSD": 12,
}
psm_label = st.sidebar.selectbox("OCR Layout Mode (PSM)", list(psm_options.keys()), index=1)
pdf_dpi = st.sidebar.slider("PDF OCR தரம் (DPI)", 150, 300, 200, step=25)
preview_dpi = st.sidebar.slider("Preview DPI", 100, 200, 120, step=10)

auto_rotate = st.sidebar.checkbox("Auto Rotate / Deskew முயற்சி", value=True)
apply_threshold = st.sidebar.checkbox("Black & White Threshold", value=False)
threshold_value = st.sidebar.slider("Threshold", 80, 220, 150, step=5, disabled=not apply_threshold)
brightness_value = st.sidebar.slider("Brightness", 0.5, 2.0, 1.0, step=0.1)
contrast_value = st.sidebar.slider("Contrast", 1.0, 3.0, 2.0, step=0.1)
sharpness_value = st.sidebar.slider("Sharpness", 1.0, 3.0, 1.2, step=0.1)

enable_crop = st.sidebar.checkbox("Crop before OCR", value=False)
if enable_crop:
    crop_left = st.sidebar.slider("Crop Left %", 0, 40, 0, step=1)
    crop_right = st.sidebar.slider("Crop Right %", 0, 40, 0, step=1)
    crop_top = st.sidebar.slider("Crop Top %", 0, 40, 0, step=1)
    crop_bottom = st.sidebar.slider("Crop Bottom %", 0, 40, 0, step=1)
else:
    crop_left = crop_right = crop_top = crop_bottom = 0

page_range_text = st.sidebar.text_input("PDF page range", value="all", help="உதாரணம்: all, 1-5, 1,3,7-9")


input_mode = st.radio("உள்ளீட்டு முறையைத் தேர்வு செய்யவும்:", ["கோப்பை பதிவேற்ற (Upload)", "படம் எடுக்க (Camera)"])


DEFAULT_STATE = {
    "ocr_results": [],
    "ocr_error": "",
    "ocr_done": False,
    "input_signature": None,
    "ocr_history": [],
}
for key, value in DEFAULT_STATE.items():
    if key not in st.session_state:
        st.session_state[key] = value


def reset_current_results():
    st.session_state["ocr_results"] = []
    st.session_state["ocr_error"] = ""
    st.session_state["ocr_done"] = False


def create_word_doc(text):
    doc = Document()
    doc.add_paragraph(text)
    bio = io.BytesIO()
    doc.save(bio)
    return bio.getvalue()


def create_json_bytes(payload):
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")


def safe_base_name(name):
    stem = name.rsplit(".", 1)[0]
    cleaned = re.sub(r"[^\w\-]+", "_", stem).strip("_")
    return cleaned or "output"


def average_confidence(confidences):
    values = [value for value in confidences if value is not None]
    if not values:
        return None
    return round(sum(values) / len(values), 2)


def build_combined_text(result):
    parts = []
    for page in result["pages"]:
        parts.append(f"--- Page {page['page_number']} ---\n{page['text']}")
    return "\n\n".join(parts).strip()


def build_result_payload(result):
    return {
        "file_name": result["file_name"],
        "file_type": result["file_type"],
        "file_size_mb": result["file_size_mb"],
        "pages": [
            {
                "page_number": page["page_number"],
                "confidence": page["confidence"],
                "text": page["text"],
            }
            for page in result["pages"]
        ],
        "combined_text": build_combined_text(result),
        "average_confidence": average_confidence([page["confidence"] for page in result["pages"]]),
    }


def create_zip_bundle(results):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for result in results:
            base_name = safe_base_name(result["file_name"])
            combined_text = build_combined_text(result)
            archive.writestr(f"{base_name}.txt", combined_text)
            archive.writestr(f"{base_name}.json", create_json_bytes(build_result_payload(result)))
            archive.writestr(f"{base_name}.docx", create_word_doc(combined_text))
    return buffer.getvalue()


def parse_page_range(selection, total_pages):
    if total_pages <= 0:
        raise ValueError("PDF-ல் எந்த page-யும் கண்டுபிடிக்க முடியவில்லை.")

    selection = selection.strip().lower()
    if not selection or selection == "all":
        return list(range(1, total_pages + 1))

    pages = set()
    for part in selection.split(","):
        token = part.strip()
        if not token:
            continue
        if "-" in token:
            start_text, end_text = token.split("-", 1)
            start = int(start_text)
            end = int(end_text)
            if start > end:
                start, end = end, start
            for page in range(start, end + 1):
                if 1 <= page <= total_pages:
                    pages.add(page)
        else:
            page = int(token)
            if 1 <= page <= total_pages:
                pages.add(page)

    if not pages:
        raise ValueError("சரியான page range-ஐ கொடுக்கவும்.")

    return sorted(pages)


def detect_rotation(image):
    try:
        osd = pytesseract.image_to_osd(image)
        match = re.search(r"Rotate:\s+(\d+)", osd)
        if match:
            return int(match.group(1))
    except Exception:
        return 0
    return 0


def crop_image(image):
    if not enable_crop:
        return image

    width, height = image.size
    left = int(width * crop_left / 100)
    right = int(width * (100 - crop_right) / 100)
    top = int(height * crop_top / 100)
    bottom = int(height * (100 - crop_bottom) / 100)

    if right <= left or bottom <= top:
        return image

    return image.crop((left, top, right, bottom))


def improve_image(image):
    image = crop_image(image)
    if auto_rotate:
        rotation = detect_rotation(image)
        if rotation:
            image = image.rotate(-rotation, expand=True)

    image = image.convert("L")
    image = ImageEnhance.Brightness(image).enhance(brightness_value)
    image = ImageEnhance.Contrast(image).enhance(contrast_value)
    image = ImageEnhance.Sharpness(image).enhance(sharpness_value)

    if apply_threshold:
        image = image.point(lambda pixel: 255 if pixel > threshold_value else 0)

    return image


def extract_page_text(image):
    custom_config = f"--oem 3 --psm {psm_options[psm_label]}"
    text = pytesseract.image_to_string(image, lang=lang_code, config=custom_config)
    data = pytesseract.image_to_data(image, lang=lang_code, config=custom_config, output_type=Output.DICT)

    confidences = []
    for text_value, conf in zip(data.get("text", []), data.get("conf", [])):
        try:
            numeric_conf = float(conf)
        except (TypeError, ValueError):
            continue
        if text_value.strip() and numeric_conf >= 0:
            confidences.append(numeric_conf)

    confidence = round(sum(confidences) / len(confidences), 2) if confidences else None
    return text, confidence


def file_label(uploaded_item, index):
    if getattr(uploaded_item, "name", None):
        return uploaded_item.name
    return f"camera_capture_{index + 1}.jpg"


def make_input_signature(items):
    file_bits = []
    for index, item in enumerate(items):
        item_bytes = item.getvalue()
        file_bits.append((file_label(item, index), len(item_bytes), item.type))
    return (
        tuple(file_bits),
        input_mode,
        language,
        pdf_dpi,
        psm_label,
        auto_rotate,
        apply_threshold,
        threshold_value,
        brightness_value,
        contrast_value,
        sharpness_value,
        enable_crop,
        crop_left,
        crop_right,
        crop_top,
        crop_bottom,
        page_range_text,
    )


def process_uploaded_file(uploaded_item, index, progress_bar, status_text, overall_position, overall_total):
    item_bytes = uploaded_item.getvalue()
    item_name = file_label(uploaded_item, index)
    item_type = uploaded_item.type
    is_pdf = item_type == "application/pdf" or item_name.lower().endswith(".pdf")

    result = {
        "file_name": item_name,
        "file_type": item_type,
        "file_size_mb": round(len(item_bytes) / (1024 * 1024), 2),
        "pages": [],
        "preview_page": 1,
    }

    if is_pdf:
        pdf_info = pdfinfo_from_bytes(item_bytes)
        total_pages = int(pdf_info.get("Pages", 0))
        page_numbers = parse_page_range(page_range_text, total_pages)
        result["total_pages"] = total_pages
        result["selected_pages"] = page_numbers

        for local_index, page_number in enumerate(page_numbers, start=1):
            status_text.write(f"{item_name}: Page {page_number} OCR நடைபெறுகிறது...")
            images = convert_from_bytes(
                item_bytes,
                dpi=pdf_dpi,
                first_page=page_number,
                last_page=page_number,
                fmt="png",
                thread_count=1,
            )
            processed_image = improve_image(images[0])
            text, confidence = extract_page_text(processed_image)
            result["pages"].append({
                "page_number": page_number,
                "text": text.strip(),
                "confidence": confidence,
            })
            completed = overall_position + local_index
            progress_bar.progress(min(completed / overall_total, 1.0), text=f"{item_name}: {local_index}/{len(page_numbers)} pages முடிந்தது")
    else:
        status_text.write(f"{item_name}: OCR நடைபெறுகிறது...")
        image = Image.open(io.BytesIO(item_bytes))
        processed_image = improve_image(image)
        text, confidence = extract_page_text(processed_image)
        result["total_pages"] = 1
        result["selected_pages"] = [1]
        result["pages"].append({
            "page_number": 1,
            "text": text.strip(),
            "confidence": confidence,
        })
        progress_bar.progress(min((overall_position + 1) / overall_total, 1.0), text=f"{item_name}: 1/1 page முடிந்தது")

    return result


def replace_text_in_results(find_text, replace_text, file_index=None):
    if not find_text:
        return
    targets = [st.session_state["ocr_results"][file_index]] if file_index is not None else st.session_state["ocr_results"]
    for result in targets:
        for page in result["pages"]:
            page["text"] = page["text"].replace(find_text, replace_text)


uploaded_items = []
if input_mode == "கோப்பை பதிவேற்ற (Upload)":
    uploaded_items = st.file_uploader(
        "படம் அல்லது PDF-ஐப் பதிவேற்றவும்",
        type=["png", "jpg", "jpeg", "pdf"],
        accept_multiple_files=True,
    ) or []
else:
    camera_file = st.camera_input("நேரடியாகப் படம் எடுக்கவும்")
    if camera_file is not None:
        uploaded_items = [camera_file]

if uploaded_items:
    current_signature = make_input_signature(uploaded_items)
    if st.session_state["input_signature"] != current_signature:
        reset_current_results()
        st.session_state["input_signature"] = current_signature

    preview_col, output_col = st.columns([1, 1.4])

    with preview_col:
        st.subheader("Preview & Controls")
        preview_name = st.selectbox("Preview file", [file_label(item, idx) for idx, item in enumerate(uploaded_items)])
        preview_index = [file_label(item, idx) for idx, item in enumerate(uploaded_items)].index(preview_name)
        preview_item = uploaded_items[preview_index]
        preview_bytes = preview_item.getvalue()
        preview_is_pdf = preview_item.type == "application/pdf" or preview_name.lower().endswith(".pdf")

        if preview_is_pdf:
            try:
                preview_info = pdfinfo_from_bytes(preview_bytes)
                preview_pages = int(preview_info.get("Pages", 0))
                preview_page_number = st.number_input("Preview page", min_value=1, max_value=max(preview_pages, 1), value=1, step=1)
                preview_images = convert_from_bytes(
                    preview_bytes,
                    dpi=preview_dpi,
                    first_page=preview_page_number,
                    last_page=preview_page_number,
                    fmt="png",
                    thread_count=1,
                )
                st.image(preview_images[0], caption=f"{preview_name} - Page {preview_page_number}", use_container_width=True)
                st.caption(f"Pages: {preview_pages} | Size: {len(preview_bytes) / (1024 * 1024):.2f} MB")
            except Exception as error:
                st.error(f"PDF preview error: {error}")
        else:
            preview_image = Image.open(io.BytesIO(preview_bytes))
            preview_rotation = st.slider("Preview Rotate", 0, 360, 0, step=90)
            if preview_rotation:
                preview_image = preview_image.rotate(-preview_rotation, expand=True)
            preview_processed = st.checkbox("Processed preview show", value=False)
            if preview_processed:
                preview_image = improve_image(preview_image)
            st.image(preview_image, caption=preview_name, use_container_width=True)
            st.caption(f"Size: {len(preview_bytes) / (1024 * 1024):.2f} MB")

        if st.button("🚀 Start OCR", use_container_width=True):
            try:
                total_work_units = 0
                for index, item in enumerate(uploaded_items):
                    item_name = file_label(item, index)
                    item_bytes = item.getvalue()
                    if item.type == "application/pdf" or item_name.lower().endswith(".pdf"):
                        info = pdfinfo_from_bytes(item_bytes)
                        total_work_units += len(parse_page_range(page_range_text, int(info.get("Pages", 0))))
                    else:
                        total_work_units += 1

                progress_bar = st.progress(0, text="OCR தொடங்குகிறது...")
                status_text = st.empty()
                results = []
                completed_units = 0

                for index, item in enumerate(uploaded_items):
                    result = process_uploaded_file(item, index, progress_bar, status_text, completed_units, total_work_units)
                    results.append(result)
                    completed_units += len(result["pages"])

                st.session_state["ocr_results"] = results
                st.session_state["ocr_error"] = ""
                st.session_state["ocr_done"] = True
                progress_bar.empty()
                status_text.empty()
                st.session_state["ocr_history"].append({
                    "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "files": len(results),
                    "pages": sum(len(result["pages"]) for result in results),
                    "language": language,
                })
            except Exception as error:
                reset_current_results()
                st.session_state["ocr_error"] = f"பிழை ஏற்பட்டுள்ளது: {error}"

    with output_col:
        st.subheader("Extracted Output")
        if st.session_state["ocr_error"]:
            st.error(st.session_state["ocr_error"])

        if st.session_state["ocr_done"] and st.session_state["ocr_results"]:
            results = st.session_state["ocr_results"]
            total_files = len(results)
            total_pages = sum(len(result["pages"]) for result in results)
            confidence_values = [page["confidence"] for result in results for page in result["pages"] if page["confidence"] is not None]
            overall_confidence = round(sum(confidence_values) / len(confidence_values), 2) if confidence_values else None

            metric_a, metric_b, metric_c = st.columns(3)
            metric_a.metric("Files", total_files)
            metric_b.metric("Pages OCR", total_pages)
            metric_c.metric("Avg Confidence", f"{overall_confidence}%" if overall_confidence is not None else "N/A")

            st.markdown("### Global Tools")
            global_find, global_replace = st.columns(2)
            with global_find:
                find_text = st.text_input("Find text", key="global_find_text")
            with global_replace:
                replace_text = st.text_input("Replace text", key="global_replace_text")

            action_col1, action_col2 = st.columns(2)
            with action_col1:
                if st.button("Replace in all files", use_container_width=True):
                    replace_text_in_results(find_text, replace_text)
            with action_col2:
                st.download_button(
                    "📦 Download ZIP Bundle",
                    data=create_zip_bundle(results),
                    file_name="TamizhVizhi_Bundle.zip",
                    use_container_width=True,
                )

            file_tabs = st.tabs([result["file_name"] for result in results])
            for file_index, (tab, result) in enumerate(zip(file_tabs, results)):
                with tab:
                    avg_conf = average_confidence([page["confidence"] for page in result["pages"]])
                    st.caption(
                        f"Pages processed: {len(result['pages'])} | File size: {result['file_size_mb']:.2f} MB | Average confidence: {avg_conf if avg_conf is not None else 'N/A'}"
                    )

                    local_find_col, local_replace_col = st.columns(2)
                    with local_find_col:
                        local_find = st.text_input("Find", key=f"local_find_{file_index}")
                    with local_replace_col:
                        local_replace = st.text_input("Replace", key=f"local_replace_{file_index}")
                    if st.button("Replace in this file", key=f"replace_btn_{file_index}"):
                        replace_text_in_results(local_find, local_replace, file_index=file_index)

                    page_tabs = st.tabs([
                        f"Page {page['page_number']} ({page['confidence']}%)" if page["confidence"] is not None else f"Page {page['page_number']}"
                        for page in result["pages"]
                    ])
                    for page_index, (page_tab, page) in enumerate(zip(page_tabs, result["pages"])):
                        with page_tab:
                            edited_text = st.text_area(
                                f"Page {page['page_number']} text",
                                value=page["text"],
                                height=220,
                                key=f"page_text_{file_index}_{page_index}",
                            )
                            st.session_state["ocr_results"][file_index]["pages"][page_index]["text"] = edited_text
                            if page["confidence"] is not None and page["confidence"] < 75:
                                st.warning(f"இந்த page confidence குறைவாக உள்ளது: {page['confidence']}%")

                    combined_text = build_combined_text(st.session_state["ocr_results"][file_index])
                    st.text_area(
                        "Combined output",
                        value=combined_text,
                        height=220,
                        key=f"combined_view_{file_index}",
                    )

                    download_col1, download_col2, download_col3 = st.columns(3)
                    with download_col1:
                        st.download_button(
                            "📄 TXT",
                            data=combined_text,
                            file_name=f"{safe_base_name(result['file_name'])}.txt",
                            key=f"txt_dl_{file_index}",
                            use_container_width=True,
                        )
                    with download_col2:
                        st.download_button(
                            "📥 DOCX",
                            data=create_word_doc(combined_text),
                            file_name=f"{safe_base_name(result['file_name'])}.docx",
                            key=f"docx_dl_{file_index}",
                            use_container_width=True,
                        )
                    with download_col3:
                        st.download_button(
                            "🧾 JSON",
                            data=create_json_bytes(build_result_payload(st.session_state['ocr_results'][file_index])),
                            file_name=f"{safe_base_name(result['file_name'])}.json",
                            key=f"json_dl_{file_index}",
                            use_container_width=True,
                        )

            with st.expander("OCR History"):
                if st.session_state["ocr_history"]:
                    for item in reversed(st.session_state["ocr_history"]):
                        st.write(f"{item['time']} | Files: {item['files']} | Pages: {item['pages']} | Language: {item['language']}")
                else:
                    st.caption("History இன்னும் இல்லை.")

st.markdown("---")
st.caption("Developed by Nandhu | TamizhVizhi OCR Project")
