"""
IITA Google Search Console MCP Server
Provides Search Console data via MCP protocol.
"""

import os
import json
import logging
from datetime import datetime, timedelta
from typing import Optional, List

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field, ConfigDict

from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("iita-gsc-mcp")

SITE_URL = os.environ.get("GSC_SITE_URL", "https://iita.com.ar/")
CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
REFRESH_TOKEN = os.environ.get("GOOGLE_REFRESH_TOKEN", "")
TOKEN_URI = "https://oauth2.googleapis.com/token"
SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly"]

def _get_service():
    creds = Credentials(token=None, refresh_token=REFRESH_TOKEN, client_id=CLIENT_ID,
                        client_secret=CLIENT_SECRET, token_uri=TOKEN_URI, scopes=SCOPES)
    return build("searchconsole", "v1", credentials=creds)

def _resolve_dates(date_range, start_date, end_date):
    if start_date and end_date: return start_date, end_date
    today = datetime.now().date()
    available = today - timedelta(days=3)
    presets = {"LAST_7_DAYS":7,"LAST_14_DAYS":14,"LAST_28_DAYS":28,"LAST_30_DAYS":30,
               "LAST_90_DAYS":90,"LAST_6_MONTHS":180,"LAST_12_MONTHS":365,"LAST_16_MONTHS":480}
    days = presets.get(date_range, 28)
    return (available - timedelta(days=days)).isoformat(), available.isoformat()

def _format_table(rows, columns):
    if not rows: return "No data found."
    lines = ["| " + " | ".join(columns) + " |", "| " + " | ".join(["---"]*len(columns)) + " |"]
    for row in rows:
        vals = []
        for c in columns:
            v = row.get(c, "")
            if isinstance(v, float):
                vals.append(f"{v:.2%}" if c=="ctr" else (f"{v:.1f}" if c=="position" else str(v)))
            elif isinstance(v, list): vals.append(", ".join(v))
            else: vals.append(str(v))
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines)

mcp = FastMCP("iita_gsc_mcp")

class SearchQueriesInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    date_range: str = Field(default="LAST_28_DAYS")
    start_date: Optional[str] = Field(default=None)
    end_date: Optional[str] = Field(default=None)
    limit: int = Field(default=25, ge=1, le=1000)
    query_contains: Optional[str] = Field(default=None)
    page_contains: Optional[str] = Field(default=None)
    country: Optional[str] = Field(default=None, description="3-letter country code, e.g. 'arg'")
    device: Optional[str] = Field(default=None, description="DESKTOP, MOBILE, TABLET")
    search_type: str = Field(default="web")
    site_url: Optional[str] = Field(default=None)

@mcp.tool(name="gsc_search_queries", annotations={"readOnlyHint":True,"destructiveHint":False,"idempotentHint":True})
async def gsc_search_queries(params: SearchQueriesInput) -> str:
    """Get top search queries with clicks, impressions, CTR, and position."""
    site = params.site_url or SITE_URL
    sd, ed = _resolve_dates(params.date_range, params.start_date, params.end_date)
    body = {"startDate":sd,"endDate":ed,"dimensions":["query"],"rowLimit":params.limit,"type":params.search_type}
    filters = []
    if params.query_contains: filters.append({"dimension":"query","operator":"contains","expression":params.query_contains})
    if params.page_contains: filters.append({"dimension":"page","operator":"contains","expression":params.page_contains})
    if params.country: filters.append({"dimension":"country","operator":"equals","expression":params.country})
    if params.device: filters.append({"dimension":"device","operator":"equals","expression":params.device})
    if filters: body["dimensionFilterGroups"] = [{"groupType":"and","filters":filters}]
    response = _get_service().searchanalytics().query(siteUrl=site, body=body).execute()
    rows = [{"query":r["keys"][0],"clicks":int(r["clicks"]),"impressions":int(r["impressions"]),"ctr":r["ctr"],"position":r["position"]} for r in response.get("rows",[])]
    return f"### Search Console -- Top Queries\n**Site**: {site} | **Period**: {sd} to {ed}\n\n" + _format_table(rows, ["query","clicks","impressions","ctr","position"])

class SearchPagesInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    date_range: str = Field(default="LAST_28_DAYS")
    start_date: Optional[str] = Field(default=None)
    end_date: Optional[str] = Field(default=None)
    limit: int = Field(default=25, ge=1, le=1000)
    query_contains: Optional[str] = Field(default=None)
    page_contains: Optional[str] = Field(default=None)
    search_type: str = Field(default="web")
    site_url: Optional[str] = Field(default=None)

@mcp.tool(name="gsc_search_pages", annotations={"readOnlyHint":True,"destructiveHint":False,"idempotentHint":True})
async def gsc_search_pages(params: SearchPagesInput) -> str:
    """Get top pages by search performance: clicks, impressions, CTR, position."""
    site = params.site_url or SITE_URL
    sd, ed = _resolve_dates(params.date_range, params.start_date, params.end_date)
    body = {"startDate":sd,"endDate":ed,"dimensions":["page"],"rowLimit":params.limit,"type":params.search_type}
    filters = []
    if params.query_contains: filters.append({"dimension":"query","operator":"contains","expression":params.query_contains})
    if params.page_contains: filters.append({"dimension":"page","operator":"contains","expression":params.page_contains})
    if filters: body["dimensionFilterGroups"] = [{"groupType":"and","filters":filters}]
    response = _get_service().searchanalytics().query(siteUrl=site, body=body).execute()
    rows = [{"page":r["keys"][0].replace(site.rstrip("/"),"") or "/","clicks":int(r["clicks"]),"impressions":int(r["impressions"]),"ctr":r["ctr"],"position":r["position"]} for r in response.get("rows",[])]
    return f"### Search Console -- Top Pages\n**Site**: {site} | **Period**: {sd} to {ed}\n\n" + _format_table(rows, ["page","clicks","impressions","ctr","position"])

class QueriesByPageInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    page_url: str = Field(description="Full URL or path fragment")
    date_range: str = Field(default="LAST_28_DAYS")
    start_date: Optional[str] = Field(default=None)
    end_date: Optional[str] = Field(default=None)
    limit: int = Field(default=25, ge=1, le=100)
    site_url: Optional[str] = Field(default=None)

@mcp.tool(name="gsc_queries_by_page", annotations={"readOnlyHint":True,"destructiveHint":False,"idempotentHint":True})
async def gsc_queries_by_page(params: QueriesByPageInput) -> str:
    """Get search queries that drive traffic to a specific page."""
    site = params.site_url or SITE_URL
    sd, ed = _resolve_dates(params.date_range, params.start_date, params.end_date)
    op = "equals" if params.page_url.startswith("http") else "contains"
    body = {"startDate":sd,"endDate":ed,"dimensions":["query"],"rowLimit":params.limit,
            "dimensionFilterGroups":[{"groupType":"and","filters":[{"dimension":"page","operator":op,"expression":params.page_url}]}]}
    response = _get_service().searchanalytics().query(siteUrl=site, body=body).execute()
    rows = [{"query":r["keys"][0],"clicks":int(r["clicks"]),"impressions":int(r["impressions"]),"ctr":r["ctr"],"position":r["position"]} for r in response.get("rows",[])]
    return f"### Search Console -- Queries for: {params.page_url}\n**Period**: {sd} to {ed}\n\n" + _format_table(rows, ["query","clicks","impressions","ctr","position"])

class DailyTrendInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    date_range: str = Field(default="LAST_28_DAYS")
    start_date: Optional[str] = Field(default=None)
    end_date: Optional[str] = Field(default=None)
    query_contains: Optional[str] = Field(default=None)
    page_contains: Optional[str] = Field(default=None)
    site_url: Optional[str] = Field(default=None)

