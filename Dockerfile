# Container image for a PaaS deploy (Railway).
#
# An explicit Dockerfile rather than letting the platform's autodetection guess.
# Nixpacks would have to infer the Python version, which extras matter, and what
# to run -- and it has no way to know that `serve_public.py` is the wrong entry
# point here, because that one binds loopback on purpose.
#
# `documents-docling`, `documents-unstructured` and the marker/mineru workers are
# deliberately absent. They add gigabytes, they are optional at runtime, and the
# project installs the heavy ones into isolated worker environments by design.
# A PDF falls back to the built-in text-layer reader; add the extra here when a
# deploy actually needs structure extraction.
FROM python:3.12-slim

# libgomp1 is scikit-learn's OpenMP runtime. Without it the image builds fine
# and then dies on import, which is a confusing way to discover a missing
# system library.
RUN apt-get update \
    && apt-get install --no-install-recommends -y libgomp1 \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# One copy, then one install. Splitting this to cache the dependency layer was
# tempting and wrong: hatchling force-includes `src/ads/api/static` and
# `src/ads/skills` into the wheel, so installing before the tree is present
# fails on a missing path. A slower build beats a build that does not work.
COPY . .

RUN pip install --upgrade pip && pip install -e ".[api,kesif,sandbox]"

# The volume mount point. Uploads, artifacts and run state must live here --
# anything written elsewhere is discarded on the next deploy.
ENV ADS_DATA_DIR=/data
VOLUME ["/data"]

# The platform injects PORT and routes to it; 8080 is only for running locally.
ENV PORT=8080
EXPOSE 8080

CMD ["python", "scripts/serve_container.py"]
