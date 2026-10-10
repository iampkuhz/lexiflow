/** Only a local port is configurable; builds cannot silently send captions to remote hosts. */
export function localApiPort(value) {
  if (value === undefined) return 18080;
  if (!/^[1-9][0-9]{0,4}$/.test(value) || Number(value) > 65535) {
    throw new Error("LEXIFLOW_API_PORT must be a canonical integer port from 1 to 65535");
  }
  return Number(value);
}
