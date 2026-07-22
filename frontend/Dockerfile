FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
ARG VITE_ADMIN_API_TOKEN=
ARG VITE_USER_ID=local-user
ENV VITE_ADMIN_API_TOKEN=${VITE_ADMIN_API_TOKEN}
ENV VITE_USER_ID=${VITE_USER_ID}
RUN npm run build

FROM nginxinc/nginx-unprivileged:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 8080
