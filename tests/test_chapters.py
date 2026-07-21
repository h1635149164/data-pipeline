"""tests/test_chapters.py — Unit tests for src/support/chapters.py"""

import json
from datetime import date


from src.support.chapters import (
    DEFAULT_FUZZY_THRESHOLD,
    chapters_to_json,
    fuzzy_match_chapter,
    merge_chapters,
    next_iid,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

CHAPTER_A = {
    "iid": 1,
    "id": 301,
    "name": "Office of the President of the Republic",
    "startDate": "1900-01-01",
    "endDate": "9999-12-31",
    "expired": False,
}

CHAPTER_B = {
    "iid": 2,
    "id": 302,
    "name": "Chamber of Deputies of Parliament of the Czech Republic",
    "startDate": "1900-01-01",
    "endDate": "9999-12-31",
    "expired": False,
}

CHAPTER_C = {
    "iid": 3,
    "id": 303,
    "name": "Senate Parliament of the Czech Republic",
    "startDate": "1900-01-01",
    "endDate": "9999-12-31",
    "expired": False,
}


# ---------------------------------------------------------------------------
# fuzzy_match_chapter
# ---------------------------------------------------------------------------


class TestFuzzyMatchChapter:
    def test_exact_match(self):
        result = fuzzy_match_chapter(CHAPTER_A["name"], [CHAPTER_A, CHAPTER_B])
        assert result is CHAPTER_A

    def test_case_insensitive_exact(self):
        result = fuzzy_match_chapter(
            CHAPTER_A["name"].lower(), [CHAPTER_A, CHAPTER_B]
        )
        assert result is CHAPTER_A

    def test_fuzzy_match_minor_variation(self):
        # A slight rename of the Ministry of Finance.
        existing = [
            {"iid": 1, "name": "Ministry of Finance"},
        ]
        result = fuzzy_match_chapter("Ministry of Finances", existing, threshold=0.80)
        assert result is not None
        assert result["name"] == "Ministry of Finance"

    def test_no_match_below_threshold(self):
        existing = [{"iid": 1, "name": "Ministry of Finance"}]
        result = fuzzy_match_chapter("Totally Different Agency", existing, threshold=DEFAULT_FUZZY_THRESHOLD)
        assert result is None

    def test_empty_existing_list(self):
        result = fuzzy_match_chapter("Anything", [])
        assert result is None

    def test_returns_best_match(self):
        existing = [
            {"iid": 1, "name": "Ministry of Finance"},
            {"iid": 2, "name": "Ministry of Finance and Economics"},
        ]
        # "Ministry of Finance" should score higher for the exact name.
        result = fuzzy_match_chapter("Ministry of Finance", existing, threshold=0.7)
        assert result is not None
        assert result["iid"] == 1


# ---------------------------------------------------------------------------
# next_iid
# ---------------------------------------------------------------------------


class TestNextIid:
    def test_normal_list(self):
        chapters = [{"iid": 1}, {"iid": 3}, {"iid": 2}]
        assert next_iid(chapters) == 4

    def test_single_element(self):
        assert next_iid([{"iid": 5}]) == 6

    def test_empty_list(self):
        assert next_iid([]) == 1


# ---------------------------------------------------------------------------
# merge_chapters
# ---------------------------------------------------------------------------


class TestMergeChapters:
    def test_no_changes_returns_false(self):
        """Identical lists → changed must be False."""
        official = [
            {"id": 301, "name": CHAPTER_A["name"], "startDate": "1900-01-01", "endDate": "9999-12-31", "expired": False},
        ]
        ours = [dict(CHAPTER_A)]
        merged, changed = merge_chapters(official, ours)
        assert changed is False
        assert len(merged) == 1

    def test_new_chapter_appended(self):
        """An official chapter not in ours should be added with next_iid."""
        official = [
            {"id": 301, "name": CHAPTER_A["name"], "startDate": "1900-01-01", "endDate": "9999-12-31", "expired": False},
            {"id": 302, "name": CHAPTER_B["name"], "startDate": "1900-01-01", "endDate": "9999-12-31", "expired": False},
        ]
        ours = [dict(CHAPTER_A)]
        merged, changed = merge_chapters(official, ours)
        assert changed is True
        assert len(merged) == 2
        # The newly added chapter should get iid = 2
        new_ch = next(c for c in merged if c["name"] == CHAPTER_B["name"])
        assert new_ch["iid"] == 2
        assert new_ch["id"] == 302

    def test_existing_chapter_updated_expired(self):
        """When official marks a chapter expired, our mirror should reflect that."""
        official = [
            {
                "id": 363,
                "name": "Supreme building authority",
                "startDate": "2022-01-01",
                "endDate": "2023-12-31",
                "expired": True,
            }
        ]
        ours = [
            {
                "iid": 5,
                "id": 363,
                "name": "Supreme building authority",
                "startDate": "2022-01-01",
                "endDate": "9999-12-31",
                "expired": False,
            }
        ]
        merged, changed = merge_chapters(official, ours)
        assert changed is True
        ch = merged[0]
        assert ch["expired"] is True
        assert ch["endDate"] == "2023-12-31"
        assert ch["iid"] == 5  # iid preserved

    def test_iid_preserved_on_update(self):
        """iid must never change on update."""
        official = [
            {"id": 301, "name": CHAPTER_A["name"], "startDate": "1900-01-01", "endDate": "2025-12-31", "expired": True},
        ]
        ours = [dict(CHAPTER_A)]
        merged, changed = merge_chapters(official, ours)
        assert merged[0]["iid"] == CHAPTER_A["iid"]

    def test_fuzzy_rename_detected(self):
        """A minor spelling variation should be treated as a rename, iid kept."""
        official = [
            {
                "id": 315,
                # Original: "Ministry of the enviroment of the Czech Republic" (typo in real data)
                # Renamed: fixed spelling
                "name": "Ministry of the Environment of the Czech Republic",
                "startDate": "1900-01-01",
                "endDate": "9999-12-31",
                "expired": False,
            }
        ]
        ours = [
            {
                "iid": 7,
                "id": 315,
                "name": "Ministry of the enviroment of the Czech Republic",
                "startDate": "1900-01-01",
                "endDate": "9999-12-31",
                "expired": False,
            }
        ]
        merged, changed = merge_chapters(official, ours, threshold=0.80)
        assert changed is True
        assert len(merged) == 1
        assert merged[0]["iid"] == 7  # iid preserved
        assert merged[0]["name"] == "Ministry of the Environment of the Czech Republic"

    def test_disappeared_chapter_marked_expired(self):
        """A chapter in ours but absent from official should be marked expired."""
        official = [
            {"id": 302, "name": CHAPTER_B["name"], "startDate": "1900-01-01", "endDate": "9999-12-31", "expired": False},
        ]
        ours = [dict(CHAPTER_A), dict(CHAPTER_B)]
        merged, changed = merge_chapters(official, ours)
        assert changed is True
        disappeared = next(c for c in merged if c["iid"] == CHAPTER_A["iid"])
        assert disappeared["expired"] is True
        assert disappeared["endDate"] == date.today().isoformat()

    def test_already_expired_not_re_expired(self):
        """Already-expired chapters in ours should not trigger the disappearance logic again."""
        official = []
        ours = [
            {
                "iid": 3,
                "id": 363,
                "name": "Supreme building authority",
                "startDate": "2022-01-01",
                "endDate": "2023-12-31",
                "expired": True,
            }
        ]
        merged, changed = merge_chapters(official, ours)
        # No change expected — it was already expired
        assert merged[0]["expired"] is True
        assert merged[0]["endDate"] == "2023-12-31"

    def test_empty_official_expires_all_active(self):
        """If the official list is empty, all active ours should be marked expired."""
        ours = [dict(CHAPTER_A), dict(CHAPTER_B)]
        merged, changed = merge_chapters([], ours)
        assert changed is True
        assert all(c["expired"] for c in merged)

    def test_does_not_mutate_input(self):
        """merge_chapters must not modify the input lists."""
        official = [
            {"id": 301, "name": CHAPTER_A["name"], "startDate": "1900-01-01", "endDate": "2025-01-01", "expired": True},
        ]
        ours = [dict(CHAPTER_A)]
        original_ours = dict(ours[0])
        merge_chapters(official, ours)
        assert ours[0] == original_ours


# ---------------------------------------------------------------------------
# chapters_to_json
# ---------------------------------------------------------------------------


class TestChaptersToJson:
    def test_serialises_correctly(self):
        chapters = [dict(CHAPTER_A), dict(CHAPTER_B)]
        result = chapters_to_json(chapters)
        parsed = json.loads(result)
        assert len(parsed) == 2
        assert parsed[0]["iid"] == CHAPTER_A["iid"]

    def test_empty_list(self):
        result = chapters_to_json([])
        assert json.loads(result) == []

    def test_unicode_preserved(self):
        chapters = [{"iid": 1, "name": "Ministerstvo životního prostředí"}]
        result = chapters_to_json(chapters)
        assert "životního prostředí" in result
