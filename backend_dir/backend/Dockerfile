# syntax=docker/dockerfile:1
FROM python:3.11

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

ARG USER_ID_TO_SAVE_FILES
ARG GROUP_ID_TO_SAVE_FILES

# Install system dependencies
RUN apt-get update && apt-get install -y \
    supervisor \
    redis-server \
    libpq-dev \
    python3-dev \
    build-essential \
    libffi-dev \
    libssl-dev

# Create non-root user
RUN groupadd -g ${GROUP_ID_TO_SAVE_FILES} dummy-user && \
    useradd -u ${USER_ID_TO_SAVE_FILES} -g dummy-user -m dummy-user

# Install Python dependencies
RUN pip install --upgrade pip && \
    pip install wheel setuptools --upgrade

WORKDIR /code

COPY requirements.txt /code/
COPY modified_packages/drf-spectacular /code/modified_packages/drf-spectacular
RUN pip install --no-cache-dir -r requirements.txt

COPY . /code/

RUN mkdir -p /var/log/gunicorn && chmod 755 /var/log/gunicorn

COPY ./supervisord.conf /etc/supervisor/conf.d/supervisord.conf
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
