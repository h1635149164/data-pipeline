"""tests/test_task.py — Unit tests for src/task.py (run_task)"""

import json

import httpx
import respx

from src.support.getenv import config, target
from src.task import run_task, CHAPTERS_FILENAME, compute_sha256, update_dataset_entry

CHAPTERS_ENDPOINT = "https://api.state.gov/kapitola"
SUMMARY_ENDPOINT = "https://api.state.gov/souhrnny"

BASE_URL = "https://gitlab.example.com"
ENDPOINT = "https://gitlab.example.com/api/v4/projects/openbudget%2Fdata-pipeline/snippets/2"
TOKEN = "mytoken"

OFFICIAL_CHAPTERS = [
    {
        "id": 301,
        "name": "Office of the President of the Republic",
        "startDate": "1900-01-01",
        "endDate": "9999-12-31",
        "expired": False,
    }
]

OUR_CHAPTERS = [
    {
        "iid": 1,
        "id": 301,
        "name": "Office of the President of the Republic",
        "startDate": "1900-01-01",
        "endDate": "9999-12-31",
        "expired": False,
    }
]


def _make_config() -> config:
    return config(
        conf_path=None,
        endpoint=ENDPOINT,
        token=TOKEN,
        interval=60,
    )


def _make_target() -> target:
    return target(
        chapters_endpoint=CHAPTERS_ENDPOINT,
        summary_endpoint=SUMMARY_ENDPOINT,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRunTask:
    @respx.mock
    def test_no_change_does_not_write(self):
        respx.get(CHAPTERS_ENDPOINT).mock(
            return_value=httpx.Response(200, json=OFFICIAL_CHAPTERS)
        )
        
        raw_url = f"{ENDPOINT}/files/main/{CHAPTERS_FILENAME}/raw"
        respx.get(raw_url).mock(
            return_value=httpx.Response(200, text=json.dumps(OUR_CHAPTERS))
        )
        
        with httpx.Client() as client:
            run_task(_make_config(), _make_target(), client)
            
        assert not any(r.request.method == "PUT" for r in respx.calls)

    @respx.mock
    def test_change_triggers_write(self):
        new_official = OFFICIAL_CHAPTERS + [
            {
                "id": 302,
                "name": "Chamber of Deputies",
                "startDate": "1900-01-01",
                "endDate": "9999-12-31",
                "expired": False,
            }
        ]
        respx.get(CHAPTERS_ENDPOINT).mock(
            return_value=httpx.Response(200, json=new_official)
        )
        
        raw_url = f"{ENDPOINT}/files/main/{CHAPTERS_FILENAME}/raw"
        respx.get(raw_url).mock(
            return_value=httpx.Response(200, text=json.dumps(OUR_CHAPTERS))
        )
        
        # upsert preflight
        respx.get(ENDPOINT).mock(
            return_value=httpx.Response(200, json={"files": [{"path": CHAPTERS_FILENAME}]})
        )
        
        put_route = respx.put(ENDPOINT).mock(
            return_value=httpx.Response(200, json={})
        )
        
        with httpx.Client() as client:
            run_task(_make_config(), _make_target(), client)
            
        assert put_route.called

    @respx.mock
    def test_fetch_chapters_failure_returns_early(self):
        respx.get(CHAPTERS_ENDPOINT).mock(return_value=httpx.Response(500))
        with httpx.Client() as client:
            run_task(_make_config(), _make_target(), client)
        assert all(str(r.request.url) != ENDPOINT for r in respx.calls)

    @respx.mock
    def test_missing_snippet_file_starts_from_empty(self):
        respx.get(CHAPTERS_ENDPOINT).mock(
            return_value=httpx.Response(200, json=OFFICIAL_CHAPTERS)
        )
        
        raw_url = f"{ENDPOINT}/files/main/{CHAPTERS_FILENAME}/raw"
        fallback_url = f"{ENDPOINT}/raw/{CHAPTERS_FILENAME}"
        respx.get(raw_url).mock(return_value=httpx.Response(404))
        respx.get(fallback_url).mock(return_value=httpx.Response(404))
        
        respx.get(ENDPOINT).mock(return_value=httpx.Response(200, json={"files": []}))
        
        put_route = respx.put(ENDPOINT).mock(return_value=httpx.Response(200, json={}))
        
        with httpx.Client() as client:
            run_task(_make_config(), _make_target(), client)
            
        assert put_route.called


class TestCalculationsAndSync:
    def test_is_all_zero(self):
        from src.task import is_all_zero
        assert is_all_zero({}) is True
        assert is_all_zero({"reality": 0.0}) is True
        assert is_all_zero({"reality": 0.0, "nested": {"approved": 0.0}}) is True
        assert is_all_zero({"reality": 1.0}) is False
        assert is_all_zero({"nested": {"approved": 2.5}}) is False

    def test_get_decade_snippet_endpoint(self):
        from src.task import get_decade_snippet_endpoint
        url = "https://gitlab.example.com/api/v4/projects/1/snippets/2"
        assert get_decade_snippet_endpoint(url, 3) == "https://gitlab.example.com/api/v4/projects/1/snippets/3"

    def test_map_raw_node(self):
        from src.task import map_raw_node
        raw = {
            "name": "Income Tax",
            "code": 11,
            "budget": {"approved": 100.0},
            "children": [
                {"name": "Personal Income Tax", "code": 111, "budget": {"approved": 50.0}}
            ],
            "note": "Some note"
        }
        res = map_raw_node(raw)
        assert res["name"] == "Income Tax"
        assert res["code"] == "11"
        assert res["budget"]["approved"] == 100.0
        assert res["items"][0]["name"] == "Personal Income Tax"
        assert res["items"][0]["code"] == "111"
        assert res["note"] == "Some note"

    def test_trickle_up_node_leaf_unchanged(self):
        """Leaf nodes must not gain a calc object and budget stays untouched."""
        from src.task import trickle_up_node
        leaf = {
            "name": "Leaf",
            "code": "L",
            "budget": {"approved": 42.0, "afterChanges": 0.0, "finalBudget": 0.0, "reality": 5.0},
            "items": []
        }
        res = trickle_up_node(leaf)
        assert res["budget"]["approved"] == 42.0
        assert "calc" not in res

    def test_trickle_up_node_non_leaf_adds_calc(self):
        """Non-leaf nodes keep original budget and gain calc.budget + calc.delta."""
        from src.task import trickle_up_node
        node = {
            "name": "Root",
            "code": "",
            "budget": {"approved": 99.0, "afterChanges": 0.0, "finalBudget": 0.0, "reality": 0.0},
            "items": [
                {
                    "name": "A",
                    "code": "A",
                    "budget": {"approved": 10.0, "afterChanges": 20.0, "finalBudget": 0.0, "reality": 5.0},
                    "items": []
                },
                {
                    "name": "B",
                    "code": "B",
                    "budget": {"approved": 15.0, "afterChanges": 5.0, "finalBudget": 0.0, "reality": 2.0},
                    "items": []
                }
            ]
        }
        res = trickle_up_node(node)
        # Original declared budget preserved.
        assert res["budget"]["approved"] == 99.0
        # calc.budget = sum of children.
        assert res["calc"]["budget"]["approved"] == 25.0
        assert res["calc"]["budget"]["afterChanges"] == 25.0
        assert res["calc"]["budget"]["reality"] == 7.0
        # calc.delta = calc.budget - declared budget.
        assert res["calc"]["delta"]["approved"] == 25.0 - 99.0
        # Children are leaves: no calc on them.
        assert "calc" not in res["items"][0]
        assert "calc" not in res["items"][1]

    def test_build_net_detail_empty(self):
        from src.task import build_net_detail
        res = build_net_detail(None, "revenue")
        assert res["net"] == "revenue"
        assert res["approved"] == 0.0
        assert res["items"] == []

    def test_build_whole_budget(self):
        from src.task import build_whole_budget
        rev = {"approved": 100.0, "afterChanges": 110.0, "finalBudget": 0.0, "reality": 90.0}
        exp = {"approved": 80.0, "afterChanges": 85.0, "finalBudget": 0.0, "reality": 70.0}
        res = build_whole_budget(rev, exp)
        assert res["approved"] == 20.0
        assert res["afterChanges"] == 25.0
        assert res["reality"] == 20.0

    def test_merge_item_lists(self):
        from src.task import merge_item_lists
        list1 = [
            {
                "name": "Item 1",
                "code": "1",
                "budget": {"approved": 10.0, "afterChanges": 0.0, "finalBudget": 0.0, "reality": 0.0},
                "items": []
            }
        ]
        list2 = [
            {
                "name": "Item 1 Diff Name",
                "code": "1",
                "budget": {"approved": 15.0, "afterChanges": 0.0, "finalBudget": 0.0, "reality": 0.0},
                "items": []
            }
        ]
        merged = merge_item_lists([list1, list2])
        assert len(merged) == 1
        assert merged[0]["code"] == "1"
        assert merged[0]["budget"]["approved"] == 25.0

    @respx.mock
    def test_sync_yearly_datasets_skips_when_no_chapters(self):
        from src.task import sync_yearly_datasets
        respx.get(f"{ENDPOINT}/files/main/{CHAPTERS_FILENAME}/raw").respond(json=[])
        with httpx.Client() as client:
            sync_yearly_datasets(_make_config(), _make_target(), client)

    def test_build_net_detail_languages(self):
        from src.task import build_net_detail
        
        # Test English ("Revenues")
        raw_rev_en = {
            "name": "Revenues",
            "budget": {"approved": 100.0, "afterChanges": 100.0, "finalBudget": 0.0, "reality": 50.0},
            "children": [
                {"name": "Tax Revenue", "code": "1", "budget": {"approved": 100.0, "afterChanges": 100.0, "finalBudget": 0.0, "reality": 50.0}}
            ]
        }
        res_en = build_net_detail(raw_rev_en, "revenue")
        assert res_en["net"] == "revenue"
        assert res_en["approved"] == 100.0
        assert len(res_en["items"]) == 1
        assert res_en["items"][0]["code"] == "1"

        # Test Czech ("Příjmy")
        raw_rev_cs = {
            "name": "Příjmy",
            "budget": {"approved": 100.0, "afterChanges": 100.0, "finalBudget": 0.0, "reality": 50.0},
            "children": [
                {"name": "Tax Revenue", "code": "1", "budget": {"approved": 100.0, "afterChanges": 100.0, "finalBudget": 0.0, "reality": 50.0}}
            ]
        }
        res_cs = build_net_detail(raw_rev_cs, "revenue")
        assert res_cs["net"] == "revenue"
        assert res_cs["approved"] == 100.0
        assert len(res_cs["items"]) == 1
        assert res_cs["items"][0]["code"] == "1"

    def test_calculate_national_calc(self):
        """calc.budget = trickle-up net; calc.delta = calc.budget - declared national_whole."""
        from src.task import calculate_national_calc

        # national_revenue: official declared total = 150; no items (flat, no trickle-up).
        national_revenue = {
            "net": "revenue",
            "approved": 150.0,
            "afterChanges": 150.0,
            "finalBudget": 150.0,
            "reality": 150.0,
            "items": [],
        }
        # national_expenditure: official declared total = 120.
        national_expenditure = {
            "net": "expenditure",
            "approved": 120.0,
            "afterChanges": 120.0,
            "finalBudget": 120.0,
            "reality": 120.0,
            "items": [],
        }
        # Declared national whole (official): 100 (could differ from rev - exp).
        national_whole = {
            "approved": 100.0,
            "afterChanges": 100.0,
            "finalBudget": 100.0,
            "reality": 100.0,
        }
        # calc.budget = 150 - 120 = 30
        # calc.delta = 30 - 100 = -70
        calc = calculate_national_calc(national_revenue, national_expenditure, national_whole)
        assert calc["budget"]["approved"] == 30.0
        assert calc["delta"]["approved"] == 30.0 - 100.0  # -70.0

    def test_compute_sha256(self):
        """SHA-256 of identical inputs must match; different inputs must not."""
        h1 = compute_sha256("hello world")
        h2 = compute_sha256("hello world")
        h3 = compute_sha256("different")
        assert h1 == h2
        assert h1 != h3
        assert len(h1) == 64
        assert all(c in "0123456789abcdef" for c in h1)

    def test_update_dataset_entry_with_sha256(self):
        """update_dataset_entry stores sha256 when provided and omits it when not."""
        datasets = [
            {
                "periodStart": "2020",
                "periodEnd": "2029",
                "link": "https://example.com/snippets/3",
                "api": "https://example.com/api/v4/snippets/3",
                "datasets": []
            }
        ]
        # With sha256.
        updated = update_dataset_entry(datasets, 2021, "https://example.com/v2021.json", sha256="abc123" + "d" * 58)
        entry = next(ds for ds in updated[0]["datasets"] if ds["year"] == 2021)
        assert entry["sha256"] == "abc123" + "d" * 58
        assert entry["link"] == "https://example.com/v2021.json"

        # Without sha256 (None).
        updated2 = update_dataset_entry(datasets, 2022, "https://example.com/v2022.json", sha256=None)
        entry2 = next(ds for ds in updated2[0]["datasets"] if ds["year"] == 2022)
        assert "sha256" not in entry2

