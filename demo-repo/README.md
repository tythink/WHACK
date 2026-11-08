# Demo Repo: Real-Time Metrics Pipeline

A streaming telemetry processing microservice with sliding-window aggregation.

## Architecture

- `pipeline.py`: Core `MetricsPipeline` engine.
- `utils.py`: Window calculations, batch slicing, and percentile computing.
- `scenarios.py`: 8 scripted common coding pitfalls for agent evaluation.
- `tests/`: Automated unit tests verifying pipeline correct behavior.

## Running Tests

```bash
pytest tests/
```
