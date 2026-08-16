# ADS sandbox image

Build the runtime image from this directory:

```powershell
docker build -t ads-sandbox:latest src/ads/sandbox
```

The host manager never opens a container network route. It starts one persistent
IPython kernel per run and sends code to it through a short-lived
`jupyter_client` process invoked with `docker exec`; the user code itself always
runs in the same kernel namespace. Only `/data` (read-only) and `/artifacts`
(read-write) cross the container boundary.
