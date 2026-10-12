# Example image: CUDA + PyTorch + the topotask example pipelines.
# Build from the repository root:
#   docker build -f examples/Dockerfile -t topotask-examples .
FROM nvcr.io/nvidia/pytorch:24.05-py3
WORKDIR /app

# cuRobo is installed from source (see its docs for the build flags for your GPU arch)
# RUN git clone https://github.com/NVlabs/curobo.git && pip install -e curobo --no-build-isolation

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install -e .
# GPU deps for these example nodes. Add cuRobo from source above if you need the planner.
RUN pip install warp-lang
COPY examples ./examples

# Register the example nodes, then point the server at the example pipelines.
ENV TOPOTASK_PLUGINS=examples.nodes
ENV PIPELINE_DIR=/app/examples/configs/pipelines
EXPOSE 8000
CMD ["uvicorn", "topotask.server:app", "--host", "0.0.0.0", "--port", "8000"]
