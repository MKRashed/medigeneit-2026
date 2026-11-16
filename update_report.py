from __future__ import annotations

import calendar
import html
import json
import os
import re
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen


YEAR = 2026
OWNER = "MKRashed"
REPORT_NAME = "github-2026-ytd-monthly-report.html"
API_ROOT = "https://api.github.com"
PER_PAGE = 100

AUTO_BLOCK = re.compile(
    r'<details class="sources auto-source-prs" data-generated="true">.*?</details>',
    re.DOTALL,
)
SECTION = re.compile(
    r'<section class="month" data-month="([A-Z]{3})">.*?</section>',
    re.DOTALL,
)
PR_LINK = re.compile(r'href="(https://github\.com/[^"]+/pull/\d+)"')


def new_prs_label(count: int) -> str:
    return f"{count} new PR" + ("" if count == 1 else "s")


def github_json(url: str) -> dict:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "MKRashed-public-report-updater",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(url, headers=headers)
    with urlopen(request, timeout=30) as response:
        return json.load(response)


def search_url(query: str, page: int = 1, per_page: int = PER_PAGE) -> str:
    params = urlencode(
        {
            "q": query,
            "page": page,
            "per_page": per_page,
            "sort": "created",
            "order": "asc",
        }
    )
    return f"{API_ROOT}/search/issues?{params}"


