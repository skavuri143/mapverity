# MapVerity CLI image: docker run --rm -v "$PWD:/work" ghcr.io/<owner>/mapverity validate spec.mapverity.yaml
FROM python:3.15-slim AS build
WORKDIR /src
COPY pyproject.toml README.md LICENSE NOTICE ./
COPY src ./src
RUN pip install --no-cache-dir build && python -m build --wheel --outdir /dist

FROM python:3.15-slim
LABEL org.opencontainers.image.title="MapVerity" \
      org.opencontainers.image.description="Field-level assurance for data pipelines" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.source="https://github.com/YOUR-GITHUB-USER/mapverity"
ARG EXTRAS="aws,sql,api,excel,parquet"
COPY --from=build /dist /dist
RUN pip install --no-cache-dir "$(ls /dist/*.whl)[${EXTRAS}]" && rm -rf /dist \
    && useradd --create-home --uid 10001 mapverity
USER mapverity
WORKDIR /work
ENTRYPOINT ["mapverity"]
CMD ["--help"]
