from datetime import datetime
from typing import Optional
from pydantic import BaseModel


class LogEntry(BaseModel):
    id: str
    timestamp: str
    source: str
    message: str

    # 5-tuple
    src_ip:   str
    dst_ip:   str
    src_port: Optional[int] = None
    dst_port: Optional[int] = None
    proto:    str

    # AP info
    ap_name:  str
    ap_mac:   str = ""
    wlan_id:  str = ""

    # Client (enriched from Ruckus One)
    client_mac:   str
    client_label: str  # best available name: alias > guest_name > hostname > MAC
    hostname:     str = ""
    alias:        str = ""
    username:     str = ""
    os_type:      str = ""
    device_type:  str = ""
    venue:        str = ""
    ssid:         str = ""
    is_guest:     bool = False

    # Guest fields (only populated for guest network clients)
    guest_name: str = ""
    email:      str = ""
    phone:      str = ""

    # DNS enriched destination
    dst_hostname: str


class SearchResponse(BaseModel):
    logs:     list[LogEntry]
    total:    int
    returned: int
    query:    str
    error:    Optional[str] = None


class SearchRequest(BaseModel):
    query: str = "*"
    from_dt: Optional[datetime] = None
    to_dt:   Optional[datetime] = None

    # Structured filters
    src_ip:       Optional[str]  = None
    dst_ip:       Optional[str]  = None
    dst_hostname: Optional[str]  = None   # e.g. "yahoo.fr"
    client_mac:   Optional[str]  = None
    client_label: Optional[str]  = None   # search across alias/guest_name/hostname
    username:     Optional[str]  = None
    ap_name:      Optional[str]  = None
    venue:        Optional[str]  = None
    ssid:         Optional[str]  = None
    proto:        Optional[str]  = None   # TCP, UDP, ICMP
    dst_port:     Optional[int]  = None
    is_guest:     Optional[bool] = None   # filter guest-only or non-guest

    limit:  int = 100
    offset: int = 0


class SyncStatus(BaseModel):
    ruckus_last_sync:     Optional[str] = None
    ruckus_clients_cached: int = 0
    dns_pending:          int = 0
    dns_cached:           int = 0


class WorkerResult(BaseModel):
    status: str
    detail: dict = {}


class InvestigationReport(BaseModel):
    """Structured report for a single incident investigation."""
    generated_at:  str
    time_range:    str
    total_events:  int
    client_mac:    str
    client_label:  str
    hostname:      str = ""
    alias:         str = ""
    username:      str = ""
    os_type:       str = ""
    device_type:   str = ""
    venue:         str = ""
    ssid:          str = ""
    is_guest:      bool = False
    guest_name:    str = ""
    email:         str = ""
    phone:         str = ""
    logs:          list[LogEntry]
