### Steps to deploy using Docker in local system
1. Build the image `sudo docker build -t reqForward .`
2. Run the image
```sh
sudo docker run --name reqForward -d \
--restart unless-stopped \
-p 8010:8000 \
-e CONFIG_FILE_URL="paste_config_file_url" \
reqForward gunicorn --workers 1 --bind 0.0.0.0:8000 main:flask_app`
```
3. Navigate to `http://127.0.0.1:8010/status`
