FROM node:22 AS build

WORKDIR /app
COPY . /app
COPY .env /app/.env

COPY package*.json /app/

RUN npm ci
RUN npm run build

FROM nginx:latest

# Install Tini and gettext (for envsubst)
RUN apt-get update && apt-get install -y tini gettext-base

COPY --from=build /app/dist /usr/share/nginx/html/
COPY srv/nginx.conf.template /etc/nginx/nginx.conf.template
COPY srv/mime.types /etc/nginx/mime.types

# Configure tini as the init system
ENTRYPOINT ["/usr/bin/tini", "--"]

#EXPOSE 80

# Process template and start nginx
CMD envsubst '${NGINX_PORT},${SERVER_NAME}' < /etc/nginx/nginx.conf.template > /etc/nginx/nginx.conf && nginx -g 'daemon off;'