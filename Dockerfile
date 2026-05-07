# Imagem oficial Playwright Python — já vem com Chromium + libs do SO
# (libnss, libxkbcommon, fontconfig, etc.) — necessário pro src/render.py
# rodar HTML→PNG no Railway via Playwright/Chromium.
FROM mcr.microsoft.com/playwright/python:v1.48.0-jammy

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Instala deps Python (Playwright já está instalado no base; instalamos novamente
# pra garantir versão pinada do requirements). Os browsers (chromium) já estão
# no path padrão da imagem base.
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copia o resto do código
COPY . /app

# Railway usa este CMD (equivalente ao Procfile worker:)
CMD ["python", "-u", "src/scheduler.py"]
