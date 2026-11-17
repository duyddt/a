FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_DISABLE_PIP_VERSION_CHECK=1

# OCR offline cho PDF scan khi người dùng chỉ tải lên PDF: Tesseract kèm gói tiếng Việt (vie)
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-vie \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY migrations ./migrations
COPY scripts ./scripts

# Chạy bằng tài khoản thường (không phải root); /storage chứa PDF, ảnh trang và khóa ký phiên
RUN useradd --create-home --uid 1000 appuser \
    && mkdir -p /storage \
    && chown appuser:appuser /storage
USER appuser

EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=40s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
