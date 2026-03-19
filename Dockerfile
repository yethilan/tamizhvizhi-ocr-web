# Python இமேஜைப் பயன்படுத்துதல்
FROM python:3.9-slim

# சிஸ்டம் அப்டேட் மற்றும் Tesseract, Poppler நிறுவுதல்
RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    tesseract-ocr-tam \
    poppler-utils \
    libgl1-mesa-glx \
    && rm -rf /var/lib/apt/lists/*

# ஆப் கோப்புகளை காப்பி செய்தல்
WORKDIR /app
COPY . /app

# தேவையான Python லைப்ரரிகளை நிறுவுதல்
RUN pip install --no-cache-dir -r requirements.txt

# Streamlit ரன் ஆவதற்கான போர்ட்
EXPOSE 8501

# ஆப்பை இயக்குவதற்கான கமெண்ட்
CMD ["streamlit", "run", "app.py", "--server.port=8501", "--server.address=0.0.0.0"]
