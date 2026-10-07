# Web image of the appliance: the React build served by nginx, which also
# terminates HTTPS and reverse-proxies /api/ to the backend.
FROM node:20-alpine AS build
WORKDIR /app
COPY package*.json ./
RUN npm ci --no-audit --no-fund
COPY . .
RUN npm run build

FROM nginx:stable-alpine
RUN apk add --no-cache openssl
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY docker/10-certs.sh docker/20-cert-watch.sh /docker-entrypoint.d/
RUN chmod +x /docker-entrypoint.d/10-certs.sh /docker-entrypoint.d/20-cert-watch.sh
EXPOSE 80 443
HEALTHCHECK --interval=30s --timeout=3s CMD wget --no-check-certificate -qO /dev/null https://127.0.0.1/ || exit 1
