"""
task.py — Single pipeline sync task.

run_task() is called by main.py on every interval tick.  It:
  1. Fetches the official chapter list from the state API.
  2. Reads our current mirror from the main GitLab snippet.
  3. Merges (diff + fuzzy-anchor + expired/disappearance detection).
  4. Writes back to the main snippet only when something changed.

Decennial dataset helpers (to be wired up when budget parsing is implemented):
  - find_decade_entry(): look up a year's decade entry in datasets.json.
  - update_dataset_entry(): update/insert a year's metadata in datasets.json.
"""

import hashlib
import json
import logging
from datetime import date, datetime, timezone

import httpx

from src.support.chapters import chapters_to_json, merge_chapters
from src.support.getenv import config, target
from src.support.scrapper import fetch_chapters, fetch_summary
from src.support.storage import read_snippet_file, upsert_snippet_files, list_snippet_files

logger = logging.getLogger(__name__)

CHAPTERS_FILENAME = "kapitola.json"
DATASETS_FILENAME = "datasets.json"


# ── Decennial dataset helpers ────────────────────────────────────────────────


def decade_range(year: int) -> tuple[int, int]:
    """Return the (start, end) decade range for a given year.

    Args:
        year: Four-digit year integer (e.g. 2026).

    Returns:
        Tuple of (decade_start, decade_end), e.g. (2020, 2029).
    """
    start = (year // 10) * 10
    return start, start + 9


def find_decade_entry(datasets: list[dict], year: int) -> dict | None:
    """Find the decade entry in a ``datasets.json`` list for *year*.

    Args:
        datasets: Parsed ``datasets.json`` list.
        year:     Four-digit year integer.

    Returns:
        The matching decade dict, or None if not found.
    """
    start, end = decade_range(year)
    for entry in datasets:
        if str(entry.get("periodStart")) == str(start) and str(entry.get("periodEnd")) == str(end):
            return entry
    return None


def extract_snippet_id_from_api_url(api_url: str) -> str:
    """Extract the GitLab snippet ID from an API URL.

    Example: ``https://gitlab.h163.xyz/api/v4/snippets/3`` → ``"3"``.

    Args:
        api_url: Full GitLab API URL of a snippet.

    Returns:
        Snippet ID as a string.

    Raises:
        ValueError: When the URL does not end with a numeric ID.
    """
    snippet_id = api_url.rstrip("/").rsplit("/", 1)[-1]
    if not snippet_id.isdigit():
        raise ValueError(
            f"Cannot extract snippet ID from API URL: '{api_url}'"
        )
    return snippet_id


def compute_sha256(content: str) -> str:
    """Compute the SHA-256 hex digest of a UTF-8 string.

    Args:
        content: The string to hash (e.g. serialized JSON payload).

    Returns:
        Lowercase hexadecimal SHA-256 digest string (64 characters).
    """
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def update_dataset_entry(
    datasets: list[dict],
    year: int,
    raw_link: str,
    sha256: str | None = None,
) -> list[dict]:
    """Return a new datasets list with the year entry updated/inserted.

    Locates the decade entry for *year*, then updates or inserts the year's
    dataset metadata dict inside its ``datasets`` sub-list.

    Args:
        datasets: Parsed ``datasets.json`` list (not mutated).
        year:     Four-digit year integer.
        raw_link: Raw URL to the year's dataset file in the decade snippet.
        sha256:   Optional SHA-256 hex digest of the pushed file content.

    Returns:
        New datasets list with the updated entry.

    Raises:
        KeyError: When no decade entry exists for the given year.
    """
    decade_entry = find_decade_entry(datasets, year)
    if decade_entry is None:
        raise KeyError(
            f"No decade entry found in datasets.json for year {year}. "
            "Please create the decade snippet entry manually first."
        )

    today = date.today().isoformat()
    result = [dict(e) for e in datasets]
    for entry in result:
        start, end = decade_range(year)
        if str(entry.get("periodStart")) == str(start) and str(entry.get("periodEnd")) == str(end):
            year_datasets: list[dict] = list(entry.get("datasets", []))
            # Build the year dataset entry, including optional sha256.
            year_entry: dict = {
                "type": "rozpocet",
                "year": year,
                "link": raw_link,
                "lastUpdate": today,
            }
            if sha256 is not None:
                year_entry["sha256"] = sha256

            # Find and replace or append the year's entry.
            for i, ds in enumerate(year_datasets):
                if ds.get("year") == year:
                    year_datasets[i] = year_entry
                    break
            else:
                year_datasets.append(year_entry)
            entry["datasets"] = year_datasets
    return result


# ── Main task ────────────────────────────────────────────────────────────────


def is_all_zero(node) -> bool:
    """Check if all budget numbers in the tree are zero."""
    values = []

    def collect(n):
        if isinstance(n, dict):
            for k, v in n.items():
                if k in ("approved", "afterChanges", "finalBudget", "reality") and isinstance(v, (int, float)):
                    values.append(v)
                else:
                    collect(v)
        elif isinstance(n, list):
            for item in n:
                collect(item)

    collect(node)
    return all(v == 0.0 for v in values)


def get_decade_snippet_endpoint(main_endpoint: str, snippet_id: int) -> str:
    """Derive the endpoint for a decade snippet based on the main snippet endpoint."""
    base = main_endpoint.rstrip("/")
    parent_url = base.rsplit("/", 1)[0]
    return f"{parent_url}/{snippet_id}"


def map_raw_node(node: dict) -> dict:
    """Convert a raw souhrnny node to the target budget_item structure."""
    mapped = {
        "name": node.get("name", ""),
        "code": str(node.get("code", "")),
        "budget": {
            "approved": float(node.get("budget", {}).get("approved", 0.0)),
            "afterChanges": float(node.get("budget", {}).get("afterChanges", 0.0)),
            "finalBudget": float(node.get("budget", {}).get("finalBudget", 0.0)),
            "reality": float(node.get("budget", {}).get("reality", 0.0)),
        },
        "items": [map_raw_node(child) for child in node.get("children", [])]
    }
    if "note" in node:
        mapped["note"] = str(node["note"])
    return mapped


def trickle_up_node(node: dict) -> dict:
    """Recursively calculate budget sums for non-leaf nodes.

    Leaf nodes (``items`` is empty) are returned unchanged — they retain
    their original ``budget`` and have no ``calc`` key.

    Non-leaf nodes retain the official declared ``budget`` and gain a ``calc``
    object with:
      - ``calc.budget``: sum of children's effective budgets (recursively
        resolved — uses ``calc.budget`` if the child is non-leaf, else the
        child's own ``budget``).
      - ``calc.delta``:  ``calc.budget - budget``, pointing out discrepancies
        between the official declared value and the child-rollup total.

    Args:
        node: A budget_item dict with ``budget`` and ``items`` keys.

    Returns:
        The same dict (mutated in-place) with ``calc`` added when non-leaf.
    """
    if not node.get("items"):
        # Leaf: nothing to compute.
        return node

    # Recurse first so children are fully computed before we sum them.
    node["items"] = [trickle_up_node(item) for item in node["items"]]

    # Preserve the original declared budget so we can compute the delta.
    original_budget = dict(node["budget"])

    calc_approved = 0.0
    calc_after_changes = 0.0
    calc_final_budget = 0.0
    calc_reality = 0.0

    for child in node["items"]:
        # If child is itself non-leaf it has a calc.budget; otherwise use budget.
        effective = child.get("calc", {}).get("budget") or child["budget"]
        calc_approved += effective.get("approved", 0.0)
        calc_after_changes += effective.get("afterChanges", 0.0)
        calc_final_budget += effective.get("finalBudget", 0.0)
        calc_reality += effective.get("reality", 0.0)

    calc_budget = {
        "approved": calc_approved,
        "afterChanges": calc_after_changes,
        "finalBudget": calc_final_budget,
        "reality": calc_reality,
    }

    node["calc"] = {
        "budget": calc_budget,
        "delta": {
            "approved": calc_approved - original_budget.get("approved", 0.0),
            "afterChanges": calc_after_changes - original_budget.get("afterChanges", 0.0),
            "finalBudget": calc_final_budget - original_budget.get("finalBudget", 0.0),
            "reality": calc_reality - original_budget.get("reality", 0.0),
        },
    }
    return node


def _effective_budget(node: dict) -> dict:
    """Return the effective (calc) budget for a node if available, else its declared budget."""
    return node.get("calc", {}).get("budget") or node["budget"]


def build_net_detail(raw_root: dict | None, net_type: str) -> dict:
    """Build a details block (revenue or expenditure) from the raw Revenues/Expenditures node.

    The returned dict exposes the official top-level values (approved, afterChanges,
    finalBudget, reality) from the API as well as the full ``items`` tree with
    ``calc`` objects on every non-leaf node.
    """
    if not raw_root:
        return {
            "net": net_type,
            "approved": 0.0,
            "afterChanges": 0.0,
            "finalBudget": 0.0,
            "reality": 0.0,
            "items": []
        }

    mapped = map_raw_node(raw_root)
    # Preserve original top-level declared values before trickle-up mutates the node.
    official_b = dict(mapped["budget"])
    trickled = trickle_up_node(mapped)

    return {
        "net": net_type,
        "approved": official_b["approved"],
        "afterChanges": official_b["afterChanges"],
        "finalBudget": official_b["finalBudget"],
        "reality": official_b["reality"],
        "items": trickled["items"]
    }


def build_whole_budget(revenue: dict, expenditure: dict) -> dict:
    """Build the whole net budget values by subtracting expenditures from revenues."""
    return {
        "approved": revenue["approved"] - expenditure["approved"],
        "afterChanges": revenue["afterChanges"] - expenditure["afterChanges"],
        "finalBudget": revenue["finalBudget"] - expenditure["finalBudget"],
        "reality": revenue["reality"] - expenditure["reality"]
    }


def merge_item_lists(lists_of_items: list[list[dict]]) -> list[dict]:
    """Merge multiple lists of items by matching their codes.

    Non-leaf merged nodes get a ``calc`` object (budget = sum of children,
    delta = calc.budget - budget) after the recursive merge.
    Leaf merged nodes retain only their summed ``budget``.
    """
    merged_map: dict[str, dict] = {}

    for item_list in lists_of_items:
        for item in item_list:
            code = item["code"]
            if code not in merged_map:
                merged_map[code] = {
                    "name": item["name"],
                    "code": code,
                    "budget": {
                        "approved": 0.0,
                        "afterChanges": 0.0,
                        "finalBudget": 0.0,
                        "reality": 0.0
                    },
                    "items_to_merge": []
                }
                if "note" in item:
                    merged_map[code]["note"] = item["note"]

            merged_map[code]["items_to_merge"].append(item["items"])

    results = []
    for code, m_item in merged_map.items():
        merged_children = merge_item_lists(m_item["items_to_merge"])
        del m_item["items_to_merge"]
        m_item["items"] = merged_children

        if not m_item["items"]:
            # Leaf: sum raw budgets from all contributing items.
            approved = 0.0
            after_changes = 0.0
            final_budget = 0.0
            reality = 0.0
            for item_list in lists_of_items:
                for item in item_list:
                    if item["code"] == code:
                        approved += item["budget"]["approved"]
                        after_changes += item["budget"]["afterChanges"]
                        final_budget += item["budget"]["finalBudget"]
                        reality += item["budget"]["reality"]
            m_item["budget"] = {
                "approved": approved,
                "afterChanges": after_changes,
                "finalBudget": final_budget,
                "reality": reality
            }
        else:
            # Non-leaf: the declared budget is 0 (no single official declared value
            # for a cross-resort aggregate). calc.budget is the sum of children;
            # delta reflects the discrepancy vs the declared 0 (i.e. = calc.budget).
            calc_approved = sum(_effective_budget(c).get("approved", 0.0) for c in m_item["items"])
            calc_after_changes = sum(_effective_budget(c).get("afterChanges", 0.0) for c in m_item["items"])
            calc_final_budget = sum(_effective_budget(c).get("finalBudget", 0.0) for c in m_item["items"])
            calc_reality = sum(_effective_budget(c).get("reality", 0.0) for c in m_item["items"])
            calc_bud = {
                "approved": calc_approved,
                "afterChanges": calc_after_changes,
                "finalBudget": calc_final_budget,
                "reality": calc_reality,
            }
            declared = m_item["budget"]
            m_item["calc"] = {
                "budget": calc_bud,
                "delta": {
                    "approved": calc_approved - declared.get("approved", 0.0),
                    "afterChanges": calc_after_changes - declared.get("afterChanges", 0.0),
                    "finalBudget": calc_final_budget - declared.get("finalBudget", 0.0),
                    "reality": calc_reality - declared.get("reality", 0.0),
                },
            }
        results.append(m_item)

    return results


def build_national_net_detail(resorts_details: list[dict], net_type: str) -> dict:
    """Build a unified national net detail by merging resort details recursively."""
    lists_of_items = []
    for r in resorts_details:
        for detail in r["details"]:
            if detail["net"] == net_type:
                lists_of_items.append(detail["items"])
                break

    merged_items = merge_item_lists(lists_of_items)

    # Sum the official declared totals across all resorts for this net type.
    total_approved = 0.0
    total_after_changes = 0.0
    total_final_budget = 0.0
    total_reality = 0.0
    for r in resorts_details:
        for detail in r["details"]:
            if detail["net"] == net_type:
                total_approved += detail.get("approved", 0.0)
                total_after_changes += detail.get("afterChanges", 0.0)
                total_final_budget += detail.get("finalBudget", 0.0)
                total_reality += detail.get("reality", 0.0)
                break

    return {
        "net": net_type,
        "approved": total_approved,
        "afterChanges": total_after_changes,
        "finalBudget": total_final_budget,
        "reality": total_reality,
        "items": merged_items
    }


def _net_detail_effective(nd: dict) -> dict:
    """Extract the effective (trickle-up) budget from a net_detail dict.

    net_detail dicts store declared values flat (approved, afterChanges, …)
    rather than under a ``budget`` sub-key. When items exist, the effective
    total is the sum of each top-level item's effective budget (using
    calc.budget when the item is non-leaf, otherwise its own budget).

    Args:
        nd: A net_detail dict with ``approved``, ``afterChanges``, etc. and
            an ``items`` list.

    Returns:
        A budget_values dict representing the effective rolled-up total.
    """
    items = nd.get("items", [])
    if not items:
        # No children — the declared top-level values are the best we have.
        return {k: nd.get(k, 0.0) for k in ("approved", "afterChanges", "finalBudget", "reality")}

    approved = sum(_effective_budget(c).get("approved", 0.0) for c in items)
    after_changes = sum(_effective_budget(c).get("afterChanges", 0.0) for c in items)
    final_budget = sum(_effective_budget(c).get("finalBudget", 0.0) for c in items)
    reality = sum(_effective_budget(c).get("reality", 0.0) for c in items)
    return {
        "approved": approved,
        "afterChanges": after_changes,
        "finalBudget": final_budget,
        "reality": reality,
    }


def calculate_national_calc(
    national_revenue: dict,
    national_expenditure: dict,
    national_whole: dict,
) -> dict:
    """Calculate the national calc object (calc.budget and calc.delta).

    The official ``national_whole`` contains the *declared* net total from the
    API (revenue - expenditure at the top level as reported).

    ``calc.budget`` is the *computed* net: the trickle-up sum of all resort
    revenue items minus expenditure items.

    ``calc.delta`` = ``calc.budget - national_whole`` — highlights discrepancies
    between what the government declares as the total and what their own line
    items sum to.

    Args:
        national_revenue:     Aggregated national revenue net_detail dict.
        national_expenditure: Aggregated national expenditure net_detail dict.
        national_whole:       Official declared national net budget_values dict.

    Returns:
        Dict with keys ``budget`` (budget_values) and ``delta`` (budget_values).
    """
    rev_effective = _net_detail_effective(national_revenue)
    exp_effective = _net_detail_effective(national_expenditure)

    calc_net = {
        "approved": rev_effective.get("approved", 0.0) - exp_effective.get("approved", 0.0),
        "afterChanges": rev_effective.get("afterChanges", 0.0) - exp_effective.get("afterChanges", 0.0),
        "finalBudget": rev_effective.get("finalBudget", 0.0) - exp_effective.get("finalBudget", 0.0),
        "reality": rev_effective.get("reality", 0.0) - exp_effective.get("reality", 0.0),
    }

    return {
        "budget": calc_net,
        "delta": {
            "approved": calc_net["approved"] - national_whole.get("approved", 0.0),
            "afterChanges": calc_net["afterChanges"] - national_whole.get("afterChanges", 0.0),
            "finalBudget": calc_net["finalBudget"] - national_whole.get("finalBudget", 0.0),
            "reality": calc_net["reality"] - national_whole.get("reality", 0.0),
        },
    }


def sync_yearly_datasets(cfg: config, tgt: target, client: httpx.Client) -> None:
    """Scrape and sync yearly budget datasets from backlog_start_year to the current year."""
    logger.info("Starting yearly datasets sync. Backlog start year: %d", tgt.backlog_start_year)

    # 1. Fetch current chapters mirror from main snippet
    try:
        raw_chapters = read_snippet_file(cfg.endpoint, CHAPTERS_FILENAME, cfg.token, cfg.base_url, client)
        chapters = json.loads(raw_chapters)
    except Exception as exc:
        logger.error("Failed to read chapters mirror for yearly sync: %s", exc)
        return

    non_expired_chapters = [ch for ch in chapters if not ch.get("expired")]
    if not non_expired_chapters:
        logger.warning("No active chapters found; skipping yearly datasets sync")
        return

    # 2. Retrieve or initialize datasets.json
    try:
        raw_datasets = read_snippet_file(cfg.endpoint, DATASETS_FILENAME, cfg.token, cfg.base_url, client)
        datasets = json.loads(raw_datasets)
        if not isinstance(datasets, list) or not datasets or not any("periodStart" in entry for entry in datasets):
            raise ValueError("datasets.json is empty or invalid")
    except Exception:
        logger.warning("datasets.json not found or malformed; initializing decade mapping")
        web_link_4 = f"{cfg.base_url}/openbudget/data-pipeline/-/snippets/4"
        api_link_4 = f"{cfg.api_prefix}/projects/openbudget%2Fdata-pipeline/snippets/4"
        web_link_3 = f"{cfg.base_url}/openbudget/data-pipeline/-/snippets/3"
        api_link_3 = f"{cfg.api_prefix}/projects/openbudget%2Fdata-pipeline/snippets/3"

        datasets = [
            {
                "periodStart": "2010",
                "periodEnd": "2019",
                "link": web_link_4,
                "api": api_link_4,
                "datasets": []
            },
            {
                "periodStart": "2020",
                "periodEnd": "2029",
                "link": web_link_3,
                "api": api_link_3,
                "datasets": []
            }
        ]

    current_year = datetime.now().year
    datasets_changed = False

    # 3. Iterate over years from backlog_start_year to current_year
    for year in range(tgt.backlog_start_year, current_year + 1):
        decade_entry = find_decade_entry(datasets, year)
        if decade_entry is None:
            logger.warning("No decade entry found for year %d; skipping", year)
            continue

        # Check if this year already has a sha256 in datasets.json.
        existing_year_entry = next((ds for ds in decade_entry.get("datasets", []) if ds.get("year") == year), None)
        existing_sha256 = existing_year_entry.get("sha256") if existing_year_entry else None

        if year < current_year and existing_year_entry is not None:
            decade_snippet_endpoint = get_decade_snippet_endpoint(cfg.endpoint, int(decade_entry["api"].rsplit("/", 1)[-1]))
            try:
                decade_files = set(list_snippet_files(decade_snippet_endpoint, cfg.token, cfg.base_url, client))
            except Exception as exc:
                logger.error("Failed to list files in decade snippet %s: %s", decade_entry["api"], exc)
                decade_files = set()

            if f"v{year}.json" in decade_files and existing_sha256 is not None:
                logger.info("Dataset v%d.json already exists with sha256; skipping", year)
                continue

        # 4. Find the last reported month
        found_timeframe = None
        chapters_data = []

        chapters_to_check = non_expired_chapters[:3]

        for month in range(12, 0, -1):
            timeframe = f"{year % 100:02d}{month:02d}"
            month_has_data = False
            fetched_temp = {}

            for ch in chapters_to_check:
                try:
                    summary = fetch_summary(tgt.summary_endpoint, timeframe, ch["id"], client)
                    fetched_temp[ch["id"]] = (ch, summary)
                    if not is_all_zero(summary):
                        month_has_data = True
                        break
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code == 404:
                        break
                except Exception:
                    pass

            if month_has_data:
                found_timeframe = timeframe
                logger.info("Found last reported month %d (timeframe %s) for year %d", month, timeframe, year)

                for ch in non_expired_chapters:
                    ch_id = ch["id"]
                    if ch_id in fetched_temp:
                        ch, summary = fetched_temp[ch_id]
                    else:
                        try:
                            summary = fetch_summary(tgt.summary_endpoint, timeframe, ch_id, client)
                        except Exception as exc:
                            logger.error("Failed to fetch summary for chapter %d, timeframe %s: %s", ch_id, timeframe, exc)
                            summary = {"name": ch["name"], "budget": {}, "children": []}

                    chapters_data.append({
                        "chapter": ch,
                        "raw_data": summary
                    })
                break

        if not found_timeframe or not chapters_data:
            logger.warning("No data found for year %d", year)
            continue

        # 5. Process and calculate the data structure
        resorts_details = []
        for item in chapters_data:
            ch = item["chapter"]
            raw_data = item["raw_data"]

            raw_children = raw_data.get("children", [])
            raw_rev = next((child for child in raw_children if child.get("name") in ("Revenues", "Příjmy")), None)
            raw_exp = next((child for child in raw_children if child.get("name") in ("Expenditures", "Výdaje")), None)

            rev_detail = build_net_detail(raw_rev, "revenue")
            exp_detail = build_net_detail(raw_exp, "expenditure")

            resort_whole = build_whole_budget(rev_detail, exp_detail)

            resorts_details.append({
                "name": ch["name"],
                "code": str(ch["id"]),
                "budget": {
                    "whole": resort_whole
                },
                "details": [rev_detail, exp_detail],
                "raw_data": raw_data
            })

        # 6. Aggregate at national level
        national_revenue = build_national_net_detail(resorts_details, "revenue")
        national_expenditure = build_national_net_detail(resorts_details, "expenditure")
        national_whole = build_whole_budget(national_revenue, national_expenditure)

        calc_fields = calculate_national_calc(national_revenue, national_expenditure, national_whole)

        cleaned_resorts = []
        for r in resorts_details:
            cleaned_resorts.append({
                "name": r["name"],
                "code": r["code"],
                "budget": r["budget"],
                "details": r["details"]
            })

        v_year_data = {
            "name": "Czech Republic",
            "budget": {
                "whole": national_whole,
                "details": [national_revenue, national_expenditure]
            },
            "calc": calc_fields,
            "resorts": cleaned_resorts
        }

        # 7. Compute SHA-256 and skip if unchanged
        content_json = json.dumps(v_year_data, indent=2, ensure_ascii=False)
        content_sha256 = compute_sha256(content_json)

        if existing_sha256 is not None and existing_sha256 == content_sha256:
            logger.info("SHA-256 unchanged for v%d.json; skipping upload", year)
            continue

        # 8. Upload vYYYY.json to its decade snippet
        decade_snippet_id = int(decade_entry["api"].rsplit("/", 1)[-1])
        decade_endpoint = get_decade_snippet_endpoint(cfg.endpoint, decade_snippet_id)

        filename = f"v{year}.json"

        try:
            upsert_snippet_files(
                snippet_target=decade_endpoint,
                files=[{"file_path": filename, "content": content_json}],
                token=cfg.token,
                base_url=cfg.base_url,
                client=client
            )
            logger.info("Successfully pushed %s to decade snippet %d", filename, decade_snippet_id)
        except Exception as exc:
            logger.error("Failed to push %s to decade snippet: %s", filename, exc)
            continue

        # 9. Update datasets.json with the raw link and sha256
        raw_link = f"{decade_entry['link'].rstrip('/')}/raw/main/{filename}"
        datasets = update_dataset_entry(datasets, year, raw_link, sha256=content_sha256)
        datasets_changed = True

    # 10. Write back datasets.json if updated
    if datasets_changed:
        try:
            upsert_snippet_files(
                snippet_target=cfg.endpoint,
                files=[{"file_path": DATASETS_FILENAME, "content": json.dumps(datasets, indent=2, ensure_ascii=False)}],
                token=cfg.token,
                base_url=cfg.base_url,
                client=client
            )
            logger.info("Successfully updated datasets.json in main snippet")
        except Exception as exc:
            logger.error("Failed to write datasets.json to main snippet: %s", exc)


def run_task(cfg: config, tgt: target, client: httpx.Client) -> None:
    """Execute one chapter mirror sync cycle and synchronize yearly datasets.

    Args:
        cfg:    Loaded pipeline config (GitLab endpoint, token, interval).
        tgt:    Loaded target config (chapters_endpoint, summary_endpoint).
        client: Shared httpx.Client instance (injected for testability).
    """
    started_at = datetime.now(tz=timezone.utc).isoformat()
    logger.info("[%s] Starting sync cycle", started_at)

    try:
        official = fetch_chapters(tgt.chapters_endpoint, client)
    except Exception as exc:
        logger.error("Failed to fetch official chapters: %s", exc)
        return

    logger.info("Fetched %d official chapters", len(official))

    try:
        raw_ours = read_snippet_file(
            snippet_target=cfg.endpoint,
            file_path=CHAPTERS_FILENAME,
            token=cfg.token,
            base_url=cfg.base_url,
            client=client,
        )
        ours: list[dict] = json.loads(raw_ours)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            logger.warning(
                "'%s' not found in snippet; starting from empty mirror",
                CHAPTERS_FILENAME,
            )
            ours = []
        else:
            logger.error("Failed to read mirror from snippet (HTTP %s): %s", exc.response.status_code, exc)
            return
    except Exception as exc:
        logger.error("Failed to read current mirror from snippet: %s", exc)
        return

    logger.info("Loaded %d chapters from our mirror", len(ours))

    merged, changed = merge_chapters(official, ours)

    if changed:
        logger.info("Changes detected (%d total chapters after merge)", len(merged))
        try:
            upsert_snippet_files(
                snippet_target=cfg.endpoint,
                files=[{"file_path": CHAPTERS_FILENAME, "content": chapters_to_json(merged)}],
                token=cfg.token,
                base_url=cfg.base_url,
                client=client,
            )
            logger.info("Mirror updated successfully")
        except Exception as exc:
            logger.error("Failed to write updated mirror to snippet: %s", exc)
            return
    else:
        logger.info("No changes detected in chapters list")

    # Run yearly datasets sync
    sync_yearly_datasets(cfg, tgt, client)

    # Push updated schemas to the misc snippet (snippet 5) and update misc.json.
    try:
        from src.support.schemas import push_schemas_to_snippet
        push_schemas_to_snippet(cfg, client=client)
    except Exception as exc:
        logger.warning("Could not push schemas to misc snippet: %s", exc)