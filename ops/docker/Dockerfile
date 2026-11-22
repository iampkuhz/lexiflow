ARG JAVA_RUNTIME_IMAGE
FROM ${JAVA_RUNTIME_IMAGE}
WORKDIR /app
COPY --chown=10001:10001 lexiflow-api.jar /app/lexiflow-api.jar
COPY --chown=10001:10001 --chmod=0555 run-container-command.sh /app/run-container-command.sh
RUN test -x /bin/bash && test -x /usr/bin/timeout && test -x /usr/bin/env \
    && mkdir -p /work /var/lib/lexiflow/preferences \
    && chown -R 10001:10001 /work /var/lib/lexiflow
USER 10001:10001
ENTRYPOINT ["/app/run-container-command.sh"]
