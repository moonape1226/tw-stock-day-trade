FROM python:3.12-slim

ENV TZ=Asia/Taipei \
    PYTHONUNBUFFERED=1

# tzdata 讓容器內 date(baseline 排程) 正確套用 Asia/Taipei;
# Python 程式邏輯本身用固定 +8 偏移常數,不依賴系統時區。
RUN apt-get update \
 && apt-get install -y --no-install-recommends tzdata \
 && rm -rf /var/lib/apt/lists/* \
 && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime \
 && echo $TZ > /etc/timezone

WORKDIR /app
COPY . /app

# 三支程式僅用 Python 標準函式庫,無第三方依賴,故無 pip install。
CMD ["python3", "watch.py"]
