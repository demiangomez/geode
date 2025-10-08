# Installation

This document explains how to deploy the app using Docker Compose. We will use it to build and start two containers with a single command: one for the back end and one for the front end.

### Prerequirements

1. Docker installed.

### Procedure

All configuration files params are explained below.

1. Define a conf file named ".env" inside the root folder following '.env.sample'
2. Define a conf file named "gnss_data.cfg" under 'backend_dir/backend/' following 'backend_dir/backend/conf_example.txt'
3. Define a conf file named ".env" under 'front_dir/' following 'front_dir/.env.sample'
4. From the root directory:

```
   docker compose up --build -d
```

### Params Explanation

.env file on root folder
1. MEDIA_FOLDER_HOST_PATH: path to where media (documents, images, etc.) uploaded by the user will be placed.
2. APP_PORT: port where the app will be served.
3. USER_ID_TO_SAVE_FILES: all the media saved by the app will be owned by this user.    
4. GROUP_ID_TO_SAVE_FILES: all the media saved by the app will be owned by this group. 
5. SERVER_NAME: domain name where the app will be served
6. NGINX_PORT: leave it in "5100"

.env file on front_dir folder
1. VITE_API_URL: The API URL is the same as the app URL (as the front end and the back end are running on the same machine). For example, if users access the app at https://192.168.18.10:2375/, then that will also be the value for this parameter.
2. SERVER_NAME: domain name where the app will be served
3. NGINX_PORT: same as NGINX_PORT in root .env file
4. APP_PORT: same as APP_PORT in root .env file

gnss_data.cfg on backend_dir/backend/
[django]
1. rinex_status_date_span_seconds: Time window (in seconds) to group RINEX records by their observation date.
2. secret_key: Used by Django for cryptographic signing (can be generated on https://djecrety.ir/)
3. debug: Enables Django's debug mode with detailed error pages. Should be False in production
4. max_size_image_mb: Maximum allowed image upload size
5. max_size_file_mb: Maximum allowed file upload size
6. https: False
7. user_id_to_save_files: same as USER_ID_TO_SAVE_FILES in .env file
8. group_id_to_save_files: same as GROUP_ID_TO_SAVE_FILES in .env file