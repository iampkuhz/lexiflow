#!/bin/sh
printf '%s\n' "$*" > "$LOG"
printf 'JVM_ENV=%s|%s|%s\n' "${JAVA_TOOL_OPTIONS:-}" "${JDK_JAVA_OPTIONS:-}" "${_JAVA_OPTIONS:-}" >> "$LOG"
printf 'DB_ENV=%s|%s\n' "${SPRING_DATASOURCE_URL:-}" "${SPRING_DATASOURCE_USERNAME:-}" >> "$LOG"
if [ -z "${SPRING_DATASOURCE_PASSWORD:-}" ]; then printf 'DB_PASSWORD_EMPTY=true\n' >> "$LOG"; else printf 'DB_PASSWORD_EMPTY=false\n' >> "$LOG"; fi
exit "${JAVA_EXIT:-0}"
