"""Team Player: synthetic football tracking, online tactical analysis and AI explanations.

Folders (each does one job):
  config/     editable definitions: lineups, teams, styles (private), analysis thresholds
  simulator/  generates synthetic matches (private: knows player personalities)
  analyser/   online, position-only analysis of a match stream (no simulator access)
  agents/     Microsoft Foundry agents and plain-English narration
  app/        FastAPI live viewer, HTML replays, Azure Blob helper
  utils/      shared geometry, number formatting and file helpers
  schema.py   the public data contract between simulator and analyser
"""

__version__ = "0.5.0"
