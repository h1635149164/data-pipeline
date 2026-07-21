IMAGE_NAME  ?= openbudget/data-pipeline
IMAGE_TAG   ?= latest
CONTAINER   ?= data-pipeline

.PHONY: build run test lint format publish-podman publish-docker clean

## Build the container image using Docker.
build:
	docker build -t $(IMAGE_NAME):$(IMAGE_TAG) .

## Run the pipeline locally (reads config from environment).
run:
	uv run python -m src.main

## Run the full test suite.
test:
	uv run pytest -v

## Run type checking.
typecheck:
	uv run mypy src

## Run linter.
lint:
	uv run ruff check

## Auto-format source files.
format:
	uv run ruff format

## Run all quality checks (test + typecheck + lint).
check: test typecheck lint

## Push the image to a Podman-managed registry / local compose.
publish-podman:
	podman build -t $(IMAGE_NAME):$(IMAGE_TAG) .
	podman push $(IMAGE_NAME):$(IMAGE_TAG)

## Push the image to a Docker-managed registry.
publish-docker:
	docker build -t $(IMAGE_NAME):$(IMAGE_TAG) .
	docker push $(IMAGE_NAME):$(IMAGE_TAG)

## Remove build artefacts and the local venv.
clean:
	rm -rf .venv __pycache__ src/__pycache__ tests/__pycache__ .mypy_cache .ruff_cache
