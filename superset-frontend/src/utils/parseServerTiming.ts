/**
 * Licensed to the Apache Software Foundation (ASF) under one
 * or more contributor license agreements.  See the NOTICE file
 * distributed with this work for additional information
 * regarding copyright ownership.  The ASF licenses this file
 * to you under the Apache License, Version 2.0 (the
 * "License"); you may not use this file except in compliance
 * with the License.  You may obtain a copy of the License at
 *
 *   http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing,
 * software distributed under the License is distributed on an
 * "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
 * KIND, either express or implied.  See the License for the
 * specific language governing permissions and limitations
 * under the License.
 */

/**
 * Parses a `Server-Timing` HTTP header into a map of phase name -> duration (ms).
 *
 * The backend emits phases such as `total`, `db`, and `cache` (see
 * `superset/utils/server_timing.py`). Folding these into the `load_chart`
 * timing log makes frontend-vs-backend latency attribution unambiguous:
 *   - `server_timing.total`              => time spent in the backend
 *   - `load_chart.duration - total`      => network + client overhead
 *   - `render_chart.duration`            => pure frontend render
 *
 * Only the `dur` token of each metric is kept; `desc` and other tokens are
 * ignored. Metrics without a numeric `dur` are skipped. Returns `undefined`
 * when the header is absent or yields no usable durations, so callers can omit
 * the field entirely rather than logging an empty object.
 *
 * @see https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Server-Timing
 */
export default function parseServerTiming(
  header: string | null | undefined,
): Record<string, number> | undefined {
  if (!header) {
    return undefined;
  }

  const result: Record<string, number> = {};
  // Metrics are comma-separated; each metric's tokens are semicolon-separated.
  header.split(',').forEach(metric => {
    const tokens = metric.split(';').map(token => token.trim());
    const name = tokens.shift();
    if (!name) {
      return;
    }
    const durToken = tokens.find(token =>
      token.toLowerCase().startsWith('dur'),
    );
    if (!durToken) {
      return;
    }
    const value = Number(durToken.slice(durToken.indexOf('=') + 1));
    if (Number.isFinite(value)) {
      result[name] = value;
    }
  });

  return Object.keys(result).length > 0 ? result : undefined;
}
