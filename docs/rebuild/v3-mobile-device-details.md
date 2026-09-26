# Mobile scoped device details

This milestone adds a read-only mobile view for devices currently authorized to
the authenticated mobile user. It does not add a migration and does not change
the administrator device or traffic APIs. The GNN capability remains
unavailable; no score, feature, graph, or model data is returned or inferred.

## Endpoints

Both endpoints require the existing Mobile Bearer session. A Web cookie is not
an alternative credential. Requests are evaluated against the user's current,
non-revoked device and area scopes every time; the response is not a durable
authorization grant.

- `GET /api/v3/mobile/devices/{device_id}` returns a minimized device profile,
  connection/operation status, a bounded summary of currently published
  notices for affected devices, and capability availability. It deliberately
  excludes MAC, IP, peer addresses, ports, model evidence, and administrator
  incident fields.
- `GET /api/v3/mobile/devices/{device_id}/traffic?window=15m|1h|24h` returns
  minute aggregates downsampled to the requested fixed window, a short-lived
  in-process rate snapshot, coarse protocol categories, freshness, and a
  data-quality note. It does not return peers, IP addresses, raw protocols, or
  packet payloads. The API does not accept arbitrary time bounds or resolution
  parameters.

An unknown device and a device outside the caller's current scope use the same
not-found response to avoid confirming that an inaccessible device exists.
Revoked scopes and area changes take effect on the next request. Retired devices
remain readable if still in scope and are marked as historical; their traffic
history is not deleted.

## Availability and freshness

Device connection status and operation mode are independent. `unknown` is never
presented as online. Traffic distinguishes:

- `no_samples`: no persisted sample is available; totals/rates are not invented;
- `warming_up`: the process-local short-term rate window has not accumulated
  enough observations;
- available data with a fresh or stale freshness indicator;
- unavailable service, reported as a structured service error.

Only actual aggregate rows can produce historical totals, including a real zero
counter. Gaps between returned time buckets are omitted rather than filled with
zero. The short-term window is bounded, process-local, and starts empty after a
backend restart; it is a display cache, not the historical source of truth.
Multiple backend processes do not share this rate window, so deployments should
route a user's requests consistently or accept that the live-rate capability may
be warming up on a worker. Persisted minute aggregates remain the historical
source of truth.

Traffic data is read-only and does not alter device identity, connection state,
operation mode, or security conclusions. Maintenance and disabled devices may
still have real traffic history. No traffic result implies that a device is
safe or under attack.

## Mobile presentation and privacy

The APP may show the user's own device name/type/area, coarse status, last update,
bounded user-visible notice summaries, aggregate traffic, and broad protocol
categories. It must not call the administrator device or peer endpoints as a
fallback. It must not display MAC/IP/ports, peer identities, graph identifiers,
GNN scores/features, or internal incident evidence.

The device and traffic screens reuse the existing Mobile Bearer client and token
coordinator. Detail and traffic requests are abortable and scoped to the active
device. A scope-related not-found result clears the local detail, refreshes the
overview, and returns to the authorized list. In-memory data may be retained
after a transient network failure only with an explicit stale/offline indicator;
it is not persisted to AsyncStorage.

These screens contain household device information. This milestone does not
force screenshot prevention; deployments should use normal device lock and
screen privacy controls where needed. Polling runs only while the detail route is
focused and the APP is foregrounded (30 seconds for profile/status and 8 seconds
for the simplified traffic view). Returning to foreground starts fresh requests;
leaving the route aborts pending reads.

The current traffic view is intentionally a compact summary, not a full flow
forensics interface. Future work that expands mobile detail requires a separate
scope-reviewed API contract; it must not expose administrator-only peers or
unfiltered global traffic.
