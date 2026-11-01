# =============================================================================
# Imagen del EJECUTOR: el entorno dentro del cual se procesa cada repositorio.
# Se construye una sola vez:   python -m pipeline construir-imagen
# Cada repositorio corre luego en un contenedor NUEVO creado a partir de esta imagen.
# =============================================================================
FROM python:3.12-slim-bookworm

# git: algunos repos instalan dependencias desde GitHub.
# build-essential: algunas dependencias se compilan al instalarse.
RUN apt-get update \
 && apt-get install -y --no-install-recommends git build-essential \
 && rm -rf /var/lib/apt/lists/*

# --- Qodo Cover en un entorno virtual PROPIO (/opt/qodo) -----------------------
# Separado del Python "del repositorio" para que las dependencias de uno no rompan al otro.
# Fijado a un commit exacto (el repositorio no se mantiene desde junio de 2025).
ARG QODO_COVER_COMMIT=adcf55baba40e8fbf8a84e68b70d08bef1e2b9c9
COPY qodo-constraints.txt /tmp/qodo-constraints.txt
RUN python -m venv /opt/qodo \
 && /opt/qodo/bin/pip install --no-cache-dir --upgrade pip \
 && /opt/qodo/bin/pip install --no-cache-dir -c /tmp/qodo-constraints.txt \
      "cover-agent @ git+https://github.com/qodo-ai/qodo-cover.git@${QODO_COVER_COMMIT}"

# --- Ajustes del estudio sobre Qodo Cover (ver docker/parche_qodo.py y docker/tesis_llm.py) ---
# Controlan el consumo del modelo (nivel de razonamiento, explicación local de las fallas) y anotan
# los tokens de cada llamada. No cambian los criterios de filtrado de la herramienta. Si el código de
# cover-agent no fuera el esperado, la construcción de la imagen falla en vez de aplicarlos a ciegas.
COPY tesis_llm.py parche_qodo.py /tmp/tesis/
RUN /opt/qodo/bin/python /tmp/tesis/parche_qodo.py

# --- Python "del repositorio": acá se instalan sus dependencias y corren sus pruebas ---
RUN python -m pip install --no-cache-dir pytest==9.1.1 pytest-cov==7.1.0 pytest-timeout==2.4.0

ENV PYTHON_REPO=/usr/local/bin/python \
    QODO_BIN=/opt/qodo/bin/cover-agent \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /trabajo
