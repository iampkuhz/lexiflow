/** Only a local port is configurable; builds cannot silently send captions to remote hosts. */
export function localApiPort(value) {
  if (value === undefined) return 18080;
  if (value !== "18080" && value !== "18081") {
    throw new Error("LEXIFLOW_API_PORT may only select fixed formal port 18080 or explicit development port 18081");
  }
  return Number(value);
}
