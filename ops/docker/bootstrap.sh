#!/bin/sh
set -eu

[ "$#" -eq 0 ] || exit 64
[ -r /run/secrets/app-password ] || exit 64
bytes=$(wc -c < /run/secrets/app-password | tr -d ' ')
case "$bytes" in
  64) ;;
  65) [ "$(tail -c 1 /run/secrets/app-password | od -An -t x1 | tr -d ' \n')" = 0a ] || exit 64 ;;
  *) exit 64 ;;
esac
IFS= read -r password < /run/secrets/app-password || [ -n "${password:-}" ] || exit 64
case "$password" in *[!0-9a-f]*|'') exit 64;; esac
[ "${#password}" -eq 64 ] || exit 64
if ! psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --set ON_ERROR_STOP=1 >/dev/null 2>&1 <<SQL
\\set app_password '$password'
CREATE ROLE lexiflow LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT PASSWORD :'app_password';
CREATE SCHEMA lexiflow_release AUTHORIZATION lexiflow;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT CONNECT ON DATABASE lexiflow TO lexiflow;
GRANT USAGE, CREATE ON SCHEMA lexiflow_release TO lexiflow;
SQL
then
  printf '%s\n' 'lexiflow database bootstrap failed' >&2
  exit 1
fi
unset password
