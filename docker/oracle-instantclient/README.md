<!--
Licensed to the Apache Software Foundation (ASF) under one or more
contributor license agreements.  See the NOTICE file distributed with
this work for additional information regarding copyright ownership.
The ASF licenses this file to You under the Apache License, Version 2.0
(the "License"); you may not use this file except in compliance with
the License.  You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
-->

# Local Oracle Instant Client fallback

The Dockerfile installs the Oracle Instant Client to enable python-oracledb
**Thick mode** (required for legacy `10G` password verifiers that Thin mode
rejects with `DPY-3015`). By default it downloads the client from Oracle's CDN.

When the CDN is **blocked or unreachable** (air-gapped / corporate network),
drop an unzipped Instant Client here and the build will use it instead of the
CDN. If this directory has no client and the CDN is unreachable, the build still
succeeds — Oracle just runs in Thin mode.

## ⚠️ It must be the LINUX client, not Windows

The Superset container is **Linux** (`linux/amd64` under Docker Desktop on
Windows/Mac, or `linux/arm64` on Apple Silicon). The Linux dynamic loader cannot
load Windows libraries.

| Provides | Package | Works in container? |
|----------|---------|---------------------|
| `*.so` (e.g. `libclntsh.so.19.1`, `libnnz19.so`) | Linux Instant Client | ✅ yes |
| `*.dll`, `*.exe`, `vc14/` | Windows Instant Client | ❌ no — wrong OS |

The build detects a usable client by looking for `libclntsh.so*`. A Windows
package has no such file, so it is ignored and the build falls through to the
CDN / Thin-mode warning.

## How to populate it

1. Pick the architecture that matches the container:
   - Docker Desktop on **Windows or Intel Mac** → **Linux x64**
   - **Apple Silicon** Mac → **Linux ARM64 (aarch64)**
2. Download "Basic" or "Basic Light" for that arch from
   <https://www.oracle.com/database/technologies/instant-client/downloads.html>
   (on a machine that can reach the site, e.g. the Mac), e.g.
   `instantclient-basiclite-linux.x64-19.12.0.0.0dbru.zip`.
3. Unzip it and copy the resulting `instantclient_19_12/` folder (the one
   containing `libclntsh.so*`) into this directory:

   ```
   docker/oracle-instantclient/
   └── instantclient_19_12/
       ├── libclntsh.so.19.1
       ├── libclntshcore.so.19.1
       ├── libnnz19.so
       └── ...
   ```
4. Rebuild. Look for `Using bundled Linux Instant Client from ...` in the build
   log, then `python-oracledb Thick mode enabled` in the Superset/worker logs.

## Multiple clients

If more than one client folder is present (e.g. `instantclient_19_12` and
`instantclient_23_26`), the build deterministically selects the **highest
version** (`sort -V` on the discovered `libclntsh.so*`). Newer Instant Clients
are backward-compatible, so a 23.x client connects fine to a 19c database.
Prefer keeping just one folder here to avoid surprises; the build log prints
`Using bundled Linux Instant Client from ...` so you can confirm which was used.

The binaries here are git-ignored (see `.gitignore`) — they stay local to each
machine and are never committed.
