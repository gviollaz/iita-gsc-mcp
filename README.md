# IITA Google Search Console MCP Server

MCP server providing Search Console data for SEO analysis of iita.com.ar.

## Tools (7)

| Tool | Description |
|------|-------------|
| `gsc_search_queries` | Top queries with clicks, impressions, CTR, position |
| `gsc_search_pages` | Top pages by search performance |
| `gsc_queries_by_page` | Queries driving traffic to a specific page |
| `gsc_daily_trend` | Daily search performance trend |
| `gsc_device_breakdown` | Mobile vs Desktop vs Tablet |
| `gsc_country_breakdown` | Traffic by country |
| `gsc_sitemaps` | Sitemap status |

## Env Vars

```
GSC_SITE_URL=https://iita.com.ar/
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
GOOGLE_REFRESH_TOKEN=...
PORT=8080
```
