# Surface approval and event recovery

This behavior uses the core protocol pinned in `exo.pin`. It needs no changes to
the base Exo checkout. The explicit-review runtime requires a new image and fresh
acceptance; earlier image results do not validate it.

## Human answers and dismissal

An arriving request is pending and preserves typing focus. **Review request (F6)**
explicitly opens one exact request for keyboard review; then Y or N sends an
`ApprovalRespond`. Yes/No buttons capture the full displayed request and can
answer that exact preview. The local token includes a unique retained generation,
request identity, binding and displayed content, so an empty binding or a stale
widget cannot authorize a replacement. A submitted card stays hidden while delivery is in flight, so another tap
cannot submit the same request. The status says “Checking your answer…”. A
replacement or reopened card receives a fresh 700 ms anti-misclick window.

Escape defers the current card locally. It sends neither allow nor deny. The
request stays pending; only **Review request (F6)** explicitly reopens it.
Opening the prompt preserves deferrals and ends keyboard review.
Incidental rendering and pending-list recovery preserve this deferral. If another
request is queued, that next card can appear. New decisions are blocked while the
connection is down.

Once a reviewed card has actually become active, losing activation ends review
and releases its keyboard grab without presenting or refocusing the prompt.
Replacement, resolution, pending-list reconciliation and connection changes also
revoke review. An unchanged repaint is not another consent gesture, and the next
queued request is pending until separately reviewed.
F6 press state survives answers and request changes across the prompt, drawer
and card. Autorepeat cannot review the next request; a fresh unmodified press
requires an observed release, including after a modified F6 press.

## Uncertain delivery

Neither a send attempt nor a successful command reply is itself a local pending
state snapshot. After an approval response finishes, the surface fetches
`approvals.list`. On transport failure or a lost reply it drops the connection,
then fetches the list during reconnection. It **never automatically resends** an
approval answer. If the request remains pending, it requires a fresh review
gesture before a keyboard answer. If absent, it stays closed, including when a resolution event was
missed. Even an empty list replaces stale local state.

In-flight sends are cancelled before a replacement connection publishes its
pending snapshot. A snapshot queried while a send remains in flight cannot rearm
that request. Minimal pending rows retain the original card text and binding;
complete rows refresh them from the daemon. New approval requests retain their
normal principal filtering.

`hello.seq` is read before `approvals.list`; the snapshot supersedes requested
event replay through that sequence. Event delivery waits until the snapshot is
posted, so a live resolution received during the query is applied after its
possibly stale row. Requests with newer event sequences remain until replay
resolves them. The current protocol does not provide an atomic pending snapshot
plus event cursor: transient display of a newer request resolved during the query
is possible until its resolution event arrives, and the daemon's binding and
pending checks remain authoritative.

## Bounded liveness and observable retries

The surface makes a `hello` probe every 15 seconds, with a 10 second wall-clock
command bound. A failed or stalled command causes disconnection and retry with
0.5–10 second exponential backoff. Only a connection that stays healthy for at least 30 seconds resets that delay. Normal idle time produces no disconnect.
If `hello.seq` shows events that the feed has not delivered, the feed gets 15
seconds to reach that observed sequence; a probe then disconnects if it still
lags. Detection is bounded by probe scheduling (at most about 50 seconds
for a lag observed just after a probe, including two slow probe responses), rather
than relying on event arrival as a heartbeat. A lower daemon sequence also causes
reconnection. Replay follows the existing 2,000-event limit; pending approvals
are recovered independently of that replay limit.

An event socket stalled while the daemon stays entirely idle cannot be proven
stalled by this protocol. Command probes establish daemon responsiveness, and
sequence lag detects a stuck feed once the daemon produces events. A per-stream
heartbeat or stronger subscription acknowledgement would require core protocol
support. No inactivity timeout is imposed on an otherwise healthy idle feed.

Reconnect errors, including malformed replies and unexpected exceptions, log a
traceback on the first failure and the latest failure at most once per minute,
with the intervening retry count. Creation and close errors share that bound.
The connection status remains a calm up/down state rather than a log stream.

The surface preserves the core's per-launch principal credential on requests.
This change does not establish peer authentication beyond the core's existing
credential handling; Unix peer authentication is a separate core integration
requirement (issue #16). No live machine configuration is changed by these fixes.
