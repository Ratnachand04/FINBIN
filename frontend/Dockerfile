FROM nginx:alpine

# Remove default nginx static assets
RUN rm -rf /usr/share/nginx/html/*

# Copy static frontend files (this will be mounted in dev, mapped in prod)
# COPY public /usr/share/nginx/html

# Copy the generated internal nginx conf
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