def fetch_all_prs(query: str) -> list[dict]:
    first = github_json(search_url(query))
    if first.get("incomplete_results"):
        raise RuntimeError("GitHub returned an incomplete PR search result.")

    total = first["total_count"]
    if total > 1000:
        raise RuntimeError(f"PR search returned {total} results; GitHub search is capped at 1000.")

    items = list(first["items"])
    for page in range(2, (total + PER_PAGE - 1) // PER_PAGE + 1):
        result = github_json(search_url(query, page))
        if result.get("incomplete_results"):
            raise RuntimeError(f"GitHub returned an incomplete PR result on page {page}.")
        items.extend(result["items"])

    if len(items) != total:
        raise RuntimeError(f"Expected {total} PRs from GitHub search, received {len(items)}.")
    return items


def search_count(query: str) -> int:
    result = github_json(search_url(query, per_page=1))
    if result.get("incomplete_results"):
        raise RuntimeError("GitHub returned an incomplete PR count.")
    return result["total_count"]


def manual_pr_urls(report: str) -> set[str]:
    report_without_generated = AUTO_BLOCK.sub("", report)
    return set(PR_LINK.findall(report_without_generated))


def pull_request_state(item: dict) -> str:
    repository = item["repository_url"].split("/repos/", 1)[1]
    url = f"{API_ROOT}/repos/{repository}/pulls/{item['number']}"
    data = github_json(url)
    if data["merged"]:
        return "Merged"
    if data["state"] == "open":
        return "Open"
    return "Closed unmerged"


def render_new_prs(items: list[dict]) -> str:
    if not items:
        return ""
    rows = []
    for item in sorted(items, key=lambda pr: (pr["created_at"], pr["number"])):
        repository = item["repository_url"].split("/repos/", 1)[1]
        url = html.escape(item["html_url"], quote=True)
        label = html.escape(f"{repository} — PR #{item['number']}: {item['title']}")
        state = html.escape(item["_report_state"])
        rows.append(
            f'<li><a href="{url}">{label}</a> '
            f'<span class="source-state">({state})</span></li>'
        )
    return (
        '<details class="sources auto-source-prs" data-generated="true">'
        f"<summary>New PRs ({len(items)})</summary><ul>"
        + "\n".join(rows)
        + "</ul></details>"
    )


def month_count_text(
    text: str, month_number: int, total_prs: int, new_count: int, end_date: date
) -> str:
    text = re.sub(r"\s*·\s*\d+ new PRs?", "", text)
    text = re.sub(r"\d+ source PRs", f"{total_prs} source PRs", text)

    month_abbr = calendar.month_abbr[month_number]
    text = re.sub(
        r"\s*·\s*(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) 1–\d+ only",
        "",
        text,
    )
    if new_count:
        text += f" · {new_prs_label(new_count)}"
    if month_number == end_date.month and end_date.day < calendar.monthrange(YEAR, month_number)[1]:
        text += f" · {month_abbr} 1–{end_date.day} only"
    return text


def render_new_month(month_number: int, items: list[dict], end_date: date) -> str:
    month_abbr = calendar.month_abbr[month_number].upper()
    month_title = calendar.month_name[month_number].upper()
    month_total = len(items)
    partial = ""
    if month_number == end_date.month and end_date.day < calendar.monthrange(YEAR, month_number)[1]:
        partial = f" · {calendar.month_abbr[month_number]} 1–{end_date.day} only"

    return (
        f'<section class="month" data-month="{month_abbr}">'
        f"<h2>Monthly Report of {month_title} {YEAR}</h2>"
        '<table class="meta"><tbody><tr><td>Name</td><td>Md. Rashedul Islam</td></tr>'
        "<tr><td>Designation</td><td>Software Engineer</td></tr></tbody></table>"
        f'<p class="month-count">0 summarized work items · 0 projects · '
        f"{month_total} source PRs · {new_prs_label(month_total)}{partial}</p>"
        '<div class="table-scroll"><table class="report">'
        '<colgroup><col style="width:6%"><col style="width:17%">'
        '<col style="width:42%"><col style="width:10%">'
        '<col style="width:12.5%"><col style="width:12.5%"></colgroup>'
        "<thead><tr><th>SL</th><th>Project</th><th>Details</th>"
        "<th>Status<br>(%)</th><th>Assigned<br>Score</th>"
        "<th>Earned<br>Score</th></tr></thead><tbody></tbody></table></div>"
        + render_new_prs(items)
        + "</section>"
    )


def refresh_report(
    report: str,
    prs: list[dict],
    merged: int,
    closed_unmerged: int,
    open_prs: int,
    end_date: date,
) -> str:
    total = len(prs)
    if merged + closed_unmerged + open_prs != total:
        raise RuntimeError(
            "Merged, closed-unmerged, and open PR counts do not match the total."
        )

    report = AUTO_BLOCK.sub("", report)
    known_urls = manual_pr_urls(report)
    fetched_urls = {item["html_url"] for item in prs}
    missing_urls = known_urls - fetched_urls
    if missing_urls:
        raise RuntimeError(
            f"{len(missing_urls)} existing source PRs are absent from public search; "
            "refusing to alter the report totals."
        )

    new_prs = [item for item in prs if item["html_url"] not in known_urls]
    for item in new_prs:
        item["_report_state"] = pull_request_state(item)

    prs_by_month: dict[str, list[dict]] = defaultdict(list)
    new_by_month: dict[str, list[dict]] = defaultdict(list)
    for item in prs:
        prs_by_month[item["created_at"][:7]].append(item)
    for item in new_prs:
        new_by_month[item["created_at"][:7]].append(item)

    summary = (
        f"based on <strong>{total} PRs</strong> "
        f"({merged} merged; {closed_unmerged} closed without merging; {open_prs} open)."
    )
    report, summary_replacements = re.subn(
        r"based on <strong>\d+ PRs</strong> "
        r"\(\d+ merged; \d+ closed without merging(?:; \d+ open)?\)\.",
        summary,
        report,
        count=1,
    )
    if summary_replacements != 1:
        raise RuntimeError("Could not locate the report overview PR totals.")

    end_label = f"Jan 1–{end_date.strftime('%b')} {end_date.day}, {YEAR}"
    report, date_replacements = re.subn(
        r"Jan 1–[A-Za-z]{3} \d{1,2}, 2026", end_label, report, count=1
    )
    if date_replacements != 1:
        raise RuntimeError("Could not locate the report date range.")

    partial_note = re.compile(
        r"(?:January|February|March|April|May|June|July|August|September|October|"
        r"November|December) covers (?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|"
        r"Nov|Dec) 1–\d+ only\."
    )
    if end_date.day < calendar.monthrange(YEAR, end_date.month)[1]:
        note = (
            f"{calendar.month_name[end_date.month]} covers "
            f"{calendar.month_abbr[end_date.month]} 1–{end_date.day} only."
        )
        report = partial_note.sub(note, report, count=1)
    else:
        report = partial_note.sub("", report, count=1)

    seen_months: set[int] = set()

    def update_section(match: re.Match) -> str:
        month_abbr = match.group(1)
        month_number = list(calendar.month_abbr).index(month_abbr.title())
        month_key = f"{YEAR}-{month_number:02d}"
        section = match.group(0)
        seen_months.add(month_number)

        count_match = re.search(r'<p class="month-count">(.*?)</p>', section, re.DOTALL)
        if count_match:
            updated_count = month_count_text(
                count_match.group(1),
                month_number,
                len(prs_by_month[month_key]),
                len(new_by_month[month_key]),
                end_date,
            )
            section = section[: count_match.start(1)] + updated_count + section[count_match.end(1) :]

        new_block = render_new_prs(new_by_month[month_key])
        if new_block:
            section = section.replace("</section>", new_block + "</section>", 1)
        return section

    report = SECTION.sub(update_section, report)

    new_months = sorted(
        int(month_key[5:7])
        for month_key, items in new_by_month.items()
        if items and int(month_key[5:7]) not in seen_months
    )
    for month_number in new_months:
        section = render_new_month(
            month_number,
            new_by_month[f"{YEAR}-{month_number:02d}"],
            end_date,
        )
        report = report.replace("</body>", section + "</body>", 1)

    return report


def main() -> None:
    now_dhaka = datetime.now(timezone.utc) + timedelta(hours=6)
    today = now_dhaka.date()
    year_end = date(YEAR, 12, 31)
    end_date = min(today, year_end)
    base_query = f"author:{OWNER} is:pr created:{YEAR}-01-01..{end_date.isoformat()}"

    prs = fetch_all_prs(base_query)
    merged = search_count(f"{base_query} is:merged")
    closed_unmerged = search_count(f"{base_query} is:closed is:unmerged")
    open_prs = search_count(f"{base_query} is:open")

    report_path = Path(__file__).resolve().with_name(REPORT_NAME)
    report = report_path.read_text(encoding="utf-8")
    updated = refresh_report(report, prs, merged, closed_unmerged, open_prs, end_date)
    if updated != report:
        report_path.write_text(updated, encoding="utf-8")

    print(
        f"Checked {len(prs)} public PRs through {end_date.isoformat()}; "
        f"{merged} merged, {closed_unmerged} closed unmerged, {open_prs} open; "
        f"{sum(1 for item in prs if item['html_url'] not in manual_pr_urls(report))} new."
    )


if __name__ == "__main__":
    main()
