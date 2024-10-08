FROM python:3.10-slim
RUN DEBIAN_FRONTEND=noninteractive apt-get update && apt-get install --assume-yes --quiet --no-install-recommends build-essential
COPY . /usr/src/app
WORKDIR /usr/src/app
RUN ["pip", "install", "--no-cache-dir", "-r", "requirements.txt"]
EXPOSE 8000
CMD ["gunicorn", "--bind" , ":8000", "--workers", "1", "main:flask_app"]
