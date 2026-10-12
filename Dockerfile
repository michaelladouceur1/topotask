# Base image with CUDA + PyTorch. Pin to whatever your cuRobo/Warp versions support.
FROM nvcr.io/nvidia/pytorch:24.05-py3
WORKDIR /app

# cuRobo is installed from source (see its docs for the build flags for your GPU arch)
# RUN git clone https://github.com/NVlabs/curobo.git && pip install -e curobo --no-build-isolation

COPY pyproject.toml .
COPY src ./src
RUN pip install -e ".[gpu]"
COPY configs ./configs

ENV PIPELINE_DIR=/app/configs/pipelines
EXPOSE 8000
CMD ["uvicorn", "topo.server:app", "--host", "0.0.0.0", "--port", "8000"]
