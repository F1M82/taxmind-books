"""Report computation services per docs/REPORTS.md.

Each report module exposes a single `compute_<report>` function that
takes a `Session` + filters and returns a structured result. The HTTP
layer (`app/api/v1/reports.py`) is a thin wrapper that maps those
results to response schemas.

Universal rules R1–R9 live in this package and are not API-layer
concerns. Adding a new report: build the query here, schema in
`app/schemas/reports.py`, route in `app/api/v1/reports.py`.
"""
