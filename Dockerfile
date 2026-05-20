FROM python:3.12-slim

WORKDIR /app

COPY . .

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app
ENV TZ=Asia/Shanghai

RUN pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt