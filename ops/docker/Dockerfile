ARG JAVA_RUNTIME_IMAGE
FROM ${JAVA_RUNTIME_IMAGE}
WORKDIR /app
COPY --chown=10001:10001 lexiflow-api.jar /app/lexiflow-api.jar
COPY --chown=10001:10001 --chmod=0555 entrypoint.sh /app/entrypoint.sh
RUN mkdir /work && chown 10001:10001 /work
USER 10001:10001
ENTRYPOINT ["/app/entrypoint.sh"]
