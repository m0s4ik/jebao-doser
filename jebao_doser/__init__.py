"""Local control of Jebao (Gizwits) aquarium dosing pumps, no cloud needed."""

from .doser import MAX_DOSE_SECONDS, Dose, Doser, dosing, interval, schedule
from .protocol import DeviceInfo, ProtocolError, discover
from .schema import DataPoints, SchemaError, get_schema, load_file

__all__ = [
    "MAX_DOSE_SECONDS", "DataPoints", "DeviceInfo", "Dose", "Doser", "ProtocolError", "SchemaError",
    "discover", "dosing", "get_schema", "interval", "load_file", "schedule",
]
__version__ = "0.2.0"
