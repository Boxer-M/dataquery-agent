FROM node:24-slim@sha256:dc26ae92c9fb15cf6b1d63e20f0944b2efe0f115b20e00be48fb23fceb061c54 AS build
WORKDIR /work
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM node:24-slim@sha256:dc26ae92c9fb15cf6b1d63e20f0944b2efe0f115b20e00be48fb23fceb061c54
WORKDIR /work
RUN mkdir -p /run/frontend && chown node:node /run/frontend
COPY --from=build --chown=node:node /work/dist ./dist
COPY --from=build --chown=node:node /work/server/local-api.mjs /work/server/production.mjs ./server/
USER node
CMD ["node", "server/production.mjs"]
