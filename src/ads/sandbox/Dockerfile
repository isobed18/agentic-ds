FROM python:3.12-slim

RUN pip install --no-cache-dir \
    ipykernel==6.30.1 \
    jupyter_client==8.6.3 \
    matplotlib==3.10.5 \
    pandas==2.3.1 \
    scikit-learn==1.7.1

RUN mkdir -p /opt/ads /data /artifacts && \
    chown -R 65532:65532 /opt/ads /data /artifacts
COPY --chown=65532:65532 kernel_client.py /opt/ads/kernel_client.py

USER 65532:65532
WORKDIR /tmp
