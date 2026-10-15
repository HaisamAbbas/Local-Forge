# Security and recovery behavior

LOCALFORGE is a single-user local workbench. Its API is not an authentication boundary for other
programs already running as the same Windows account or POSIX user.

## Local API access

The documented development commands bind Vite and Uvicorn to `127.0.0.1`; do not expose the API
on a LAN or through a public reverse proxy. LOCALFORGE does not enable cross-origin resource
sharing. Every `POST`, `PUT`, `PATCH`, and `DELETE` request must target a loopback host and include
the in-memory session token returned by `GET /api/v1/session`. A supplied browser `Origin` must
also be loopback. The token response is marked `no-store`; the token changes when the API process
restarts. The token and host checks protect against ordinary cross-site browser requests, but do
not protect against software that can read the local API response or browser process.

## Remote endpoints and credentials

Remote engine endpoints must use HTTPS. A remote probe explains the target host and possible
probe requests before contact, and remote benchmark preflight identifies the host that will receive
prompts, request settings, and generated output. The user must enable remote access before the
plan can run. LOCALFORGE does not resolve secrets into profile fields: credentials use opaque
references to a supported operating-system credential store. The OpenAI-compatible adapter disables
ambient proxy settings and redirects so the credential and request are sent only to the configured
endpoint.

## Model downloads

LOCALFORGE never downloads model weights itself and never starts a download without an explicit
request. On **Models**, **Pull with Ollama** shows the download size and asks for confirmation in
the page; only then does the UI call `POST /api/v1/models/pull` with `confirm: true` and the session
token. The API refuses remote and disabled profiles, non-Ollama profiles, and tags outside the
curated catalog unless the request also marks the tag as a confirmed custom tag. The request goes
to the user's own local Ollama (`POST /api/pull`), and **Ollama** then opens network connections to
its registry (`registry.ollama.ai`, or the host named in a custom tag) to fetch the files into its
own model directory. Cancelling, or stopping the API, closes that stream, which stops the download;
partial layers stay in Ollama's cache. Model suggestions only read local state and call Ollama's
read-only `GET /api/tags`; the **Get the GGUF** action is a plain link to Hugging Face that the
browser opens.

## Retention, diagnostics, and reports

Dataset content and benchmark prompt/output text are not retained by default. Dataset previews,
run events, and adapter diagnostics contain counts or stable error descriptions rather than prompt
bodies. A benchmark plan can explicitly retain request content; report content export also requires
an explicit opt-in. Reports remove credential-like fields and labeled secret values, and mark
credentials as excluded. This redaction cannot identify arbitrary sensitive text that a user has
placed in an opted-in prompt or output, so review retained content before exporting it. API request
validation errors omit submitted values. Worker subprocess standard output and standard error are
discarded.

Runs are kept until the user deletes them. Only terminal runs (completed, failed, cancelled,
interrupted) can be deleted, one at a time or through the cleanup endpoint, which is a dry run
unless `dry_run` is `false`. A deletion removes the run with its request samples, metric samples,
events, and any leftover locks or lease in one transaction. Experiment trials that pointed at a
deleted run are kept as failed trials without a run and name the deleted run in their error.
Saved reports are snapshots: they are not deleted with their runs, stay downloadable, and list
the runs that no longer exist. Deleting a report removes its record and only its own file inside
the reports directory; a report whose stored path fails the path checks has only its record
removed.

## Files and database

Dataset uploads are bounded by `LOCALFORGE_MAX_DATASET_UPLOAD_BYTES` (100 MiB by default) and the
parser also limits row count and row size. Dataset filenames are never used as storage paths.
Content-addressed snapshots, upload staging, and report paths are checked against the configured
data directory; dataset and report storage refuses symbolic links and Windows junctions at the
managed directory/file components. Report exports have a 25 MiB limit and generated local-only
filenames. SQLite database files are created with user-only POSIX permissions; dataset snapshots
and reports are also created as private files on POSIX systems. Windows uses the account ACLs of
the configured data directory.

SQLite lock waits are bounded at five seconds. Schema migrations are forward-only and each
migration is transactional, so an interrupted migration can be retried. Unsupported newer schemas
and SQLite corruption fail startup; LOCALFORGE does not repair or overwrite a damaged database and
does not create an automatic backup. Close LOCALFORGE and copy the complete data directory before
manual recovery. Keep the original database unchanged until recovery is verified.

## Child processes and interrupted runs

The API stops only worker `Popen` objects it launched during the current process lifetime. Managed
engine adapters stop only handles that they created and still own. A worker crash or stale lease
marks the run interrupted, preserves completed samples, finalizes in-progress samples as
interrupted, and releases its locks. After an API restart, workers see the persisted interrupted
state and stop themselves; LOCALFORGE never kills a process based only on a stored PID. A hard API
crash can leave a managed engine process running because the in-memory ownership handle is lost.
On restart the profile is disabled and the process may need to be stopped manually; an occupied
port is reported without terminating its owner.

Run workers cap request concurrency at 128. Adapter streams use a bounded 32-item queue. Request
timeouts are bounded by the configured plan timeout; cancellation asks the adapter to abort for at
most one second and allows a two-second drain before recording a bounded-drain failure. Some
attached engines do not support cancellation and can continue their own server-side generation
after LOCALFORGE closes the client request.

## Dependency checks

The repository pins Python dependencies in `backend/uv.lock` and runs formatter, linter, type
checker, and offline test suites. It does not currently configure a dependency vulnerability
scanner. Prompt 17 therefore did not add a network-dependent audit to ordinary development or CI.
