FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# 공개 배포 모드 (개인 KIS 키 저장 차단, 요청 제한 활성화)
ENV STOCKLENS_PUBLIC=1

# 클라우드가 주입하는 $PORT 로 바인딩 (없으면 8080)
CMD uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080}