@mcp.tool(name="gsc_daily_trend", annotations={"readOnlyHint":True,"destructiveHint":False,"idempotentHint":True})
async def gsc_daily_trend(params: DailyTrendInput) -> str:
    """Get daily search performance trend."""
    site = params.site_url or SITE_URL
    sd, ed = _resolve_dates(params.date_range, params.start_date, params.end_date)
    body = {"startDate":sd,"endDate":ed,"dimensions":["date"],"rowLimit":500}
    filters = []
    if params.query_contains: filters.append({"dimension":"query","operator":"contains","expression":params.query_contains})
    if params.page_contains: filters.append({"dimension":"page","operator":"contains","expression":params.page_contains})
    if filters: body["dimensionFilterGroups"] = [{"groupType":"and","filters":filters}]
    response = _get_service().searchanalytics().query(siteUrl=site, body=body).execute()
    rows = [{"date":r["keys"][0],"clicks":int(r["clicks"]),"impressions":int(r["impressions"]),"ctr":r["ctr"],"position":r["position"]} for r in sorted(response.get("rows",[]), key=lambda r: r["keys"][0])]
    return f"### Search Console -- Daily Trend\n**Site**: {site} | **Period**: {sd} to {ed}\n\n" + _format_table(rows, ["date","clicks","impressions","ctr","position"])

class DeviceBreakdownInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    date_range: str = Field(default="LAST_28_DAYS")
    start_date: Optional[str] = Field(default=None)
    end_date: Optional[str] = Field(default=None)
    site_url: Optional[str] = Field(default=None)

@mcp.tool(name="gsc_device_breakdown", annotations={"readOnlyHint":True,"destructiveHint":False,"idempotentHint":True})
async def gsc_device_breakdown(params: DeviceBreakdownInput) -> str:
    """Get search performance by device: DESKTOP, MOBILE, TABLET."""
    site = params.site_url or SITE_URL
    sd, ed = _resolve_dates(params.date_range, params.start_date, params.end_date)
    body = {"startDate":sd,"endDate":ed,"dimensions":["device"]}
    response = _get_service().searchanalytics().query(siteUrl=site, body=body).execute()
    rows = [{"device":r["keys"][0],"clicks":int(r["clicks"]),"impressions":int(r["impressions"]),"ctr":r["ctr"],"position":r["position"]} for r in response.get("rows",[])]
    return f"### Search Console -- Device Breakdown\n**Period**: {sd} to {ed}\n\n" + _format_table(rows, ["device","clicks","impressions","ctr","position"])

class CountryBreakdownInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    date_range: str = Field(default="LAST_28_DAYS")
    start_date: Optional[str] = Field(default=None)
    end_date: Optional[str] = Field(default=None)
    limit: int = Field(default=15, ge=1, le=100)
    site_url: Optional[str] = Field(default=None)

@mcp.tool(name="gsc_country_breakdown", annotations={"readOnlyHint":True,"destructiveHint":False,"idempotentHint":True})
async def gsc_country_breakdown(params: CountryBreakdownInput) -> str:
    """Get search performance by country."""
    site = params.site_url or SITE_URL
    sd, ed = _resolve_dates(params.date_range, params.start_date, params.end_date)
    body = {"startDate":sd,"endDate":ed,"dimensions":["country"],"rowLimit":params.limit}
    response = _get_service().searchanalytics().query(siteUrl=site, body=body).execute()
    rows = [{"country":r["keys"][0],"clicks":int(r["clicks"]),"impressions":int(r["impressions"]),"ctr":r["ctr"],"position":r["position"]} for r in response.get("rows",[])]
    return f"### Search Console -- Country Breakdown\n**Period**: {sd} to {ed}\n\n" + _format_table(rows, ["country","clicks","impressions","ctr","position"])

class SitemapsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    site_url: Optional[str] = Field(default=None)

@mcp.tool(name="gsc_sitemaps", annotations={"readOnlyHint":True,"destructiveHint":False,"idempotentHint":True})
async def gsc_sitemaps(params: SitemapsInput) -> str:
    """List sitemaps submitted to Search Console with status."""
    site = params.site_url or SITE_URL
    response = _get_service().sitemaps().list(siteUrl=site).execute()
    sitemaps = response.get("sitemap", [])
    if not sitemaps: return "No sitemaps found."
    rows = [{"path":sm.get("path",""),"type":sm.get("type",""),"submitted":sm.get("lastSubmitted","")[:10],
             "downloaded":sm.get("lastDownloaded","")[:10],"warnings":sm.get("warnings",0),"errors":sm.get("errors",0)} for sm in sitemaps]
    return f"### Search Console -- Sitemaps\n**Site**: {site}\n\n" + _format_table(rows, ["path","type","submitted","downloaded","warnings","errors"])

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    logger.info(f"Starting IITA GSC MCP on port {port}")
    mcp.run(transport="sse", port=port)
