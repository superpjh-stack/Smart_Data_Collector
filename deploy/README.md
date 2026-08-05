# Edge Collector — Deployment & Remote Update

Implements the "Deployment & Update Strategy" section of `docs/smart-data-collector-plan.md`:
the collector ships as a Docker image, and a version bump at an edge site happens by pulling a
new image and restarting — no site visit, and no inbound port ever opened at the site.

Files:

| File | Role |
|---|---|
| `deploy/docker-compose.edge.yml` | The edge-site stack: `collector` + `watchtower`. Runs on the field device. |
| `deploy/.env.example` | Per-site configuration template. Copy to `deploy/.env`. |
| `.github/workflows/build-collector.yml` | CI: builds `collector/Dockerfile` and pushes to the registry on a `v*` tag. |

The root `docker-compose.yml` is the **cloud** stack (cloud-api + TimescaleDB). It is not
deployed at an edge site; do not mix the two.

## 1. The CI/CD pipeline

Release is a two-halves flow, and the halves never connect directly to each other:

```
  developer                    CI (GitHub Actions)              container registry
  ---------                    -------------------              ------------------
  git tag v1.2.3  ────────────► build collector/Dockerfile ────► ghcr.io/OWNER/sdc-collector
  git push --tags               push tags: 1.2.3, 1.2, latest        :1.2.3  :1.2  :latest
                                                                          ▲
                                                                          │ (poll, outbound)
                                                       ┌──────────────────┘
                                          [ edge site: watchtower ] ──► pulls, recreates collector
```

**Half 1 — publish.** `.github/workflows/build-collector.yml` triggers on a `v*` tag push. It
builds with `context: ./collector` and publishes three tags: the exact version (`1.2.3`), the
minor line (`1.2`), and `latest`. The exact version tags are immutable and exist so a rollback
has something stable to pin to; `latest` is the moving pointer that edge sites follow.

**Half 2 — remote pull trigger.** There is no push trigger. Watchtower at each site polls the
registry every `WATCHTOWER_POLL_INTERVAL` seconds (default 300 = 5 min), compares the digest of
the tag it tracks against the running container's image, and on a difference pulls the new image
and recreates the container with the identical config, volumes, and labels. Worst-case
propagation delay from `git push --tags` to a site running the new build is one CI run plus one
poll interval.

Run the test suite before tagging — the tag *is* the deploy, and CI as configured builds and
publishes without gating on tests.

## 2. Why this keeps the edge site outbound-only

The plan doc's topology says the Edge Collector opens **outbound connections only**; inbound is
blocked. The update mechanism has to live inside that boundary, and this is the design decision
that makes it do so:

- **Pull model (what we do).** Watchtower runs *inside* the OT network and initiates an outbound
  HTTPS request to the registry. It listens on nothing. `docker-compose.edge.yml` declares no
  `ports:` for any service, so the site's firewall needs zero inbound rules — the same trust
  boundary the collector's own cloud uploads already use.
- **Push model (what we deliberately avoid).** A "cloud tells the site to update" design — an
  agent listening for a webhook, an SSH/Ansible run from a deploy server, a Portainer/K8s agent
  with an exposed endpoint — requires a reachable inbound port or a hole in the OT firewall.
  That inverts the trust direction: the cloud becomes able to reach into the OT network, so a
  compromise of the deploy server becomes a path into the plant floor. Not acceptable here.

The only local privilege granted is Watchtower's read-write mount of `/var/run/docker.sock`,
which is what lets it recreate containers. That socket is never network-exposed.

`WATCHTOWER_LABEL_ENABLE=true` plus `--label-enable` scope Watchtower to containers carrying
`com.centurylinklabs.watchtower.enable=true`. Only `collector` has that label, so nothing else
on the field device (including Watchtower itself) is auto-updated.

## 3. Deploying a new edge site

Prerequisite on the field device: a container runtime with Compose v2, outbound HTTPS to the
registry and to the cloud API, and local network reachability to the OPC UA gateway. Nothing
else — no Python, no application dependencies.

1. Copy this repo's `deploy/` directory (or just the two files) to the device.
2. `cp deploy/.env.example deploy/.env` and fill in:
   - `COLLECTOR_IMAGE` — registry path of the published image.
   - `COLLECTOR_TAG` — `latest` to follow releases, or an exact version to freeze this site.
   - `REGISTRY_USER` / `REGISTRY_PASSWORD` — only for a private registry.
   - `OPC_ENDPOINT_URL`, `CLOUD_API_BASE_URL`, `COLLECTOR_PLC_IDS` — required by
     `CollectorSettings.from_env()` in `collector/src/collector/config.py`; the process exits at
     startup if any is missing. The remaining vars have defaults.
   - Do **not** override `COLLECTOR_SQLITE_PATH`. It is pinned to `/data/collector_buffer.db` on
     the `collector_buffer` named volume; that volume is what carries buffered readings across a
     version update.
3. `docker compose -f deploy/docker-compose.edge.yml config` — validates the merged config. It
   exits non-zero naming the offender if any of the three required vars is missing, so this is a
   real pre-flight check, not just a dump.
4. `docker compose -f deploy/docker-compose.edge.yml up -d`
5. Verify: `docker compose -f deploy/docker-compose.edge.yml logs -f collector` should show the
   OPC UA subscription established and batches being accepted by the cloud API. Confirm the
   site's PLCs appear via the cloud API's status endpoint.

**Updating an existing site** requires no action at the site: tag a release, CI publishes, and
Watchtower applies it within one poll interval. To force it immediately (or to apply a config
change from `.env`):

```
docker compose -f deploy/docker-compose.edge.yml pull
docker compose -f deploy/docker-compose.edge.yml up -d
```

## 4. Rollback

Per the plan doc's Rollback Plan, the collector runs **in parallel with the existing manual
recording process** — it replaces nothing. A bad version means degraded or paused data
collection, not a production outage, so rollback is a routine tag revert rather than an
emergency procedure.

**Rolling back one site** (fastest, no CI involved):

1. Set `COLLECTOR_TAG` in that site's `deploy/.env` to the last known-good version, e.g.
   `COLLECTOR_TAG=1.2.2`.
2. `docker compose -f deploy/docker-compose.edge.yml up -d --force-recreate collector`

The site is now pinned: since `1.2.2` is immutable, Watchtower finds no newer digest and will
not drag the site forward again until the tag is changed back to `latest`. Remember to unpin
once a fix ships, or the site silently stops receiving updates.

**Rolling back the release channel** (all sites following `latest`): re-point `latest` at the
good image so every site self-heals on its next poll.

```
docker pull   ghcr.io/OWNER/sdc-collector:1.2.2
docker tag    ghcr.io/OWNER/sdc-collector:1.2.2 ghcr.io/OWNER/sdc-collector:latest
docker push   ghcr.io/OWNER/sdc-collector:latest
```

**Stopping collection entirely** — the plan's escape hatch, since manual recording continues
regardless:

```
docker compose -f deploy/docker-compose.edge.yml stop collector
```

Buffered data is preserved: the SQLite buffer lives on the `collector_buffer` volume, which
survives `stop`, `down`, container recreation, and rollback. It is destroyed only by
`down -v` or an explicit `docker volume rm` — never run those to resolve a bad version.
