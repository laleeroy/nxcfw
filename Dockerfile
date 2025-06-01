FROM devkitpro/devkita64:latest

WORKDIR /app

RUN apt-get update -y && \
    apt-get install -y --no-install-recommends \
        jq \
        curl \
        zip \
        git \
        python3-venv && \
    rm -rf /var/lib/apt/lists/*

COPY ./requirements.txt /tmp/requirements.txt

RUN python3 -m venv /opt/venv && \
    . /opt/venv/bin/activate && \
    pip install --no-cache-dir -r /tmp/requirements.txt && \
    rm /tmp/requirements.txt

RUN dkp-pacman -S --needed --noconfirm \
    switch-glm \
    switch-libjpeg-turbo \
    devkitarm-rules \
    devkitA64 \
    hactool
