"""TecDoc vehicle-tree staging, mapping, and provenance pipeline."""

from ingestion.tecdoc.models import TecDocVehicleRow
from ingestion.tecdoc.service import ingest_tecdoc_vehicle_tree

__all__ = ["TecDocVehicleRow", "ingest_tecdoc_vehicle_tree"]
