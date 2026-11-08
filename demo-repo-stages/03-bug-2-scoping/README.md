# Real-Time Metrics Pipeline

A streaming telemetry processing microservice with sliding-window aggregation.

## Architecture

- `pipeline.py`: Core `MetricsPipeline` engine.
- `utils.py`: Window calculations, batch slicing, and percentile computing.
- `scenarios.py`: Telemetry data processing and threshold validation routines.
- `tests/`: Automated unit tests verifying pipeline behavior.

## Running Tests

```bash
pytest tests/
```
