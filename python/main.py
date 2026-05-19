"""
Thin entrypoint so you can run: ``python main.py <command>`` from ``hyperion/python``.

Prefer: ``hyperion-pipeline --help`` after ``pip install -e .``
"""

from __future__ import annotations

from hyperion_pipeline.cli.main import main

if __name__ == "__main__":
    main()
